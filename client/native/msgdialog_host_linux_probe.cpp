// Synthetic probe for the Linux GTK3 dialog presenter.
//
// Defines only the host bridge functions the presenter calls, backed by an
// in-process synthetic dialog state. No game code, no mock GTK.
//
//   --expect-no-display  CPU-only: blank display variables, assert clean refusal.
//   --ui                 Real GTK dialogs driven through real widget signals.

#if !defined(_WIN32)

#include "msgdialog_host_linux.h"

#include "cellMsgDialog.h"

#include <dlfcn.h>

#include <chrono>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

namespace {

using gboolean = int;
using gint = int;
using gpointer = void*;
struct GtkWidget;

constexpr gint kResponseDeleteEvent = -4;
constexpr gint kResponseOk = -5;
constexpr gint kResponseCancel = -6;
constexpr gint kResponseYes = -8;
constexpr gint kResponseNo = -9;

using Generation = decltype(Ps3MsgDialogHostState::generation);

struct RespondCall {
    Generation generation;
    std::uint32_t result;
    int status;
};

struct SyntheticBridge {
    std::mutex mutex;
    bool enabled = false;
    Ps3MsgDialogHostState state{};
    int enable_calls = 0;
    int shutdown_calls = 0;
    std::vector<RespondCall> responds;
};

SyntheticBridge g_bridge;

bool probe_result_allowed(std::uint32_t type, std::uint32_t result) {
    const std::uint32_t buttons = (type >> 4) & 7u;
    switch (result) {
    case 1: return buttons == 1 || buttons == 2;
    case 2: return buttons == 1;
    case 3: return (type & 0x80u) == 0;
    default: return false;
    }
}

int g_passed = 0;
int g_failed = 0;

void check(bool condition, const char* what) {
    if (condition) {
        ++g_passed;
    } else {
        ++g_failed;
        std::fprintf(stderr, "FAIL: %s\n", what);
    }
}

void copy_text(char* dst, std::size_t capacity, const char* src) {
    std::snprintf(dst, capacity, "%s", src);
}

void guest_open(Generation generation, std::uint32_t type, const char* message) {
    std::lock_guard<std::mutex> lock(g_bridge.mutex);
    g_bridge.state = Ps3MsgDialogHostState{};
    g_bridge.state.generation = generation;
    g_bridge.state.open = 1;
    g_bridge.state.type = type;
    copy_text(g_bridge.state.message, sizeof(g_bridge.state.message), message);
}

void guest_progress(int index, std::uint32_t value, const char* message) {
    std::lock_guard<std::mutex> lock(g_bridge.mutex);
    g_bridge.state.progress[index].value = value;
    copy_text(g_bridge.state.progress[index].message, sizeof(g_bridge.state.progress[index].message), message);
}

void guest_message(const char* message) {
    std::lock_guard<std::mutex> lock(g_bridge.mutex);
    copy_text(g_bridge.state.message, sizeof(g_bridge.state.message), message);
}

void guest_close() {
    std::lock_guard<std::mutex> lock(g_bridge.mutex);
    g_bridge.state.open = 0;
}

std::size_t respond_count() {
    std::lock_guard<std::mutex> lock(g_bridge.mutex);
    return g_bridge.responds.size();
}

RespondCall last_respond() {
    std::lock_guard<std::mutex> lock(g_bridge.mutex);
    return g_bridge.responds.empty() ? RespondCall{0, 0, -1} : g_bridge.responds.back();
}

int enable_calls() {
    std::lock_guard<std::mutex> lock(g_bridge.mutex);
    return g_bridge.enable_calls;
}

int shutdown_calls() {
    std::lock_guard<std::mutex> lock(g_bridge.mutex);
    return g_bridge.shutdown_calls;
}

bool bridge_enabled() {
    std::lock_guard<std::mutex> lock(g_bridge.mutex);
    return g_bridge.enabled;
}

bool last_is(Generation generation, std::uint32_t result) {
    const RespondCall call = last_respond();
    return call.generation == generation && call.result == result && call.status == 0;
}

struct ProbeGtk {
    void (*dialog_response)(GtkWidget*, gint) = nullptr;
    GtkWidget* (*dialog_get_widget_for_response)(GtkWidget*, gint) = nullptr;
    void (*button_clicked)(GtkWidget*) = nullptr;
    gboolean (*widget_has_default)(GtkWidget*) = nullptr;
    const char* (*window_get_title)(GtkWidget*) = nullptr;
    void (*window_close)(GtkWidget*) = nullptr;
    gboolean (*events_pending)() = nullptr;
    gboolean (*main_iteration_do)(gboolean) = nullptr;
    gpointer (*object_ref)(gpointer) = nullptr;
    void (*object_unref)(gpointer) = nullptr;

