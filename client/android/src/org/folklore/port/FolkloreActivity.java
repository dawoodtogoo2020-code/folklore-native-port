package org.folklore.port;

import android.util.Log;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.hardware.input.InputManager;
import android.view.InputDevice;
import android.view.KeyEvent;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Toast;
import java.io.File;
import org.libsdl.app.SDLActivity;
import org.libsdl.app.SDLControllerManager;

/**
 * Native Folklore host. SDL owns the surface, native thread, lifecycle and input;
 * this subclass selects libraries/arguments and supplies human touch controls.
 */
public class FolkloreActivity extends SDLActivity {
    private static final String TAG = "Folklore";
    private static final int TOUCH_ID=-2000000047;
    private FolkloreTouchView touch;
    private InputManager inputManager;
    private boolean resumed, registered, listening, touchFailed, touchReady;
    private static native boolean nativeTouchReady();
    private final Handler handler=new Handler(Looper.getMainLooper());
    private final Runnable readyCheck=new Runnable() {public void run() {refreshTouch();}};
    private static final int[] KEYS={KeyEvent.KEYCODE_BUTTON_A,KeyEvent.KEYCODE_BUTTON_B,
        KeyEvent.KEYCODE_BUTTON_X,KeyEvent.KEYCODE_BUTTON_Y,KeyEvent.KEYCODE_BUTTON_SELECT,0,
        KeyEvent.KEYCODE_BUTTON_START,KeyEvent.KEYCODE_BUTTON_THUMBL,KeyEvent.KEYCODE_BUTTON_THUMBR,
        KeyEvent.KEYCODE_BUTTON_L1,KeyEvent.KEYCODE_BUTTON_R1,KeyEvent.KEYCODE_DPAD_UP,
        KeyEvent.KEYCODE_DPAD_DOWN,KeyEvent.KEYCODE_DPAD_LEFT,KeyEvent.KEYCODE_DPAD_RIGHT};
    private final InputManager.InputDeviceListener devices=new InputManager.InputDeviceListener() {
        public void onInputDeviceAdded(int id) {refreshTouch();}
        public void onInputDeviceRemoved(int id) {refreshTouch();}
        public void onInputDeviceChanged(int id) {refreshTouch();}
    };
    @Override protected void onCreate(Bundle state) {
        super.onCreate(state);
        if(mBrokenLibraries||mLayout==null)return;
        inputManager=(InputManager)getSystemService(INPUT_SERVICE);
        touch=new FolkloreTouchView(this,new FolkloreTouchState.Sink() {
            public void button(int button,boolean down) {
                if(!registered||!touchReady||KEYS[button]==0)return;
                try {
                    if(down)SDLControllerManager.onNativePadDown(TOUCH_ID,KEYS[button]);
                    else SDLControllerManager.onNativePadUp(TOUCH_ID,KEYS[button]);
                } catch(RuntimeException|UnsatisfiedLinkError e) {failTouch(e);}
            }
            public void axis(int axis,float value) {
                if(!registered||!touchReady)return;
                try {SDLControllerManager.onNativeJoy(TOUCH_ID,axis,value);}
                catch(RuntimeException|UnsatisfiedLinkError e) {failTouch(e);}
            }
        });
        touch.setVisibility(View.INVISIBLE);
        mLayout.addView(touch,new ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,ViewGroup.LayoutParams.MATCH_PARENT));
    }
    private boolean hasPhysicalController() {
        for(int id:InputDevice.getDeviceIds()) {
            InputDevice d=InputDevice.getDevice(id);
            if(d==null||d.isVirtual())continue;
            int sources=d.getSources();
            if((sources&InputDevice.SOURCE_GAMEPAD)==InputDevice.SOURCE_GAMEPAD ||
               (sources&InputDevice.SOURCE_JOYSTICK)==InputDevice.SOURCE_JOYSTICK)return true;
        }
        return false;
    }
    private void failTouch(Throwable error) {
        if(touchFailed)return;
        touchFailed=true;Log.e(TAG,"Touch input unavailable",error);
        handler.removeCallbacks(readyCheck);
        boolean remove=registered;registered=false;touchReady=false;
        if(touch!=null) {touch.neutralize();touch.setVisibility(View.INVISIBLE);}
        if(remove)try {SDLControllerManager.nativeRemoveJoystick(TOUCH_ID);}
            catch(RuntimeException|UnsatisfiedLinkError ignored) {Log.e(TAG,"Touch device cleanup failed",ignored);}
        Toast.makeText(this,"Touch controls unavailable. Connect a controller.",Toast.LENGTH_LONG).show();
    }
    private void deactivateTouch() {
        handler.removeCallbacks(readyCheck);
        if(touch!=null) {touch.neutralize();touch.setVisibility(View.INVISIBLE);}
        touchReady=false;
        if(registered) {
            registered=false;
            try {SDLControllerManager.nativeRemoveJoystick(TOUCH_ID);}
            catch(RuntimeException|UnsatisfiedLinkError e) {failTouch(e);}
        }
    }
    private void refreshTouch() {
        handler.removeCallbacks(readyCheck);
        if(touch==null||mBrokenLibraries)return;
        try {
            if(!resumed||!hasWindowFocus()||touchFailed||hasPhysicalController()) {deactivateTouch();return;}
            if(!registered) {
                int result=SDLControllerManager.nativeAddJoystick(TOUCH_ID,"Folklore touch controls",
                    "Folklore touch controls",0,0,false,0x7fff&~(1<<5),6,0x3f,0,0);
                if(result<0)throw new IllegalStateException("SDL rejected touch controller");
                registered=true;
            }
            if(!touchReady) {
                if(!nativeTouchReady()) {handler.postDelayed(readyCheck,100);return;}
                touchReady=true;touch.neutralize();
            }
            if(!touchFailed)touch.setVisibility(View.VISIBLE);
        } catch(RuntimeException|UnsatisfiedLinkError e) {failTouch(e);}
    }
    @Override protected void onResume() {
        super.onResume();resumed=true;
        if(touch==null||mBrokenLibraries)return;
        if(inputManager==null) {failTouch(new IllegalStateException("Input manager unavailable"));return;}
        inputManager.registerInputDeviceListener(devices,null);listening=true;refreshTouch();
    }
    @Override protected void onPause() {
        resumed=false;deactivateTouch();
        if(listening) {inputManager.unregisterInputDeviceListener(devices);listening=false;}
        super.onPause();
    }
    @Override public void onWindowFocusChanged(boolean focused) {
        super.onWindowFocusChanged(focused);
        if(focused)refreshTouch();else deactivateTouch();
    }

    @Override
    protected void onDestroy() {
        resumed=false;deactivateTouch();
        if(listening) {inputManager.unregisterInputDeviceListener(devices);listening=false;}
        // Let SDL finish its native-thread and device cleanup first. The native
        // runtime has process-global state and supports one session per process.
        // The manifest isolates this Activity in :game, leaving the launcher alive.
        super.onDestroy();
        if (isFinishing() && !isChangingConfigurations()) {
            android.os.Process.killProcess(android.os.Process.myPid());
        }
    }

    @Override
    protected String[] getLibraries() {
        return new String[] {
            "SDL2",
            "avutil",
            "avcodec",
            "FolkloreBoot"
        };
    }

    /**
     * Called by SDL on the native thread. Derives the same canonical paths the
     * launcher validated; on any failure returns no arguments so native usage
     * validation exits instead of running with substituted paths.
     */
    @Override
    protected String[] getArguments() {
        try {
            File base = getExternalFilesDir(null);
            if (base == null) {
                Log.e(TAG, "Startup failed: app-specific external storage is unavailable; not starting.");
                return new String[0];
            }
            return new FolklorePaths(base, getFilesDir()).buildArguments();
        } catch (Exception e) {
            Log.e(TAG, "Startup failed: task root paths are invalid; not starting.", e);
            return new String[0];
        }
    }
}
