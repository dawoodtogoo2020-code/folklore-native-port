#pragma once

// Call all three functions on the graphics/UI thread. tick is nonblocking.
bool folklore_dialog_host_start();
bool folklore_dialog_host_tick();
void folklore_dialog_host_stop();
#ifdef _WIN32
// Opaque HWND for a bounded UI test on the owning thread.
void* folklore_dialog_host_window();
struct Ps3MsgDialogHostState;
// Copied state actually displayed by this UI thread, independent of later
// guest bridge transitions. Returns false once this window is gone.
bool folklore_dialog_host_displayed(Ps3MsgDialogHostState* output);
#endif
