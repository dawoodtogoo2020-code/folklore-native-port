# Windows gameplay acceptance

Use this checklist to compare exact owner builds. The [October 20, 2026 release](https://github.com/dawoodtogoo2020-code/folklore-native-port/releases)
is not the November maintenance branch. Its [setup receipt](owner-build-validation.json)
covers a fresh ZIP on the development PC with prerequisites already installed.
It does not show a clean-machine install, gameplay or physical motion pass.
A regular GitHub source ZIP lacks the owner kit's shader pack.

## Pin the candidate

- Record the release tag or full build-source commit, exact archive name and
  SHA-256, and the built executable's SHA-256. On Windows use
  `Get-FileHash -Algorithm SHA256 -LiteralPath '<file>'` for each artifact.
  Record the shader-pack checksum separately if supplied outside the archive.
  If testing directly from a checkout, record its commit and whether it is clean;
  do not invent an archive checksum.
- Record Windows version, CPU, GPU/driver, renderer and preset, game copy
  (USA BCUS98147 original disc 1.00), and the controller model, firmware,
  connection mode and active motion backend. Follow [owner setup](OWNER_BUILD.md)
  and use a new installation and save profile. Record the machine's starting
  prerequisites, and do not count a development machine as a clean install.

## Run the same sequence at stock 30 FPS

1. **Setup and boot:** Build from the supported owner's files, launch at default
   graphics settings and confirm the title reaches controllable gameplay. Note
   opening movie playback and audio; a launcher saying Ready to Play is not a pass.
2. **Combat:** Record character, scene and encounter. Finish one fight with a
   capturable enemy; note freezes, obvious rendering errors or audio dropouts.
3. **Physical capture:** Leave `PS3RECOMP_CAPTURE_MAPPING` unset or `off`.
   Confirm usable motion samples change when the controller moves, then capture
   the soul by moving the controller. Record device detection and successful
   in-game capture as separate results. For example, a
   [GameSir G7 Pro 8K PC](https://gamesir.com/support/manuals/gamesir-g7-pro-aimlabs-edition)
   has a gyro, but its hardware modes are only candidates: record the actual
   backend used rather than infer support from the product name.
4. **Save and reload:** Save at the next valid point. Record the slot, location and
   captured soul. Exit fully, relaunch, reload that slot and verify the saved
   location or progress and the soul if the game persists it at that point.

## Keep alternate paths separate

- For a 30 FPS accessibility run use a device without usable motion and set
  `PS3RECOMP_CAPTURE_MAPPING=fallback`. Follow [the fallback controls](MOTION_CONTROLS.md).
  If using `force` to isolate synthetic input on a motion-capable device, label
  it as a different mode. Neither can stand in for physical capture.
- After a physical 30 FPS pass, repeat the same capture at 60 FPS if supported by
  the candidate. Keep the scene, controller path and other settings unchanged.
  Record observed cadence, outcome and any timing change. Leave 60 FPS untested
  if the build has no verified way to enable it or if 30 FPS is blocked.

## Record results

Copy the blank receipt per candidate. Use `pass`, `fail`, `blocked` or
`not-run` per row, with a short observation or sanitized evidence reference.
Do not include game files, generated code or binaries, firmware, saves, serial
numbers or personal paths.

| Check | Status | Evidence or blocker |
| --- | --- | --- |
| Clean Windows setup and build | not-run | |
| Native launch through opening to controllable gameplay | not-run | |
| One stock 30 FPS fight | not-run | |
| 30 FPS physical motion detected | not-run | |
| 30 FPS physical capture completed | not-run | |
| Save, exit and reload | not-run | |
| 30 FPS accessibility capture (report mode) | not-run | |
| Matched 60 FPS physical capture | not-run | |

A passing receipt is an early Windows smoke test, not full campaign, performance
or cross-platform certification. Cloud-only tooling tests cannot fill in physical
controller or gameplay rows.
