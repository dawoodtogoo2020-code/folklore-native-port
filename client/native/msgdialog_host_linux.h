// Linux GTK3 presenter for the guest cellMsgDialog host bridge.
//
// Same public surface as the Win32 host. All functions except stop must be
// called from the single owner thread that successfully started the host;
// calls from any other thread are rejected without touching GTK.

#ifndef FOLKLORE_MSGDIALOG_HOST_LINUX_H
#define FOLKLORE_MSGDIALOG_HOST_LINUX_H

struct Ps3MsgDialogHostState;

// Loads and initializes GTK3, then enables the guest bridge. Returns false (and
// leaves the bridge disabled) when GTK, a required symbol or a display is
// unavailable, or when called from a thread other than the owner.
bool folklore_dialog_host_start();

// Reconciles the presented window with the bridge snapshot and processes at
// most a bounded number of pending GTK events. Never blocks. Returns true while
// the host is running on the owner thread.
bool folklore_dialog_host_tick();

// Detaches the bridge, then destroys any host-owned window without submitting
// a response. From a non-owner thread only the bridge is detached and window
// destruction is deferred to the owner thread.
void folklore_dialog_host_stop();

// GtkWidget* of the current dialog window, or nullptr.
void* folklore_dialog_host_window();

// Copies the state currently rendered in the live window. Returns false when no
// window exists.
bool folklore_dialog_host_displayed(Ps3MsgDialogHostState* out);

#endif
