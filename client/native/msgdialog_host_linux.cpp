// Linux GTK3 presenter for the guest cellMsgDialog host bridge.
//
// GTK is resolved at runtime with dlopen/dlsym so the build does not need GTK
// development headers. Once loaded the libraries stay resident for the life of
// the process: GTypes, closures and global hooks registered by GTK can outlive
// any window we create, so they are never dlclosed.

#if !defined(_WIN32)

#include "msgdialog_host_linux.h"

#include "cellMsgDialog.h"

#include <dlfcn.h>

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <string>
#include <thread>

namespace {

// Public GLib/GTK ABI types. Widgets are opaque; every GTK3 widget method takes
// a plain pointer, so a single opaque type is ABI-equivalent for all of them.
using gboolean = int;
using gint = int;
using guint = unsigned int;
using gulong = unsigned long;
using gdouble = double;
using gpointer = void*;
using GCallback = void (*)();
struct GClosure;
using GClosureNotify = void (*)(gpointer data, GClosure* closure);
struct GtkWidget;
struct GdkEvent;

constexpr gboolean kFalse = 0;
constexpr gboolean kTrue = 1;

constexpr gint kResponseDeleteEvent = -4;
constexpr gint kResponseOk = -5;
constexpr gint kResponseCancel = -6;
constexpr gint kResponseClose = -7;
constexpr gint kResponseYes = -8;
constexpr gint kResponseNo = -9;

constexpr std::uint32_t kResultYesOk = 1;
constexpr std::uint32_t kResultNo = 2;
constexpr std::uint32_t kResultCancel = 3;

constexpr std::uint32_t kTypeDisableCancel = 0x80u;
constexpr std::uint32_t kTypeDefaultNo = 0x100u;

constexpr const char* kDialogTitle = "Folklore system dialog";
constexpr gint kDialogWidth = 560;
constexpr gint kDialogHeight = 340;
constexpr int kMaxIterationsPerTick = 64;

constexpr const char* kGtkLibrary = "libgtk-3.so.0";
constexpr const char* kGObjectLibrary = "libgobject-2.0.so.0";

using Generation = decltype(Ps3MsgDialogHostState::generation);

struct GtkApi {
    gboolean (*init_check)(int*, char***) = nullptr;
    gboolean (*events_pending)() = nullptr;
    gboolean (*main_iteration_do)(gboolean) = nullptr;
    GtkWidget* (*dialog_new)() = nullptr;
    GtkWidget* (*dialog_get_content_area)(GtkWidget*) = nullptr;
    GtkWidget* (*dialog_add_button)(GtkWidget*, const char*, gint) = nullptr;
    void (*dialog_set_default_response)(GtkWidget*, gint) = nullptr;
    void (*window_set_title)(GtkWidget*, const char*) = nullptr;
    void (*window_set_default_size)(GtkWidget*, gint, gint) = nullptr;
    void (*window_set_deletable)(GtkWidget*, gboolean) = nullptr;
    void (*window_present)(GtkWidget*) = nullptr;
    GtkWidget* (*label_new)(const char*) = nullptr;
    void (*label_set_text)(GtkWidget*, const char*) = nullptr;
    void (*label_set_line_wrap)(GtkWidget*, gboolean) = nullptr;
    void (*label_set_max_width_chars)(GtkWidget*, gint) = nullptr;
    GtkWidget* (*progress_bar_new)() = nullptr;
    void (*progress_bar_set_fraction)(GtkWidget*, gdouble) = nullptr;
    void (*progress_bar_set_text)(GtkWidget*, const char*) = nullptr;
    void (*progress_bar_set_show_text)(GtkWidget*, gboolean) = nullptr;
    void (*box_pack_start)(GtkWidget*, GtkWidget*, gboolean, gboolean, guint) = nullptr;
    void (*box_set_spacing)(GtkWidget*, gint) = nullptr;
    void (*container_set_border_width)(GtkWidget*, guint) = nullptr;
    void (*widget_show_all)(GtkWidget*) = nullptr;
    void (*widget_destroy)(GtkWidget*) = nullptr;
    void (*widget_grab_focus)(GtkWidget*) = nullptr;
    gulong (*signal_connect_data)(gpointer, const char*, GCallback, gpointer, GClosureNotify, int) = nullptr;
};

enum class LoadState { NotTried, Loaded, Failed };

struct Runtime {
    LoadState load = LoadState::NotTried;
    std::string load_error;
    void* gtk_lib = nullptr;
    void* gobject_lib = nullptr;
    GtkApi api;
    bool gtk_initialized = false;
    bool gtk_init_failed = false;
};

// Shared between a window's signal closures and the presenter. Freed when the
// last of them lets go, so a handler running while its own window is destroyed
// never touches freed memory.
struct Binding {
    Generation generation = 0;
    std::uint32_t type = 0;
    GtkWidget* window = nullptr;
    bool live = true;
    bool answered = false;
    int refs = 1;
};

struct Ui {
    GtkWidget* window = nullptr;
    GtkWidget* message_label = nullptr;
    GtkWidget* progress_label[2] = {nullptr, nullptr};
    GtkWidget* progress_bar[2] = {nullptr, nullptr};
    Binding* binding = nullptr;
    Ps3MsgDialogHostState rendered{};
    bool has_rendered = false;
    bool skip_valid = false;
    Generation skip_generation = 0;
};

Runtime g_rt;
Ui g_ui;

std::mutex g_lifecycle_mutex;
std::mutex g_owner_mutex;
bool g_owner_set = false;
std::thread::id g_owner_id;

std::atomic<bool> g_running{false};
std::atomic<bool> g_bridge_enabled{false};
std::atomic<bool> g_destroy_pending{false};
std::atomic<bool> g_warned_wrong_thread{false};

void log_message(const std::string& text) {
    std::fprintf(stderr, "[folklore msgdialog host] %s\n", text.c_str());
}

bool on_owner_thread() {
    std::lock_guard<std::mutex> lock(g_owner_mutex);
    return g_owner_set && g_owner_id == std::this_thread::get_id();
}

bool owner_conflicts() {
    std::lock_guard<std::mutex> lock(g_owner_mutex);
    return g_owner_set && g_owner_id != std::this_thread::get_id();
}

bool claim_owner() {
    std::lock_guard<std::mutex> lock(g_owner_mutex);
    if (g_owner_set) {
        return g_owner_id == std::this_thread::get_id();
    }
    g_owner_set = true;
    g_owner_id = std::this_thread::get_id();
    return true;
}

bool owner_is_set() {
    std::lock_guard<std::mutex> lock(g_owner_mutex);
    return g_owner_set;
}

void warn_wrong_thread(const char* operation) {
    if (!g_warned_wrong_thread.exchange(true)) {
        log_message(std::string(operation) + " rejected: called from a thread other than the GTK owner thread");
    }
}

std::uint32_t buttons_type(std::uint32_t type) { return (type >> 4) & 7u; }
std::uint32_t progress_count(std::uint32_t type) { return (type >> 12) & 3u; }
bool cancel_allowed(std::uint32_t type) { return (type & kTypeDisableCancel) == 0; }

bool type_supported(std::uint32_t type) {
    return buttons_type(type) <= 2 && progress_count(type) <= 2;
}

bool result_allowed(std::uint32_t type, std::uint32_t result) {
    switch (result) {
    case kResultYesOk:
        return buttons_type(type) == 1 || buttons_type(type) == 2;
    case kResultNo:
        return buttons_type(type) == 1;
    case kResultCancel:
        return cancel_allowed(type);
    default:
        return false;
    }
}

std::uint32_t result_for_response(gint response) {
    switch (response) {
    case kResponseYes:
    case kResponseOk:
        return kResultYesOk;
    case kResponseNo:
        return kResultNo;
    case kResponseCancel:
    case kResponseDeleteEvent:
    case kResponseClose:
        return kResultCancel;
    default:
        return 0;
    }
}

// GTK requires valid UTF-8; invalid sequences become U+FFFD instead of being
// dropped or passed through.
std::string sanitize_utf8(const char* src, std::size_t capacity) {
    static const char kReplacement[] = "\xEF\xBF\xBD";
    std::size_t len = 0;
    while (len < capacity && src[len] != '\0') {
        ++len;
    }
    std::string out;
    out.reserve(len);
    std::size_t i = 0;
    while (i < len) {
        const unsigned char lead = static_cast<unsigned char>(src[i]);
        if (lead < 0x80) {
            out.push_back(static_cast<char>(lead));
            ++i;
            continue;
        }
        std::size_t extra = 0;
        std::uint32_t cp = 0;
        std::uint32_t minimum = 0;
        if ((lead & 0xE0) == 0xC0) {
            extra = 1; cp = lead & 0x1Fu; minimum = 0x80;
        } else if ((lead & 0xF0) == 0xE0) {
            extra = 2; cp = lead & 0x0Fu; minimum = 0x800;
        } else if ((lead & 0xF8) == 0xF0) {
            extra = 3; cp = lead & 0x07u; minimum = 0x10000;
        } else {
            out += kReplacement;
            ++i;
            continue;
        }
        bool ok = i + extra < len;
        for (std::size_t k = 1; ok && k <= extra; ++k) {
            const unsigned char cont = static_cast<unsigned char>(src[i + k]);
            if ((cont & 0xC0) != 0x80) {
                ok = false;
            } else {
                cp = (cp << 6) | (cont & 0x3Fu);
            }
        }
        if (ok && (cp < minimum || cp > 0x10FFFF || (cp >= 0xD800 && cp <= 0xDFFF))) {
            ok = false;
        }
        if (ok) {
            out.append(src + i, extra + 1);
            i += extra + 1;
        } else {
            out += kReplacement;
            ++i;
        }
    }
    return out;
}

void normalize_state(Ps3MsgDialogHostState& state) {
    state.message[sizeof(state.message) - 1] = '\0';
    for (auto& bar : state.progress) {
        bar.message[sizeof(bar.message) - 1] = '\0';
        if (bar.value > 100) {
            bar.value = 100;
        }
    }
}

template <typename F>
bool resolve(void* lib, const char* lib_name, const char* name, F& out, std::string& error) {
    dlerror();
    void* sym = dlsym(lib, name);
    if (!sym) {
        error = std::string("missing symbol ") + name + " in " + lib_name;
        if (const char* detail = dlerror()) {
            error += std::string(" (") + detail + ")";
        }
        return false;
    }
    out = reinterpret_cast<F>(sym);
    return true;
}

bool load_gtk(std::string& error) {
    if (g_rt.load == LoadState::Loaded) {
        return true;
    }
    if (g_rt.load == LoadState::Failed) {
        error = g_rt.load_error;
        return false;
    }

    g_rt.gtk_lib = dlopen(kGtkLibrary, RTLD_NOW | RTLD_GLOBAL);
    if (!g_rt.gtk_lib) {
        const char* detail = dlerror();
        error = std::string("cannot load ") + kGtkLibrary + (detail ? std::string(": ") + detail : std::string());
        g_rt.load = LoadState::Failed;
        g_rt.load_error = error;
        return false;
    }
    g_rt.gobject_lib = dlopen(kGObjectLibrary, RTLD_NOW | RTLD_GLOBAL);
    if (!g_rt.gobject_lib) {
        const char* detail = dlerror();
        error = std::string("cannot load ") + kGObjectLibrary + (detail ? std::string(": ") + detail : std::string());
        g_rt.load = LoadState::Failed;
        g_rt.load_error = error;
        return false;
    }

    void* gtk = g_rt.gtk_lib;
    void* gobj = g_rt.gobject_lib;
    GtkApi& a = g_rt.api;
    const bool ok =
        resolve(gtk, kGtkLibrary, "gtk_init_check", a.init_check, error) &&
        resolve(gtk, kGtkLibrary, "gtk_events_pending", a.events_pending, error) &&
        resolve(gtk, kGtkLibrary, "gtk_main_iteration_do", a.main_iteration_do, error) &&
        resolve(gtk, kGtkLibrary, "gtk_dialog_new", a.dialog_new, error) &&
        resolve(gtk, kGtkLibrary, "gtk_dialog_get_content_area", a.dialog_get_content_area, error) &&
        resolve(gtk, kGtkLibrary, "gtk_dialog_add_button", a.dialog_add_button, error) &&
        resolve(gtk, kGtkLibrary, "gtk_dialog_set_default_response", a.dialog_set_default_response, error) &&
        resolve(gtk, kGtkLibrary, "gtk_window_set_title", a.window_set_title, error) &&
        resolve(gtk, kGtkLibrary, "gtk_window_set_default_size", a.window_set_default_size, error) &&
        resolve(gtk, kGtkLibrary, "gtk_window_set_deletable", a.window_set_deletable, error) &&
        resolve(gtk, kGtkLibrary, "gtk_window_present", a.window_present, error) &&
        resolve(gtk, kGtkLibrary, "gtk_label_new", a.label_new, error) &&
        resolve(gtk, kGtkLibrary, "gtk_label_set_text", a.label_set_text, error) &&
        resolve(gtk, kGtkLibrary, "gtk_label_set_line_wrap", a.label_set_line_wrap, error) &&
        resolve(gtk, kGtkLibrary, "gtk_label_set_max_width_chars", a.label_set_max_width_chars, error) &&
        resolve(gtk, kGtkLibrary, "gtk_progress_bar_new", a.progress_bar_new, error) &&
        resolve(gtk, kGtkLibrary, "gtk_progress_bar_set_fraction", a.progress_bar_set_fraction, error) &&
        resolve(gtk, kGtkLibrary, "gtk_progress_bar_set_text", a.progress_bar_set_text, error) &&
        resolve(gtk, kGtkLibrary, "gtk_progress_bar_set_show_text", a.progress_bar_set_show_text, error) &&
        resolve(gtk, kGtkLibrary, "gtk_box_pack_start", a.box_pack_start, error) &&
        resolve(gtk, kGtkLibrary, "gtk_box_set_spacing", a.box_set_spacing, error) &&
        resolve(gtk, kGtkLibrary, "gtk_container_set_border_width", a.container_set_border_width, error) &&
        resolve(gtk, kGtkLibrary, "gtk_widget_show_all", a.widget_show_all, error) &&
        resolve(gtk, kGtkLibrary, "gtk_widget_destroy", a.widget_destroy, error) &&
        resolve(gtk, kGtkLibrary, "gtk_widget_grab_focus", a.widget_grab_focus, error) &&
        resolve(gobj, kGObjectLibrary, "g_signal_connect_data", a.signal_connect_data, error);
    if (!ok) {
        // Libraries stay mapped: GTK is not unload-safe once its constructors ran.
        g_rt.load = LoadState::Failed;
        g_rt.load_error = error;
        return false;
    }
    g_rt.load = LoadState::Loaded;
    return true;
}

bool env_nonempty(const char* name) {
    const char* value = std::getenv(name);
    return value && value[0] != '\0';
}

void retain_binding(Binding* binding) { ++binding->refs; }

void release_binding(Binding* binding) {
    if (--binding->refs == 0) {
        delete binding;
    }
}

void binding_closure_notify(gpointer data, GClosure*) {
    release_binding(static_cast<Binding*>(data));
}

void clear_ui() {
    g_ui.window = nullptr;
    g_ui.message_label = nullptr;
    g_ui.progress_label[0] = g_ui.progress_label[1] = nullptr;
    g_ui.progress_bar[0] = g_ui.progress_bar[1] = nullptr;
    g_ui.binding = nullptr;
    g_ui.rendered = Ps3MsgDialogHostState{};
    g_ui.has_rendered = false;
}

// Host-owned destruction: never submits a response.
void close_window() {
    GtkWidget* window = g_ui.window;
    Binding* binding = g_ui.binding;
    clear_ui();
    if (binding) {
        binding->live = false;
        binding->window = nullptr;
        release_binding(binding);
    }
    if (window) {
        g_rt.api.widget_destroy(window);
    }
}

void submit_result(Binding* binding, GtkWidget* widget, std::uint32_t result) {
    if (!binding->live || binding->answered || binding->window != widget) {
        return;
    }
    if (g_ui.binding != binding || g_ui.window != widget) {
        return;
    }
    if (!g_running.load() || !g_bridge_enabled.load()) {
        return;
    }
    if (result == 0 || !result_allowed(binding->type, result)) {
        return;
    }
    const int status = ps3_msgdialog_host_respond(binding->generation, result);
    if (status == 0) {
        // The bridge closed this generation; the next snapshot refresh destroys
        // the window.
        binding->answered = true;
    } else {
        log_message("bridge rejected response " + std::to_string(result) + " for generation " +
                    std::to_string(static_cast<unsigned long long>(binding->generation)) +
                    " (status " + std::to_string(status) + ")");
    }
}

void on_response(GtkWidget* widget, gint response, gpointer data) {
    Binding* binding = static_cast<Binding*>(data);
    if (!binding) {
        return;
    }
    retain_binding(binding);
    submit_result(binding, widget, result_for_response(response));
    release_binding(binding);
}

// Window-manager close and Escape arrive here. Returning TRUE keeps GTK from
// destroying the dialog; the close maps to Cancel only when the guest allows it.
gboolean on_delete_event(GtkWidget* widget, GdkEvent*, gpointer data) {
    Binding* binding = static_cast<Binding*>(data);
    if (binding) {
        retain_binding(binding);
        submit_result(binding, widget, kResultCancel);
        release_binding(binding);
    }
    return kTrue;
}

// Destruction not initiated by the presenter drops our references without
// answering the guest.
void on_destroy(GtkWidget* widget, gpointer data) {
    Binding* binding = static_cast<Binding*>(data);
    if (!binding || !binding->live || binding->window != widget || g_ui.binding != binding) {
        return;
    }
    retain_binding(binding);
    clear_ui();
    binding->live = false;
    binding->window = nullptr;
    release_binding(binding);
    release_binding(binding);
}

void apply_progress_value(GtkWidget* bar, std::uint32_t value) {
    g_rt.api.progress_bar_set_fraction(bar, static_cast<gdouble>(value) / 100.0);
    const std::string text = std::to_string(value) + "%";
    g_rt.api.progress_bar_set_text(bar, text.c_str());
}

bool build_window(const Ps3MsgDialogHostState& state) {
    const GtkApi& api = g_rt.api;
    const std::uint32_t type = state.type;

    GtkWidget* window = api.dialog_new();
    if (!window) {
        log_message("gtk_dialog_new failed");
        return false;
    }
    api.window_set_title(window, kDialogTitle);
    api.window_set_default_size(window, kDialogWidth, kDialogHeight);
    api.window_set_deletable(window, cancel_allowed(type) ? kTrue : kFalse);

    GtkWidget* content = api.dialog_get_content_area(window);
    api.container_set_border_width(content, 12);
    api.box_set_spacing(content, 10);

    GtkWidget* message = api.label_new(nullptr);
    api.label_set_text(message, sanitize_utf8(state.message, sizeof(state.message)).c_str());
    api.label_set_line_wrap(message, kTrue);
    api.label_set_max_width_chars(message, 60);
    api.box_pack_start(content, message, kTrue, kTrue, 0);

    GtkWidget* progress_labels[2] = {nullptr, nullptr};
    GtkWidget* progress_bars[2] = {nullptr, nullptr};
    const std::uint32_t bars = progress_count(type);
    for (std::uint32_t i = 0; i < bars; ++i) {
        const auto& source = state.progress[i];
        progress_labels[i] = api.label_new(nullptr);
        api.label_set_text(progress_labels[i], sanitize_utf8(source.message, sizeof(source.message)).c_str());
        api.label_set_line_wrap(progress_labels[i], kTrue);
        api.box_pack_start(content, progress_labels[i], kFalse, kFalse, 0);
        progress_bars[i] = api.progress_bar_new();
        api.progress_bar_set_show_text(progress_bars[i], kTrue);
        apply_progress_value(progress_bars[i], source.value);
        api.box_pack_start(content, progress_bars[i], kFalse, kFalse, 0);
    }

    GtkWidget* default_button = nullptr;
    gint default_response = 0;
    if (buttons_type(type) == 1) {
        GtkWidget* yes = api.dialog_add_button(window, "_Yes", kResponseYes);
        GtkWidget* no = api.dialog_add_button(window, "_No", kResponseNo);
        const bool prefer_no = (type & kTypeDefaultNo) != 0;
        default_button = prefer_no ? no : yes;
        default_response = prefer_no ? kResponseNo : kResponseYes;
    } else if (buttons_type(type) == 2) {
        default_button = api.dialog_add_button(window, "_OK", kResponseOk);
        default_response = kResponseOk;
    }
    if (cancel_allowed(type)) {
        api.dialog_add_button(window, "_Cancel", kResponseCancel);
    }
    if (default_response != 0) {
        api.dialog_set_default_response(window, default_response);
    }

    Binding* binding = new Binding;
    binding->generation = state.generation;
    binding->type = type;
    binding->window = window;

    auto connect = [&](const char* signal, GCallback callback) {
        retain_binding(binding);
        const gulong id = api.signal_connect_data(window, signal, callback, binding, &binding_closure_notify, 0);
        if (id == 0) {
            release_binding(binding);
            log_message(std::string("g_signal_connect_data failed for ") + signal);
            return false;
        }
        return true;
    };
    const bool connected =
        connect("response", reinterpret_cast<GCallback>(&on_response)) &&
        connect("delete-event", reinterpret_cast<GCallback>(&on_delete_event)) &&
        connect("destroy", reinterpret_cast<GCallback>(&on_destroy));
    if (!connected) {
        binding->live = false;
        binding->window = nullptr;
        api.widget_destroy(window);
        release_binding(binding);
        return false;
    }

    g_ui.window = window;
    g_ui.message_label = message;
    g_ui.progress_label[0] = progress_labels[0];
    g_ui.progress_label[1] = progress_labels[1];
    g_ui.progress_bar[0] = progress_bars[0];
    g_ui.progress_bar[1] = progress_bars[1];
    g_ui.binding = binding;
    g_ui.rendered = state;
    g_ui.has_rendered = true;

    api.widget_show_all(window);
    api.window_present(window);
    if (default_button) {
        api.widget_grab_focus(default_button);
    }
    return true;
}

void update_window(const Ps3MsgDialogHostState& state) {
    const GtkApi& api = g_rt.api;
    if (std::strcmp(g_ui.rendered.message, state.message) != 0) {
        api.label_set_text(g_ui.message_label, sanitize_utf8(state.message, sizeof(state.message)).c_str());
    }
    const std::uint32_t bars = progress_count(state.type);
    for (std::uint32_t i = 0; i < bars; ++i) {
        const auto& next = state.progress[i];
        const auto& prev = g_ui.rendered.progress[i];
        if (g_ui.progress_label[i] && std::strcmp(prev.message, next.message) != 0) {
            api.label_set_text(g_ui.progress_label[i], sanitize_utf8(next.message, sizeof(next.message)).c_str());
        }
        if (g_ui.progress_bar[i] && prev.value != next.value) {
            apply_progress_value(g_ui.progress_bar[i], next.value);
        }
    }
    g_ui.rendered = state;
}

bool refresh_from_bridge() {
    Ps3MsgDialogHostState state{};
    if (!g_bridge_enabled.load() || ps3_msgdialog_host_snapshot(&state) != 1) {
        log_message("dialog bridge snapshot unavailable");
        close_window();
        return false;
    }
    if (state.open == 0) {
        close_window();
        return true;
    }
    normalize_state(state);

    if (g_ui.window && g_ui.binding && g_ui.rendered.generation == state.generation &&
        g_ui.rendered.type == state.type) {
        update_window(state);
        return true;
    }

    close_window();
    if (g_ui.skip_valid && g_ui.skip_generation == state.generation) {
        return false;
    }
    if (!type_supported(state.type)) {
        log_message("refusing to present unsupported dialog type 0x" +
                    [&] { char buf[16]; std::snprintf(buf, sizeof(buf), "%x", static_cast<unsigned>(state.type)); return std::string(buf); }() +
                    " for generation " + std::to_string(static_cast<unsigned long long>(state.generation)));
        g_ui.skip_valid = true;
        g_ui.skip_generation = state.generation;
        return false;
    }
    if (!build_window(state)) {
        g_ui.skip_valid = true;
        g_ui.skip_generation = state.generation;
        return false;
    }
    return true;
}

} // namespace

