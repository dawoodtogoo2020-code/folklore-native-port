#include "msgdialog_host_android.h"
#include "cellMsgDialog.h"
#include <SDL_system.h>
#include <jni.h>
#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <limits>
#include <mutex>
#include <string>
#include <thread>

namespace {
// Serialization is safe across JNI calls here: Java never calls native code,
// never waits for the owner, and posts UI work without waiting for it to run.
// This also keeps a concurrent stop from deleting an in-use global reference.
std::mutex gate;
std::thread::id owner;
jobject presenter=nullptr;
jmethodID submit_method=nullptr, poll_method=nullptr, stop_method=nullptr, acknowledged_method=nullptr;
bool running=false, have_state=false;
jlong revision=0, acknowledged=0;
Ps3MsgDialogHostState submitted{};

bool exception(JNIEnv* env, const char* operation) {
    if (!env->ExceptionCheck()) return false;
    std::fprintf(stderr,"[android dialog] JNI failure in %s\n",operation);
    env->ExceptionDescribe();
    env->ExceptionClear();
    return true;
}

struct Frame {
    JNIEnv* env=static_cast<JNIEnv*>(SDL_AndroidGetJNIEnv());
    bool ready=false;
    Frame() {
        if (env) {
            ready=env->PushLocalFrame(32)==0;
            if (exception(env,"PushLocalFrame")) ready=false;
        }
    }
    ~Frame() { if (ready) env->PopLocalFrame(nullptr); }
};

// Android NewStringUTF consumes modified UTF-8, not the game's UTF-8.
// Decode bounded arrays explicitly, replacing malformed scalar sequences.
std::u16string utf16(const char* input, size_t bound) {
    std::u16string out;
    size_t end=0;
    while (end<bound && input[end]) ++end;
    for (size_t i=0; i<end;) {
        const unsigned char first=static_cast<unsigned char>(input[i]);
        unsigned scalar=0; size_t length=0; unsigned minimum=0;
        if (first<0x80) { scalar=first; length=1; }
        else if (first>=0xc2 && first<=0xdf) { scalar=first&31; length=2; minimum=0x80; }
        else if (first>=0xe0 && first<=0xef) { scalar=first&15; length=3; minimum=0x800; }
        else if (first>=0xf0 && first<=0xf4) { scalar=first&7; length=4; minimum=0x10000; }
        bool valid=length && i+length<=end;
        for (size_t j=1; valid && j<length; ++j) {
            unsigned next=static_cast<unsigned char>(input[i+j]);
            if ((next&0xc0)!=0x80) valid=false;
            else scalar=(scalar<<6)|(next&63);
        }
        valid=valid && scalar>=minimum && scalar<=0x10ffff && !(scalar>=0xd800 && scalar<=0xdfff);
        if (!valid) { out.push_back(u'\ufffd'); ++i; continue; }
        i+=length;
        if (scalar<=0xffff) out.push_back(static_cast<char16_t>(scalar));
        else {
            scalar-=0x10000;
            out.push_back(static_cast<char16_t>(0xd800+(scalar>>10)));
            out.push_back(static_cast<char16_t>(0xdc00+(scalar&1023)));
        }
    }
    return out;
}

template<size_t N> jstring string(JNIEnv* env, const char (&bytes)[N]) {
    auto text=utf16(bytes,N);
    return env->NewString(reinterpret_cast<const jchar*>(text.data()),static_cast<jsize>(text.size()));
}

void stop_locked(JNIEnv* env) {
    if (running && ps3_msgdialog_host_shutdown()!=0) {
        std::fprintf(stderr,"[android dialog] guest bridge shutdown failed\n");
        std::fflush(nullptr); std::_Exit(89);
    }
    running=false; have_state=false; acknowledged=0;
    if (presenter) {
        if (!env) {
            std::fprintf(stderr,"[android dialog] no JNI environment for cleanup\n");
            std::fflush(nullptr); std::_Exit(89);
        }
        env->CallVoidMethod(presenter,stop_method);
        bool failed=exception(env,"stop");
        env->DeleteGlobalRef(presenter); presenter=nullptr;
        if (failed) { std::fflush(nullptr); std::_Exit(89); }
    }
    owner={};
}

bool fail_locked(JNIEnv* env, const char* message) {
    std::fprintf(stderr,"[android dialog] %s\n",message);
    stop_locked(env);
    return false;
}
}

