#include <jni.h>
#include <SDL2/SDL.h>
#include <cstring>
#include <SDL2/SDL_system.h>
#include <android/log.h>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <vector>
#include <unistd.h>

// Android discards the inherited stdout/stderr descriptors. Keep each private
// test session in its own app-owned file so startup failures are observable.
bool folklore_android_start_log()
{
    const char* root=SDL_AndroidGetInternalStoragePath();
    if(!root)return false;
    std::string name=std::string(root)+"/native-session-XXXXXX";
    std::vector<char> path(name.begin(),name.end());path.push_back(0);
    int fd=mkstemp(path.data());
    if(fd<0)return false;
    bool ok=dup2(fd,STDOUT_FILENO)>=0 && dup2(fd,STDERR_FILENO)>=0;
    close(fd);
    if(!ok)return false;
    std::setvbuf(stdout,nullptr,_IONBF,0);
    std::setvbuf(stderr,nullptr,_IONBF,0);
    __android_log_print(ANDROID_LOG_INFO,"Folklore","Native session log: %s",path.data());
    std::fprintf(stderr,"[android] private native session log active\n");
    return true;
}

/* Readiness only. The game opens controllers through cellPad; Java must not
 * send Android joystick events before that, or SDL falls back to keyboard. */
extern "C" JNIEXPORT jboolean JNICALL
Java_org_folklore_port_FolkloreActivity_nativeTouchReady(JNIEnv*, jclass)
{
    if (!(SDL_WasInit(SDL_INIT_GAMECONTROLLER)&SDL_INIT_GAMECONTROLLER)) return JNI_FALSE;
    jboolean ready=JNI_FALSE;
    SDL_LockJoysticks();
    for(int i=0;i<SDL_NumJoysticks();++i) {
        SDL_JoystickID id=SDL_JoystickGetDeviceInstanceID(i);
        SDL_GameController* pad=SDL_GameControllerFromInstanceID(id);
        if(!pad||!SDL_GameControllerGetAttached(pad))continue;
        const char* name=SDL_GameControllerName(pad);
        if(name&&std::strcmp(name,"Folklore touch controls")==0) {ready=JNI_TRUE;break;}
    }
    SDL_UnlockJoysticks();
    return ready;
}
