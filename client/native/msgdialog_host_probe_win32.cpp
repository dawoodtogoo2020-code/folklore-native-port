// Exercises the real presenter and service with synthetic memory only.
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include "msgdialog_host_win32.h"
#include "cellMsgDialog.h"
#include "cellSysutil.h"
#include "ps3emu/guest_call.h"
#include <cstdio>
#include <cstring>
#include <cwchar>
#include <filesystem>
#include <fstream>
#include <vector>
#include <string>
extern "C" { extern unsigned char* vm_base; extern unsigned int ppu_vm_size; }

namespace {
int completions;
int last_result;
void callback(u32 opd,u64 a0,u64,u64,u64,u64,u64,u64,u64)
{
    if(opd==0x70000) { ++completions;last_result=static_cast<s32>(a0); }
}
bool capture(HWND window,const std::filesystem::path& path)
{
    if(std::filesystem::exists(path))return false;
    RECT bounds{};if(!GetWindowRect(window,&bounds))return false;
    int width=bounds.right-bounds.left,height=bounds.bottom-bounds.top;
    if(width<=0 || height<=0 || width>4096 || height>4096)return false;
    HDC screen=GetDC(window),memory=CreateCompatibleDC(screen);
    HBITMAP bitmap=CreateCompatibleBitmap(screen,width,height);
    if(!screen || !memory || !bitmap) {
        if(bitmap)DeleteObject(bitmap);if(memory)DeleteDC(memory);if(screen)ReleaseDC(window,screen);
        return false;
    }
    HGDIOBJ old=SelectObject(memory,bitmap);
    bool drawn=PrintWindow(window,memory,0)!=0;
    SelectObject(memory,old);
    BITMAPINFO info{};info.bmiHeader.biSize=sizeof(BITMAPINFOHEADER);
    info.bmiHeader.biWidth=width;info.bmiHeader.biHeight=-height;
    info.bmiHeader.biPlanes=1;info.bmiHeader.biBitCount=32;info.bmiHeader.biCompression=BI_RGB;
    std::vector<unsigned char> pixels(static_cast<size_t>(width)*height*4);
    bool read=GetDIBits(memory,bitmap,0,height,pixels.data(),&info,DIB_RGB_COLORS)==height;
    DeleteObject(bitmap);DeleteDC(memory);ReleaseDC(window,screen);
    if(!drawn || !read)return false;
    BITMAPFILEHEADER header{};header.bfType=0x4D42;
    header.bfOffBits=sizeof(header)+sizeof(info.bmiHeader);
    header.bfSize=header.bfOffBits+static_cast<DWORD>(pixels.size());
    std::ofstream output(path,std::ios::binary);
    output.write(reinterpret_cast<const char*>(&header),sizeof(header));
    output.write(reinterpret_cast<const char*>(&info.bmiHeader),sizeof(info.bmiHeader));
    output.write(reinterpret_cast<const char*>(pixels.data()),pixels.size());
    return output.good();
}
void pump()
{
    MSG message;
    while(PeekMessageW(&message,nullptr,0,0,PM_REMOVE)) {
        TranslateMessage(&message);DispatchMessageW(&message);
    }
}
}

int folklore_capture_dialog_host_window(const char* destination)
{
    HWND window=static_cast<HWND>(folklore_dialog_host_window());
    Ps3MsgDialogHostState state{};
    if(!folklore_dialog_host_displayed(&state))return 0;
    const bool cancel_visible=window && IsWindowVisible(GetDlgItem(window,204));
    if(cancel_visible!=!(state.type&0x80))return 0;
    if(window)RedrawWindow(window,nullptr,nullptr,RDW_INVALIDATE|RDW_UPDATENOW|RDW_ALLCHILDREN|RDW_FRAME);
    std::fprintf(stderr,"[dialog-capture] type=0x%X cancel_visible=%u generation=%llu\n",
        state.type,unsigned(cancel_visible),static_cast<unsigned long long>(state.generation));
    if(window && capture(window,std::filesystem::path(destination)))return 1;
    Ps3MsgDialogHostState current{};
    if(!folklore_dialog_host_displayed(&current) || current.generation!=state.generation ||
       folklore_dialog_host_window()!=window)return 0;
    return -1;
}

