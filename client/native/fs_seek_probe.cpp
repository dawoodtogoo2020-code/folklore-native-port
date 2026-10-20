/* Verify 64-bit cellFsLseek/Fstat through actual registered HLE NIDs. */
#include "fs_seek_probe.h"
#include "ppu_recomp.h"
#include "ps3emu/nid.h"

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <string>
#include <system_error>
#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <winioctl.h>
#else
#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
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
constexpr uint32_t kFdOut = 0x11000;
constexpr uint32_t kPosOut = 0x12000;
constexpr uint32_t kReadBuf = 0x13000;
constexpr uint32_t kReadOut = 0x14000;
constexpr uint32_t kStatOut = 0x15000;
constexpr uint64_t kTailOffset = 2388652496ull;
constexpr char kPrefix[] = "PREFIX-distinct-from-tail";
constexpr char kTail[] = "TAIL-at-large-offset";
constexpr uint64_t kTailLength = sizeof(kTail) - 1;
constexpr uint64_t kLogicalSize = kTailOffset + kTailLength;
constexpr uint64_t kInvalid = 0xFFFFFFFF80010002ull;

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

int fail(int code, const char* what, uint64_t actual = 0) {
    std::fprintf(stderr, "[fs-seek-probe] %s: code=%d actual=0x%016llX\n",
                 what, code, static_cast<unsigned long long>(actual));
    return code;
}

uint64_t be64(const uint8_t* bytes) {
    uint64_t value = 0;
    for (unsigned i = 0; i < 8; ++i) value = (value << 8) | bytes[i];
    return value;
}
uint32_t be32(const uint8_t* bytes) {
    return (uint32_t(bytes[0]) << 24) | (uint32_t(bytes[1]) << 16) |
           (uint32_t(bytes[2]) << 8) | uint32_t(bytes[3]);
}

