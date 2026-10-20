/* Synthetic guest-VM checks against the actual registered cellFsStat HLE.
 * This is a private diagnostic, not evidence of title execution. */
#include "fs_stat_probe.h"
#include "ppu_recomp.h"
#include "ps3emu/nid.h"

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <fstream>
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
constexpr uint32_t kOut = 0x11000;
constexpr uint32_t kStatSize = 52;
constexpr uint64_t kFault = 0xFFFFFFFF8001000Dull;
constexpr uint64_t kNoEntry = 0xFFFFFFFF80010006ull;
constexpr uint64_t kNameTooLong = 0xFFFFFFFF80010034ull;
constexpr char kContents[] = "folklore-fs-stat-probe\n";

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

uint64_t call_stat(uint32_t path, uint32_t output) {
    ppu_context context{};
    context.gpr[1] = 0x18000;
    context.gpr[2] = 0x19000;
    context.gpr[3] = path;
    context.gpr[4] = output;
    ps3_hle_call(ps3_compute_nid("cellFsStat"), &context);
    return context.gpr[3];
}

uint64_t big_endian_64(const uint8_t* bytes) {
    uint64_t value = 0;
    for (unsigned i = 0; i < 8; ++i) value = (value << 8) | bytes[i];
    return value;
}

uint32_t big_endian_32(const uint8_t* bytes) {
    uint32_t value = 0;
    for (unsigned i = 0; i < 4; ++i) value = (value << 8) | bytes[i];
    return value;
}

bool unchanged(const uint8_t* output) {
    for (unsigned i = 0; i < kStatSize; ++i) if (output[i] != 0xA5) return false;
    return true;
}

int failure(int code, const char* message, uint64_t actual = 0) {
    std::fprintf(stderr, "[fs-stat-probe] %s: code=%d actual=0x%016llX\n",
                 message, code, static_cast<unsigned long long>(actual));
    return code;
}
} // namespace