bool folklore_dialog_host_start() {
    std::lock_guard<std::mutex> lifecycle(g_lifecycle_mutex);

    if (owner_conflicts()) {
        log_message("start rejected: GTK is owned by another thread");
        return false;
    }
    if (owner_is_set() && g_destroy_pending.exchange(false)) {
        close_window();
    }
    if (g_running.load()) {
        return true;
    }
    if (g_bridge_enabled.load()) {
        log_message("start rejected: previous stop could not detach the dialog bridge");
        return false;
    }
    if (g_rt.gtk_init_failed) {
        log_message("start rejected: gtk_init_check failed earlier; GTK is unusable in this process");
        return false;
    }

    std::string error;
    if (!load_gtk(error)) {
        log_message("GTK unavailable: " + error);
        return false;
    }

    if (!g_rt.gtk_initialized) {
        if (!env_nonempty("GDK_BACKEND") && !env_nonempty("DISPLAY") && !env_nonempty("WAYLAND_DISPLAY")) {
            log_message("no display: DISPLAY and WAYLAND_DISPLAY are unset or empty");
            return false;
        }
        if (!claim_owner()) {
            log_message("start rejected: GTK is owned by another thread");
            return false;
        }
        if (!g_rt.api.init_check(nullptr, nullptr)) {
            g_rt.gtk_init_failed = true;
            log_message("gtk_init_check failed: cannot open display");
            return false;
        }
        g_rt.gtk_initialized = true;
    }

    const int status = ps3_msgdialog_host_enable(1);
    if (status != 0) {
        log_message("ps3_msgdialog_host_enable failed (status " + std::to_string(status) + ")");
        return false;
    }
    g_ui.skip_valid = false;
    g_bridge_enabled.store(true);
    g_running.store(true);
    g_warned_wrong_thread.store(false);
    return true;
}

