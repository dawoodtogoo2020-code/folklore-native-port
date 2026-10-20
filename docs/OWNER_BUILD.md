# Build and install from your own copy

This experimental Windows build kit generates the game-specific executable on
your computer. It includes the engine, launcher, motion-control support and
validated public CACAO shaders. It contains no game files, firmware, translated
game code, model weights or NVIDIA runtime DLLs.

Only **Folklore USA BCUS98147, original disc version 1.00** is accepted.
Other regions and updated executables fail the identity checks.
Use Windows x64 with a Vulkan-capable graphics driver and at least 10 GB free
for the generated build, in addition to the game and toolchain. Choose a short
installation path, such as `D:\Games\Folklore-Native`; setup rejects a save
profile path longer than the native build supports before starting compilation.

## Prepare the dependencies once

Install [Python](https://www.python.org/downloads/windows/) with Tkinter and
[MSYS2](https://www.msys2.org/). Python 3.12 is the tested version.
Choose a drive with room for the toolchain, generated source and build files.
Keep the extracted source kit outside your game backup and installation folder.

Open the **MSYS2 CLANG64** terminal, update MSYS2 as its installer instructs,
then install:

```sh
pacman -S --needed mingw-w64-clang-x86_64-clang mingw-w64-clang-x86_64-cmake mingw-w64-clang-x86_64-ninja mingw-w64-clang-x86_64-SDL2 mingw-w64-clang-x86_64-ffmpeg mingw-w64-clang-x86_64-vulkan-headers mingw-w64-clang-x86_64-glslang mingw-w64-clang-x86_64-spirv-cross mingw-w64-clang-x86_64-zlib
```

Use the [CLANG64 environment](https://www.msys2.org/docs/environments/);
do not mix libraries from its other environments. The local validation used
Clang 23.1.3, CMake 4.4.4, Ninja 1.13.2, SDL 2.32.10, FFmpeg 9.0.2,
glslang 16.6.0, Vulkan headers/SPIRV-Cross 1.4.363 and zlib 1.3.2.
MSYS2 is a rolling distribution; later versions require fresh validation.

## Run setup

1. Download and extract the **Owner Build Kit** from
   [GitHub Releases](https://github.com/dawoodtogoo2020-code/folklore-native-port/releases).
   The ordinary GitHub source ZIP does not contain the compiled shader pack.
2. Run `Install.py` with Python. Choose your disc backup, your decrypted
   `EBOOT.elf`, your firmware `dev_flash`, a new installation folder and your
   MSYS2 `clang64` folder.
3. Select **Build and set up**. Setup validates your copy, regenerates and checks
   PPU/SPU code, compiles the native executable and creates a separate save
   profile. Build logs stay in the selected installation folder.
4. Select **Open launcher**, then choose your settings and Play.
   Keep the source kit, MSYS2, Python, disc and firmware at their original paths.
   Later, run `Launch.py` with Python in the installation folder to reopen the
   launcher. A `Launch.ps1` helper is also provided for PowerShell users.

Setup defaults to two compiler processes. It never starts gameplay automatically.
The default launcher configuration uses 30 FPS, original aspect and raster
rendering. DLSS remains unavailable without a separately verified runtime.
The public package does not supply the nonredistributable development DLL.

The graphical setup requires the shader pack at `shaders/` inside the kit.
If you are using a source checkout, extract the separate CACAO shader download
there, or generate it with `engine/tools/build_cacao_shaders.py`.

## Command-line setup

Use absolute paths. The following paths are examples; the output is not restricted
to a particular drive:

```powershell
python -B .\client\scripts\setup_owned_copy.py `
  --disc "D:\Games\Folklore-Disc" `
  --elf "D:\Games\Folklore-Disc\PS3_GAME\USRDIR\EBOOT.elf" `
  --dev-flash "D:\PS3-Firmware\dev_flash" `
  --workspace "D:\Games\Folklore-Native" `
  --prefix "D:\msys64\clang64" `
  --shaders "D:\Folklore-Build-Kit\shaders"
```

`--prepare-only` verifies translation without compiling or creating launcher
settings. To continue with the same source and inputs, repeat the command with
`--resume`. A changed input or source snapshot is rejected; use a new output
folder when updating the kit. Existing save files and launcher graphics settings
are preserved on a valid resume.

Game-specific output remains private. Do not upload the generated executable,
metadata, PPU/SPU sources, game archives, firmware or your save profile.

## Textures and controllers

The native build includes the published motion-control backends. See
[controller requirements and validation limits](MOTION_CONTROLS.md).
Physical soul capture and every controller combination are not established by
the synthetic tests.

Generate optional Medium/High replacement packs from your own archives using
[the texture tools](../tools/textures/README.md), then choose their folder in the
launcher. Texture generation requires its separate Python/ML dependencies.
The 109 GiB private texture pack is not included in the build kit.

## Validation boundary

See the accompanying release notes and validation receipt for the exact tested
build, startup evidence and remaining limitations. A successful build alone
does not prove complete campaign compatibility, stable frame rates, correct
garment physics or physical motion capture.
