# Source and component notices

This repository contains differently licensed components. Its root [LICENSE](LICENSE) applies only within its stated scope. It must not be applied to restrict rights in GPL, LGPL, MIT, BSD or other third-party material.

| Component | Notice |
| --- | --- |
| PS3Recomp upstream | Copyright 2026 Ned Heller / sp00nznet; [MIT](engine/LICENSE). Upstream: https://github.com/sp00nznet/ps3recomp |
| Combined runtime and Folklore native integration | GPL-2.0-only; [license text](engine/COPYING.RPCS3). RPCS3 semantic/derived references use revision `4d5def5880217135554ed434cd5055cceebe9cc2`. File notices identify SPU FPSCR, MSAA, shadow-sampling and input conversions. Upstream: https://github.com/RPCS3/rpcs3 |
| Standalone ATRAC3plus code | [LGPL-2.1](engine/third_party/at3_standalone/COPYING.LGPLv2.1); retain its source notices |
| AMD CACAO | [MIT license](engine/third_party/cacao/license.txt), [additional notices](engine/third_party/cacao/NOTICES.txt), [pinned upstream](engine/third_party/cacao/UPSTREAM.json) |
| AMD FSR1 | [MIT license](engine/third_party/fsr1/LICENSE.txt), [source receipt](engine/third_party/fsr1/source.json) |
| NVIDIA FXAA source | License and attribution embedded in [Fxaa3_11.h](engine/third_party/fxaa/Fxaa3_11.h); [source receipt](engine/third_party/fxaa/source.json) |
| libucontext patch | [ISC license](engine/third_party/libucontext/LICENSE); [provenance](engine/third_party/libucontext/manifest.json). External library source is a separate dependency |
| Original standalone texture tools and standalone client tooling | Personal-use license in [LICENSE](LICENSE), except where a file or third-party license states otherwise |

No Folklore game assets, game-derived executable, Sony firmware, model weights, NVIDIA DLSS DLL, FFmpeg binary or system toolchain is distributed in this source preview. Folklore and associated artwork/trademarks belong to their respective owners; this project is unofficial.

The optional texture model is obtained separately from the [official Real-ESRGAN project](https://github.com/xinntao/Real-ESRGAN), whose repository publishes a [BSD 3-Clause license](https://github.com/xinntao/Real-ESRGAN/blob/master/LICENSE). That license does not grant redistribution rights to game-derived textures. PyTorch, Pillow, NumPy, Spandrel and system dependencies retain their own licenses.
