#define SDL_MAIN_HANDLED
#include "../../native/android_touch_jni.cpp"
#include <cstdio>
static int checks;
#define CHECK(v) do {++checks;if(!(v)){std::fprintf(stderr,"FAIL line%d: %s\n",__LINE__,#v);return 1;}} while(0)
static bool ready() {return Java_org_folklore_port_FolkloreActivity_nativeTouchReady(nullptr,nullptr)==JNI_TRUE;}
int main()
{
    SDL_SetMainReady();CHECK(!ready());CHECK(SDL_Init(SDL_INIT_GAMECONTROLLER)==0);
    CHECK(SDL_NumJoysticks()==0);CHECK(!ready());
    SDL_VirtualJoystickDesc d={};d.version=SDL_VIRTUAL_JOYSTICK_DESC_VERSION;
    d.type=SDL_JOYSTICK_TYPE_GAMECONTROLLER;d.naxes=6;d.nbuttons=15;d.axis_mask=0x3f;d.button_mask=0x7fff;
    d.name="Other fixture";CHECK(SDL_JoystickAttachVirtualEx(&d)==0);
    SDL_GameController* other=SDL_GameControllerOpen(0);CHECK(other);CHECK(!ready());
    d.name="Folklore touch controls";CHECK(SDL_JoystickAttachVirtualEx(&d)==1);CHECK(!ready());
    SDL_GameController* touch=SDL_GameControllerOpen(1);CHECK(touch);CHECK(ready());
    SDL_GameControllerClose(touch);CHECK(!ready());touch=SDL_GameControllerOpen(1);CHECK(touch&&ready());
    CHECK(SDL_JoystickDetachVirtual(1)==0);CHECK(!ready());SDL_GameControllerClose(touch);
    SDL_GameControllerClose(other);CHECK(SDL_JoystickDetachVirtual(0)==0);SDL_Quit();CHECK(!ready());
    std::printf("Touch JNI readiness: %d checks passed\n",checks);return 0;
}
