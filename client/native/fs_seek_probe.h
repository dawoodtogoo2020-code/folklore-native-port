#pragma once

// Private sparse-file HLE diagnostic. Caller supplies an existing absolute
// empty folder; the probe creates only seek.bin there. No guest code runs.
int folklore_check_fs_seek(const char* existing_absolute_empty_folder);
int folklore_check_fs_seek_existing(const char* owned_host_fixture_folder);
