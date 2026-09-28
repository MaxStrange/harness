"""Image and 3D generation service for the LLM machine (Wally).

Two components, each loaded on demand and dropped again on request or after a
spell of idleness, so the memory goes back to the llama.cpp router:

    image   FLUX.1-schnell (diffusers)             text -> PNG
    mesh    Hunyuan3D-2mini turbo + rembg          image -> GLB (untextured)

The harness decides what is resident: before a generation skill runs it unloads
router models to make room and calls POST /load here, and afterwards POST
/unload and reloads the models it evicted. This service therefore never loads
anything implicitly except as a fallback when a job arrives for a component
that is not loaded (a caller that skipped /load).

Generation is serialised (one GPU, one job). Everything runs in fp16/bf16 on
ROCm; see the notes in the sprite service (~/pixelart/server.py) for why peft
LoRA layers are avoided on this stack.
"""

from __future__ import annotations

import asyncio
import base64
import gc
import io
import os
import threading
import time
from contextlib import asynccontextmanager
from typing import Literal

import torch
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

IMAGE_MODEL = os.environ.get("GEN_IMAGE_MODEL", "black-forest-labs/FLUX.1-schnell")
MESH_MODEL = os.environ.get("GEN_MESH_MODEL", "tencent/Hunyuan3D-2mini")
MESH_SUBFOLDER = os.environ.get("GEN_MESH_SUBFOLDER", "hunyuan3d-dit-v2-mini-turbo")
IDLE_UNLOAD_S = int(os.environ.get("GEN_IDLE_UNLOAD_S", "600"))
OUT_DIR = os.environ.get("GEN_OUT_DIR", "/home/max/gen/out")

Component = Literal["image", "mesh"]
COMPONENTS: tuple[str, ...] = ("image", "mesh")


class _Slot:
    def __init__(self, name: str) -> None:
        self.name = name
        self.value = None  # the loaded pipeline(s)
        self.last_used = 0.0
        self.load_seconds: float | None = None
        self.error: str | None = None  # last load failure, reported by /health


_slots = {name: _Slot(name) for name in COMPONENTS}
_job_lock = threading.Lock()  # one generation at a time
_load_lock = threading.Lock()  # loads and unloads never overlap
_busy: str | None = None


def _load_image():
    from diffusers import FluxPipeline

    pipe = FluxPipeline.from_pretrained(IMAGE_MODEL, torch_dtype=torch.bfloat16)
    pipe.to("cuda")
    pipe.set_progress_bar_config(disable=True)
    return pipe


def _load_mesh():
    from hy3dgen.rembg import BackgroundRemover
    from hy3dgen.shapegen import Hunyuan3DDiTFlowMatchingPipeline

    pipe = Hunyuan3DDiTFlowMatchingPipeline.from_pretrained(
        MESH_MODEL, subfolder=MESH_SUBFOLDER, use_safetensors=True, variant="fp16", device="cuda"
    )
    # FlashVDM with plain marching cubes: 'dmc' needs the CUDA-only diso extension.
    pipe.enable_flashvdm(mc_algo="mc")
    return {"pipe": pipe, "rembg": BackgroundRemover()}


_LOADERS = {"image": _load_image, "mesh": _load_mesh}


def _gpu_gib() -> float | None:
    try:
        free, total = torch.cuda.mem_get_info()
        return round((total - free) / 2**30, 1)
    except Exception:  # noqa: BLE001 - diagnostics only
        return None


def _ensure(name: str) -> object:
    slot = _slots[name]
    if slot.value is not None:
        return slot.value
    with _load_lock:
        if slot.value is None:
            started = time.time()
            try:
                slot.value = _LOADERS[name]()
            except Exception as exc:
                slot.error = f"{type(exc).__name__}: {exc}"
                _release()
                raise
            slot.error = None
            slot.load_seconds = round(time.time() - started, 1)
            slot.last_used = time.time()
            print(f"[gen] loaded {name} in {slot.load_seconds}s", flush=True)
    return slot.value


