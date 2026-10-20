# Install from your own copy

The experimental **Windows Owner Build Kit** can generate and compile a native
executable from a supported owner's copy. It creates launcher settings and a
separate save folder. The complete ZIP setup flow has passed on the development
PC, including launcher validation. Clean-machine and gameplay acceptance for
this exact kit remain pending.

1. Download **Folklore-Owner-Build-Kit-2026.10.20.zip** from
   [GitHub Releases](https://github.com/dawoodtogoo2020-code/folklore-native-port/releases).
2. Follow [the dependency and setup instructions](docs/OWNER_BUILD.md).
3. Run `Install.py`, choose your files and a short, new installation folder,
   then select **Build and set up**.
4. When setup finishes, open the launcher. Start with its default graphics settings.

You need your own **Folklore USA BCUS98147, original disc version 1.00** backup,
the matching decrypted `EBOOT.elf`, and an extracted firmware `dev_flash` folder.
See the [RPCS3 quickstart guide](https://rpcs3.net/quickstart) for background on
preparing your own backup and firmware. Setup does not download or decrypt them.

The kit includes source, a setup interface and public shaders. Python with
Tkinter and MSYS2 CLANG64 build dependencies are separate prerequisites.
It contains no game files or prebuilt game executable. The older source-preview
download and GitHub's ordinary source ZIP do not include the shader pack.

The built runtime includes the published [motion-control backends](docs/MOTION_CONTROLS.md).
Optional replacement textures are generated locally with the
[texture tools](tools/textures/README.md). Physical soul capture and full-game
texture coverage still need validation.

Known development limitations include possible opening-movie hangs, frame
pacing, audio continuity, garment physics and incomplete campaign coverage.
DLSS is unavailable in the kit; its development DLL is not redistributable.
See [open issues](docs/OPEN_ISSUES.md). Linux/Steam Deck, Android and macOS do
not yet have validated player packages.

Keep generated game code, executables, firmware and save profiles private.
Report reproducible setup failures through
[GitHub Issues](https://github.com/dawoodtogoo2020-code/folklore-native-port/issues),
with personal paths removed from logs.
