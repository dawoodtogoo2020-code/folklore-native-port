# Source preview, 20 October 2026

This download contains current engine, controller and launcher source plus owner-local texture-generation tools. It does **not** contain a playable executable or a finished game installer.

## Included changes

| Area | Included source | Validation boundary |
| --- | --- | --- |
| Rendering | DLSS motion coverage diagnostics, delayed frame IDs, extent reporting, safer timestamp readback, and perspective weights derived from gathered triangle positions | Offline C/C++, shader and synthetic projection tests; gameplay comparison pending |
| Motion controls | SDL accelerometer/gyro conversion and stale-report rejection, DS3 through DsHidMini SXS, GameSir backend lifecycle/reconnection, Nintendo button positions, optional stick-flick accessibility mapping | Mocked guest ABI and backend tests; physical soul-capture acceptance pending |
| Textures | Archive extraction, conservative classification, native 4x inference blended with the original, 2x/4x FTX4 pack writing and verification | Synthetic archive/pack tests; new portable command is separate from the previously tested private pack |
| Launcher | Current display, texture, graphics and profile/shader-cache source | Source export only; artwork and player build inputs omitted |

`engine/` starts from SDK revision `7c7592c731266636fe33927261db8d651bb93668` with renderer commits `f0539a7e36d101ca456f688558527f1c0b223771` and `3ab0a52219fd82aaf79ba257c705c7ed35dd513b` applied. `client/` starts from main revision `18ac8276ab3d19fb357923f8c52fe972d5e2469d`. [The export manifest](source-export.json) records file hashes and exclusions. These are exported snapshots; private repository histories are not included.

The projection change corrects a synthetic case with a nondefault viewport depth range that produced about 2.29 pixels of object-motion error at width 1280. This is mathematical regression evidence, not proof that gameplay jitter is fixed. The final combined source has not been built and accepted as a complete runtime.

## Why a playable installer is still pending

The development builder in `client/scripts/build_owned_backup.py` needs bounded PPU function/import metadata, PPU and SPU acceptance manifests, historical translator revisions and compiled CACAO shader inputs. These inputs are not all present in this snapshot. Some development commands also assume local paths and must be made portable. Running that script is not a supported installation procedure.

The next player milestone is a self-contained generation pipeline that derives the needed metadata and code from each owner's compatible copy, builds with documented dependencies, and passes setup, save and gameplay checks on a clean machine. Do not upload generated PPU/SPU source, executables, game archives or firmware while helping with this work.

## What is deliberately absent

Game artwork and the approximately 109 GiB private Medium/High packs are not included. Those packs transform original game artwork. The public tools instead generate a supported color-texture subset from the owner's archives. Automatic classification is conservative and does not reproduce every manual inclusion in the private catalog or guarantee all-game texture coverage.

Also absent: game executables and translated game code, firmware, keys, saves, shader caches, private captures, launcher artwork, model weights, NVIDIA runtime DLLs and private build history. The private DLSS DLL was explicitly marked nonredistributable and is not a release dependency source.

## Validation

See [validation.json](validation.json) for this snapshot's recorded checks. Synthetic tests cannot establish physical controller behavior, graphics quality, stable FPS, audio continuity or complete campaign correctness. No live game/GPU session was run for this export.

## Licensing

Read [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md). The combined runtime includes GPL-2.0-only material; the top-level personal-use license does not restrict rights granted by the GPL or other component licenses.
