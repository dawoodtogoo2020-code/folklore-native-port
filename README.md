# Folklore Native Port

Early feasibility research toward a faithful native port of Folklore for PlayStation 3. The project currently evaluates game-specific static recompilation with [PS3Recomp](https://github.com/sp00nznet/ps3recomp), alongside strict instruction and function validation.

**Status: Exploring.** Native game startup, menus and gameplay have not been verified. There is no playable release.

This initial public-source snapshot contains the bounded PPU validation harness and independent synthetic tests. Two short functions from the selected BCUS98147 revision passed native Windows and Linux/WSL tests against a small independent instruction reference, with 1,024 register cases per function and host. These tests establish narrow register agreement. Game traces, hardware equivalence, complete calling contexts, graphics, audio, saves and campaign behavior remain unvalidated. ARM64 compilation has been exercised; ARM64 execution is pending.

Planned native targets are Windows x86-64, Linux x86-64 including Steam Deck, and Android ARM64. Planned optional enhancements include 4K, ultrawide, higher frame rates, DLSS/FSR, high-quality ambient occlusion, anti-aliasing, anisotropic filtering up to 16x, ray/path tracing, and later texture upscaling. Feasibility and performance require separate validation on each target.

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
