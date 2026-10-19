# Folklore Native Port

An experimental native port of Folklore for PlayStation 3, using game-specific static recompilation with [PS3Recomp](https://github.com/sp00nznet/ps3recomp).

**Development status, 20 October 2026:** private Windows development builds have reached menus and gameplay, loaded saves, and exercised ultrawide rendering. The public repository below remains an earlier research/tools snapshot. It does not yet contain the current playable runtime or a complete player-facing builder.

## Download and play

**New here? Read the short [installation and launcher guide](INSTALL.md) first.**

**There is currently no downloadable playable release.** GitHub's Code > Download ZIP downloads the research sources, not a game you can launch. See [Releases](https://github.com/dawoodtogoo2020-code/folklore-native-port/releases) for published packages when available.

The intended player package will build locally from the player's own compatible Folklore USA (BCUS98147, disc 1.00) backup and decrypted executable. It must not bundle game assets, firmware, keys, or generated game code. The current local build flow still depends on developer metadata, manifests, SDK history and toolchain paths; a clean-machine setup/build/play flow has not been validated. A ready-to-play download cannot be offered yet.

## Current development features and limitations

These describe local development builds, not features included in this public source snapshot. Different candidates contain different features; no single fully validated release combines all of them.

- **Ultrawide:** the existing Windows launcher offers 3440x1440 fullscreen with output camera aspect and HUD scaling. Menu/tutorial transitions, HUD particle overflow and broader scene coverage still need work.
- **DLSS Super Resolution:** gameplay evaluations and presentation have been exercised in an experimental graphics candidate. **DLSS performance is currently not optimal.** One RTX 5080 test at 2560x1440 output recorded about 18.25 FPS with hybrid PT plus DLSS Quality, versus about 26.27 FPS for the PT-only test. These are separate scene measurements, not a controlled universal comparison. The tested DLSS development runtime also shows a watermark. Frame generation is not accepted gameplay functionality.
- **Path tracing:** improvements are ongoing. The current experiment uses rasterized primary visibility with traced shadows, bounces and reflections. Many secondary materials remain generic, temporal reprojection is incomplete, and visible water reflections are unproven. This is hybrid rendering, not complete source-faithful full path tracing.
- **Performance and gameplay:** stable timing-correct 60 FPS is not established. Audio/music hitches, coat/hair physics, controller soul-capture motion and some AO/transparency transitions remain open. Physical controller acceptance and complete campaign coverage are pending.
- **Platforms:** Windows is the main development target. Linux/Steam Deck require further validation; Android remains a planned target with implementation work parked. macOS is a longer-term goal. No public playable package is available for any platform yet.

The immediate release work is a self-contained owned-copy builder, dependency/license review, clean-machine installation and launch validation, and broader gameplay/save testing. Current executables remain private because they include locally generated game-derived code.

## Public source snapshot

The public snapshot contains a bounded PPU validation harness and independent synthetic tests. Two short functions from the selected BCUS98147 revision passed native Windows and Linux/WSL tests against an independent instruction reference, with 1,024 register cases per function and host. These narrow checks do not establish full game correctness, hardware equivalence, campaign coverage or other-platform gameplay.

## Validation tools

The harness accepts an explicitly selected local executable and upstream tool checkout. It restricts the experiment to a small whitelist of straight-line integer register operations. Unsupported instructions, unresolved generated bodies, calls, fallthrough and comparison failures produce errors. Success receipts record input/source/executable hashes, compiler details and upstream identity.

Run synthetic tests with Python 3:

```sh
python -m unittest discover -s tests -v
```

Inspect the probe's supported arguments with:

```sh
python scripts/leaf_probe.py --help
```

Synthetic tests require no game files, compiler or upstream checkout. Probe runs require the selected compatible local inputs, PS3Recomp tools, Git and a suitable host C compiler. Test results from Windows/WSL do not establish Steam Deck or Android gameplay.

This source snapshot contains no game assets, firmware, keys, generated game code, private paths, raw traces or downloaded game packages.

## License and collaboration

The original material identified in [LICENSE](LICENSE) uses a custom personal-use license. You may download, inspect, build, run and privately modify it for personal, noncommercial use. Reusing its code in another project, redistributing it or using it commercially requires the maintainer's prior written permission, subject to the license's GitHub, third-party and legal exceptions. This is source-available software, not open-source software.

Collaboration is welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) to request permission or propose a contribution. Private code and unpublished information require separately approved access. Public files are not confidential, and GitHub's viewing and forking rights remain intact.

These terms cover only independently owned material within the license's stated scope. They do not replace third-party licenses, restrict rights in GPL-covered runtime code or authorize distribution of game or firmware content. Any future builder or runtime release must retain its applicable component licenses and meet their source-distribution requirements.