def _release() -> None:
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.synchronize()


def _unload(name: str) -> bool:
    slot = _slots[name]
    if slot.value is None:
        return False
    slot.value = None
    _release()
    print(f"[gen] unloaded {name}", flush=True)
    return True


async def _idle_reaper():
    while True:
        await asyncio.sleep(30)
        now = time.time()
        for slot in _slots.values():
            if slot.value is not None and now - slot.last_used > IDLE_UNLOAD_S and _busy is None:
                with _load_lock:
                    if slot.value is not None and time.time() - slot.last_used > IDLE_UNLOAD_S:
                        _unload(slot.name)


@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs(OUT_DIR, exist_ok=True)
    task = asyncio.create_task(_idle_reaper())
    yield
    task.cancel()


app = FastAPI(title="wally generation service", lifespan=lifespan)


class LoadRequest(BaseModel):
    component: Component


class UnloadRequest(BaseModel):
    component: Component | None = None  # None = everything


class ImageRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=2000)
    width: int = Field(1024, ge=256, le=1536, multiple_of=16)
    height: int = Field(1024, ge=256, le=1536, multiple_of=16)
    steps: int = Field(4, ge=1, le=12)  # schnell is distilled for ~4
    seed: int | None = None


class MeshRequest(BaseModel):
    image_base64: str = Field(..., description="PNG or JPEG, base64")
    remove_background: bool = True
    steps: int = Field(5, ge=1, le=50)  # the turbo model is distilled for ~5
    octree_resolution: int = Field(256, ge=128, le=512)
    max_faces: int | None = Field(40000, ge=1000, le=500000)
    seed: int | None = None


@app.get("/health")
def health():
    return {
        "status": "ok",
        "busy": _busy,
        "gpu_used_gib": _gpu_gib(),
        "components": {
            name: {
                "loaded": slot.value is not None,
                "load_seconds": slot.load_seconds,
                "idle_for": round(time.time() - slot.last_used, 1) if slot.last_used else None,
                "error": slot.error,
            }
            for name, slot in _slots.items()
        },
    }


@app.post("/load")
def load(req: LoadRequest):
    try:
        _ensure(req.component)
    except Exception as exc:
        raise HTTPException(
            500, f"loading {req.component} failed: {type(exc).__name__}: {exc}"
        ) from exc
    return {"loaded": req.component, "seconds": _slots[req.component].load_seconds}


@app.post("/unload")
def unload(req: UnloadRequest):
    names = [req.component] if req.component else list(COMPONENTS)
    with _job_lock, _load_lock:  # never pull a model out from under a running job
        done = [name for name in names if _unload(name)]
    return {"unloaded": done}


def _render_preview(mesh, size: int = 360) -> bytes:
    """Three shaded views of ``mesh`` side by side (flat shading, painter's algorithm)."""
    import numpy as np
    from PIL import Image, ImageDraw

    vertices = mesh.vertices - mesh.bounds.mean(axis=0)
    vertices = vertices / (np.abs(vertices).max() or 1.0)
    light = np.array([0.4, 0.5, 0.75]) / np.linalg.norm([0.4, 0.5, 0.75])
    tilt = np.array([[1, 0, 0], [0, np.cos(0.3), -np.sin(0.3)], [0, np.sin(0.3), np.cos(0.3)]])
    sheet = Image.new("RGB", (size * 3, size), (40, 44, 52))
    for view, yaw in enumerate((0.6, 2.2, 3.8)):
        c, s = np.cos(yaw), np.sin(yaw)
        rotation = np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]]) @ tilt
        tri = (vertices @ rotation.T)[mesh.faces]
        normals = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
        normals /= np.linalg.norm(normals, axis=1, keepdims=True) + 1e-9
        shade = np.clip(normals @ light, 0, 1)
        panel = Image.new("RGB", (size, size), (40, 44, 52))
        draw = ImageDraw.Draw(panel)
        half, scale = size / 2, size * 0.43
        for i in np.argsort(tri[:, :, 2].mean(axis=1)):
            if normals[i, 2] <= 0:
                continue
            grey = int(60 + 180 * shade[i])
            points = [(half + x * scale, half - y * scale) for x, y, _ in tri[i]]
            draw.polygon(points, fill=(grey, grey, int(grey * 0.9)))
        sheet.paste(panel, (view * size, 0))
    buf = io.BytesIO()
    sheet.save(buf, format="PNG")
    return buf.getvalue()