    template <typename F>
    static bool need(void* lib, const char* name, F& out) {
        void* sym = lib ? dlsym(lib, name) : nullptr;
        if (!sym) {
            std::fprintf(stderr, "probe: missing symbol %s\n", name);
            return false;
        }
        out = reinterpret_cast<F>(sym);
        return true;
    }

    // Handles are intentionally never closed; GTK stays resident.
    bool load() {
        void* gtk = dlopen("libgtk-3.so.0", RTLD_NOW | RTLD_GLOBAL);
        void* gobj = dlopen("libgobject-2.0.so.0", RTLD_NOW | RTLD_GLOBAL);
        return need(gtk, "gtk_dialog_response", dialog_response) &&
               need(gtk, "gtk_dialog_get_widget_for_response", dialog_get_widget_for_response) &&
               need(gtk, "gtk_button_clicked", button_clicked) &&
               need(gtk, "gtk_widget_has_default", widget_has_default) &&
               need(gtk, "gtk_window_get_title", window_get_title) &&
               need(gtk, "gtk_window_close", window_close) &&
               need(gtk, "gtk_events_pending", events_pending) &&
               need(gtk, "gtk_main_iteration_do", main_iteration_do) &&
               need(gobj, "g_object_ref", object_ref) &&
               need(gobj, "g_object_unref", object_unref);
    }
};

GtkWidget* host_window() {
    return static_cast<GtkWidget*>(folklore_dialog_host_window());
}

void pump(int ticks = 25) {
    for (int i = 0; i < ticks; ++i) {
        folklore_dialog_host_tick();
        std::this_thread::sleep_for(std::chrono::milliseconds(4));
    }
}

bool shown_generation(Generation generation, Ps3MsgDialogHostState* out = nullptr) {
    Ps3MsgDialogHostState state{};
    const bool shown = folklore_dialog_host_displayed(&state);
    if (out) {
        *out = state;
    }
    return shown && state.open != 0 && state.generation == generation;
}

bool nothing_shown() {
    Ps3MsgDialogHostState state{};
    return !folklore_dialog_host_displayed(&state) && host_window() == nullptr;
}

void run_no_display() {
    setenv("DISPLAY", "", 1);
    setenv("WAYLAND_DISPLAY", "", 1);
    unsetenv("GDK_BACKEND");

    check(!folklore_dialog_host_start(), "start fails without display");
    check(enable_calls() == 0 && !bridge_enabled(), "bridge not enabled without display");
    check(!folklore_dialog_host_tick(), "tick false without display");
    folklore_dialog_host_stop();
    check(shutdown_calls() == 0, "stop without start does not shut down bridge");
    check(nothing_shown(), "no window without display");
    check(!folklore_dialog_host_start(), "repeated start still fails");
    check(enable_calls() == 0 && !bridge_enabled(), "repeated start does not enable bridge");
    check(respond_count() == 0, "no responses without display");
}

void run_ui() {
    const bool started = folklore_dialog_host_start();
    check(started, "start succeeds with display");
    if (!started) {
        return;
    }
    check(enable_calls() == 1 && bridge_enabled(), "start enables bridge once");

    ProbeGtk gtk;
    if (!gtk.load()) {
        check(false, "probe GTK symbols resolved");
        folklore_dialog_host_stop();
        return;
    }

    {
        bool other_start = true;
        bool other_tick = true;
        bool other_displayed = true;
        std::thread other([&] {
            other_start = folklore_dialog_host_start();
            other_tick = folklore_dialog_host_tick();
            Ps3MsgDialogHostState state{};
            other_displayed = folklore_dialog_host_displayed(&state);
        });
        other.join();
        check(!other_start && !other_tick && !other_displayed, "wrong-thread start/tick/displayed rejected");
        check(enable_calls() == 1, "wrong-thread start does not enable bridge");
    }

    // Yes/No with Unicode text; default Yes; Cancel permitted.
    {
        const char* message =
            "Save your progress before leaving? Ünïcödé 日本語 ✓ "
            "This message is intentionally long so that it must wrap across several lines in the dialog.";
        guest_open(1, 0x10, message);
        pump();
        Ps3MsgDialogHostState shown{};
        check(shown_generation(1, &shown) && std::strcmp(shown.message, message) == 0, "yes/no text displayed");
        GtkWidget* window = host_window();
        check(window != nullptr, "yes/no window exists");
        if (window) {
            const char* title = gtk.window_get_title(window);
            check(title && std::strcmp(title, "Folklore system dialog") == 0, "window title");
            GtkWidget* yes = gtk.dialog_get_widget_for_response(window, kResponseYes);
            GtkWidget* no = gtk.dialog_get_widget_for_response(window, kResponseNo);
            GtkWidget* cancel = gtk.dialog_get_widget_for_response(window, kResponseCancel);
            check(yes && no && cancel, "yes/no/cancel buttons present");
            check(yes && no && gtk.widget_has_default(yes) && !gtk.widget_has_default(no), "default Yes");
            const std::size_t before = respond_count();
            if (yes) {
                gtk.button_clicked(yes);
            }
            check(respond_count() == before + 1 && last_is(1, 1), "Yes maps to result 1");
            pump();
            check(nothing_shown(), "answered dialog closed");
        }
    }

    // Default No, cancel disabled.
    {
        guest_open(2, 0x10 | 0x100 | 0x80, "Overwrite existing data?");
        pump();
        GtkWidget* window = host_window();
        check(window && shown_generation(2), "default-no dialog shown");
        if (window) {
            GtkWidget* yes = gtk.dialog_get_widget_for_response(window, kResponseYes);
            GtkWidget* no = gtk.dialog_get_widget_for_response(window, kResponseNo);
            check(gtk.dialog_get_widget_for_response(window, kResponseCancel) == nullptr, "cancel absent when disabled");
            check(yes && no && gtk.widget_has_default(no) && !gtk.widget_has_default(yes), "default No");
            const std::size_t before = respond_count();
            if (no) {
                gtk.button_clicked(no);
            }
            check(respond_count() == before + 1 && last_is(2, 2), "No maps to result 2");
            pump();
            check(nothing_shown(), "default-no dialog closed");
        }
    }

    // Generation replacement; old widget signals must not answer.
    {
        guest_open(3, 0x20, "First notice");
        pump();
        GtkWidget* old_window = host_window();
        GtkWidget* old_ok = old_window ? gtk.dialog_get_widget_for_response(old_window, kResponseOk) : nullptr;
        check(old_window && old_ok && shown_generation(3), "generation 3 shown");
        if (old_window && old_ok) {
            gtk.object_ref(old_window);
            gtk.object_ref(old_ok);
            guest_open(4, 0x20, "Second notice");
            pump();
            GtkWidget* window = host_window();
            Ps3MsgDialogHostState shown{};
            check(window && window != old_window, "replacement constructs new window");
            check(shown_generation(4, &shown) && std::strcmp(shown.message, "Second notice") == 0, "generation 4 shown");
            const std::size_t before = respond_count();
            gtk.button_clicked(old_ok);
            gtk.dialog_response(old_window, kResponseOk);
            gtk.dialog_response(old_window, kResponseCancel);
            pump();
            check(respond_count() == before, "stale widget responses rejected");
            check(shown_generation(4), "generation 4 still shown after stale signals");
            gtk.object_unref(old_ok);
            gtk.object_unref(old_window);
            GtkWidget* ok = window ? gtk.dialog_get_widget_for_response(window, kResponseOk) : nullptr;
            if (ok) {
                gtk.button_clicked(ok);
            }
            check(respond_count() == before + 1 && last_is(4, 1), "OK answers current generation");
            pump();
            check(nothing_shown(), "replacement dialog closed");
        }
    }

    // Cancel disabled: window close / delete / cancel responses ignored.
    {
        guest_open(5, 0x10 | 0x80, "This choice cannot be cancelled.");
        pump();
        GtkWidget* window = host_window();
        check(window && shown_generation(5), "cancel-disabled dialog shown");
        if (window) {
            const std::size_t before = respond_count();
            gtk.window_close(window);
            pump();
            gtk.dialog_response(window, kResponseDeleteEvent);
            gtk.dialog_response(window, kResponseCancel);
            pump();
            check(respond_count() == before, "close ignored when cancel disabled");
            check(host_window() == window && shown_generation(5), "dialog survives close when cancel disabled");
            GtkWidget* yes = gtk.dialog_get_widget_for_response(window, kResponseYes);
            if (yes) {
                gtk.button_clicked(yes);
            }
            check(respond_count() == before + 1 && last_is(5, 1), "cancel-disabled dialog answered");
            pump();
            check(nothing_shown(), "cancel-disabled dialog closed");
        }
    }

    // Cancel allowed: window close maps to result 3.
    {
        guest_open(6, 0x20, "Closing this window cancels.");
        pump();
        GtkWidget* window = host_window();
        check(window && shown_generation(6), "cancellable dialog shown");
        if (window) {
            check(gtk.dialog_get_widget_for_response(window, kResponseCancel) != nullptr, "cancel button present");
            const std::size_t before = respond_count();
            gtk.window_close(window);
            pump();
            check(respond_count() == before + 1 && last_is(6, 3), "window close maps to cancel");
            check(nothing_shown(), "cancelled dialog closed");
        }
    }

    // Cancel button.
    {
        guest_open(7, 0x10, "Cancel via button.");
        pump();
        GtkWidget* window = host_window();
        GtkWidget* cancel = window ? gtk.dialog_get_widget_for_response(window, kResponseCancel) : nullptr;
        check(cancel != nullptr, "cancel button available");
        if (cancel) {
            const std::size_t before = respond_count();
            gtk.button_clicked(cancel);
            check(respond_count() == before + 1 && last_is(7, 3), "cancel button maps to result 3");
            pump();
            check(nothing_shown(), "cancel-button dialog closed");
        }
    }

    // Two progress bars, no buttons, update and guest close.
    {
        guest_open(8, 0x2000 | 0x80, "Installing");
        guest_progress(0, 10, "Copying");
        guest_progress(1, 20, "Verifying");
        pump();
        Ps3MsgDialogHostState shown{};
        check(shown_generation(8, &shown) && shown.progress[0].value == 10 && shown.progress[1].value == 20 &&
                  std::strcmp(shown.progress[0].message, "Copying") == 0 &&
                  std::strcmp(shown.progress[1].message, "Verifying") == 0,
              "progress displayed");
        GtkWidget* window = host_window();
        check(window && !gtk.dialog_get_widget_for_response(window, kResponseOk) &&
                  !gtk.dialog_get_widget_for_response(window, kResponseYes) &&
                  !gtk.dialog_get_widget_for_response(window, kResponseCancel),
              "progress dialog has no buttons");
        guest_message("Installing – almost done");
        guest_progress(0, 55, "Copying 2/3");
        guest_progress(1, 100, "Verified");
        pump();
        check(shown_generation(8, &shown) && shown.progress[0].value == 55 && shown.progress[1].value == 100 &&
                  std::strcmp(shown.progress[0].message, "Copying 2/3") == 0 &&
                  std::strcmp(shown.message, "Installing – almost done") == 0,
              "progress update displayed");
        check(host_window() == window, "progress update reuses window");
        const std::size_t before = respond_count();
        guest_close();
        pump();
        check(nothing_shown(), "guest close destroys window");
        check(respond_count() == before, "guest close submits no response");
    }

    // Stop with a live dialog, then restart on the same owner.
    {
        guest_open(9, 0x20, "Interrupted by stop.");
        pump();
        check(shown_generation(9), "dialog shown before stop");
        const std::size_t before = respond_count();
        folklore_dialog_host_stop();
        check(shutdown_calls() == 1 && !bridge_enabled(), "stop shuts down bridge");
        check(nothing_shown(), "stop destroys window");
        check(!folklore_dialog_host_tick(), "tick false after stop");
        check(respond_count() == before, "stop submits no response");

        check(folklore_dialog_host_start(), "restart succeeds");
        check(enable_calls() == 2 && bridge_enabled(), "restart re-enables bridge");
        guest_open(10, 0x20, "After restart.");
        pump();
        GtkWidget* window = host_window();
        check(window && shown_generation(10), "dialog shown after restart");
        GtkWidget* ok = window ? gtk.dialog_get_widget_for_response(window, kResponseOk) : nullptr;
        if (ok) {
            gtk.button_clicked(ok);
        }
        check(respond_count() == before + 1 && last_is(10, 1), "response after restart");
        pump();
        check(nothing_shown(), "restart dialog closed");
    }

    folklore_dialog_host_stop();
    check(shutdown_calls() == 2 && nothing_shown(), "final stop clean");
    for (int i = 0; i < 64 && gtk.events_pending(); ++i) {
        gtk.main_iteration_do(0);
    }
}

} // namespace

