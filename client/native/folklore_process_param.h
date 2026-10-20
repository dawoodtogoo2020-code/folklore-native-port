#ifndef FOLKLORE_PROCESS_PARAM_H
#define FOLKLORE_PROCESS_PARAM_H
#include <cstddef>
#include <cstdint>
#include <fstream>
#include <limits>

namespace folklore {
constexpr uint32_t kUnknownSdk = 0xffffffffu;
enum class ProcessParamStatus {
    ok, absent, io, invalid_elf, program_headers, segment_bounds, duplicate,
    short_parameter, parameter_size, parameter_magic, parameter_address
};
struct ProcessParam {
    bool has_param = false;
    uint32_t sdk_version = kUnknownSdk;
    uint64_t file_offset = 0, guest_vaddr = 0;
    uint32_t size = 0, version = 0;
    uint32_t primary_priority_raw = 0, primary_stack_size = 0;
    uint32_t malloc_page_size = 0, ppc_seg = 0;
};
using ProcessParamReadAt = bool (*)(void*, uint64_t, void*, size_t);
inline uint32_t process_be32(const uint8_t* p) {
    return uint32_t(p[0]) << 24 | uint32_t(p[1]) << 16 | uint32_t(p[2]) << 8 | p[3];
}
inline uint16_t process_be16(const uint8_t* p) { return uint16_t(p[0]) << 8 | p[1]; }
inline uint64_t process_be64(const uint8_t* p) { return uint64_t(process_be32(p)) << 32 | process_be32(p+4); }
inline bool process_range(uint64_t at, uint64_t size, uint64_t limit) { return at <= limit && size <= limit-at; }

/* ELF64 big-endian PPU executable only. No guest pointer dereference or full
 * ELF allocation: 64-byte header, one56-byte PH at a time,32-byte parameter.
 * Missing/empty optional PROC_PARAM uses the pinned loader's unknown sentinel.
 * Unknown version/extension fields are retained, never applied to execution. */
inline ProcessParamStatus parse_process_param(ProcessParamReadAt read, void* opaque,
                                              uint64_t file_size, ProcessParam& out) {
    out = ProcessParam{};
    uint8_t h[64];
    if (!read || file_size < sizeof h) return ProcessParamStatus::invalid_elf;
    if (!read(opaque,0,h,sizeof h)) return ProcessParamStatus::io;
    if (h[0]!=0x7f || h[1]!='E' || h[2]!='L' || h[3]!='F' || h[4]!=2 || h[5]!=2 ||
        h[6]!=1 || process_be16(h+16)!=2 || process_be16(h+18)!=21 ||
        process_be32(h+20)!=1 || process_be16(h+52)!=64) return ProcessParamStatus::invalid_elf;
    const uint64_t phoff = process_be64(h+32);
    const uint16_t stride = process_be16(h+54), count = process_be16(h+56);
    // PN_XNUM stores the real count in section zero, outside this bounded parser.
    if (count == 0xffffu || stride != 56 || (count && phoff < sizeof h) ||
        !process_range(phoff,uint64_t(stride)*count,file_size)) return ProcessParamStatus::program_headers;
    bool found = false;
    uint64_t param_offset=0,param_address=0,param_size=0,param_memory_size=0;
    for (uint32_t i=0;i<count;++i) {
        uint8_t p[56];
        if (!read(opaque,phoff+uint64_t(i)*stride,p,sizeof p)) return ProcessParamStatus::io;
        const uint32_t type=process_be32(p);
        const uint64_t offset=process_be64(p+8),address=process_be64(p+16);
        const uint64_t filesz=process_be64(p+32),memsz=process_be64(p+40);
        if (!process_range(offset,filesz,file_size) || memsz > UINT64_MAX-address ||
            (type==1 && filesz>memsz)) return ProcessParamStatus::segment_bounds;
        if (type != 0x60000001u) continue;
        if (found) return ProcessParamStatus::duplicate;
        found=true; param_offset=offset;param_address=address;param_size=filesz;param_memory_size=memsz;
    }
    if (!found || !param_size) return ProcessParamStatus::absent;
    if (param_size < 32) return ProcessParamStatus::short_parameter;
    if (param_size > param_memory_size || !process_range(param_address,param_size,0x100000000ull))
        return ProcessParamStatus::parameter_address;
    uint8_t p[32];
    if (!read(opaque,param_offset,p,sizeof p)) return ProcessParamStatus::io;
    const uint32_t declared=process_be32(p);
    if (declared<sizeof p || declared>param_size) return ProcessParamStatus::parameter_size;
    if (process_be32(p+4)!=0x13bcc5f6u) return ProcessParamStatus::parameter_magic;
    ProcessParam decoded;
    decoded.has_param=true;decoded.file_offset=param_offset;decoded.guest_vaddr=param_address;
    decoded.size=declared;decoded.version=process_be32(p+8);decoded.sdk_version=process_be32(p+12);
    decoded.primary_priority_raw=process_be32(p+16);decoded.primary_stack_size=process_be32(p+20);
    decoded.malloc_page_size=process_be32(p+24);decoded.ppc_seg=process_be32(p+28);
    out=decoded;
    return ProcessParamStatus::ok;
}

inline const char* process_param_status_name(ProcessParamStatus s) {
    switch(s) {
    case ProcessParamStatus::ok:return "ok";
    case ProcessParamStatus::absent:return "absent-sdk-unknown";
    case ProcessParamStatus::io:return "io";
    case ProcessParamStatus::invalid_elf:return "invalid-elf64-be-ppu-executable";
    case ProcessParamStatus::program_headers:return "program-header-bounds";
    case ProcessParamStatus::segment_bounds:return "segment-bounds";
    case ProcessParamStatus::duplicate:return "duplicate-process-param";
    case ProcessParamStatus::short_parameter:return "short-process-param";
    case ProcessParamStatus::parameter_size:return "process-param-size";
    case ProcessParamStatus::parameter_magic:return "process-param-magic";
    case ProcessParamStatus::parameter_address:return "process-param-address-or-memory-size";
    }
    return "unknown";
}

inline ProcessParamStatus read_process_param(const char* path, ProcessParam& out) {
    out=ProcessParam{};
    if (!path) return ProcessParamStatus::io;
    std::ifstream file(path,std::ios::binary);
    if (!file) return ProcessParamStatus::io;
    file.seekg(0,std::ios::end);
    const std::streamoff size=file.tellg();
    if (size<0) return ProcessParamStatus::io;
    auto reader=[](void* opaque,uint64_t offset,void* destination,size_t bytes)->bool {
        auto& stream=*static_cast<std::ifstream*>(opaque);
        if (offset>uint64_t(std::numeric_limits<std::streamoff>::max()) ||
            bytes>size_t(std::numeric_limits<std::streamsize>::max())) return false;
        stream.clear();stream.seekg(static_cast<std::streamoff>(offset),std::ios::beg);
        if (!stream) return false;
        stream.read(static_cast<char*>(destination),static_cast<std::streamsize>(bytes));
        return stream.gcount()==static_cast<std::streamsize>(bytes) && !stream.bad();
    };
    return parse_process_param(reader,&file,static_cast<uint64_t>(size),out);
}
}
#endif
