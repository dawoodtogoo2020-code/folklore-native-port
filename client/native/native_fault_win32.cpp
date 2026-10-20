#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <cstdio>
#include <cstdint>
#include "native_fault_win32.h"

namespace {
void* guest_base;
std::uint32_t guest_limit;
unsigned long long module_base;
LONG recorded;
LONG WINAPI record_fault(EXCEPTION_POINTERS* exception)
{
    if(InterlockedCompareExchange(&recorded,1,0)!=0)return EXCEPTION_CONTINUE_SEARCH;
    const EXCEPTION_RECORD& e=*exception->ExceptionRecord;
    const CONTEXT& c=*exception->ContextRecord;
    char output[4096];
    int size=std::snprintf(output,sizeof(output),
        "{\n\"schema\":\"folklore-unhandled-host-fault-v1\",\n"
        "\"code\":%lu,\"thread_id\":%lu,\n"
        "\"exception_address\":\"0x%llX\",\"operation\":%llu,\"access_address\":\"0x%llX\",\n"
        "\"module_base\":\"0x%llX\",\"guest_base\":\"0x%llX\",\"guest_limit\":%lu,\n"
        "\"rip\":\"0x%llX\",\"rsp\":\"0x%llX\",\"rbp\":\"0x%llX\",\n"
        "\"rax\":\"0x%llX\",\"rbx\":\"0x%llX\",\"rcx\":\"0x%llX\",\"rdx\":\"0x%llX\",\n"
        "\"rsi\":\"0x%llX\",\"rdi\":\"0x%llX\",\n"
        "\"r8\":\"0x%llX\",\"r9\":\"0x%llX\",\"r10\":\"0x%llX\",\"r11\":\"0x%llX\",\n"
        "\"r12\":\"0x%llX\",\"r13\":\"0x%llX\",\"r14\":\"0x%llX\",\"r15\":\"0x%llX\",\n"
        "\"fault_handled\":false,\"guest_memory_written\":false\n}\n",
        static_cast<unsigned long>(e.ExceptionCode),static_cast<unsigned long>(GetCurrentThreadId()),
        reinterpret_cast<unsigned long long>(e.ExceptionAddress),
        e.NumberParameters>0?static_cast<unsigned long long>(e.ExceptionInformation[0]):0,
        e.NumberParameters>1?static_cast<unsigned long long>(e.ExceptionInformation[1]):0,
        module_base,
        reinterpret_cast<unsigned long long>(guest_base),static_cast<unsigned long>(guest_limit),
        c.Rip,c.Rsp,c.Rbp,c.Rax,c.Rbx,c.Rcx,c.Rdx,c.Rsi,c.Rdi,c.R8,c.R9,c.R10,c.R11,c.R12,c.R13,c.R14,c.R15);
    if(size>0 && size<int(sizeof(output))) {
        DWORD written=0;
        HANDLE file=CreateFileW(L"native-fault.json",GENERIC_WRITE,0,nullptr,CREATE_NEW,FILE_ATTRIBUTE_NORMAL,nullptr);
        bool saved=false;
        if(file!=INVALID_HANDLE_VALUE) {
            saved=WriteFile(file,output,static_cast<DWORD>(size),&written,nullptr) && written==static_cast<DWORD>(size);
            CloseHandle(file);
        }
        const char* marker=saved
            ? "[folklore] unhandled host fault; native-fault.json contains diagnostic registers\n"
            : "[folklore] unhandled host fault; diagnostic file creation or full write failed\n";
        DWORD marker_length=0;while(marker[marker_length])++marker_length;
        WriteFile(GetStdHandle(STD_ERROR_HANDLE),marker,marker_length,&written,nullptr);
    }
    return EXCEPTION_CONTINUE_SEARCH;
}
}

void folklore_install_fault_capture(void* base,std::uint32_t limit)
{
    guest_base=base;guest_limit=limit;
    module_base=reinterpret_cast<unsigned long long>(GetModuleHandleW(nullptr));
    SetErrorMode(GetErrorMode()|SEM_NOGPFAULTERRORBOX);
    SetUnhandledExceptionFilter(record_fault);
}

int folklore_check_fault_capture()
{
    folklore_install_fault_capture(nullptr,0);
    ULONG_PTR args[2]={0,1};
    RaiseException(EXCEPTION_ACCESS_VIOLATION,0,2,args);
    return 117;
}