int folklore_check_dialog_host_ui(const char* destination)
{
    const std::filesystem::path folder(destination);
    if(!folder.is_absolute() || !std::filesystem::is_directory(folder))return 90;
    unsigned char memory[0x20000]{};vm_base=memory;ppu_vm_size=sizeof(memory);
    g_ps3_guest_caller=callback;
    std::strcpy(reinterpret_cast<char*>(memory+0x10000),
        "Synthetic dialog UI probe\nNo game or assets loaded.\nUnicode: caf\xC3\xA9 & text");
    auto open=[&](u32 type) {
        return cellMsgDialogOpen2(type,reinterpret_cast<const char*>(0x10000),
            reinterpret_cast<CellMsgDialogCallback>(0x70000),nullptr,nullptr)==0 &&
            folklore_dialog_host_tick();
    };
    auto hwnd=[] { return static_cast<HWND>(folklore_dialog_host_window()); };
    auto finish=[&](int result) {
        int before=completions;cellSysutilCheckCallback();
        return completions==before+1 && last_result==result && folklore_dialog_host_tick() && !hwnd();
    };
    int failure=0;
    auto require=[&](bool ok,int code) { if(!ok && !failure)failure=code;return ok; };
    if(!folklore_dialog_host_start())return 91;
    do {
        if(!require(open(1),92))break;
        HWND window=hwnd();wchar_t text[600]{};
        GetWindowTextW(GetDlgItem(window,100),text,600);
        if(!require(IsWindowVisible(GetDlgItem(window,204)) && !IsWindowVisible(GetDlgItem(window,201)) &&
                    std::wcscmp(text,L"Synthetic dialog UI probe\r\nNo game or assets loaded.\r\nUnicode: caf\u00E9 & text")==0 &&
                    capture(window,folder/"no-buttons.bmp"),93))break;
        for(int i=0;i<30;++i){pump();Sleep(1);}
        Ps3MsgDialogHostState state{};ps3_msgdialog_host_snapshot(&state);
        if(!require(state.open && completions==0,94))break;
        SendMessageW(window,WM_CLOSE,0,0);
        if(!require(completions==0 && finish(3),95))break;

        if(!require(open(0x111),96))break;
        window=hwnd();
        if(!require(GetFocus()==GetDlgItem(window,202) && capture(window,folder/"yes-no.bmp"),97))break;
        SendMessageW(GetFocus(),WM_KEYDOWN,VK_TAB,0);
        if(!require(GetFocus()==GetDlgItem(window,204),98))break;
        SendMessageW(GetDlgItem(window,202),BM_CLICK,0,0);
        if(!require(finish(2),99))break;

        if(!require(open(0xA1),100))break;
        window=hwnd();SendMessageW(window,WM_CLOSE,0,0);
        SendMessageW(window,WM_KEYDOWN,VK_ESCAPE,0);
        SendMessageW(GetDlgItem(window,204),BM_CLICK,0,0);
        ps3_msgdialog_host_snapshot(&state);
        if(!require(state.open && !IsWindowVisible(GetDlgItem(window,204)) &&
                    capture(window,folder/"cancel-disabled.bmp"),101))break;
        SendMessageW(GetDlgItem(window,203),BM_CLICK,0,0);
        if(!require(finish(1),102))break;

        std::string long_text;
        for(int i=0;i<18;++i)long_text+="Read this full line & text\n";
        std::strcpy(reinterpret_cast<char*>(memory+0x10000),long_text.c_str());
        if(!require(open(0x2001),103))break;
        HWND edit=GetDlgItem(hwnd(),100);
        SendMessageW(edit,EM_LINESCROLL,0,10);
        LRESULT first_line=SendMessageW(edit,EM_GETFIRSTVISIBLELINE,0,0);
        std::strcpy(reinterpret_cast<char*>(memory+0x11000),"Synthetic bar & label");
        cellMsgDialogProgressBarSetMsg(0,reinterpret_cast<const char*>(0x11000));
        cellMsgDialogProgressBarInc(0,42);cellMsgDialogProgressBarInc(1,87);
        HWND focus=GetFocus();
        if(!require(first_line>0 && folklore_dialog_host_tick() && GetFocus()==focus &&
                    SendMessageW(edit,EM_GETFIRSTVISIBLELINE,0,0)==first_line &&
                    capture(hwnd(),folder/"progress.bmp"),104))break;
        SendMessageW(hwnd(),WM_CLOSE,0,0);
        if(!require(finish(3),105))break;

        if(!require(open(0x81) && GetFocus()==GetDlgItem(hwnd(),100),113))break;
        cellMsgDialogClose(0);
        if(!require(finish(-1),114))break;

        // A real queued old button command must not acquire a new generation.
        if(!require(open(0x11),106))break;
        HWND old=hwnd(),old_button=GetDlgItem(old,202);
        PostMessageW(old,WM_COMMAND,MAKEWPARAM(202,BN_CLICKED),reinterpret_cast<LPARAM>(old_button));
        cellMsgDialogAbort();
        if(!require(open(0x11),107))break;
        pump();ps3_msgdialog_host_snapshot(&state);
        if(!require(state.open && IsWindow(hwnd()),108))break;
        SendMessageW(hwnd(),WM_COMMAND,MAKEWPARAM(202,BN_CLICKED),reinterpret_cast<LPARAM>(old_button));
        ps3_msgdialog_host_snapshot(&state);
        if(!require(state.open,109))break;
        SendMessageW(GetDlgItem(hwnd(),201),BM_CLICK,0,0);
        if(!require(finish(1),110))break;

        // A guest may replace a bridge snapshot between two UI ticks. Capture
        // the generation actually on screen, then capture its replacement.
        if(!require(open(1),115))break;
        Ps3MsgDialogHostState displayed{};
        if(!require(folklore_dialog_host_displayed(&displayed),116))break;
        cellMsgDialogAbort();
        if(!require(cellMsgDialogOpenErrorCode(0x80010006u,nullptr,nullptr,nullptr)==0,117))break;
        Ps3MsgDialogHostState pending{};ps3_msgdialog_host_snapshot(&pending);
        Ps3MsgDialogHostState still_displayed{};
        if(!require(pending.generation!=displayed.generation &&
                    folklore_dialog_host_displayed(&still_displayed) &&
                    still_displayed.generation==displayed.generation &&
                    folklore_capture_dialog_host_window((folder/"transition-old.bmp").string().c_str())==1,118))break;
        if(!require(folklore_dialog_host_tick() && folklore_dialog_host_displayed(&still_displayed) &&
                    still_displayed.generation==pending.generation && still_displayed.type==0x20 &&
                    folklore_capture_dialog_host_window((folder/"error-code.bmp").string().c_str())==1,119))break;
        cellMsgDialogAbort();cellSysutilCheckCallback();
        if(!require(folklore_dialog_host_tick(),120))break;
        if(!require(open(0x21),111))break;
    } while(false);
    int before=completions;
    folklore_dialog_host_stop();cellSysutilCheckCallback();
    Ps3MsgDialogHostState disabled{};
    if(ps3_msgdialog_host_snapshot(&disabled)!=0 || completions!=before)failure=failure?failure:112;
    if(failure)std::fprintf(stderr,"synthetic dialog UI probe failed at %d\n",failure);
    else std::puts("synthetic dialog UI probe passed: real windows, copied text, controls, queued stale input and shutdown; no game executed");
    return failure;
}