uint64_t call(const char* name, uint64_t a0, uint64_t a1 = 0,
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

bool make_sparse_file(const std::filesystem::path& path, uint64_t* allocated) {
#ifdef _WIN32
    HANDLE file = CreateFileW(path.c_str(), GENERIC_WRITE | FILE_READ_ATTRIBUTES,
                              0, nullptr, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (file == INVALID_HANDLE_VALUE) return false;
    DWORD ignored = 0, count = 0;
    bool ok = DeviceIoControl(file, FSCTL_SET_SPARSE, nullptr, 0,
                              nullptr, 0, &ignored, nullptr) != 0;
    if (ok) ok = WriteFile(file, kPrefix, sizeof(kPrefix) - 1, &count, nullptr) != 0 &&
                 count == sizeof(kPrefix) - 1;
    LARGE_INTEGER position{};
    position.QuadPart = static_cast<LONGLONG>(kTailOffset);
    if (ok) ok = SetFilePointerEx(file, position, nullptr, FILE_BEGIN) != 0;
    if (ok) ok = WriteFile(file, kTail, static_cast<DWORD>(kTailLength),
                           &count, nullptr) != 0 && count == kTailLength;
    FILE_STANDARD_INFO info{};
    if (ok) ok = GetFileInformationByHandleEx(file, FileStandardInfo,
                                               &info, sizeof(info)) != 0;
    if (ok) *allocated = static_cast<uint64_t>(info.AllocationSize.QuadPart);
    if (!CloseHandle(file)) ok = false;
    return ok;
#else
    static_assert(sizeof(off_t) >= 8, "probe requires 64-bit off_t");
    const int file = open(path.c_str(), O_CREAT | O_EXCL | O_RDWR, 0600);
    if (file < 0) return false;
    // Verify this mount creates holes before requesting a multi-GiB extent.
    struct stat sparse_check{};
    bool ok = ftruncate(file, 8 * 1024 * 1024) == 0 && fstat(file,&sparse_check) == 0 &&
        static_cast<uint64_t>(sparse_check.st_blocks)*512 <= 1024*1024;
    if(ok)ok=ftruncate(file, static_cast<off_t>(kLogicalSize)) == 0;
    if (ok) ok = pwrite(file, kPrefix, sizeof(kPrefix) - 1, 0) ==
                 static_cast<ssize_t>(sizeof(kPrefix) - 1);
    if (ok) ok = pwrite(file, kTail, kTailLength,
                        static_cast<off_t>(kTailOffset)) ==
                 static_cast<ssize_t>(kTailLength);
    struct stat info{};
    if (ok) ok = fstat(file, &info) == 0;
    if (ok) *allocated = static_cast<uint64_t>(info.st_blocks) * 512;
    if (close(file) != 0) ok = false;
    return ok;
#endif
}
} // namespace

static int check_fs_seek(const char* existing_absolute_empty_folder,bool create) {
    namespace fs = std::filesystem;
    if (!existing_absolute_empty_folder || !*existing_absolute_empty_folder)
        return fail(80, "missing private folder");
    const fs::path root(existing_absolute_empty_folder);
    std::error_code error;
    if (!root.is_absolute() || !fs::is_directory(root, error) || error ||
        (create && !fs::is_empty(root, error)) || error)
        return fail(81, "folder must exist, be absolute, and be empty");
    const fs::path sparse_path = root / "seek.bin";
    uint64_t allocated = 0;
    if (create && !make_sparse_file(sparse_path, &allocated))
        return fail(82, "sparse file creation failed");
    const uint64_t logical = fs::file_size(sparse_path, error);
    if (error || logical != kLogicalSize)
        return fail(83, "sparse logical length differs", logical);
    if(!create) {
        size_t files=0;for(const auto& entry:fs::directory_iterator(root,error)) {
            if(entry.path().filename()!="seek.bin" || !entry.is_regular_file(error) || error)
                return fail(86,"host fixture folder has unexpected contents");
            ++files;
        }
        if(error || files!=1)return fail(87,"host fixture must contain only seek.bin");
        std::fputs("[fs-seek-probe] host-prepared synthetic fixture; physical allocation requires its host receipt\n",stderr);
    }
    if(create)std::fprintf(stderr, "[fs-seek-probe] sparse logical=%llu physical-allocated=%llu bytes\n",
        static_cast<unsigned long long>(logical),static_cast<unsigned long long>(allocated));
    else std::fprintf(stderr,"[fs-seek-probe] host fixture logical=%llu; no local allocation measurement\n",
        static_cast<unsigned long long>(logical));
    if (allocated > 1024 * 1024)
        return fail(84, "physical allocation exceeds one MiB sparse limit", allocated);

    const std::string root_text = root.string();
    VmGuard vm;
    if (!vm.allocate()) return fail(85, "guarded VM allocation failed");
    ppu_vfs_root = root_text.c_str();
    ppu_hle_init();
    ppu_fs_register();
    std::memcpy(vm.memory + kPath, "/app_home/seek.bin", sizeof("/app_home/seek.bin"));
    std::memset(vm.memory + kFdOut, 0xA5, 8);
    const uint64_t opened = call("cellFsOpen", kPath, 0, kFdOut, 0);
    if (opened != 0) return fail(90, "actual read-only open failed", opened);
    const uint32_t fd = be32(vm.memory + kFdOut);
    if (fd < 3 || fd >= 256) return fail(91, "invalid descriptor output", fd);
    struct CloseOnExit {
        uint32_t fd;
        ~CloseOnExit() { if (fd) call("cellFsClose", fd); }
    } close_on_exit{fd};

    auto seek = [&](int64_t offset, uint32_t whence, uint64_t expected,
                    int code, const char* label) -> int {
        std::memset(vm.memory + kPosOut, 0xA5, 8);
        const uint64_t result = call("cellFsLseek", fd,
                                     static_cast<uint64_t>(offset), whence, kPosOut);
        if (result != 0) return fail(code, label, result);
        if (be64(vm.memory + kPosOut) != expected)
            return fail(code + 1, "64-bit seek position mismatch",
                        be64(vm.memory + kPosOut));
        return 0;
    };
    auto read = [&](const char* expected, size_t length,
                    int code, const char* label) -> int {
        std::memset(vm.memory + kReadBuf, 0xA5, length + 4);
        std::memset(vm.memory + kReadOut, 0xA5, 8);
        const uint64_t result = call("cellFsRead", fd, kReadBuf, length, kReadOut);
        if (result != 0) return fail(code, label, result);
        if (be64(vm.memory + kReadOut) != length ||
            std::memcmp(vm.memory + kReadBuf, expected, length) != 0 ||
            vm.memory[kReadBuf + length] != 0xA5)
            return fail(code + 1, "read bytes/count or canary mismatch");
        return 0;
    };
    auto rejected_seek = [&](int64_t offset, uint32_t whence,
                             int code, const char* label) -> int {
        std::memset(vm.memory + kPosOut, 0xA5, 8);
        const uint64_t result = call("cellFsLseek", fd,
                                     static_cast<uint64_t>(offset), whence, kPosOut);
        if (result != kInvalid) return fail(code, label, result);
        for (unsigned i = 0; i < 8; ++i)
            if (vm.memory[kPosOut + i] != 0xA5)
                return fail(code + 1, "rejected seek mutated position output");
        return 0;
    };

    if (int code = seek(static_cast<int64_t>(kTailOffset), 0, kTailOffset,
                        92, "large SEEK_SET failed")) return code;
    std::memset(vm.memory + kStatOut - 1, 0xA5, 54);
    if (uint64_t result = call("cellFsFstat", fd, kStatOut); result != 0)
        return fail(94, "actual Fstat failed", result);
    if (be64(vm.memory + kStatOut + 0x24) != kLogicalSize ||
        vm.memory[kStatOut - 1] != 0xA5 || vm.memory[kStatOut + 52] != 0xA5)
        return fail(95, "Fstat 64-bit size or boundary mismatch");
    if (int code = seek(0, 1, kTailOffset,
                        96, "Fstat changed current position")) return code;
    if (int code = read(kTail, kTailLength, 98, "large-offset tail read failed")) return code;
    if (int code = seek(-static_cast<int64_t>(kTailLength), 1, kTailOffset,
                        100, "negative SEEK_CUR failed")) return code;
    if (int code = read(kTail, kTailLength, 102, "SEEK_CUR tail read failed")) return code;
    if (int code = seek(-static_cast<int64_t>(kTailLength), 2, kTailOffset,
                        104, "negative SEEK_END failed")) return code;
    if (int code = read(kTail, kTailLength, 106, "SEEK_END tail read failed")) return code;

    if (int code = seek(static_cast<int64_t>(kTailOffset + 2), 0,
                        kTailOffset + 2, 108, "position setup failed")) return code;
    if (int code = rejected_seek(0, 3, 110, "invalid whence not EINVAL")) return code;
    if (int code = seek(0, 1, kTailOffset + 2,
                        112, "invalid whence moved position")) return code;
    if (int code = rejected_seek(-1, 0, 114, "negative SEEK_SET not EINVAL")) return code;
    if (int code = seek(0, 1, kTailOffset + 2,
                        116, "negative SEEK_SET moved position")) return code;
    if (int code = seek(0, 0, 0, 118, "prefix SEEK_SET failed")) return code;
    if (int code = read(kPrefix, sizeof(kPrefix) - 1,
                        120, "prefix read failed")) return code;
    const uint64_t closed = call("cellFsClose", fd);
    if (closed != 0) return fail(122, "actual Close failed", closed);
    close_on_exit.fd = 0;
    std::puts("[fs-seek-probe] actual sparse 64-bit Open/Lseek/Read/Fstat/Close checks passed; no guest code executed");
    return 0;
}

int folklore_check_fs_seek(const char* folder) {return check_fs_seek(folder,true);}
int folklore_check_fs_seek_existing(const char* folder) {return check_fs_seek(folder,false);}
