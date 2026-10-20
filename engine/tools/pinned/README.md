# Pinned translator source

These four MIT-licensed translator files are preserved byte for byte from
the development SDK revisions recorded in `manifest.json`. The SDK starts
from [ps3recomp](https://github.com/sp00nznet/ps3recomp); these revision IDs
identify the development history and are not promised as public remote refs.
See [the SDK license](../../LICENSE).

They contain translator implementation, not translated game instructions.
The public owner-copy tools use these files directly and verify SHA-256
before loading or executing them. They never fetch historical Git objects.
The active tools in the parent directory remain available for current
translation and the verified switch-recovery step.
