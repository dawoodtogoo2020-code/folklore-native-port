# Folklore recomp maintenance

The owner-build project vendors its own engine snapshot. Updating the upstream
ps3recomp fork does not update this kit. Keep a pinned, independently tested
Windows baseline before trying newer engine code.

## November 27, 2026

- Corrected the static timing scanner to match the load width, invalidate the
  register range overwritten by `lmw`, and include the selected address window
  in JSON. Results are still static leads, not timing or gameplay evidence.
- The owner texture workflow now leaves both pack tiers disabled if extraction
  misses required inputs or a tier contains unverified .frp files. It preserves
  the inputs and the unverified packs instead of deleting them.
- The upstream [Folklore change #447](https://github.com/sp00nznet/ps3recomp/pull/447)
  was closed unmerged. Do not merge the old mega-PR or use it as a wholesale
  update. Dependency/build wiring and FPSCR cleanup can be reviewed separately.
  The [renderer provenance draft](https://github.com/dawoodtogoo2020-code/ps3recomp/pull/1)
  does not establish MSAA sample order or shadow behavior; keep attribution and
  isolate unresolved changes.
- [ps3recomp v0.20.0](https://github.com/sp00nznet/ps3recomp/releases/tag/v0.20.0)
  is a comparison target, not a replacement pin. The [RSX foliage candidate
  #815](https://github.com/sp00nznet/ps3recomp/pull/815) remains separate. If
  black foliage is reproduced, compare its synthetic regression and a
  matched Windows raster capture before adopting anything.
- Acceptance still needs a reproducible Windows boot, save/load, combat and
  physical soul capture. Establish renderer correctness before any DLSS or
  path-tracing performance claim; validate Steam Deck and Android separately.

Use the [Windows gameplay acceptance checklist](WINDOWS_GAMEPLAY_ACCEPTANCE.md)
to record the exact build tested and separate physical capture from fallback.
The October release and a build from this maintenance branch need independent
receipts; a pass on one does not validate the other.