def _seed(seed: int | None) -> int:
    return seed if seed is not None else int.from_bytes(os.urandom(4), "little") & 0x7FFFFFFF


def _stamp(kind: str, seed: int, ext: str) -> str:
    return os.path.join(OUT_DIR, f"{time.strftime('%Y%m%d-%H%M%S')}-{kind}-{seed}.{ext}")


@app.post("/image")
def image(req: ImageRequest):
    global _busy
    with _job_lock:
        _busy = "image"
        try:
            pipe = _ensure("image")
            seed = _seed(req.seed)
            started = time.time()
            result = pipe(
                req.prompt,
                width=req.width,
                height=req.height,
                num_inference_steps=req.steps,
                guidance_scale=0.0,
                max_sequence_length=256,
                generator=torch.Generator("cpu").manual_seed(seed),
            ).images[0]
            seconds = round(time.time() - started, 1)
            buf = io.BytesIO()
            result.save(buf, format="PNG")
            png = buf.getvalue()
            path = _stamp("image", seed, "png")
            with open(path, "wb") as f:
                f.write(png)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(500, f"{type(exc).__name__}: {exc}") from exc
        finally:
            _slots["image"].last_used = time.time()
            _busy = None
    return {
        "png_base64": base64.b64encode(png).decode(),
        "path": path,
        "seed": seed,
        "width": req.width,
        "height": req.height,
        "seconds": seconds,
    }


@app.post("/mesh")
def mesh(req: MeshRequest):
    global _busy
    from PIL import Image

    try:
        picture = Image.open(io.BytesIO(base64.b64decode(req.image_base64)))
        picture.load()
    except Exception as exc:
        raise HTTPException(400, f"image_base64 is not a readable image: {exc}") from exc
    with _job_lock:
        _busy = "mesh"
        try:
            parts = _ensure("mesh")
            seed = _seed(req.seed)
            started = time.time()
            picture = picture.convert("RGBA")
            if req.remove_background:
                picture = parts["rembg"](picture.convert("RGB"))
            result = parts["pipe"](
                image=picture,
                num_inference_steps=req.steps,
                octree_resolution=req.octree_resolution,
                num_chunks=20000,
                generator=torch.manual_seed(seed),
                output_type="trimesh",
            )[0]
            from hy3dgen.shapegen import DegenerateFaceRemover, FaceReducer, FloaterRemover

            result = FloaterRemover()(result)
            result = DegenerateFaceRemover()(result)
            if req.max_faces and len(result.faces) > req.max_faces:
                result = FaceReducer()(result, max_facenum=req.max_faces)
            seconds = round(time.time() - started, 1)
            path = _stamp("mesh", seed, "glb")
            result.export(path)
            with open(path, "rb") as f:
                glb = f.read()
            preview = _render_preview(result)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(500, f"{type(exc).__name__}: {exc}") from exc
        finally:
            _slots["mesh"].last_used = time.time()
            _busy = None
    return {
        "glb_base64": base64.b64encode(glb).decode(),
        "preview_png_base64": base64.b64encode(preview).decode(),
        "path": path,
        "seed": seed,
        "vertices": int(len(result.vertices)),
        "faces": int(len(result.faces)),
        "seconds": seconds,
    }