int ps3_msgdialog_host_enable(int enable) {
    std::lock_guard<std::mutex> lock(g_bridge.mutex);
    ++g_bridge.enable_calls;
    g_bridge.enabled = enable != 0;
    return 0;
}

int ps3_msgdialog_host_snapshot(Ps3MsgDialogHostState* out) {
    std::lock_guard<std::mutex> lock(g_bridge.mutex);
    if (!g_bridge.enabled || !out) {
        return 0;
    }
    *out = g_bridge.state;
    return 1;
}

int ps3_msgdialog_host_respond(Generation generation, std::int32_t result) {
    std::lock_guard<std::mutex> lock(g_bridge.mutex);
    int status = -1;
    if (g_bridge.enabled && g_bridge.state.open != 0 && g_bridge.state.generation == generation &&
        probe_result_allowed(g_bridge.state.type, result)) {
        g_bridge.state.open = 0;
        status = 0;
    }
    g_bridge.responds.push_back(RespondCall{generation, static_cast<std::uint32_t>(result), status});
    return status;
}

int ps3_msgdialog_host_shutdown() {
    std::lock_guard<std::mutex> lock(g_bridge.mutex);
    ++g_bridge.shutdown_calls;
    g_bridge.enabled = false;
    g_bridge.state.open = 0;
    return 0;
}

int main(int argc, char** argv) {
    const std::string mode = argc > 1 ? argv[1] : "";
    if (mode == "--expect-no-display") {
        run_no_display();
    } else if (mode == "--ui") {
        run_ui();
    } else {
        std::fprintf(stderr, "usage: %s --expect-no-display | --ui\n", argc > 0 ? argv[0] : "msgdialog_host_linux_probe");
        return 2;
    }
    std::printf("msgdialog_host_linux_probe %s: %d passed, %d failed\n", mode.c_str(), g_passed, g_failed);
    return g_failed == 0 ? 0 : 1;
}

#endif
