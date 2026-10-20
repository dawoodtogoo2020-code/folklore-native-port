#ifdef _WIN32

#include "msgdialog_host_win32.h"
#include "cellMsgDialog.h"

#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <algorithm>
#include <cstring>
#include <initializer_list>
#include <string>

namespace {
constexpr wchar_t kClassName[] = L"FolkloreNativeSystemDialog";
constexpr wchar_t kTitle[] = L"Folklore system dialog";
constexpr int kMessageId = 100;
constexpr int kProgressLabel0 = 110;
constexpr int kYesId = 201;
constexpr int kNoId = 202;
constexpr int kOkId = 203;
constexpr int kCancelId = 204;
constexpr int kWidth = 560;
constexpr int kHeight = 340;

struct Host {
    DWORD thread = 0;
    HINSTANCE instance = nullptr;
    HWND window = nullptr;
    HWND message = nullptr;
    HWND progress_labels[2]{};
    HWND yes = nullptr;
    HWND no = nullptr;
    HWND ok = nullptr;
    HWND cancel = nullptr;
    u64 generation = 0;
    u64 answered_generation = 0;
    u32 type = 0;
    int focused_control = 0;
    bool started = false;
    bool registered = false;
    bool failing = false;
    Ps3MsgDialogHostState state{};
};
Host host;

template <size_t N>
std::wstring utf8(const char (&data)[N])
{
    size_t length = 0;
    while (length < N && data[length]) ++length;
    if (!length) return {};
    const int count = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, data,
                                           static_cast<int>(length), nullptr, 0);
    if (count <= 0) return L"[Invalid UTF-8 text]";
    std::wstring result(static_cast<size_t>(count), L'\0');
    if (!MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, data,
                             static_cast<int>(length), result.data(), count)) return L"[Invalid UTF-8 text]";
    return result;
}

void show_control(HWND control, bool visible)
{
    if (control) ShowWindow(control, visible ? SW_SHOW : SW_HIDE);
}

bool focusable_child(HWND control)
{
    return host.window && control && GetParent(control) == host.window &&
           IsWindowVisible(control) && IsWindowEnabled(control) &&
           host.generation && static_cast<u64>(GetWindowLongPtrW(host.window, GWLP_USERDATA)) == host.generation;
}

HWND default_focus()
{
    const unsigned buttons = (host.type >> 4) & 7u;
    HWND preferred = buttons == 1 ? ((host.type & 0x100) ? host.no : host.yes) :
                     buttons == 2 ? host.ok : !(host.type & 0x80) ? host.cancel : host.message;
    if (focusable_child(preferred)) return preferred;
    for (HWND control : {host.yes, host.no, host.ok, host.cancel, host.message})
        if (focusable_child(control)) return control;
    return nullptr;
}

void restore_child_focus(HWND window)
{
    if (!host.window || window != host.window) return;
    HWND control = host.focused_control ? GetDlgItem(window, host.focused_control) : nullptr;
    if (!focusable_child(control)) control = default_focus();
    if (control) SetFocus(control);
}

void respond(HWND source_window,s32 result)
{
    if (!host.window || source_window!=host.window) return;
    const u64 generation=static_cast<u64>(GetWindowLongPtrW(source_window,GWLP_USERDATA));
    if(!generation || generation!=host.generation)return;
    // The bridge checks the generation; a stale click cannot answer a new dialog.
    if (ps3_msgdialog_host_respond(generation, result) == 0) {
        host.answered_generation = generation;
        DestroyWindow(host.window);
        host.window = nullptr;
        host.generation = 0;
    }
}

LRESULT CALLBACK button_proc(HWND window, UINT message, WPARAM wparam, LPARAM lparam)
{
    if (message == WM_SETFOCUS && focusable_child(window))
        host.focused_control = GetDlgCtrlID(window);
    if (message == WM_KEYDOWN && wparam == VK_TAB) {
        HWND next=GetNextDlgTabItem(host.window,window,GetKeyState(VK_SHIFT)<0);
        if(next)SetFocus(next);
        return 0;
    }
    if (message == WM_KEYDOWN && wparam == VK_ESCAPE) {
        if (!(host.type & 0x80)) respond(GetParent(window),3);
        return 0;
    }
    if (message == WM_KEYDOWN && wparam == VK_RETURN) {
        const int id = GetDlgCtrlID(window);
        if (id == kYesId || id == kOkId) respond(GetParent(window),1);
        else if (id == kNoId) respond(GetParent(window),2);
        else if (id == kCancelId && !(host.type & 0x80)) respond(GetParent(window),3);
        return 0;
    }
    const WNDPROC previous=reinterpret_cast<WNDPROC>(GetWindowLongPtrW(window,GWLP_USERDATA));
    return CallWindowProcW(previous, window, message, wparam, lparam);
}

