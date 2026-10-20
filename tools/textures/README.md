# Generate textures from your own copy

These tools extract supported textures from your own Folklore archives and generate Medium (2x) and High (4x) FTX4 replacement packs. They do not modify the game archives or install the native port. A public playable native runtime is still pending; see [INSTALL.md](../../INSTALL.md).

## Setup

1. Install Python 3.12 and create a virtual environment in a writable work folder: `python -m venv .venv`. Run subsequent commands with that environment's Python (`.venv\Scripts\python.exe` on Windows).
2. Install PyTorch using its [official platform selector](https://pytorch.org/get-started/locally/), then run `python -m pip install -r tools/textures/requirements.txt`. The private reference environment used PyTorch `2.11.0+cu128`; CPU operation uses float32 and can be very slow. This source release does not install a GPU driver or choose a CUDA build for you.
3. Obtain the native 4x `RealESRGAN_x4plus.pth` model from the [official Real-ESRGAN releases](https://github.com/xinntao/Real-ESRGAN/releases). Use a trusted model file. The reference model SHA-256 is `4fa0d38905f75ac06eb49a7951b426670021be3018265fd191d2125df9d682f1`. No weights or model downloader are bundled.
4. Identify your own archive folder containing `etc.fht_`/`etc.bin_`, `model.fht_`/`model.bin_` and `mt.fht_`/`mt.bin_`. Choose a **new** workspace folder outside the game and model folders, with an existing writable parent. Keep only one generation process running for that workspace.

From the repository root, replace the example paths with your own:

```powershell
python tools/textures/generate_owned_pack.py --input-root "D:/MyFolklore/PS3_GAME/USRDIR" --workspace "D:/FolkloreTextureBuild" --model "D:/Models/RealESRGAN_x4plus.pth"
```

`--input-root` must be the actual directory containing the six archive files, which may be a subdirectory of `USRDIR`. Do not use the example unchanged if your files are elsewhere. The default is CPU float32. For a compatible, separately configured NVIDIA environment, add `--device cuda --precision float16 --tile 512`. CUDA generation stops when a Folklore or RPCS3 process is detected. It does not stop that process or automatically retry.

The reference private Medium/High collection occupies about 109 GiB, plus extraction and temporary workspace. This automatic subset can differ. Allow substantial free space; each pack write checks space with a reserve. Completion time and peak memory depend on model, tile size, hardware and source textures. Lower tile size if needed, using a new workspace when changing the recipe.

## Output and resume

The command creates a workspace marker, `catalog/`, `batch/` and `texture-packs/`. Rerun the same command to resume. A different input path, model hash or recipe is refused. Original game data and the model remain read only.

Only after all selected textures have verified Medium and High packs does `texture-packs/pack-index.json` mark both tiers complete. `receipt.json` records hashes, counts and recipe; `runtime_tested` remains false. If a run fails, inspect the error and receipt. Do not manually turn an incomplete index on or delete your source files. Existing corrupted packs are preserved for diagnosis; a new workspace is the straightforward recovery route.

An already working compatible development launcher can select the resulting `texture-packs` folder, then Medium or High. This does not make the public source preview a playable port. The `.frp` format is specific to this renderer and is not an RPCS3 texture replacement package.

## Fidelity and scope

RGB uses native 4x model output blended 40% with 60% bicubic original. Medium RGB is downsampled from High; alpha comes from the original. Data maps, uncertain classifications and unsupported formats remain original. Classification is heuristic. This command does not include the private hand-reviewed catalog or guarantee the exact 4,668-texture private selection, every character, every scene or every effect. Generated game-derived images/packs should remain with their owner's copy.

## Tests and license

```powershell
python -B -m unittest discover -s tools/textures -p "test_*.py"
```

The tests use synthetic archives and images. The portable command is tested with real extraction, classification, pack writing and verification, with model inference replaced by a deterministic image function. This release has not repeated a full GPU upscale or clean-machine game installation.

Original standalone files in this directory are covered by the repository's [personal-use license](../../LICENSE), subject to third-party rights. Dependencies and model weights retain their separate licenses. No game pixels, archives, model weights or executable are included.
