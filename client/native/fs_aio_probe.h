#pragma once
// Isolated actual-HLE AIO probe. Creates only synthetic files in an existing,
// absolute, empty fixture directory. Must run before any game/VM initialization.
int folklore_check_fs_aio(const char* existing_absolute_empty_fixture, const char* prepared_sparse_directory=nullptr);
int folklore_check_fs_aio_capacity(const char* existing_absolute_empty_fixture);
