package org.folklore.port;

import android.app.Activity;
import android.app.AlertDialog;
import android.app.Application;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;

/** Real Android UI, polled by the native owner. Never calls back into JNI.
 * Each instance is one native session. At most one UI task is queued.
 */
public final class FolkloreDialogPresenter implements Application.ActivityLifecycleCallbacks {
    private final Handler main = new Handler(Looper.getMainLooper());
    private final Application application;
    private Activity activity;
    private boolean stopped, failed, queued, registered;
    private State desired, shown;
    private long acknowledged, responseGeneration;
    private int response;
    private long answeredGeneration;
    // The following view fields are accessed on the Android UI thread only.
    private AlertDialog dialog;
    private TextView message;
    private final TextView[] labels = new TextView[2];
    private final ProgressBar[] bars = new ProgressBar[2];

    private static final class State {
        final long revision, generation;
        final int open, type, firstValue, secondValue;
        final String message, firstLabel, secondLabel;
        State(long r, long g, int o, int t, String m, int v0, String l0, int v1, String l1) {
            revision=r; generation=g; open=o; type=t; message=m;
            firstValue=v0; firstLabel=l0; secondValue=v1; secondLabel=l1;
        }
    }

    private FolkloreDialogPresenter(Activity a) {
        activity=a;
        application=a.getApplication();
        if (!main.post(() -> {
            synchronized (this) {
                if (stopped) return;
                if (!validActivity()) { fail(); return; }
                application.registerActivityLifecycleCallbacks(this);
                registered=true;
            }
        })) fail();
    }

    public static FolkloreDialogPresenter create(Activity a) {
        if (a == null || a.isFinishing() || a.isDestroyed()) return null;
        return new FolkloreDialogPresenter(a);
    }

    // Called by the native owner. Strings are already decoded from bounded UTF-8.
    public void submit(long revision, long generation, int open, int type, String text,
                       int value0, String label0, int value1, String label1) {
        synchronized (this) {
            if (stopped || failed) return;
            if (revision <= 0 || text == null || label0 == null || label1 == null ||
                ((type >>> 4) & 7) > 2 || ((type >>> 12) & 3) > 2) {
                fail(); return;
            }
            desired=new State(revision,generation,open,type,text,value0,label0,value1,label1);
            acknowledged=0;
            if (queued) return;
            queued=true;
        }
        if (!main.post(this::drain)) fail();
    }

    // [acknowledged revision, failure, response generation, actual button].
    // Consuming a response does not reopen that generation while the guest closes it.
    public synchronized long[] poll() {
        long[] result={acknowledged, failed ? 1 : 0, responseGeneration, response};
        response=0;
        responseGeneration=0;
        return result;
    }

    public synchronized long acknowledgedRevision() {
        return stopped || failed ? 0 : acknowledged;
    }

    private boolean validActivity() {
        return activity != null && !activity.isFinishing() && !activity.isDestroyed();
    }

    private void drain() {
        State state;
        synchronized (this) {
            if (stopped || failed) { queued=false; return; }
            if (!validActivity()) { fail(); queued=false; return; }
            state=desired;
        }
        try {
            render(state);
        } catch (RuntimeException e) {
            android.util.Log.e("FolkloreDialog", "Cannot present system dialog", e);
            fail();
        }
        synchronized (this) {
            if (stopped || failed) { queued=false; return; }
            if (desired != state) {
                if (!main.post(this::drain)) fail();
            } else queued=false;
        }
    }