LRESULT CALLBACK dialog_proc(HWND window, UINT message, WPARAM wparam, LPARAM lparam)
{
    switch (message) {
    case WM_SETFOCUS:
        restore_child_focus(window);
        return 0;
    case WM_COMMAND:
        if (HIWORD(wparam) != BN_CLICKED) break;
        if(reinterpret_cast<HWND>(lparam)!=GetDlgItem(window,LOWORD(wparam)) ||
           !IsWindowVisible(reinterpret_cast<HWND>(lparam)))return 0;
        switch (LOWORD(wparam)) {
        case kYesId: case kOkId: respond(window,1); return 0;
        case kNoId: respond(window,2); return 0;
        case kCancelId: if (!(host.type & 0x80)) respond(window,3); return 0;
        }
        break;
    case WM_KEYDOWN:
        if (wparam == VK_ESCAPE && !(host.type & 0x80)) { respond(window,3); return 0; }
        break;
    case WM_CLOSE:
        if (!(host.type & 0x80)) respond(window,3);
        return 0;
    case WM_PAINT: {
        PAINTSTRUCT paint{};
        HDC dc = BeginPaint(window, &paint);
        const int bars = std::min(2u, (host.type >> 12) & 3u);
        for (int i = 0; i < bars; ++i) {
            RECT box{24, 183 + i * 37, kWidth - 24, 197 + i * 37};
            HBRUSH background = CreateSolidBrush(RGB(215, 215, 215));
            HBRUSH foreground = CreateSolidBrush(RGB(40, 113, 198));
            if (!background || !foreground) host.failing = true;
            if (background) FillRect(dc, &box, background);
            RECT fill = box;
            fill.right = fill.left + (fill.right - fill.left) *
                         std::min(100u, host.state.progress[i].value) / 100;
            if (foreground) FillRect(dc, &fill, foreground);
            if (background) DeleteObject(background);
            if (foreground) DeleteObject(foreground);
        }
        EndPaint(window, &paint);
        return 0;
    }
    case WM_DESTROY:
        if (host.window == window) {
            host.window = nullptr;
            host.message = nullptr;
            host.yes = host.no = host.ok = host.cancel = nullptr;
            host.focused_control = 0;
            host.progress_labels[0] = host.progress_labels[1] = nullptr;
        }
        return 0;
    }
    return DefWindowProcW(window, message, wparam, lparam);
}

HWND control(const wchar_t* cls, const wchar_t* text, DWORD style,
             int x, int y, int width, int height, int id)
{
    HWND child = CreateWindowExW(0, cls, text, WS_CHILD | WS_VISIBLE | style,
                                 x, y, width, height, host.window,
                                 reinterpret_cast<HMENU>(static_cast<INT_PTR>(id)),
                                 host.instance, nullptr);
    if (!child) return nullptr;
    SendMessageW(child, WM_SETFONT, reinterpret_cast<WPARAM>(GetStockObject(DEFAULT_GUI_FONT)), TRUE);
    return child;
}

bool create_dialog(u64 generation)
{
    host.focused_control = 0;
    host.window = CreateWindowExW(WS_EX_DLGMODALFRAME, kClassName, kTitle,
                                  WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU,
                                  CW_USEDEFAULT, CW_USEDEFAULT, kWidth, kHeight,
                                  nullptr, nullptr, host.instance, nullptr);
    if (!host.window) return false;
    SetLastError(0);
    SetWindowLongPtrW(host.window,GWLP_USERDATA,static_cast<LONG_PTR>(generation));
    if(GetLastError()!=0) { DestroyWindow(host.window);return false; }
    host.message = control(L"EDIT", L"", ES_MULTILINE|ES_READONLY|ES_AUTOVSCROLL|WS_VSCROLL|WS_TABSTOP,
                           24, 20, kWidth - 58, 140, kMessageId);
    for (int i = 0; i < 2; ++i)
        host.progress_labels[i] = control(L"STATIC", L"", SS_LEFT|SS_NOPREFIX, 24, 165 + i * 37,
                                          kWidth - 58, 17, kProgressLabel0 + i);
    host.yes = control(L"BUTTON", L"Yes", BS_PUSHBUTTON|WS_TABSTOP, 164, 264, 78, 27, kYesId);
    host.no = control(L"BUTTON", L"No", BS_PUSHBUTTON|WS_TABSTOP, 251, 264, 78, 27, kNoId);
    host.ok = control(L"BUTTON", L"OK", BS_PUSHBUTTON|WS_TABSTOP, 251, 264, 78, 27, kOkId);
    host.cancel = control(L"BUTTON", L"Cancel", BS_PUSHBUTTON|WS_TABSTOP, 338, 264, 78, 27, kCancelId);
    if (!host.message || !host.progress_labels[0] || !host.progress_labels[1] ||
        !host.yes || !host.no || !host.ok || !host.cancel) {
        DestroyWindow(host.window);
        host.window = nullptr;
        return false;
    }
    for (HWND button : {host.message,host.yes, host.no, host.ok, host.cancel}) {
        const LONG_PTR previous=GetWindowLongPtrW(button,GWLP_WNDPROC);
        SetLastError(0);
        SetWindowLongPtrW(button,GWLP_USERDATA,previous);
        if(!previous || GetLastError()!=0 ||
           !SetWindowLongPtrW(button,GWLP_WNDPROC,reinterpret_cast<LONG_PTR>(button_proc))) {
            DestroyWindow(host.window);return false;
        }
    }
    return true;
}

