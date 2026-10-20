/* Synthetic ELF metadata tests. No VM, runtime services or guest execution. */
#include "../native/folklore_process_param.h"
#include <cstdio>
#include <cstring>
#include <vector>

using folklore::ProcessParam;
using folklore::ProcessParamStatus;
namespace {
int passed=0,failed=0;
void check(bool okay,const char* name) {
    if (okay) ++passed;
    else {++failed;std::fprintf(stderr,"FAIL %s\n",name);}
}
void put16(std::vector<uint8_t>& b,size_t p,uint16_t v) {b[p]=uint8_t(v>>8);b[p+1]=uint8_t(v);}
void put32(std::vector<uint8_t>& b,size_t p,uint32_t v) {for(int i=0;i<4;++i)b[p+i]=uint8_t(v>>(24-i*8));}
void put64(std::vector<uint8_t>& b,size_t p,uint64_t v) {put32(b,p,uint32_t(v>>32));put32(b,p+4,uint32_t(v));}
struct Reader { std::vector<uint8_t> bytes; uint64_t fail_at=UINT64_MAX; size_t copied=0; };
bool read(void* opaque,uint64_t at,void* out,size_t size) {
    auto& r=*static_cast<Reader*>(opaque);
    if(at==r.fail_at || !folklore::process_range(at,size,r.bytes.size()))return false;
    std::memcpy(out,r.bytes.data()+size_t(at),size);r.copied+=size;return true;
}
Reader valid(uint32_t sdk=0x00150004u) {
    Reader r;auto& b=r.bytes;b.resize(256);
    b[0]=0x7f;b[1]='E';b[2]='L';b[3]='F';b[4]=2;b[5]=2;b[6]=1;
    put16(b,16,2);put16(b,18,21);put32(b,20,1);put64(b,32,64);
    put16(b,52,64);put16(b,54,56);put16(b,56,1);
    put32(b,64,0x60000001u);put64(b,72,192);put64(b,80,0x10000);
    put64(b,96,32);put64(b,104,32);
    put32(b,192,32);put32(b,196,0x13bcc5f6u);put32(b,200,0x9000);
    put32(b,204,sdk);put32(b,208,1001);put32(b,212,0x10000);
    put32(b,216,0x100000);return r;
}
ProcessParamStatus parse(Reader& r,ProcessParam& out) {
    return folklore::parse_process_param(read,&r,r.bytes.size(),out);
}
void expect(Reader r,ProcessParamStatus status,const char* name) {
    ProcessParam p;p.sdk_version=0xDEADBEEFu;p.has_param=true;
    check(parse(r,p)==status,name);
    if(status!=ProcessParamStatus::ok)check(!p.has_param && p.sdk_version==folklore::kUnknownSdk,"failure/absence resets output");
}
}
int main(int argc,char** argv) {
    auto r=valid();ProcessParam p;
    check(parse(r,p)==ProcessParamStatus::ok && p.has_param && p.sdk_version==0x150004 &&
          p.size==32 && p.version==0x9000 && p.file_offset==192 && p.guest_vaddr==0x10000 &&
          p.primary_priority_raw==1001 && p.primary_stack_size==0x10000 && p.malloc_page_size==0x100000,
          "real layout and untouched decoded parameters");
    check(r.copied==64+56+32,"bounded reads only header PH and fixed prefix");
    r=valid(0x360001);check(parse(r,p)==ProcessParamStatus::ok && p.sdk_version==0x360001,"SDK comes from bytes not title constant");
    r=valid();put32(r.bytes,200,0x12345678);check(parse(r,p)==ProcessParamStatus::ok && p.version==0x12345678,"opaque version retained per primary loader");
    r=valid();put32(r.bytes,192,48);put64(r.bytes,96,48);put64(r.bytes,104,48);
    check(parse(r,p)==ProcessParamStatus::ok && p.size==48,"bounded compatible prefix with extension size");
    r=valid();put16(r.bytes,56,0);expect(r,ProcessParamStatus::absent,"missing optional parameter uses unknown SDK");
    r=valid();put64(r.bytes,96,0);expect(r,ProcessParamStatus::absent,"empty optional parameter uses unknown SDK");
    r=valid();put16(r.bytes,56,2);std::memcpy(r.bytes.data()+120,r.bytes.data()+64,56);
    expect(r,ProcessParamStatus::duplicate,"duplicate parameters rejected");
    r=valid();r.bytes.resize(63);expect(r,ProcessParamStatus::invalid_elf,"short ELF");
    r=valid();r.bytes[0]=0;expect(r,ProcessParamStatus::invalid_elf,"ELF magic");
    r=valid();r.bytes[4]=1;expect(r,ProcessParamStatus::invalid_elf,"ELF class");
    r=valid();r.bytes[5]=1;expect(r,ProcessParamStatus::invalid_elf,"ELF byte order");
    r=valid();put16(r.bytes,18,23);expect(r,ProcessParamStatus::invalid_elf,"wrong machine");
    r=valid();put16(r.bytes,54,55);expect(r,ProcessParamStatus::program_headers,"PH stride");
    r=valid();put64(r.bytes,32,UINT64_MAX-8);expect(r,ProcessParamStatus::program_headers,"PH arithmetic overflow");
    r=valid();put16(r.bytes,56,65535);expect(r,ProcessParamStatus::program_headers,"PH table truncated");
    r=valid();{
        const std::vector<uint8_t> header(r.bytes.begin(),r.bytes.begin()+64);
        r.bytes.assign(64+size_t(56)*65535,0);
        std::memcpy(r.bytes.data(),header.data(),header.size());
        put16(r.bytes,56,65535);
        expect(r,ProcessParamStatus::program_headers,"PN_XNUM is unsupported even with space for65535 PHs");
    }
    r=valid();put64(r.bytes,72,UINT64_MAX-16);expect(r,ProcessParamStatus::segment_bounds,"segment offset overflow");
    r=valid();put64(r.bytes,96,65);expect(r,ProcessParamStatus::segment_bounds,"segment past file");
    r=valid();put64(r.bytes,96,31);expect(r,ProcessParamStatus::short_parameter,"short parameter prefix");
    r=valid();put32(r.bytes,192,31);expect(r,ProcessParamStatus::parameter_size,"declared parameter too short");
    r=valid();put32(r.bytes,192,33);expect(r,ProcessParamStatus::parameter_size,"declared parameter exceeds file span");
    r=valid();put32(r.bytes,196,0);expect(r,ProcessParamStatus::parameter_magic,"parameter magic");
    r=valid();put64(r.bytes,80,0x100000000ull);expect(r,ProcessParamStatus::parameter_address,"parameter high guest address");
    r=valid();put64(r.bytes,80,UINT64_MAX-16);expect(r,ProcessParamStatus::segment_bounds,"guest memory extent overflow");
    r=valid();put64(r.bytes,104,16);expect(r,ProcessParamStatus::parameter_address,"parameter memory span too small");
    r=valid();r.fail_at=0;expect(r,ProcessParamStatus::io,"failed header read");
    r=valid();r.fail_at=64;expect(r,ProcessParamStatus::io,"failed PH read");
    r=valid();r.fail_at=192;expect(r,ProcessParamStatus::io,"failed parameter read");
    if(argc==2) {
        const auto status=folklore::read_process_param(argv[1],p);
        check(status==ProcessParamStatus::ok,"supplied actual ELF parameter parse");
        if(status==ProcessParamStatus::ok)std::printf("Actual ELF SDK=0x%08X version=0x%08X offset=0x%llX size=%u\n",
            p.sdk_version,p.version,static_cast<unsigned long long>(p.file_offset),p.size);
    }
    std::printf("Process parameter checks: %d passed, %d failed\n",passed,failed);
    return failed?1:0;
}
