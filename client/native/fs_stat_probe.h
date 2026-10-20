#pragma once

// Private HLE diagnostic. Call with a fresh, absolute, empty directory.
// Returns zero only when all synthetic cellFsStat checks pass.
// Does not load an ELF or execute guest code.
int folklore_check_fs_stat(const char* folder);