int folklore_check_fs_stat(const char* folder) {
    namespace fs = std::filesystem;
    if (!folder || !*folder) return failure(80, "missing private probe folder");
    const fs::path root(folder);
    std::error_code error;
    if (!root.is_absolute() || !fs::is_directory(root, error) || error ||
        !fs::is_empty(root, error) || error)
        return failure(81, "probe folder must exist and be absolute and empty");

    // The runner owns this new folder. The existence check prevents clobbering
    // a pre-existing file if the caller violates that contract.
    const fs::path probe_file = root / "probe.txt";
    if (fs::exists(probe_file, error) || error) return failure(82, "probe file already exists");
    {
        std::ofstream stream(probe_file, std::ios::binary | std::ios::out | std::ios::trunc);
        if (!stream) return failure(83, "cannot create probe file");
        stream.write(kContents, sizeof(kContents) - 1);
        stream.close();
        if (!stream) return failure(84, "cannot write probe file");
    }
    if (fs::file_size(probe_file, error) != sizeof(kContents) - 1 || error)
        return failure(85, "probe file size mismatch");

    // Keep the UTF-8 root alive until VmGuard restores the prior pointer.
    const std::string root_text = root.string();
    VmGuard vm;
    if (!vm.allocate()) return failure(86, "guarded synthetic VM allocation failed");
    ppu_vfs_root = root_text.c_str();
    ppu_hle_init();
    ppu_fs_register();

    auto prepare = [&](const char* path) {
        std::memset(vm.memory + kPath, 0, 0x1000);
        if (path) std::memcpy(vm.memory + kPath, path, std::strlen(path) + 1);
        std::memset(vm.memory + kOut - 1, 0xA5, kStatSize + 2);
    };
    auto expect_error = [&](uint32_t path, uint32_t output, uint64_t expected,
                            int code, const char* name) -> int {
        const uint64_t actual = call_stat(path, output);
        if (actual != expected) return failure(code, name, actual);
        if (!unchanged(vm.memory + kOut) || vm.memory[kOut - 1] != 0xA5 ||
            vm.memory[kOut + kStatSize] != 0xA5)
            return failure(code + 1, "failure mutated stat output");
        return 0;
    };

    prepare("/app_home/probe.txt");
    if (int code = expect_error(0, kOut, kFault, 90, "null path")) return code;
    if (int code = expect_error(1, kOut, kFault, 113, "protected low path")) return code;
    if (int code = expect_error(kGuardSize - 1, kOut, kFault, 115, "last protected low path")) return code;
    if (int code = expect_error(kPath, 1, kFault, 117, "protected low output")) return code;
    prepare("/app_home/probe.txt");
    if (call_stat(kPath, 0) != kFault) return failure(92, "null stat output");
    prepare("/app_home/probe.txt");
    if (int code = expect_error(kPath, kVmSize - kStatSize + 1,
                                kFault, 93, "short stat output")) return code;
    prepare("/app_home/probe.txt");
    if (int code = expect_error(kPath, kVmSize, kFault, 95, "out-of-range stat output")) return code;
    prepare("/app_home/probe.txt");
    if (int code = expect_error(kPath, 0xFFFFFFF0u,
                                kFault, 97, "overflowing stat output")) return code;
    prepare("/app_home/probe.txt");
    if (int code = expect_error(0xFFFFFFF0u, kOut,
                                kFault, 99, "out-of-range path")) return code;
    prepare("");
    if (int code = expect_error(kPath, kOut, kNoEntry, 101, "empty path")) return code;
    prepare(nullptr);
    std::memset(vm.memory + kPath, 'x', 1025);
    if (int code = expect_error(kPath, kOut, kNameTooLong, 103, "unterminated path")) return code;
    prepare(nullptr);
    std::memset(vm.memory + kVmSize - 1025, 'x', 1024);
    vm.memory[kVmSize - 1025] = '/';
    vm.memory[kVmSize - 1] = 0;
    // A NUL at the final guest byte must be read safely; long host paths may
    // legitimately fail as ENOENT or ENAMETOOLONG, but never as EFAULT.
    const uint64_t last = call_stat(kVmSize - 1025, kOut);
    if ((last != kNoEntry && last != kNameTooLong) ||
        !unchanged(vm.memory + kOut))
        return failure(105, "last-byte NUL path handling", last);

    prepare("/app_home/missing.txt");
    if (int code = expect_error(kPath, kOut, kNoEntry, 107, "missing path")) return code;
    prepare("/app_home/probe.txt");
    if (uint64_t actual = call_stat(kPath, kOut); actual != 0)
        return failure(109, "existing file stat", actual);
    const uint8_t* stat = vm.memory + kOut;
    if (vm.memory[kOut - 1] != 0xA5 || vm.memory[kOut + kStatSize] != 0xA5 ||
        big_endian_32(stat) != 0x81B6u ||
        big_endian_32(stat + 4) != 0 || big_endian_32(stat + 8) != 0 ||
        big_endian_64(stat + 0x0C) != 0 || big_endian_64(stat + 0x14) != 0 ||
        big_endian_64(stat + 0x1C) != 0 ||
        big_endian_64(stat + 0x24) != sizeof(kContents) - 1 ||
        big_endian_64(stat + 0x2C) != 0x200)
        return failure(110, "stat fields or boundary canary mismatch");
    prepare("/app_home/probe.txt");
    if (uint64_t actual = call_stat(kPath, kVmSize - kStatSize); actual != 0)
        return failure(111, "last valid stat output", actual);
    if (big_endian_64(vm.memory + kVmSize - kStatSize + 0x24) != sizeof(kContents) - 1)
        return failure(112, "last valid stat output size mismatch");

    // The runtime also represents a full 4 GiB reservation with a zero size.
    // Exercise only our committed synthetic addresses in that mode.
    prepare("/app_home/probe.txt");
    ppu_vm_size = 0;
    if (uint64_t actual = call_stat(kPath, kOut); actual != 0)
        return failure(119, "full VM size sentinel", actual);
    ppu_vm_size = kVmSize;
#ifdef _WIN32
    DWORD old_protection = 0;
    if (!VirtualProtect(vm.memory + kOut, 0x1000, PAGE_READONLY, &old_protection))
        return failure(123, "protect readonly output");
    const uint64_t readonly_result = call_stat(kPath, kOut);
    DWORD ignored_protection = 0;
    if (!VirtualProtect(vm.memory + kOut, 0x1000, old_protection, &ignored_protection))
        return failure(124, "restore readonly output");
    if (readonly_result != kFault) return failure(125, "readonly output rejected", readonly_result);
    // The real diagnostic reserves most guest memory until its first access.
    // Stat must use that same policy for an untouched valid output page.
    if (!VirtualFree(vm.memory + kVmSize - 0x1000, 0x1000, MEM_DECOMMIT))
        return failure(120, "reserve untouched stat output");
    if (uint64_t actual = call_stat(kPath, kVmSize - kStatSize); actual != 0)
        return failure(121, "demand-committed stat output", actual);
    if (big_endian_64(vm.memory + kVmSize - kStatSize + 0x24) != sizeof(kContents) - 1)
        return failure(122, "demand-committed stat output size");
#endif

    std::puts("[fs-stat-probe] actual cellFsStat NID dispatch passed guarded synthetic VM checks; no guest code executed");
    return 0;
}