    private void render(State state) {
        synchronized (this) {
            if (stopped || failed || desired != state) return;
            if (state.open == 0 || state.generation == answeredGeneration) {
                dismissView();
                shown=null;
                return;
            }
            boolean replace=shown == null || shown.generation != state.generation || shown.type != state.type;
            if (replace) {
                dismissView();
                Activity a=activity;
                LinearLayout layout=new LinearLayout(a);
                layout.setOrientation(LinearLayout.VERTICAL);
                int padding=(int)(24*a.getResources().getDisplayMetrics().density);
                layout.setPadding(padding,padding,padding,padding);
                message=new TextView(a);
                layout.addView(message);
                int count=(state.type >>> 12) & 3;
                for (int i=0; i<count; ++i) {
                    labels[i]=new TextView(a);
                    bars[i]=new ProgressBar(a,null,android.R.attr.progressBarStyleHorizontal);
                    bars[i].setMax(100);
                    layout.addView(labels[i]); layout.addView(bars[i]);
                }
                AlertDialog.Builder builder=new AlertDialog.Builder(a)
                    .setTitle("Folklore").setView(layout).setCancelable((state.type & 0x80)==0);
                int buttons=(state.type >>> 4) & 7;
                if (buttons != 0) builder.setPositiveButton(buttons==1 ? "Yes" : "OK",null);
                if (buttons == 1) builder.setNegativeButton("No",null);
                if ((state.type & 0x80)==0) builder.setNeutralButton("Cancel",null);
                dialog=builder.create();
                final AlertDialog view=dialog;
                view.setCanceledOnTouchOutside(false);
                view.setOnCancelListener(unused -> answer(view,3));
                view.setOnDismissListener(unused -> dismissed(view));
                view.show();
                // Custom click listeners avoid AlertDialog's automatic dismissal.
                if (buttons != 0) view.getButton(AlertDialog.BUTTON_POSITIVE)
                    .setOnClickListener(unused -> answer(view,1));
                if (buttons == 1) view.getButton(AlertDialog.BUTTON_NEGATIVE)
                    .setOnClickListener(unused -> answer(view,2));
                if ((state.type & 0x80)==0) view.getButton(AlertDialog.BUTTON_NEUTRAL)
                    .setOnClickListener(unused -> answer(view,3));
                if (buttons != 0) view.getButton(buttons==1 && (state.type & 0x100)!=0
                    ? AlertDialog.BUTTON_NEGATIVE : AlertDialog.BUTTON_POSITIVE).requestFocus();
            }
            message.setText(state.message);
            int count=(state.type >>> 12) & 3;
            if (count>0) { labels[0].setText(state.firstLabel); bars[0].setProgress(clamp(state.firstValue)); }
            if (count>1) { labels[1].setText(state.secondLabel); bars[1].setProgress(clamp(state.secondValue)); }
            if (!validActivity() || !dialog.isShowing()) { fail(); return; }
            shown=state;
            acknowledged=state.revision;
        }
    }

    private static int clamp(int value) { return Math.max(0,Math.min(100,value)); }

    private synchronized void answer(AlertDialog view, int button) {
        if (stopped || failed || dialog != view || shown == null || desired == null ||
            shown.generation != desired.generation || shown.type != desired.type ||
            answeredGeneration == shown.generation || response != 0) return;
        if (button==3 && (shown.type & 0x80)!=0) return;
        // Text/progress updates within a generation do not invalidate real input.
        // New generations and replaced views can never receive an old response.
        responseGeneration=shown.generation;
        response=button;
        answeredGeneration=shown.generation;
        acknowledged=0;
        dismissView();
        shown=null;
    }

    private void dismissView() {
        if (dialog != null) {
            dialog.setOnCancelListener(null);
            dialog.setOnDismissListener(null);
            dialog.dismiss();
            dialog=null;
        }
        message=null;
        for (int i=0;i<2;++i) { labels[i]=null; bars[i]=null; }
    }

    private synchronized void dismissed(AlertDialog view) {
        if (dialog != view || stopped || failed) return;
        acknowledged=0;
        if (shown != null && desired != null && desired.open != 0 &&
            shown.generation==desired.generation && answeredGeneration!=shown.generation) {
            // Unexpected window loss cannot leave the guest waiting invisibly.
            fail();
        } else {
            shown=null;
        }
    }

    private synchronized void fail() {
        failed=true;
        stop();
    }

    public void stop() {
        synchronized (this) {
            if (stopped) return;
            stopped=true;
            activity=null;
            desired=null;
            acknowledged=0;
            response=0;
            responseGeneration=0;
        }
        Runnable cleanup=() -> {
            synchronized (this) {
                dismissView(); shown=null;
                if (registered) { application.unregisterActivityLifecycleCallbacks(this); registered=false; }
            }
        };
        if (Looper.myLooper()==Looper.getMainLooper()) cleanup.run();
        else if (!main.post(cleanup)) {
            synchronized (this) { failed=true; }
            android.util.Log.e("FolkloreDialog", "UI cleanup could not be queued");
        }
    }

    @Override public synchronized void onActivityDestroyed(Activity a) { if (a==activity) fail(); }
    @Override public void onActivityCreated(Activity a, Bundle b) {}
    @Override public void onActivityStarted(Activity a) {}
    @Override public void onActivityResumed(Activity a) {}
    @Override public void onActivityPaused(Activity a) {}
    @Override public void onActivityStopped(Activity a) {}
    @Override public void onActivitySaveInstanceState(Activity a, Bundle b) {}
}