void update_dialog(const Ps3MsgDialogHostState& snapshot,bool created)
{
    const bool changed_text=created || std::strcmp(host.state.message,snapshot.message)!=0;
    host.state = snapshot;
    host.type = snapshot.type;
    const auto text = utf8(snapshot.message);
    std::wstring message;
    for(size_t i=0;i<text.size();++i) {
        if(text[i]==L'\n' && (i==0 || text[i-1]!=L'\r'))message+=L'\r';
        message+=text[i];
    }
    if(changed_text)SetWindowTextW(host.message, message.c_str());
    const unsigned bars = std::min(2u, (snapshot.type >> 12) & 3u);
    for (unsigned i = 0; i < 2; ++i) {
        show_control(host.progress_labels[i], i < bars);
        if (i < bars) {
            const auto label = utf8(snapshot.progress[i].message);
            SetWindowTextW(host.progress_labels[i], label.c_str());
        }
    }
    const unsigned button_type = (snapshot.type >> 4) & 7u;
    show_control(host.yes, button_type == 1);
    show_control(host.no, button_type == 1);
    show_control(host.ok, button_type == 2);
    const bool cancellable = !(snapshot.type & 0x80);
    show_control(host.cancel, cancellable);
    if (HMENU menu = GetSystemMenu(host.window, FALSE)) {
        EnableMenuItem(menu, SC_CLOSE, MF_BYCOMMAND | (cancellable ? MF_ENABLED : MF_GRAYED));
    }
    InvalidateRect(host.window, nullptr, TRUE);
}
} // namespace

bool folklore_dialog_host_start()
{
    if (host.started) return GetCurrentThreadId() == host.thread && !host.failing;
    host.thread = GetCurrentThreadId();
    host.instance = GetModuleHandleW(nullptr);
    WNDCLASSEXW cls{};
    cls.cbSize = sizeof(cls);
    cls.lpfnWndProc = dialog_proc;
    cls.hInstance = host.instance;
    cls.lpszClassName = kClassName;
    cls.hCursor = LoadCursorW(nullptr, MAKEINTRESOURCEW(32512));
    cls.hbrBackground = reinterpret_cast<HBRUSH>(COLOR_WINDOW + 1);
    if (!RegisterClassExW(&cls)) return false;
    host.registered = true;
    if (ps3_msgdialog_host_enable(1) != 0) {
        UnregisterClassW(kClassName, host.instance);
        host.registered = false;
        return false;
    }
    host.started = true;
    return true;
}

bool folklore_dialog_host_tick()
{
    if (!host.started || host.failing || GetCurrentThreadId() != host.thread) return false;
    Ps3MsgDialogHostState snapshot{};
    if (!ps3_msgdialog_host_snapshot(&snapshot)) return false;
    if (!snapshot.open) {
        if (host.window) DestroyWindow(host.window);
        host.generation = 0;
        host.answered_generation = 0;
        return !host.failing;
    }
    if (snapshot.generation == host.answered_generation) return !host.failing;
    if(host.window && host.generation!=snapshot.generation)DestroyWindow(host.window);
    const bool created=!host.window;
    if (created) {
        host.generation=snapshot.generation;
        if(!create_dialog(snapshot.generation))return false;
    }
    if (created || std::memcmp(&host.state, &snapshot, sizeof(snapshot)) != 0)
        update_dialog(snapshot,created);
    if(created) {
        ShowWindow(host.window,SW_SHOWNORMAL);
        UpdateWindow(host.window);
        restore_child_focus(host.window);
    }
    return !host.failing;
}

void folklore_dialog_host_stop()
{
    if (!host.started || GetCurrentThreadId() != host.thread) return;
    if (host.window) DestroyWindow(host.window);
    ps3_msgdialog_host_shutdown();
    if (host.registered) UnregisterClassW(kClassName, host.instance);
    host = Host{};
}

void* folklore_dialog_host_window()
{
    return GetCurrentThreadId() == host.thread ? host.window : nullptr;
}

bool folklore_dialog_host_displayed(Ps3MsgDialogHostState* output)
{
    if(!output || GetCurrentThreadId()!=host.thread || !host.window ||
       !host.state.open || !host.generation || host.state.generation!=host.generation ||
       static_cast<u64>(GetWindowLongPtrW(host.window,GWLP_USERDATA))!=host.generation)return false;
    *output=host.state;return true;
}

#else
#include "msgdialog_host_win32.h"
bool folklore_dialog_host_start() { return false; }
bool folklore_dialog_host_tick() { return false; }
void folklore_dialog_host_stop() {}
#endif
