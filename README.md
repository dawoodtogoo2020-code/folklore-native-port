# Folklore Native Port

An experimental native port of Folklore for PlayStation 3, using game-specific static recompilation with [PS3Recomp](https://github.com/sp00nznet/ps3recomp).

**The current download is a source preview, not a playable game installer.** It now includes current renderer and motion-control source, launcher source, and tools to generate texture packs from your own game archives. A complete public owner-local game builder is still pending.

| I want to | Go here |
| --- | --- |
| Download the source preview | [GitHub Releases](https://github.com/dawoodtogoo2020-code/folklore-native-port/releases) |
| Find out whether I can install and play | [Installation status](INSTALL.md) |
| Generate textures from my own copy | [Texture tool instructions](tools/textures/README.md) |
| Review changes and validation | [Source preview notes](docs/SOURCE_PREVIEW.md) |
| Read about motion-control support | [Motion controls](docs/MOTION_CONTROLS.md) |
| See known engine problems | [Open issues and fix plans](docs/OPEN_ISSUES.md) |
| Report a reproducible problem | [GitHub Issues](https://github.com/dawoodtogoo2020-code/folklore-native-port/issues) |

## What is included

- `engine/`: the current PS3Recomp development fork, with Vulkan/hybrid rendering, DLSS diagnostics and projection fixes, texture replacement loading and motion-control backends.
- `client/`: authored native boot integration, launcher, platform and development build tools.
- `tools/textures/`: archive extraction, heuristic classification, 2x/4x pack generation, resumable verification and synthetic tests. No game artwork is bundled.
- `scripts/` and `tests/`: the earlier bounded PPU probe and frame-rate constant scanner, with independent synthetic tests.

[The source manifest](docs/source-export.json) identifies exported revisions and file hashes. Private histories, captured game payloads and generated game executables are excluded. See [third-party notices](THIRD_PARTY_NOTICES.md) for component licensing.

## What works and what is pending

Private Windows development builds have reached menus and gameplay, loaded saves, exercised ultrawide presentation and used locally generated texture packs. These observations do not establish a clean public installation or full campaign correctness.

The source adds physical-sensor paths for SDL controllers, original DS3/SXS and GameSir, plus an optional stick-flick mapping and Nintendo face-button positioning. Mocked tests pass; physical soul-capture acceptance remains open.

The isolated renderer changes pass offline compilation, shader validation and synthetic motion tests. They have not been accepted through matched gameplay captures. DLSS jitter/performance, HDR/exposure, asynchronous frame alignment, AO/transparency, audio continuity and 60 FPS garment physics remain active work. Hybrid lighting retains rasterized primary shading and simplified secondary materials; it is not complete source-faithful path tracing. Frame generation is not accepted gameplay functionality.

Windows is the development target. Linux/Steam Deck, Android and macOS do not have validated public player packages. Do not infer gameplay or platform support from source presence or a passing CPU test.

## Validation tools

The small public probe tests require Python 3 and no game files:

```sh
python -B -m unittest discover -s tests
```

Texture tests require Pillow and use synthetic images/archives:

```sh
python -B -m unittest discover -s tools/textures
```

DLSS motion analyzer and projection tests:

```sh
python -B -m unittest discover -s engine/tests -p test_analyze_dlss_motion.py
python -B engine/tests/test_dlss_projection.py --cc clang++ --out build/projection-test
```

`scripts/frame_constants.py --elf path/to/EBOOT.elf` scans an owner's decrypted executable for timing-constant leads. It does not change the game or prove which subsystem controls physics. `scripts/leaf_probe.py --help` describes the bounded PPU probe. Neither command installs the port.

## License and collaboration

Original standalone material identified in [LICENSE](LICENSE) is source available under a custom personal-use license. The combined engine/native runtime includes GPL-2.0-only material and retains GPL rights, with MIT/LGPL/other notices for its components. Root personal-use restrictions do not override those rights. Read [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) before reuse or distribution.

See [CONTRIBUTING.md](CONTRIBUTING.md) for collaboration. Do not post game assets, firmware, keys, saves, generated game code or private diagnostics in issues. This is an unofficial project; no rights to Folklore content or trademarks are granted.
