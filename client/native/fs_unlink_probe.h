#pragma once

// Private cellFsUnlink HLE diagnostic. In delete/readonly mode the absolute
// folder must exist and be empty; the probe creates only small files there.
// Other modes require the exact parent-prepared fixtures documented in the
// implementation. Guard modes must terminate with production exit 86.
int folklore_check_fs_unlink(const char* existing_absolute_empty_folder,
                             const char* mode = nullptr);
