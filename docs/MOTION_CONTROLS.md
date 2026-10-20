# Motion-control source

The current input implementation is in [engine/libs/input](../engine/libs/input). It includes:

- SDL controller accelerometer and gyro conversion, with freshness and read-failure checks before publishing guest sensor reports.
- A Windows original DS3 path for DsHidMini SXS feature reports.
- A GameSir raw-motion backend with explicit device selection, freshness checks, reconnect and nonblocking shutdown behavior.
- Nintendo face-button mapping by physical position, matching PS3 positions.
- Optional elapsed-time stick-flick accessibility motion. With `PS3RECOMP_CAPTURE_MAPPING=fallback`, devices without usable motion can use R1 plus right-stick up; the keyboard equivalent is W plus Up. `force` selects the synthetic mapping even for motion-capable devices. Default `off` preserves physical input behavior.

These settings apply to a compatible built runtime, not to the source ZIP itself. Generated pulses are calibration candidates; the guest still decides whether a soul-capture gesture succeeds. Installing a driver is not part of this release.

Mocked tests cover guest ABI reporting, stale samples, raw-backend selection and lifecycle behavior. They do not establish successful physical soul capture on every controller, USB/Bluetooth path or frame rate. Physical gameplay acceptance remains open.

Useful source tests: `test_pad_ds3_integration.c`, `test_pad_g7_integration.c`, `test_folklore_pad_motion_integration.c`, `test_ds3_motion_backend.c`, and `libs/input/tests/test_pad_motion_sdl.c`. See [validation.json](validation.json) for the checks actually run for this export.
