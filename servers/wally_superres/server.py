"""Super-resolution service for the LLM machine (Wally): Real-ESRGAN via spandrel on ROCm.

One component, ``superres`` (RealESRGAN_x2plus and RealESRGAN_x4plus), loaded on
demand and dropped again on request or after a spell of idleness, so the memory
goes back to the llama.cpp router. It follows the generation service's protocol
(servers/wally_gen), so the harness's model stack can account for it:

    GET  /health                              components loaded, peak memory of the last job
    POST /load   {"component": "superres"}    load (and warm up) the models
    POST /unload {"component": ... | null}    drop them
    POST /upscale {image_base64, scale, strength, tile}   -> PNG (base64)

Unlike the generation service it requires the llama.cpp router's API key
("Authorization: Bearer <key>", read from SUPERRES_KEY_FILE) on everything but
/health. Jobs are serialised (one GPU, one job).
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hmac
import io
import os
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
import spandrel
import torch
import torch.nn.functional as F
from fastapi import Depends, FastAPI, Header, HTTPException
from PIL import Image, ImageOps
from pydantic import BaseModel, Field

MODEL_DIR = Path(os.environ.get("SUPERRES_MODEL_DIR", "~/models/superres")).expanduser()
KEY_FILE = Path(os.environ.get("SUPERRES_KEY_FILE", "/etc/evox3/llama-api-key"))
IDLE_UNLOAD_S = int(os.environ.get("SUPERRES_IDLE_UNLOAD_S", "600"))
MODELS = {2: "RealESRGAN_x2plus.pth", 4: "RealESRGAN_x4plus.pth"}
COMPONENT = "superres"
MAX_INPUT_PIXELS = 24_000_000
DEFAULT_TILE = 512
TILE_PAD = 24

Image.MAX_IMAGE_PIXELS = MAX_INPUT_PIXELS * 2
_device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
_dtype = torch.float16 if _device.type == "cuda" else torch.float32
_models: dict[int, torch.nn.Module] = {}
_job_lock = threading.Lock()  # one job at a time; loads and unloads wait for it
_state = {"last_used": 0.0, "load_seconds": None, "peak_gib": None, "error": None}
_key = KEY_FILE.read_text(encoding="utf-8").splitlines()[0].strip()


def require_key(authorization: str = Header(default="")) -> None:
    token = authorization.removeprefix("Bearer ").strip()
    if not hmac.compare_digest(token.encode(), _key.encode()):
        raise HTTPException(401, "invalid or missing API key")


# -- models -------------------------------------------------------------------------------


def _load_locked() -> None:
    if _models:
        return
    started = time.monotonic()
    try:
        for scale, name in MODELS.items():
            desc = spandrel.ModelLoader().load_from_file(str(MODEL_DIR / name))
            _models[scale] = desc.model.eval().to(_device, _dtype)
        # MIOpen compiles kernels per input shape; tiles are always the same shape, so
        # this compiles once (minutes, the very first time) and later hits its disk cache.
        for scale in MODELS:
            _upscale_rgb(np.zeros((DEFAULT_TILE, DEFAULT_TILE, 3), np.uint8), scale, DEFAULT_TILE)
    except Exception as exc:
        _models.clear()
        _state["error"] = f"{exc.__class__.__name__}: {exc}"
        raise
    finally:
        _release_gpu()
    _state.update(load_seconds=round(time.monotonic() - started, 1), error=None)
    _state["last_used"] = time.monotonic()


def _unload_locked() -> None:
    _models.clear()
    _release_gpu()


def _release_gpu() -> None:
    if _device.type == "cuda":
        torch.cuda.empty_cache()


@torch.inference_mode()
def _upscale_rgb(rgb: np.ndarray, scale: int, tile: int) -> np.ndarray:
    """Tiled inference; overlapping padding keeps tile seams invisible.

    Every window the model sees is exactly (tile + 2*TILE_PAD) square: the image is
    padded out to a whole number of tiles, so no odd-sized edge tile triggers a fresh
    MIOpen kernel compile.
    """
    model = _models[scale]
    img = torch.from_numpy(rgb.copy()).permute(2, 0, 1).unsqueeze(0).to(_device, _dtype) / 255.0
    _, _, h, w = img.shape
    pad_b = -h % tile + TILE_PAD
    pad_r = -w % tile + TILE_PAD
    mode = "reflect" if min(h, w) > max(pad_b, pad_r) else "replicate"
    padded = F.pad(img, (TILE_PAD, pad_r, TILE_PAD, pad_b), mode=mode)
    out = torch.empty((1, 3, h * scale, w * scale), dtype=_dtype, device=_device)
    window, inner, edge = tile + 2 * TILE_PAD, tile * scale, TILE_PAD * scale
    for y in range(0, h, tile):
        for x in range(0, w, tile):
            result = model(padded[:, :, y : y + window, x : x + window])
            core = result[:, :, edge : edge + inner, edge : edge + inner]
            th, tw = min(tile, h - y) * scale, min(tile, w - x) * scale
            out[:, :, y * scale : y * scale + th, x * scale : x * scale + tw] = core[:, :, :th, :tw]
    return (out.clamp(0, 1) * 255.0).round().byte().squeeze(0).permute(1, 2, 0).cpu().numpy()


# -- app ------------------------------------------------------------------------------------


async def _idle_unloader() -> None:
    while True:
        await asyncio.sleep(30)
        idle = time.monotonic() - _state["last_used"]
        if _models and idle > IDLE_UNLOAD_S and _job_lock.acquire(blocking=False):
            try:
                _unload_locked()
            finally:
                _job_lock.release()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    task = asyncio.create_task(_idle_unloader())
    yield
    task.cancel()


app = FastAPI(title="superres", lifespan=lifespan)


class LoadRequest(BaseModel):
    component: str = COMPONENT


class UnloadRequest(BaseModel):
    component: str | None = None


class UpscaleRequest(BaseModel):
    image_base64: str
    scale: int = Field(default=2, description="2 or 4")
    strength: float = Field(default=1.0, ge=0.0, le=1.0)
    tile: int = Field(default=DEFAULT_TILE, ge=64, le=2048)


@app.get("/health")
def health() -> dict:
    idle = time.monotonic() - _state["last_used"] if _models else None
    return {
        "status": "ok",
        "busy": _job_lock.locked(),
        "device": torch.cuda.get_device_name(0) if _device.type == "cuda" else "cpu",
        "components": {
            COMPONENT: {
                "loaded": bool(_models),
                "load_seconds": _state["load_seconds"],
                "idle_for": round(idle) if idle is not None else None,
                "peak_gib": _state["peak_gib"],
                "error": _state["error"],
            }
        },
    }


@app.post("/load", dependencies=[Depends(require_key)])
def load(req: LoadRequest) -> dict:
    if req.component != COMPONENT:
        raise HTTPException(404, f"unknown component {req.component!r}")
    with _job_lock:
        try:
            _load_locked()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, f"load failed: {_state['error']}") from exc
    return {"loaded": COMPONENT, "seconds": _state["load_seconds"]}


@app.post("/unload", dependencies=[Depends(require_key)])
def unload(req: UnloadRequest) -> dict:
    if req.component not in (None, COMPONENT):
        raise HTTPException(404, f"unknown component {req.component!r}")
    with _job_lock:
        done = [COMPONENT] if _models else []
        _unload_locked()
    return {"unloaded": done}


@app.post("/upscale", dependencies=[Depends(require_key)])
def upscale(req: UpscaleRequest) -> dict:
    if req.scale not in MODELS:
        raise HTTPException(400, f"scale must be one of {sorted(MODELS)}")
    try:
        src = ImageOps.exif_transpose(Image.open(io.BytesIO(base64.b64decode(req.image_base64))))
        src.load()
    except (binascii.Error, OSError, ValueError) as exc:
        raise HTTPException(400, f"not a readable image: {exc}") from exc
    if src.width * src.height > MAX_INPUT_PIXELS:
        raise HTTPException(413, f"image larger than {MAX_INPUT_PIXELS // 1_000_000} megapixels")
    alpha = src.getchannel("A") if "A" in src.getbands() else None
    rgb = np.asarray(src.convert("RGB"))
    with _job_lock:
        try:
            _load_locked()  # a fallback for callers that skipped /load
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, f"load failed: {_state['error']}") from exc
        started = time.monotonic()
        if _device.type == "cuda":
            torch.cuda.reset_peak_memory_stats()
        try:
            upscaled = _upscale_rgb(rgb, req.scale, req.tile)
        except torch.OutOfMemoryError as exc:
            raise HTTPException(507, "out of GPU memory; retry with a smaller tile") from exc
        finally:
            if _device.type == "cuda":
                _state["peak_gib"] = round(torch.cuda.max_memory_reserved() / 2**30, 2)
            _release_gpu()
            _state["last_used"] = time.monotonic()
        seconds = round(time.monotonic() - started, 2)
    result = Image.fromarray(upscaled, "RGB")
    if req.strength < 1.0:
        # Blend with a plain resize: brings back the fine texture (skin, freckles)
        # the model tends to smooth away, at the cost of some sharpness.
        plain = Image.fromarray(rgb, "RGB").resize(result.size, Image.Resampling.LANCZOS)
        result = Image.blend(plain, result, req.strength)
    if alpha is not None:
        result.putalpha(alpha.resize(result.size, Image.Resampling.BICUBIC))
    buf = io.BytesIO()
    result.save(buf, "PNG", compress_level=6)
    return {
        "png_base64": base64.b64encode(buf.getvalue()).decode(),
        "width": result.width,
        "height": result.height,
        "seconds": seconds,
        "peak_gib": _state["peak_gib"],
    }