bool folklore_dialog_host_tick() {
    if (!on_owner_thread()) {
        warn_wrong_thread("tick");
        return false;
    }
    if (g_destroy_pending.exchange(false)) {
        close_window();
    }
    if (!g_running.load()) {
        return false;
    }

    if (!refresh_from_bridge()) return false;
    for (int i = 0; i < kMaxIterationsPerTick && g_rt.api.events_pending(); ++i) {
        g_rt.api.main_iteration_do(kFalse);
    }
    if (g_destroy_pending.exchange(false)) {
        close_window();
    }
    if (!g_running.load()) {
        close_window();
        return false;
    }
    return refresh_from_bridge();
}

void folklore_dialog_host_stop() {
    std::lock_guard<std::mutex> lifecycle(g_lifecycle_mutex);
    const bool owner = on_owner_thread();

    g_running.store(false);
    if (g_bridge_enabled.load()) {
        const int status = ps3_msgdialog_host_shutdown();
        if (status == 0) {
            g_bridge_enabled.store(false);
        } else {
            log_message("ps3_msgdialog_host_shutdown failed (status " + std::to_string(status) +
                        "); bridge may still be attached, stop is not clean");
        }
    }

    if (owner) {
        g_destroy_pending.store(false);
        close_window();
    } else if (owner_is_set()) {
        log_message("stop called off the GTK owner thread; window destruction deferred to owner");
        g_destroy_pending.store(true);
    }
}

void* folklore_dialog_host_window() {
    if (!on_owner_thread()) {
        return nullptr;
    }
    return g_ui.window;
}

bool folklore_dialog_host_displayed(Ps3MsgDialogHostState* out) {
    if (!on_owner_thread()) {
        return false;
    }
    if (!g_ui.window || !g_ui.has_rendered) {
        return false;
    }
    if (out) {
        *out = g_ui.rendered;
    }
    return true;
}

#endif
