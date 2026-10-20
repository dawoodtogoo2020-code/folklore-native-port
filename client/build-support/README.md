# Owner-copy build support

The JSON manifests contain file names and SHA-256 acceptance values only.
They do not contain translated instructions, executable bytes or game assets.
Only the supported USA BCUS98147 disc version 1.00 ELF is accepted.

`prepare_owned_metadata.py` reads that ELF and derives the function descriptors,
TOC addresses and import records. Function ends are clamped to executable
section boundaries. It reproduces the accepted 12,894 function records and
167 imports, including the historical CRLF JSON representation.

`regenerate_ppu.py` and `regenerate_spu.py` generate the private source on the
owner's computer and compare every output against these manifests.
Historical translator source is bundled in `engine/tools/pinned/`, with exact
hashes checked before use. Git and private repository history are unnecessary.

Keep generated metadata, translated PPU/SPU source and executables private.
The manifests prove reproducibility of the accepted translation inputs; they
do not establish complete campaign, graphics or physical-controller behavior.
