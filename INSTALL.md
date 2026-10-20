# Can I install and play it yet?

**Short answer: not yet.** When a player package is ready, it will appear on the [Releases page](https://github.com/dawoodtogoo2020-code/folklore-native-port/releases) with step-by-step instructions.

The video shows a private Windows development build. This repository currently contains research and validation tools. **Code > Download ZIP will not give you the launcher or a playable game.**

Please do not follow the validation commands in the README as game-installation instructions.

## What you will need

The planned player package will require your own Folklore USA disc 1.00 backup, title ID **BCUS98147**, and your own decrypted **EBOOT.elf**. Game files are not included. See the [RPCS3 quickstart guide](https://rpcs3.net/quickstart) for background on preparing your own PS3 game backup and firmware.

## Planned launcher setup

These describe the existing development launcher's directory choices. They are **not working public installation steps**: the self-contained player builder has not been released.

| Launcher field | What you choose |
| --- | --- |
| Disc backup | Your folder containing `PS3_DISC.SFB` and `PS3_GAME` |
| Decrypted executable | Your `PS3_GAME/USRDIR/EBOOT.elf` |
| Firmware | Your own firmware installation's `dev_flash` folder, if required by the released package |
| Save profile | A separate writable `dev_hdd0` folder for saves and installed data |
| Native build | The executable generated locally from your own files by the player builder |

The intended flow is: **choose your own files, build locally, choose display settings, then Play**. No developer's drive letters or personal folders should be needed. A release must validate this flow on a clean computer before these become installation instructions.

## Why the download is pending

The development builder still needs SDK history, generation metadata, acceptance manifests, toolchains, dependencies and shader inputs that are absent from this public snapshot. The local game-derived executable cannot simply replace that builder in a public package. Packaging and component-license review remain open.

## Platforms and features

Windows is the current gameplay development target. Linux and Android are planned targets; macOS is a longer-term goal. Public playable packages are not available for these platforms yet.

Ultrawide, experimental 60 FPS, hybrid path tracing and DLSS have been exercised in different development configurations. DLSS is currently slower in the tested graphics candidate. Audio continuity, garment physics, full campaign coverage, ultrawide transitions and path-tracing fidelity still need work. See [Open engine issues](docs/OPEN_ISSUES.md) for the current fix plans. The showcase uses **PT off and DLSS off**; do not treat it as a path-tracing benchmark or a guarantee of stable 60 FPS.

For updates, follow [the repository](https://github.com/dawoodtogoo2020-code/folklore-native-port). Report reproducible issues through [GitHub Issues](https://github.com/dawoodtogoo2020-code/folklore-native-port/issues).
