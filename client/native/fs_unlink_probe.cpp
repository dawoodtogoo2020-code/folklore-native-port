/* Actual cellFsUnlink NID diagnostic against an isolated synthetic guest VM. */
#include "fs_unlink_probe.h"
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
#include <fcntl.h>
#include <sys/mman.h>
#include <unistd.h>
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
constexpr uint64_t kNoEntry = 0xFFFFFFFF80010006ull;
constexpr uint64_t kIsDir = 0xFFFFFFFF80010012ull;
constexpr uint64_t kNotDir = 0xFFFFFFFF8001002Eull;
constexpr uint64_t kFault = 0xFFFFFFFF8001000Dull;
constexpr uint64_t kNameTooLong = 0xFFFFFFFF80010034ull;
constexpr char kVictimText[] = "private unlink victim\n";
constexpr char kGuardText[] = "unrelated guard stays\n";

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

int fail(int code, const char* message, uint64_t actual = 0) {
    std::fprintf(stderr, "[fs-unlink-probe] %s: code=%d actual=0x%016llX\n",
                 message, code, static_cast<unsigned long long>(actual));
    return code;
}

bool create_exclusive(const std::filesystem::path& path, const char* text) {
    const size_t length = std::strlen(text);
#ifdef _WIN32
    HANDLE file = CreateFileW(path.c_str(), GENERIC_WRITE, 0, nullptr, CREATE_NEW,
                              FILE_ATTRIBUTE_NORMAL, nullptr);
    if (file == INVALID_HANDLE_VALUE) return false;
    DWORD written = 0;
    bool ok = WriteFile(file, text, static_cast<DWORD>(length), &written, nullptr) != 0 &&
              written == length;
    if (!CloseHandle(file)) ok = false;
    return ok;
#else
    const int fd = open(path.c_str(), O_WRONLY | O_CREAT | O_EXCL, 0600);
    if (fd < 0) return false;
    bool ok = write(fd, text, length) == static_cast<ssize_t>(length);
    if (close(fd) != 0) ok = false;
    return ok;
#endif
}

bool content_is(const std::filesystem::path& path, const char* expected) {
    std::ifstream file(path, std::ios::binary);
    if (!file) return false;
    const std::string actual((std::istreambuf_iterator<char>(file)),
                             std::istreambuf_iterator<char>());
    return !file.bad() && actual == expected;
}

uint64_t call_unlink(uint32_t path_ea) {
    ppu_context context{};
    context.gpr[1] = 0x18000;
    context.gpr[2] = 0x19000;
    context.gpr[3] = path_ea;
    ps3_hle_call(ps3_compute_nid("cellFsUnlink"), &context);
    return context.gpr[3];
}

void put_path(uint8_t* memory, const char* value) {
    std::memset(memory + kPath, 0, 2048);
    std::memcpy(memory + kPath, value, std::strlen(value) + 1);
}

#ifdef _WIN32
bool is_reparse(const std::filesystem::path& path) {
    const DWORD attributes = GetFileAttributesW(path.c_str());
    return attributes != INVALID_FILE_ATTRIBUTES &&
           (attributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0;
}
#endif
} // namespace