bool folklore_dialog_host_start() {
    std::lock_guard<std::mutex> lock(gate);
    if (running) return owner==std::this_thread::get_id();
    Frame frame;
    if (!frame.ready) return false;
    JNIEnv* env=frame.env;
    jobject activity=static_cast<jobject>(SDL_AndroidGetActivity());
    if (exception(env,"SDL Activity") || !activity) return false;
    jclass activity_class=env->GetObjectClass(activity);
    if (exception(env,"Activity class") || !activity_class) return false;
    jmethodID loader_method=env->GetMethodID(activity_class,"getClassLoader","()Ljava/lang/ClassLoader;");
    if (exception(env,"getClassLoader method") || !loader_method) return false;
    jobject loader=env->CallObjectMethod(activity,loader_method);
    if (exception(env,"getClassLoader") || !loader) return false;
    jclass loader_class=env->GetObjectClass(loader);
    if (exception(env,"ClassLoader class") || !loader_class) return false;
    jmethodID load=env->GetMethodID(loader_class,"loadClass","(Ljava/lang/String;)Ljava/lang/Class;");
    if (exception(env,"loadClass method") || !load) return false;
    jstring name=env->NewStringUTF("org.folklore.port.FolkloreDialogPresenter");
    if (exception(env,"class name") || !name) return false;
    jclass cls=static_cast<jclass>(env->CallObjectMethod(loader,load,name));
    if (exception(env,"load presenter") || !cls) return false;
    jmethodID create=env->GetStaticMethodID(cls,"create","(Landroid/app/Activity;)Lorg/folklore/port/FolkloreDialogPresenter;");
    if (exception(env,"create method") || !create) return false;
    submit_method=env->GetMethodID(cls,"submit","(JJIILjava/lang/String;ILjava/lang/String;ILjava/lang/String;)V");
    if (exception(env,"submit method") || !submit_method) return false;
    poll_method=env->GetMethodID(cls,"poll","()[J");
    if (exception(env,"poll method") || !poll_method) return false;
    stop_method=env->GetMethodID(cls,"stop","()V");
    if (exception(env,"stop method") || !stop_method) return false;
    acknowledged_method=env->GetMethodID(cls,"acknowledgedRevision","()J");
    if (exception(env,"acknowledgement method") || !acknowledged_method) return false;
    jobject local=env->CallStaticObjectMethod(cls,create,activity);
    if (exception(env,"create") || !local) return false;
    presenter=env->NewGlobalRef(local);
    if (exception(env,"presenter reference") || !presenter) {
        env->CallVoidMethod(local,stop_method); exception(env,"failed start cleanup");
        if (presenter) { env->DeleteGlobalRef(presenter); presenter=nullptr; }
        return false;
    }
    if (ps3_msgdialog_host_enable(1)!=0) return fail_locked(env,"bridge enable failed");
    owner=std::this_thread::get_id(); running=true;
    have_state=false; revision=0; acknowledged=0;
    return true;
}

bool folklore_dialog_host_tick() {
    std::lock_guard<std::mutex> lock(gate);
    if (!running || owner!=std::this_thread::get_id()) return false;
    Frame frame;
    if (!frame.ready) return fail_locked(frame.env,"JNI environment unavailable");
    JNIEnv* env=frame.env;
    auto values=static_cast<jlongArray>(env->CallObjectMethod(presenter,poll_method));
    if (exception(env,"poll") || !values) return fail_locked(env,"poll failed");
    jsize count=env->GetArrayLength(values);
    if (exception(env,"poll length") || count!=4) return fail_locked(env,"invalid poll result");
    jlong result[4]{};
    env->GetLongArrayRegion(values,0,4,result);
    if (exception(env,"poll values") || result[1]) return fail_locked(env,"Android UI/lifecycle failure");
    acknowledged=result[0];
    if (result[3]) {
        // The guest bridge validates generation and permitted result under its
        // own lock. A guest-closed/replaced dialog makes this real input stale.
        Ps3MsgDialogHostState current{};
        if (ps3_msgdialog_host_snapshot(&current) && current.open &&
            current.generation==static_cast<u64>(result[2])) {
            if (result[3]<1 || result[3]>3) return fail_locked(env,"invalid Android button");
            const s32 status=ps3_msgdialog_host_respond(current.generation,static_cast<s32>(result[3]));
            if (status) {
                Ps3MsgDialogHostState after{};
                if (ps3_msgdialog_host_snapshot(&after) && after.open && after.generation==current.generation)
                    return fail_locked(env,"guest rejected live dialog input");
            }
        }
    }
    Ps3MsgDialogHostState next{};
    if (!ps3_msgdialog_host_snapshot(&next)) next={};
    if (have_state && std::memcmp(&next,&submitted,sizeof(next))==0) return true;
    if (((next.type>>4)&7)>2 || ((next.type>>12)&3)>2)
        return fail_locked(env,"unsupported dialog flags");
    if (revision==std::numeric_limits<jlong>::max()) return fail_locked(env,"revision exhausted");
    jstring text=string(env,next.message);
    if (exception(env,"message") || !text) return fail_locked(env,"message allocation");
    jstring first=string(env,next.progress[0].message);
    if (exception(env,"progress 0") || !first) return fail_locked(env,"label allocation");
    jstring second=string(env,next.progress[1].message);
    if (exception(env,"progress 1") || !second) return fail_locked(env,"label allocation");
    ++revision; acknowledged=0;
    env->CallVoidMethod(presenter,submit_method,revision,static_cast<jlong>(next.generation),
        static_cast<jint>(next.open),static_cast<jint>(next.type),text,
        static_cast<jint>(std::min(next.progress[0].value,100u)),first,
        static_cast<jint>(std::min(next.progress[1].value,100u)),second);
    if (exception(env,"submit")) return fail_locked(env,"submit failed");
    submitted=next; have_state=true;
    return true;
}

void folklore_dialog_host_stop() {
    std::lock_guard<std::mutex> lock(gate);
    if (!running && !presenter) return;
    Frame frame;
    stop_locked(frame.env);
}

void* folklore_dialog_host_window() { return nullptr; }

bool folklore_dialog_host_displayed(Ps3MsgDialogHostState* out) {
    std::lock_guard<std::mutex> lock(gate);
    if (!out || !running || owner!=std::this_thread::get_id() || !have_state ||
        !submitted.open || acknowledged!=revision) return false;
    Frame frame;
    if (!frame.ready) return fail_locked(frame.env,"JNI environment unavailable");
    const jlong current_ack=frame.env->CallLongMethod(presenter,acknowledged_method);
    if (exception(frame.env,"acknowledgement")) return fail_locked(frame.env,"acknowledgement failed");
    if (current_ack!=revision) return false;
    Ps3MsgDialogHostState current{};
    if (!ps3_msgdialog_host_snapshot(&current) || std::memcmp(&current,&submitted,sizeof(current))!=0) return false;
    *out=submitted;
    return true;
}
