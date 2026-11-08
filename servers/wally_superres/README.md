# Super-resolution service (runs on the LLM machine)

`server.py` upscales images 2x or 4x with Real-ESRGAN (`RealESRGAN_x2plus`, `RealESRGAN_x4plus`,
loaded through [spandrel](https://github.com/chaiNNer-org/spandrel)) on port 18084. The harness's
`upscale_image` skill calls it, and the harness's model stack (`models.stack.components.superres`)
loads it before a job like the generation service's components. At 3 GiB it fits next to both
router models, so it never evicts anything.

| Endpoint | |
| --- | --- |
| `GET /health` | whether `superres` is loaded, its load time and the peak memory of the last job |
| `POST /load {"component": "superres"}` | load both models and warm them up |
| `POST /unload {"component": "superres" \| null}` | drop them; waits for a running job |
| `POST /upscale {image_base64, scale, strength, tile}` | PNG (base64), its size, GPU seconds |

Everything except `/health` needs the llama.cpp router's key (`Authorization: Bearer ...`, read
from `SUPERRES_KEY_FILE`, default `/etc/evox3/llama-api-key`), so the harness reuses
`~/.harness/secrets/llama-api-key`. `scale` is 2 (default) or 4; `strength` below 1 blends the
model's output with a plain Lanczos resize, which brings back the skin texture Real-ESRGAN tends to
smooth away on portraits at 4x (0.7 is a good start). Phone photos are rotated by their EXIF
orientation first; alpha channels are kept (resized bicubic). Inputs are capped at 24 megapixels.
Loaded models unload themselves after `SUPERRES_IDLE_UNLOAD_S` (600 s) idle.

## Tiling and MIOpen

Images are processed in 512 px tiles with 24 px of overlap, in fp16. Every tile the model sees is
padded to exactly 560x560: MIOpen compiles kernels per input shape, and with ragged edge tiles
every new image size cost minutes of compiling (a 4x job took 432 s instead of 8 s). With one fixed
shape the kernels are compiled once, cached in `~/.cache/miopen`, and loaded in about a second.

## Measured on the EVO X3 (ROCm 7.2 torch 2.14, gfx1151)

1152x2048 phone photo:

| | GPU time | round trip from the harness | peak memory | output |
| --- | --- | --- | --- | --- |
| 2x | 1.7 s | 7 s | 0.7 GiB | 2304x4096, 11 MB PNG |
| 4x | 7.9 s | 23 s | 2.35 GiB | 4608x8192, 37-41 MB PNG |

Loading (both models, warm-up included): 1.3 s with a warm MIOpen cache. Most of the 4x round trip
is PNG encoding and moving 40 MB of base64.

## Install

```bash
~/.local/bin/uv venv --python /usr/bin/python3 ~/venvs/superres
VIRTUAL_ENV=~/venvs/superres ~/.local/bin/uv pip install "torch==2.14.0+rocm7.2" \
    "torchvision==0.29.0+rocm7.2" --index-url https://download.pytorch.org/whl/rocm7.2
VIRTUAL_ENV=~/venvs/superres ~/.local/bin/uv pip install spandrel pillow numpy fastapi "uvicorn[standard]"
mkdir -p ~/models/superres && cd ~/models/superres
curl -fLO https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth
curl -fLO https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.1/RealESRGAN_x2plus.pth
sha256sum *.pth   # x4plus 4fa0d389..., x2plus 49fafd45...
mkdir -p ~/superres && cp server.py ~/superres/server.py
sudo install -m 644 evox3-superres.service /etc/systemd/system/ && sudo systemctl enable --now evox3-superres
```

## Licences

Real-ESRGAN code and weights: BSD-3-Clause. spandrel: MIT.