int folklore_check_fs_unlink(const char* existing_absolute_empty_folder,
                             const char* mode) {
    namespace fs = std::filesystem;
    const std::string selected = mode ? mode : "delete";
    const bool deleting = selected == "delete" || selected == "delete-unrestricted";
    if (!deleting && selected != "readonly" && selected != "outside" &&
        selected != "reparse" && selected != "hardlink")
        return fail(80, "unsupported probe mode");
    if (!existing_absolute_empty_folder || !*existing_absolute_empty_folder)
        return fail(81, "missing private probe folder");
    const fs::path root(existing_absolute_empty_folder);
    std::error_code error;
    if (!root.is_absolute() || !fs::is_directory(root, error) || error)
        return fail(82, "probe folder must be an existing absolute directory");
    if ((deleting || selected == "readonly") &&
        (!fs::is_empty(root, error) || error))
        return fail(83, "delete/readonly folder must be empty");

    const fs::path victim = root / "victim.txt";
    const fs::path guard = root / "guard.txt";
    const fs::path directory = root / "empty-directory";
    std::string guest;
    if (deleting || selected == "readonly") {
        if (!create_exclusive(victim, kVictimText) ||
            !create_exclusive(guard, kGuardText))
            return fail(84, "cannot create exclusive private fixture files");
        guest = "/app_home/victim.txt";
        if (deleting && !fs::create_directory(directory, error))
            return fail(85, "cannot create private empty-directory fixture");
    } else if (selected == "outside") {
        // Parent prepares <root>/../outside/victim.txt and checks it survives.
        // The path deliberately traverses out of the allowed write root.
        const fs::path outside = root.parent_path() / "outside" / "victim.txt";
        if (!fs::is_regular_file(outside, error) || error)
            return fail(86, "outside mode requires parent-owned sibling fixture");
        guest = "/app_home/../outside/victim.txt";
    } else if (selected == "reparse") {
#ifdef _WIN32
        // Parent prepares root/link as a reparse point to a file outside root.
        // No symlink privilege or outside write is assumed by this probe.
        if (!is_reparse(root / "link"))
            return fail(87, "reparse mode requires parent-prepared link");
        guest = "/app_home/link/victim.txt";
#else
        return fail(87, "reparse mode is Windows-only");
#endif
    } else {
#ifdef _WIN32
        // Parent prepares hard-victim.txt with link count >=2, including an
        // outside-root name. Parent checks both names survive the guard exit.
        const fs::path hardlink = root / "hard-victim.txt";
        if (!fs::is_regular_file(hardlink, error) || error ||
            fs::hard_link_count(hardlink, error) < 2 || error)
            return fail(88, "hardlink mode requires parent-prepared hard link");
        guest = "/app_home/hard-victim.txt";
#else
        return fail(88, "hardlink containment mode is Windows-only");
#endif
    }

    const std::string root_text = root.string();
    VmGuard vm;
    if (!vm.allocate()) return fail(89, "guarded synthetic VM allocation failed");
    ppu_vfs_root = root_text.c_str();
    ppu_hle_init();
    ppu_fs_register();
    put_path(vm.memory, guest.c_str());

    if (!deleting) {
        std::fprintf(stderr, "[fs-unlink-probe] guard mode=%s target=%s; expecting production exit 86\n",
                     selected.c_str(), guest.c_str());
        std::fflush(stderr);
        const uint64_t result = call_unlink(kPath);
        return fail(90, "guard mode returned instead of exiting 86", result);
    }

    if (uint64_t result = call_unlink(kPath); result != 0)
        return fail(91, "actual private victim delete failed", result);
    if (fs::exists(victim, error) || error || !content_is(guard, kGuardText))
        return fail(92, "victim still exists or unrelated guard changed");
    auto expect = [&](uint32_t path_ea, uint64_t expected, int code,
                      const char* name) -> int {
        const uint64_t result = call_unlink(path_ea);
        if (result != expected) return fail(code, name, result);
        if (!content_is(guard, kGuardText))
            return fail(code + 1, "failed unlink changed unrelated guard file");
        return 0;
    };
    if (int code = expect(kPath, kNoEntry, 93, "second delete not ENOENT")) return code;
    put_path(vm.memory, "/app_home/empty-directory");
    if (int code = expect(kPath, kIsDir, 95, "directory delete not EISDIR")) return code;
    if (!fs::is_directory(directory, error) || error)
        return fail(97, "directory was changed by rejected delete");
    if (selected == "delete-unrestricted") {
        put_path(vm.memory, "/app_home/guard.txt/child");
        if (int code = expect(kPath, kNotDir, 112, "file ancestor not ENOTDIR")) return code;
    }
    put_path(vm.memory, "");
    if (int code = expect(kPath, kNoEntry, 98, "empty path not ENOENT")) return code;
    put_path(vm.memory, "guard.txt");
    if (int code = expect(kPath, kNoEntry, 100, "relative path not ENOENT")) return code;
    if (int code = expect(0, kFault, 102, "null path not EFAULT")) return code;
    if (int code = expect(1, kFault, 104, "protected low path not EFAULT")) return code;
    if (int code = expect(kVmSize, kFault, 106, "out-of-range path not EFAULT")) return code;
    if (int code = expect(0xFFFFFFF0u, kFault, 108,
                          "overflowing path not EFAULT")) return code;
    std::memset(vm.memory + kPath, 'x', 1025);
    if (int code = expect(kPath, kNameTooLong, 110,
                          "unterminated 1025-byte path not ENAMETOOLONG")) return code;
    const std::string oversized_root(1200, 'x');
    ppu_vfs_root = oversized_root.c_str();
    put_path(vm.memory, "/app_home/guard.txt");
    if (int code = expect(kPath, kNameTooLong, 114,
                          "truncated host mapping not ENAMETOOLONG")) return code;
    ppu_vfs_root = root_text.c_str();
    std::puts("[fs-unlink-probe] actual private unlink and guarded rejection checks passed; no guest code executed");
    return 0;
}
