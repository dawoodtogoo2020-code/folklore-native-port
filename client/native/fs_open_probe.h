#pragma once

// Private, no-ELF diagnostic. The caller supplies an existing absolute empty
// folder owned by this run; the probe creates files only inside it.
int folklore_check_fs_open(const char* existing_absolute_empty_folder);
