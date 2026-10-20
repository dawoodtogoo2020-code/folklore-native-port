/* Actual registered AIO NID/guest callback integration, using synthetic data. */
#include "fs_aio_probe.h"
#include "ppu_recomp.h"
#include "ppu_callback_worker.h"
#include "ps3emu/nid.h"
#include <array>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <mutex>
#include <string>
#include <thread>
#include <vector>
#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#else
#include <sys/mman.h>
#include <unistd.h>
#endif
extern "C" {
extern uint8_t* vm_base;
extern uint32_t ppu_vm_size;
extern const char* ppu_vfs_root;
extern PPU_THREAD_LOCAL ppu_context* g_active_ctx;
extern PPU_THREAD_LOCAL void (*g_trampoline_fn)(void*);
void ppu_hle_init(void);
void ppu_fs_register(void);
void lv2_init_syscalls(void);
uint64_t ppu_thread_register_main(void);
void ppu_resv_register(ppu_context*);
uint32_t ppu_load_elf(const char*);
void ppu_register_function(uint64_t, void (*)(ppu_context*));
void ps3_hle_call(uint32_t, ppu_context*);
int32_t ppu_fs_aio_shutdown(void);
int ppu_run(uint32_t,uint32_t);
extern ppu_context* volatile g_ppu_main_ctx;
uint64_t ppu_guest_call(uint32_t,uint64_t,uint64_t,uint64_t,uint64_t,uint64_t,uint64_t,uint64_t,uint64_t);
}
namespace {
using namespace std::chrono_literals;
constexpr uint32_t VmSize=0xE0000000u, Opd=0x12000, NestedOpd=0x12008;
constexpr uint32_t CallbackCode=0x700000, NestedCode=0x700004, Toc=0x16000;
constexpr uint32_t EntryCode=0x700008, EntryOpd=0x12010;
constexpr uint32_t Path=0x14000, Mount=0x14500, FdOut=0x15000, PosOut=0x15100;
constexpr uint32_t ReadOut=0x15200, MainTls=0x1F000, MainSp=0x90000;
constexpr uint32_t Init=0xDB869F20, Read=0xC1C507E7, Cancel=0x7F13FC8C, Finish=0x9F951810;
constexpr uint64_t Fault=0xFFFFFFFF8001000Dull, BadFd=0xFFFFFFFF8001002Aull;
constexpr uint64_t NoInit=0xFFFFFFFF8001002Full, Cancelled=0xFFFFFFFF80010013ull;
constexpr char Original[]="0123456789-ORIGINAL-FILE-payload";
constexpr char Replacement[]="abcdefghij-REPLACEMENT-payload";
constexpr uint64_t Sentinel=0xA5A5A5A5;
static ppu_context caller{}; // Reservation registry retains stable context pointers.
uint32_t req(unsigned n){return 0x20000+n*0x100;}
uint32_t idout(unsigned n){return 0x28000+n*4;}
uint32_t buf(unsigned n){return 0x30000+n*0x100;}
void put32(uint8_t* p,uint32_t v){for(int i=3;i>=0;--i){p[i]=(uint8_t)v;v>>=8;}}
void put64(uint8_t* p,uint64_t v){for(int i=7;i>=0;--i){p[i]=(uint8_t)v;v>>=8;}}
uint32_t get32(const uint8_t* p){return(uint32_t(p[0])<<24)|(uint32_t(p[1])<<16)|(uint32_t(p[2])<<8)|p[3];}
uint64_t get64(const uint8_t* p){return(uint64_t(get32(p))<<32)|get32(p+4);}
struct Record {uint64_t request,error,id,bytes,sp,tls,tid;std::thread::id host;};
struct State {
    std::mutex mutex;std::condition_variable cv;std::vector<Record> records;
    std::thread::id main_host;
    uint32_t gate=0;bool entered=false,released=true;
    std::atomic<unsigned> failures{0},nested{0},inner_tramp{0},outer_tramp{0};
} state;
void bad(const char* text){state.failures.fetch_add(1);std::fprintf(stderr,"[fs-aio-probe] CALLBACK FAIL %s\n",text);}
void outer_trampoline(void*){state.outer_tramp.fetch_add(1);}
void inner_trampoline(void*){state.inner_tramp.fetch_add(1);}
static ppu_context* returned_main[2]{};
static unsigned entry_calls=0;
void returning_entry(ppu_context* c){
    if(entry_calls>=2){bad("unexpected entry dispatch");return;}
    returned_main[entry_calls++]=c;
    if(g_active_ctx!=c || g_ppu_main_ctx!=c || c->thread_id!=caller.thread_id || g_trampoline_fn)
        bad("main active context/identity/trampoline");
    const uint64_t sp=c->gpr[1];
    if(ppu_run(EntryOpd,MainSp)!=-1 || g_active_ctx!=c || g_ppu_main_ctx!=c || c->gpr[1]!=sp)
        bad("main reentrant rejection/restoration");
    c->gpr[20]=0xA10CA11000000000ull+entry_calls;
}
void nested_callback(ppu_context* c){
    if(c!=g_active_ctx || c->gpr[3]!=0x12345678 || !c->gpr[13] || !c->thread_id)bad("nested ABI");
    if(c->gpr[1]>=c->gpr[4] || c->gpr[13]!=c->gpr[5] || c->thread_id!=c->gpr[6])
        bad("nested stack/TLS/thread identity");
    c->gpr[20]=0xDEADBEEF;c->fpr[1]=9.5;
    c->gpr[3]=0x8877665544332211ull;g_trampoline_fn=inner_trampoline;state.nested.fetch_add(1);
}
void callback(ppu_context* c){
    Record r{c->gpr[3],c->gpr[4],c->gpr[5],c->gpr[6],c->gpr[1],c->gpr[13],c->thread_id,std::this_thread::get_id()};
    if(c!=g_active_ctx || c->gpr[2]!=Toc || (r.sp&15))bad("active context/TOC/alignment");
    if(r.request<req(0) || r.request>req(13) || ((r.request-req(0))%0x100))bad("request callback argument");
    else if(get32(vm_base+idout((unsigned)(r.request-req(0))/0x100))!=r.id)bad("ID was not published before callback");
    const uint64_t saved_sp=c->gpr[1],saved_tls=c->gpr[13],saved_tid=c->thread_id,saved_arg=c->gpr[3];
    const uint64_t canary=0xBAADF00D12345678ull;
    std::memcpy(vm_base+saved_sp+128,&canary,sizeof canary);
    c->gpr[20]=0x12344321;c->fpr[1]=3.25;
    g_trampoline_fn=outer_trampoline;
    uint64_t result=ppu_guest_call(NestedOpd,0x12345678,saved_sp,saved_tls,saved_tid,0,0,0,0);
    uint64_t observed=0;std::memcpy(&observed,vm_base+saved_sp+128,sizeof observed);
    if(result!=0x8877665544332211ull || c!=g_active_ctx || c->gpr[1]!=saved_sp ||
       c->gpr[13]!=saved_tls || c->thread_id!=saved_tid || c->gpr[3]!=saved_arg ||
       c->gpr[20]!=0x12344321 || c->fpr[1]!=3.25 ||
       g_trampoline_fn!=outer_trampoline || observed!=canary)bad("nested restoration/stack canary");
    if(r.error==0){
        if(r.host==state.main_host || r.tid==caller.thread_id || r.tls==MainTls ||
           r.sp<0xD0000000ull || r.sp>=VmSize)bad("normal completion lacks worker isolation");
        if(std::memcmp(vm_base+r.tls-0x7000,vm_base+0x10400,16)!=0)bad("worker TLS initialization");
        for(unsigned i=16;i<64;++i)if(vm_base[r.tls-0x7000+i]!=0){bad("TLS BSS");break;}
    }else if(r.error==Cancelled){
        if(r.host!=state.main_host || r.tid!=caller.thread_id || r.tls!=MainTls || r.sp>=MainSp)
            bad("cancel completion not on actual caller");
    }else bad("unexpected callback error/sign extension");
    std::unique_lock<std::mutex> lock(state.mutex);
    if(r.request==state.gate){
        state.entered=true;state.cv.notify_all();
        if(!state.cv.wait_for(lock,10s,[]{return state.released;}))bad("gate timed out");
    }
    state.records.push_back(r);state.cv.notify_all();
}
void release_gate(){std::lock_guard<std::mutex> l(state.mutex);state.released=true;state.cv.notify_all();}
bool gate(unsigned n){std::lock_guard<std::mutex> l(state.mutex);state.gate=req(n);state.entered=false;state.released=false;return true;}
bool wait_gate(){std::unique_lock<std::mutex> l(state.mutex);return state.cv.wait_for(l,5s,[]{return state.entered;});}
bool record(unsigned n,Record* out=nullptr){
    std::unique_lock<std::mutex> l(state.mutex);
    auto found=[&]{for(const auto& r:state.records)if(r.request==req(n)){if(out)*out=r;return true;}return false;};
    return state.cv.wait_for(l,5s,found);
}
size_t count(){std::lock_guard<std::mutex> l(state.mutex);return state.records.size();}
uint64_t call(uint32_t nid,uint64_t a=0,uint64_t b=0,uint64_t c=0,uint64_t d=0){
    caller.gpr[3]=a;caller.gpr[4]=b;caller.gpr[5]=c;caller.gpr[6]=d;
    ps3_hle_call(nid,&caller);return caller.gpr[3];
}
uint64_t named(const char* name,uint64_t a=0,uint64_t b=0,uint64_t c=0,uint64_t d=0){return call(ps3_compute_nid(name),a,b,c,d);}
void request(unsigned n,uint32_t fd,uint64_t offset=11,uint64_t size=8){
    std::memset(vm_base+req(n),0,0x28);put32(vm_base+req(n),fd);put64(vm_base+req(n)+8,offset);
    put32(vm_base+req(n)+0x10,buf(n));put64(vm_base+req(n)+0x18,size);
    put64(vm_base+req(n)+0x20,0x1122334455667788ull);put32(vm_base+idout(n),(uint32_t)Sentinel);
    std::memset(vm_base+buf(n),0xA5,0x100);
}
uint64_t submit(unsigned n){return call(Read,req(n),idout(n),Opd);}
bool valid(unsigned n,uint64_t error,uint64_t bytes){
    Record r{};return record(n,&r)&&r.error==error&&r.bytes==bytes&&r.id==get32(vm_base+idout(n))&&r.id<320;
}
int fail(int code,const char* message,uint64_t result=0){std::fprintf(stderr,"[fs-aio-probe] FAIL %d %s actual=0x%016llX\n",code,message,(unsigned long long)result);return code;}
void pass(const char* message){std::fprintf(stderr,"[fs-aio-probe] PASS %s\n",message);}
bool create(const std::filesystem::path& path,const void* data,size_t size){
    FILE* f=std::fopen(path.string().c_str(),"wbx");if(!f)return false;
    bool ok=std::fwrite(data,1,size,f)==size;if(std::fclose(f)!=0)ok=false;return ok;
}
bool elf(const std::filesystem::path& path){
    std::array<uint8_t,0x900> e{};std::memcpy(e.data(),"\177ELF",4);e[4]=2;e[5]=2;e[6]=1;
    e[17]=2;e[19]=21;put32(e.data()+20,1);put64(e.data()+24,Opd);put64(e.data()+32,64);
    e[53]=64;e[55]=56;e[57]=2;
    uint8_t* p=e.data()+64;put32(p,1);put32(p+4,6);put64(p+8,0x100);put64(p+16,0x10000);
    put64(p+32,0x800);put64(p+40,0x1000);put64(p+48,0x1000);
    p+=56;put32(p,7);put32(p+4,4);put64(p+8,0x500);put64(p+16,0x10400);
    put64(p+32,16);put64(p+40,64);put64(p+48,16);
    for(unsigned i=0;i<16;++i)e[0x500+i]=(uint8_t)(0x31+i);
    return create(path,e.data(),e.size());
}
struct Vm {
    uint8_t* old_base=vm_base;uint32_t old_size=ppu_vm_size;const char* old_root=ppu_vfs_root;
    ppu_context* old_active=g_active_ctx;void(*old_trampoline)(void*)=g_trampoline_fn;
    uint8_t* memory=nullptr;bool installed=false,expected_startup_failure=false;std::vector<uint32_t> fds;
    bool allocate(){
#ifdef _WIN32
        memory=(uint8_t*)VirtualAlloc(nullptr,VmSize,MEM_RESERVE,PAGE_NOACCESS);if(!memory)return false;
        if(!VirtualAlloc(memory+0x10000,0x100000,MEM_COMMIT,PAGE_READWRITE))return false;
#else
        void* p=mmap(nullptr,VmSize,PROT_NONE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);if(p==MAP_FAILED)return false;
        memory=(uint8_t*)p;if(mprotect(memory+0x10000,0x100000,PROT_READ|PROT_WRITE)!=0)return false;
#endif
        vm_base=memory;ppu_vm_size=VmSize;installed=true;return true;
    }
    bool protect(unsigned mode){
#ifdef _WIN32
        DWORD previous=0;return VirtualProtect(memory+0x50000,0x1000,mode==0?PAGE_NOACCESS:mode==1?PAGE_READONLY:PAGE_READWRITE,&previous)!=0;
#else
        return mprotect(memory+0x50000,0x1000,mode==0?PROT_NONE:mode==1?PROT_READ:PROT_READ|PROT_WRITE)==0;
#endif
    }
    bool main_pages(){
        for(uint32_t address:{0xB00000u,0x10F00000u}){
#ifdef _WIN32
            if(!VirtualAlloc(memory+address,0x1000,MEM_COMMIT,PAGE_READWRITE))return false;
#else
            if(mprotect(memory+address,0x1000,PROT_READ|PROT_WRITE)!=0)return false;
#endif
        }
        return true;
    }
    ~Vm(){
        if(installed){
            release_gate();
            const int32_t shutdown=ppu_fs_aio_shutdown();
            if((shutdown!=0 && !(expected_startup_failure && shutdown==(int32_t)0x8001002Bu)) || ppu_callback_workers_quiesce()!=0){
                std::fputs("[fs-aio-probe] unsafe teardown refused\n",stderr);std::_Exit(199);
            }
            for(uint32_t fd:fds)if(fd)named("cellFsClose",fd);
        }
        g_active_ctx=old_active;g_trampoline_fn=old_trampoline;vm_base=old_base;ppu_vm_size=old_size;ppu_vfs_root=old_root;
        if(memory){
#ifdef _WIN32
            VirtualFree(memory,0,MEM_RELEASE);
#else
            munmap(memory,VmSize);
#endif
        }
    }
};
}
static int check_fs_aio(const char* directory,const char* prepared_sparse_directory,bool capacity_only){
    namespace fs=std::filesystem;
    if(!directory || !*directory)return fail(120,"missing fixture directory");
    fs::path root(directory);std::error_code ec;
    if(!root.is_absolute() || !fs::is_directory(root,ec) || ec || !fs::is_empty(root,ec) || ec)
        return fail(121,"fixture must be absolute, existing and empty");
    if(vm_base || g_active_ctx)return fail(122,"probe requires fresh process before VM/game initialization");
    std::string sparse_root;
    if(prepared_sparse_directory){
        fs::path prepared(prepared_sparse_directory);
        if(!prepared.is_absolute() || fs::file_size(prepared/"seek.bin",ec)!=2388652516ull || ec)
            return fail(163,"prepared sparse fixture size/path");
        sparse_root=prepared.string();
    }
    if(!create(root/"original.bin",Original,sizeof Original-1) || !create(root/"replacement.bin",Replacement,sizeof Replacement-1) ||
       !elf(root/"synthetic-tls.elf"))return fail(123,"exclusive synthetic fixture creation");
    const std::string root_text=root.string();Vm vm;
    if(!vm.allocate())return fail(124,"VM reservation/commit");
    ppu_vfs_root=root_text.c_str();
    if(ppu_load_elf((root/"synthetic-tls.elf").string().c_str())!=Opd)return fail(125,"synthetic ELF loader");
    lv2_init_syscalls();ppu_hle_init();ppu_fs_register();
    caller={};caller.thread_id=ppu_thread_register_main();caller.gpr[1]=MainSp;caller.gpr[2]=Toc;caller.gpr[13]=MainTls;
    std::memset(vm_base+MainTls-0x7000,0xA7,64);g_active_ctx=&caller;g_trampoline_fn=nullptr;ppu_resv_register(&caller);
    if(ppu_callback_workers_resume()!=0)return fail(126,"callback lifecycle resume");
    state.main_host=std::this_thread::get_id();state.records.reserve(32);
    ppu_register_function(CallbackCode,callback);ppu_register_function(NestedCode,nested_callback);
    ppu_register_function(EntryCode,returning_entry);
    put32(vm_base+Opd,CallbackCode);put32(vm_base+Opd+4,Toc);put32(vm_base+NestedOpd,NestedCode);put32(vm_base+NestedOpd+4,Toc);
    put32(vm_base+EntryOpd,EntryCode);put32(vm_base+EntryOpd+4,Toc);
    if(!vm.main_pages())return fail(179,"main-entry scratch/TLS page setup");
    ppu_context* const previous_main=g_ppu_main_ctx;
    g_trampoline_fn=outer_trampoline;
    for(unsigned i=0;i<2;++i){
        if(ppu_run(EntryOpd,MainSp)!=0 || g_active_ctx!=&caller || g_trampoline_fn!=outer_trampoline || g_ppu_main_ctx!=previous_main)
            return fail(180,"main-entry exit restoration");
    }
    g_trampoline_fn=nullptr;
    if(entry_calls!=2 || returned_main[0]==returned_main[1] ||
       returned_main[0]->gpr[20]!=0xA10CA11000000001ull || returned_main[1]->gpr[20]!=0xA10CA11000000002ull)
        return fail(181,"retained main context storage");
    pass("returned main contexts remain stable, reentry rejected, active/trampoline/main pointer restored");
    std::strcpy((char*)vm_base+Mount,"/app_home");std::strcpy((char*)vm_base+Path,"/app_home/original.bin");
    if(capacity_only){
        static ppu_context retained[128]{};
        for(auto& context:retained)ppu_resv_register(&context);
        const uint64_t result=call(Init,Mount);
        if(result!=0xFFFFFFFF8001002Bull || count()!=0 || ppu_fs_aio_shutdown()!=(int32_t)0x8001002Bu || ppu_callback_workers_quiesce()!=0)
            return fail(182,"unregistered callback worker startup must fail without leaked worker",result);
        vm.expected_startup_failure=true;
        pass("reservation capacity exhausted: actual Init fails, no callback/worker survives; synthetic integration complete");
        return 0;
    }
    if(uint64_t r=named("cellFsOpen",Path,0,FdOut);r)return fail(127,"actual open",r);
    uint32_t fd=get32(vm_base+FdOut);vm.fds.push_back(fd);
    request(0,fd);
    if(uint64_t r=submit(0);r!=NoInit)return fail(128,"read before init ENXIO",r);
    if(get32(vm_base+idout(0))!=Sentinel || vm_base[buf(0)]!=0xA5)return fail(129,"pre-init mutated output");
    pass("before-init ENXIO with untouched outputs");
    if(uint64_t r=call(Init,Mount);r)return fail(130,"actual init",r);
    if(uint64_t r=named("cellFsLseek",fd,3,0,PosOut);r)return fail(131,"set normal descriptor position",r);
    if(uint64_t r=submit(0);r)return fail(132,"actual read submission",r);
    if(!valid(0,0,8) || std::memcmp(vm_base+buf(0),Original+11,8) || vm_base[buf(0)+8]!=0xA5)
        return fail(133,"read callback/data/id");
    if(uint64_t r=named("cellFsLseek",fd,0,1,PosOut);r || get64(vm_base+PosOut)!=3)return fail(134,"AIO changed stream position",r);
    if(uint64_t r=named("cellFsRead",fd,0x40000,4,ReadOut);r || get64(vm_base+ReadOut)!=4 || std::memcmp(vm_base+0x40000,Original+3,4))
        return fail(135,"normal descriptor read after AIO",r);
    pass("real read bytes/id/four arguments, worker liveness without Sysutil, separate stack/TLS/thread, nested restoration, preserved descriptor position");
    const size_t before=count();
    auto rejected=[&](unsigned n,uint32_t rq,uint32_t ip,uint32_t op,uint64_t expected){
        const uint64_t r=call(Read,rq,ip,op);
        if(r!=expected || get32(vm_base+idout(n))!=Sentinel)return false;
        for(unsigned i=0;i<0x100;++i)if(vm_base[buf(n)+i]!=0xA5)return false;
        return true;
    };
    request(1,fd);if(!rejected(1,1,idout(1),Opd,Fault))return fail(136,"invalid request pointer");
    if(!rejected(1,req(1),1,Opd,Fault))return fail(137,"invalid ID output pointer");
    put32(vm_base+req(1)+0x10,1);if(!rejected(1,req(1),idout(1),Opd,Fault))return fail(138,"invalid data pointer");
    request(1,fd);if(!rejected(1,req(1),idout(1),1,Fault))return fail(139,"invalid callback pointer");
    put32(vm_base+0x12020,0x777770);put32(vm_base+0x12024,Toc);
    if(!rejected(1,req(1),idout(1),0x12020,Fault))return fail(140,"unregistered callback code");
    request(1,0x7FFFFFFF);if(!rejected(1,req(1),idout(1),Opd,BadFd))return fail(141,"bad descriptor/sign extension");
    request(1,fd);std::memset(vm_base+0x50000,0xA5,0x1000);
    if(!vm.protect(0))return fail(164,"guard-page setup");
    if(!rejected(1,0x50000,idout(1),Opd,Fault) || !rejected(1,req(1),idout(1),0x50000,Fault))
        return fail(165,"in-range inaccessible request/OPD");
    put32(vm_base+req(1)+0x10,0x4FFF8);put64(vm_base+req(1)+0x18,16);
    if(!rejected(1,req(1),idout(1),Opd,Fault))return fail(166,"buffer crosses inaccessible page");
    if(!vm.protect(1))return fail(167,"readonly-page setup");
    request(1,fd);
    if(!rejected(1,req(1),0x50000,Opd,Fault))return fail(168,"readonly ID output");
    put32(vm_base+req(1)+0x10,0x50000);
    if(!rejected(1,req(1),idout(1),Opd,Fault))return fail(169,"readonly data output");
    if(!vm.protect(2))return fail(170,"guard-page restore");
    for(unsigned i=0;i<0x1000;++i)if(vm_base[0x50000+i]!=0xA5)return fail(171,"protected page modified");
    if(count()!=before)return fail(142,"rejected submission dispatched a callback");
    pass("invalid request/id/buffer/OPD and bad fd preserve outputs, including in-range inaccessible/cross-page/readonly spans");
    // Match the game's three simultaneous mount workers, then prove all three
    // have distinct real host/guest identities and thread-local attachment.
    std::strcpy((char*)vm_base+Mount,"/dev_bdvd");
    if(call(Init,Mount))return fail(183,"second simultaneous mount Init");
    std::strcpy((char*)vm_base+Path,"/dev_bdvd/original.bin");
    if(named("cellFsOpen",Path,0,FdOut))return fail(184,"second mount open");
    const uint32_t disc_fd=get32(vm_base+FdOut);vm.fds.push_back(disc_fd);
    std::strcpy((char*)vm_base+Mount,"/dev_hdd0");
    if(call(Init,Mount))return fail(185,"third simultaneous mount Init");
    std::strcpy((char*)vm_base+Path,"/dev_hdd0/original.bin");
    if(named("cellFsOpen",Path,0,FdOut))return fail(186,"third mount open");
    const uint32_t hdd_fd=get32(vm_base+FdOut);vm.fds.push_back(hdd_fd);
    request(12,disc_fd);request(13,hdd_fd);
    if(submit(12) || submit(13) || !valid(12,0,8) || !valid(13,0,8))return fail(187,"independent mount completions");
    Record app_record{},disc_record{},hdd_record{};
    if(!record(0,&app_record) || !record(12,&disc_record) || !record(13,&hdd_record) ||
       app_record.host==disc_record.host || app_record.host==hdd_record.host || disc_record.host==hdd_record.host ||
       app_record.tid==disc_record.tid || app_record.tid==hdd_record.tid || disc_record.tid==hdd_record.tid ||
       app_record.tls==disc_record.tls || app_record.tls==hdd_record.tls || disc_record.tls==hdd_record.tls ||
       disc_record.id/32==app_record.id/32 || hdd_record.id/32==app_record.id/32 || disc_record.id/32==hdd_record.id/32)
        return fail(188,"simultaneous mount worker identity/ID namespaces");
    if(call(Finish,Mount))return fail(189,"third mount Finish");
    std::strcpy((char*)vm_base+Mount,"/dev_bdvd");
    if(call(Finish,Mount))return fail(190,"second mount Finish");
    std::strcpy((char*)vm_base+Mount,"/app_home");
    pass("three simultaneous mounts use distinct host threads, guest IDs, TLS and request namespaces");
    // Hold one real worker callback; queued work cannot start until released.
    request(2,fd);gate(2);if(submit(2) || !wait_gate())return fail(143,"cancel gate start");
    request(3,fd);if(submit(3))return fail(144,"queued cancel submit");
    if(uint64_t r=call(Cancel,get32(vm_base+idout(3)));r)return fail(145,"queued cancel",r);
    if(!valid(3,Cancelled,0) || vm_base[buf(3)]!=0xA5)return fail(146,"cancel ABI/data");
    if(g_active_ctx!=&caller || caller.gpr[1]!=MainSp || caller.gpr[13]!=MainTls || g_trampoline_fn)
        return fail(147,"cancel caller restoration");
    release_gate();if(!valid(2,0,8))return fail(148,"gate callback did not finish");
    pass("queued Cancel executes exactly on cancelling guest caller with signed ECANCELED");
    request(4,fd);gate(4);if(submit(4) || !wait_gate())return fail(149,"descriptor pin gate");
    request(5,fd);if(submit(5))return fail(150,"pinned queued submit");
    if(named("cellFsClose",fd))return fail(151,"close descriptor with pending request");vm.fds[0]=0;
    std::strcpy((char*)vm_base+Path,"/app_home/replacement.bin");
    if(named("cellFsOpen",Path,0,FdOut))return fail(152,"replacement open");
    const uint32_t reused=get32(vm_base+FdOut);vm.fds.push_back(reused);
    if(reused!=fd)return fail(153,"descriptor reuse not exercised",reused);
    release_gate();if(!valid(4,0,8)||!valid(5,0,8)||std::memcmp(vm_base+buf(5),Original+11,8))return fail(154,"old file not pinned across reuse");
    request(6,reused);if(submit(6)||!valid(6,0,8)||std::memcmp(vm_base+buf(6),Replacement+11,8))return fail(155,"replacement descriptor mismatch");
    pass("actual close/reuse preserves accepted original file identity");
    request(7,reused);gate(7);if(submit(7)||!wait_gate())return fail(156,"Finish gate");
    request(8,reused);request(9,reused);if(submit(8)||submit(9))return fail(157,"Finish queued submits");
    std::thread releaser([]{if(!record(8)||!record(9))bad("Finish did not deliver queued cancels before waiting");release_gate();});
    const uint64_t finished=call(Finish,Mount);releaser.join();
    if(finished || !valid(7,0,8) || !valid(8,Cancelled,0) || !valid(9,Cancelled,0) ||
       vm_base[buf(8)]!=0xA5 || vm_base[buf(9)]!=0xA5)return fail(158,"Finish callbacks/cancellation",finished);
    pass("Finish cancels queued requests on caller and waits for running completion");
    if(!sparse_root.empty()){
        ppu_vfs_root=sparse_root.c_str();
        std::strcpy((char*)vm_base+Path,"/app_home/seek.bin");
        if(named("cellFsOpen",Path,0,FdOut))return fail(172,"prepared sparse open");
        const uint32_t large=get32(vm_base+FdOut);vm.fds.push_back(large);
        if(call(Init,Mount) || named("cellFsLseek",large,7,0,PosOut))return fail(173,"large-offset worker init/position");
        request(10,large,2388652496ull,20);
        if(submit(10) || !valid(10,0,20) || std::memcmp(vm_base+buf(10),"TAIL-at-large-offset",20) || vm_base[buf(10)+20]!=0xA5)
            return fail(174,"AIO read above 2 GiB");
        request(11,large,2388652511ull,16);
        if(submit(11) || !valid(11,0,5) || std::memcmp(vm_base+buf(11),"ffset",5) || vm_base[buf(11)+5]!=0xA5)
            return fail(175,"large-offset short EOF");
        if(named("cellFsLseek",large,0,1,PosOut) || get64(vm_base+PosOut)!=7)return fail(176,"large AIO changed stream position");
        if(call(Finish,Mount))return fail(177,"large-offset Finish");
        ppu_vfs_root=root_text.c_str();
        pass("actual AIO beyond 2 GiB, short EOF bytes and original file position on prepared sparse fixture");
    }
    if(ppu_fs_aio_shutdown()!=0 || ppu_callback_workers_quiesce()!=0)return fail(159,"shutdown/VM quiescence");
    const size_t completed=count();std::this_thread::sleep_for(50ms);
    if(count()!=completed || state.failures.load() || state.nested.load()!=completed ||
       state.inner_tramp.load()!=completed || state.outer_tramp.load()!=completed)
        return fail(160,"callback invariants/counts or post-shutdown activity",state.failures.load());
    for(unsigned n:{0u,2u,3u,4u,5u,6u,7u,8u,9u,12u,13u}){
        unsigned occurrences=0;for(const auto& r:state.records)if(r.request==req(n))++occurrences;
        if(occurrences!=1)return fail(161,"callback not exactly once",n);
    }
    if(!sparse_root.empty())for(unsigned n:{10u,11u}){
        unsigned occurrences=0;for(const auto& r:state.records)if(r.request==req(n))++occurrences;
        if(occurrences!=1)return fail(178,"large callback not exactly once",n);
    }
    if(completed!=(sparse_root.empty()?11:13))return fail(162,"unexpected completion count",completed);
    pass("shutdown joined callbacks, quiescence succeeds, exact-once delivery; synthetic integration complete");
    return 0;
}
int folklore_check_fs_aio(const char* directory,const char* prepared_sparse_directory){return check_fs_aio(directory,prepared_sparse_directory,false);}
int folklore_check_fs_aio_capacity(const char* directory){return check_fs_aio(directory,nullptr,true);}
