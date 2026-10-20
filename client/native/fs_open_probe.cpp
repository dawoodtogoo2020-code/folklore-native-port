/* Exercise the registered cellFsOpen/Write/Close HLE with actual PS3 flags.
 * Synthetic guarded guest memory only; no title or game assets are loaded. */
#include "fs_open_probe.h"
#include "ppu_recomp.h"
#include "ps3emu/nid.h"

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <string>
#include <system_error>
#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#else
#include <sys/mman.h>
#endif

extern "C" {
extern uint8_t* vm_base;
extern uint32_t ppu_vm_size;
extern const char* ppu_vfs_root;
void ppu_hle_init(void);
void ppu_fs_register(void);
void ps3_hle_call(uint32_t nid, ppu_context* context);
}

namespace {
constexpr uint32_t kVmSize = 0x20000;
constexpr uint32_t kGuardSize = 0x10000;
constexpr uint32_t kPath = 0x10000;
constexpr uint32_t kFdOut = 0x11000;
constexpr uint32_t kBuffer = 0x12000;
constexpr uint32_t kWrittenOut = 0x13000;
constexpr uint64_t kNoEntry = 0xFFFFFFFF80010006ull;
constexpr uint64_t kExists = 0xFFFFFFFF80010014ull;
constexpr uint32_t kWriteCreate = 0x41;  // PS3 WRONLY | CREAT, seen in title
constexpr uint32_t kWriteCreateExclusive = 0xC1;
constexpr uint32_t kWriteTruncate = 0x201;
constexpr uint32_t kWriteAppend = 0x401;
constexpr const char* kGuestPath = "/app_home/create.txt";

struct VmGuard {
    uint8_t* old_base = vm_base;
    uint32_t old_size = ppu_vm_size;
    const char* old_root = ppu_vfs_root;
    uint8_t* memory = nullptr;

