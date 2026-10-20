#ifndef FOLKLORE_MSGDIALOG_HOST_ANDROID_H
#define FOLKLORE_MSGDIALOG_HOST_ANDROID_H
struct Ps3MsgDialogHostState;
// Owner-thread operations except stop. Requires the packaged Java presenter.
// The presenter registers its Activity destruction hook itself and never calls
// JNI callbacks. Real user responses are polled on the owner thread.
bool folklore_dialog_host_start();
bool folklore_dialog_host_tick();
void folklore_dialog_host_stop();
// Android AlertDialog has no compatible native host-window pointer.
void* folklore_dialog_host_window();
// True only for the latest snapshot actually acknowledged by visible Android UI.
bool folklore_dialog_host_displayed(Ps3MsgDialogHostState* out);
#endif
