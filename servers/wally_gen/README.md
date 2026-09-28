# Generation service (runs on the LLM machine)

`server.py` serves image generation (FLUX.1-schnell) and image-to-3D (Hunyuan3D-2mini turbo,
untextured GLB) on port 18083. The harness's `generate_image` and `generate_3d_model` skills call
it, and the harness's model stack (`models.stack` in the config) decides what is loaded: before a
job it unloads router models to make room and calls `POST /load`, afterwards `POST /unload`, and it
reloads what it unloaded before the main model continues.

| Endpoint | |
| --- | --- |
| `GET /health` | which components are loaded, GPU memory in use, last load error |
| `POST /load {"component": "image" \| "mesh"}` | load one (blocks until done) |
| `POST /unload {"component": ... \| null}` | unload one or all; waits for a running job |
| `POST /image {prompt, width, height, steps, seed}` | PNG (base64) |
| `POST /mesh {image_base64, remove_background, steps, octree_resolution, max_faces, seed}` | GLB (base64) plus a three-view preview PNG |

A component idle for `GEN_IDLE_UNLOAD_S` (600 s) unloads itself.

## Measured on the EVO X3 (ROCm 7.2 torch 2.14, gfx1151)

| | resident | load | job |
| --- | --- | --- | --- |
| mesh (Hunyuan3D-2mini turbo, 5 steps, octree 256, 40k faces) | 8.4 GiB | 17-65 s (warm/cold cache) | 12-18 s |
| image (FLUX.1-schnell bf16, 4 steps, 1024²) | 31 GiB loaded, 42 GiB peak | 24-32 s | 17 s (the very first run ever: ~8 min of one-time kernel tuning) |
| router: qwen3-coder-30b reload | | ~10 s warm | |
| router: gpt-oss-120b reload | | ~24 s warm | |

Update `gib` in `models.stack.components` when you measure better numbers
(`curl :18083/health` shows `gpu_used_gib` for the whole machine).

## Install (already done on wally)

```bash
cp -a ~/venvs/pixelart ~/venvs/gen          # the ROCm torch + diffusers stack proven by the sprite service
mkdir -p ~/gen && cd ~/gen
git clone --depth 1 https://github.com/Tencent/Hunyuan3D-2.git
~/.local/bin/uv pip install --python ~/venvs/gen/bin/python einops omegaconf trimesh pygltflib \
    scikit-image pymeshlab xatlas rembg onnxruntime opencv-python-headless sentencepiece protobuf \
    accelerate python-multipart
~/.local/bin/uv pip install --python ~/venvs/gen/bin/python --no-deps -e ~/gen/Hunyuan3D-2
HF_HOME=~/models/huggingface hf download tencent/Hunyuan3D-2mini \
    --include "hunyuan3d-dit-v2-mini-turbo/*.yaml" "hunyuan3d-dit-v2-mini-turbo/model.fp16.safetensors" \
              "hunyuan3d-vae-v2-mini-turbo/*.yaml" "hunyuan3d-vae-v2-mini-turbo/model.fp16.safetensors"
cp server.py ~/gen/server.py
sudo install -m 644 evox3-gen.service /etc/systemd/system/ && sudo systemctl enable --now evox3-gen
```

FLUX.1-schnell is gated on Hugging Face (free, Apache-2.0, but you must accept its terms while
logged in): accept them at https://huggingface.co/black-forest-labs/FLUX.1-schnell, create a
read token, then on wally:

```bash
HF_HOME=~/models/huggingface ~/venvs/gen/bin/hf auth login      # paste the token
HF_HOME=~/models/huggingface ~/venvs/gen/bin/hf download black-forest-labs/FLUX.1-schnell \
    --exclude "flux1-schnell.safetensors" "ae.safetensors" "*.jpeg"
```

(The two excluded files are the single-file copies of the same weights; diffusers uses the folders.)

## Licences

FLUX.1-schnell: Apache-2.0. Hunyuan3D-2: Tencent Hunyuan community / non-commercial licence, which
does not cover use in the EU, the UK or South Korea; treat its output as for personal use.