    bool allocate() {
#ifdef _WIN32
        memory = static_cast<uint8_t*>(VirtualAlloc(nullptr, kVmSize, MEM_RESERVE, PAGE_NOACCESS));
        if (!memory) return false;
        if (!VirtualAlloc(memory + kGuardSize, kVmSize - kGuardSize,
                          MEM_COMMIT, PAGE_READWRITE)) return false;
#else
        void* mapped = mmap(nullptr, kVmSize, PROT_NONE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
        if (mapped == MAP_FAILED) return false;
        memory = static_cast<uint8_t*>(mapped);
        if (mprotect(memory + kGuardSize, kVmSize - kGuardSize,
                     PROT_READ | PROT_WRITE) != 0) return false;
#endif
        vm_base = memory;
        ppu_vm_size = kVmSize;
        return true;
    }

    ~VmGuard() {
        vm_base = old_base;
        ppu_vm_size = old_size;
        ppu_vfs_root = old_root;
        if (memory) {
#ifdef _WIN32
            VirtualFree(memory, 0, MEM_RELEASE);
#else
            munmap(memory, kVmSize);
#endif
        }
    }
};

uint32_t read_be32(const uint8_t* p) {
    return (uint32_t(p[0]) << 24) | (uint32_t(p[1]) << 16) |
           (uint32_t(p[2]) << 8) | uint32_t(p[3]);
}

uint64_t read_be64(const uint8_t* p) {
    uint64_t value = 0;
    for (unsigned i = 0; i < 8; ++i) value = (value << 8) | p[i];
    return value;
}

uint64_t call_hle(const char* name, uint64_t a0, uint64_t a1 = 0,
                  uint64_t a2 = 0, uint64_t a3 = 0) {
    ppu_context context{};
    context.gpr[1] = 0x18000;
    context.gpr[2] = 0x19000;
    context.gpr[3] = a0;
    context.gpr[4] = a1;
    context.gpr[5] = a2;
    context.gpr[6] = a3;
    ps3_hle_call(ps3_compute_nid(name), &context);
    return context.gpr[3];
}

int fail(int code, const char* message, uint64_t actual = 0) {
    std::fprintf(stderr, "[fs-open-probe] %s: code=%d actual=0x%016llX\n",
                 message, code, static_cast<unsigned long long>(actual));
    return code;
}

bool content_is(const std::filesystem::path& path, const char* expected) {
    std::ifstream file(path, std::ios::binary);
    if (!file) return false;
    const std::string actual((std::istreambuf_iterator<char>(file)),
                             std::istreambuf_iterator<char>());
    return !file.bad() && actual == expected;
}
} // namespace

int folklore_check_fs_open(const char* existing_absolute_empty_folder) {
    namespace fs = std::filesystem;
    if (!existing_absolute_empty_folder || !*existing_absolute_empty_folder)
        return fail(80, "missing private folder");
    const fs::path root(existing_absolute_empty_folder);
    std::error_code error;
    if (!root.is_absolute() || !fs::is_directory(root, error) || error ||
        !fs::is_empty(root, error) || error)
        return fail(81, "folder must be existing, absolute and empty");
    const fs::path file_path = root / "create.txt";
    if (fs::exists(file_path, error) || error)
        return fail(82, "private probe file already exists");

    const std::string root_text = root.string();
    VmGuard vm;
    if (!vm.allocate()) return fail(83, "guarded VM allocation failed");
    ppu_vfs_root = root_text.c_str();
    ppu_hle_init();
    ppu_fs_register();
    int active_fd = -1;
    struct CloseOnExit {
        int& fd;
        ~CloseOnExit() { if (fd >= 0) call_hle("cellFsClose", static_cast<uint32_t>(fd)); }
    } close_on_exit{active_fd};
    auto path = [&](const char* value) {
        std::memset(vm.memory + kPath, 0, 1024);
        std::memcpy(vm.memory + kPath, value, std::strlen(value) + 1);
    };
    auto open = [&](uint32_t flags, uint32_t* fd) -> uint64_t {
        std::memset(vm.memory + kFdOut, 0xA5, 8);
        const uint64_t result = call_hle("cellFsOpen", kPath, flags, kFdOut, 0);
        if (result == 0) {
            *fd = read_be32(vm.memory + kFdOut);
            if (*fd >= 3 && *fd < 256) active_fd = static_cast<int>(*fd);
        }
        return result;
    };
    auto close = [&](uint32_t fd) -> uint64_t {
        const uint64_t result = call_hle("cellFsClose", fd);
        if (result == 0) active_fd = -1;
        return result;
    };
    auto write = [&](uint32_t fd, const char* value) -> bool {
        const size_t length = std::strlen(value);
        std::memcpy(vm.memory + kBuffer, value, length);
        std::memset(vm.memory + kWrittenOut, 0xA5, 8);
        const uint64_t result = call_hle("cellFsWrite", fd, kBuffer,
                                         length, kWrittenOut);
        return result == 0 && read_be64(vm.memory + kWrittenOut) == length;
    };

    path(kGuestPath);
    uint32_t fd = 0;
    if (uint64_t result = open(kWriteCreate, &fd); result != 0 || fd < 3 || fd >= 256)
        return fail(90, "actual 0x41 create open failed", result);
    if (!write(fd, "original") || close(fd) != 0 || !content_is(file_path, "original"))
        return fail(91, "actual write/close did not persist created file");

    if (uint64_t result = open(kWriteCreate, &fd); result != 0)
        return fail(92, "0x41 open-existing failed", result);
    if (close(fd) != 0 || !content_is(file_path, "original"))
        return fail(93, "0x41 open-existing truncated or changed content");

    std::memset(vm.memory + kFdOut, 0xA5, 8);
    if (uint64_t result = open(kWriteCreateExclusive, &fd); result != kExists)
        return fail(94, "0xC1 exclusive create did not report EEXIST", result);
    for (unsigned i = 0; i < 8; ++i)
        if (vm.memory[kFdOut + i] != 0xA5)
            return fail(95, "exclusive create mutated fd output");
    if (!content_is(file_path, "original"))
        return fail(96, "exclusive create changed existing file");

    if (uint64_t result = open(kWriteTruncate, &fd); result != 0)
        return fail(97, "0x201 truncate open failed", result);
    if (close(fd) != 0 || !content_is(file_path, ""))
        return fail(98, "0x201 did not truncate existing content");

    if (uint64_t result = open(kWriteCreate, &fd); result != 0)
        return fail(99, "0x41 reopen after truncation failed", result);
    if (!write(fd, "base") || close(fd) != 0 || !content_is(file_path, "base"))
        return fail(100, "base content write failed");
    if (uint64_t result = open(kWriteAppend, &fd); result != 0)
        return fail(101, "0x401 append open failed", result);
    if (!write(fd, "+tail") || close(fd) != 0 || !content_is(file_path, "base+tail"))
        return fail(102, "0x401 did not append without clobbering");

    path("/app_home/missing.txt");
    if (uint64_t result = open(0, &fd); result != kNoEntry)
        return fail(103, "read-only missing file did not report ENOENT", result);
    for (unsigned i = 0; i < 8; ++i)
        if (vm.memory[kFdOut + i] != 0xA5)
            return fail(104, "missing file mutated fd output");
    if (!content_is(file_path, "base+tail"))
        return fail(105, "missing-file check changed private file");

    std::puts("[fs-open-probe] actual Open/Write/Close NID checks passed in isolated folder; no guest code executed");
    return 0;
}
