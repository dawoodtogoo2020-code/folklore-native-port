package org.folklore.port;

import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.content.res.ColorStateList;
import android.graphics.Typeface;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;
import android.util.TypedValue;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import java.io.InterruptedIOException;
import java.lang.ref.WeakReference;

/**
 * Plain launcher that checks user-supplied files before the SDL activity is
 * ever started. Missing or wrong inputs never reach native code.
 */
public final class FolkloreLauncherActivity extends Activity {
    private static final String TAG = "FolkloreLauncher";
    private static final int ERROR_COLOR = 0xFFFF8A80;

    private final Handler mainHandler = new Handler(Looper.getMainLooper());

    private TextView pathView;
    private TextView statusView;
    private TextView missingView;
    private Button checkButton;
    private Button startButton;
    private ColorStateList defaultStatusColors;

    private FolklorePaths paths;
    private Thread checkThread;
    private int checkGeneration;
    private boolean checking;
    private boolean validated;
    private boolean launching;
    private boolean resumed;
    private boolean destroyed;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        buildUi();
        refreshPaths();
        setStatus("Tap Check files to verify your files.", false);
        updateButtons();
    }

    @Override
    protected void onRestart() {
        super.onRestart();
        if (launching) {
            launching = false;
            setStatus("Returned from the native test session. Tap Check files before starting again.", false);
        }
        updateButtons();
    }

    @Override
    protected void onResume() {
        super.onResume();
        resumed = true;
        updateButtons();
    }

    @Override
    protected void onPause() {
        resumed = false;
        updateButtons();
        super.onPause();
    }

    @Override
    protected void onStop() {
        super.onStop();
        if (checking) {
            cancelCheck();
            setStatus("Check stopped because the launcher left the foreground. Tap Check files again.", true);
        } else if (validated && !launching) {
            setStatus("Files may have changed while the launcher was hidden. Tap Check files again.", false);
        }
        validated = false;
        updateButtons();
    }

    @Override
    protected void onDestroy() {
        destroyed = true;
        cancelCheck();
        mainHandler.removeCallbacksAndMessages(null);
        super.onDestroy();
    }

    private void buildUi() {
        int pad = dp(16);
        ScrollView scroll = new ScrollView(this);
        scroll.setFitsSystemWindows(true);

        LinearLayout column = new LinearLayout(this);
        column.setOrientation(LinearLayout.VERTICAL);
        column.setPadding(pad, pad, pad, pad);
        scroll.addView(column, new ScrollView.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        TextView title = addText(column, "Folklore (test)");
        title.setTextSize(TypedValue.COMPLEX_UNIT_SP, 22);
        title.setTypeface(Typeface.DEFAULT_BOLD);

        addText(column, "Private test build. No game or firmware files are included. You need your own "
                + "BCUS98147 disc (version 1.00) game dump and your own PS3 firmware dev_flash, placed under:");

        pathView = addText(column, "");
        pathView.setTypeface(Typeface.MONOSPACE);
        pathView.setTextIsSelectable(true);

        addText(column, "Then tap Check files. Start is enabled after the check passes. "
                + "Touch controls appear when the game is ready, unless a controller is connected. Motion controls still require testing.");

        LinearLayout buttons = new LinearLayout(this);
        buttons.setOrientation(LinearLayout.HORIZONTAL);
        LinearLayout.LayoutParams rowParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        rowParams.topMargin = dp(12);
        column.addView(buttons, rowParams);

        checkButton = new Button(this);
        checkButton.setText("Check files");
        checkButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                onCheckClicked();
            }
        });
        buttons.addView(checkButton, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));

        startButton = new Button(this);
        startButton.setText("Start");
        startButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                onStartClicked();
            }
        });
        buttons.addView(startButton, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));

        statusView = addText(column, "");
        defaultStatusColors = statusView.getTextColors();

        missingView = addText(column, "");
        missingView.setVisibility(View.GONE);

        setContentView(scroll);
    }

    private TextView addText(LinearLayout parent, String text) {
        TextView view = new TextView(this);
        view.setText(text);
        view.setTextSize(TypedValue.COMPLEX_UNIT_SP, 15);
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        params.topMargin = dp(8);
        parent.addView(view, params);
        return view;
    }

    private int dp(int value) {
        return (int) TypedValue.applyDimension(
                TypedValue.COMPLEX_UNIT_DIP, value, getResources().getDisplayMetrics());
    }

    private void refreshPaths() {
        paths = new FolklorePaths(getExternalFilesDir(null), getFilesDir());
        String root = paths.rootPathForDisplay();
        pathView.setText(root != null ? root : "(app-specific external storage is unavailable)");
    }

    private void setStatus(String text, boolean error) {
        statusView.setText(text);
        if (error) {
            statusView.setTextColor(ERROR_COLOR);
        } else {
            statusView.setTextColor(defaultStatusColors);
        }
    }

    private void updateButtons() {
        checkButton.setEnabled(!destroyed && !checking && !launching);
        startButton.setEnabled(!destroyed && validated && !checking && !launching && resumed);
    }

    private void onCheckClicked() {
        if (destroyed || isFinishing() || checking || launching) {
            return;
        }
        refreshPaths();
        validated = false;
        checking = true;
        final int generation = ++checkGeneration;
        missingView.setVisibility(View.GONE);
        setStatus("Checking files... Hashing EBOOT.elf can take a while.", false);
        updateButtons();
        checkThread = new Thread(new CheckTask(this, paths, generation, mainHandler), "FolkloreFileCheck");
        checkThread.start();
    }

    private void cancelCheck() {
        if (!checking) {
            return;
        }
        checking = false;
        checkGeneration++;
        if (checkThread != null) {
            checkThread.interrupt();
            checkThread = null;
        }
    }

    void onCheckFinished(int generation, FolklorePaths.Report report) {
        if (destroyed || generation != checkGeneration || !checking) {
            return;
        }
        checking = false;
        checkThread = null;
        if (report.ok) {
            validated = true;
            missingView.setVisibility(View.GONE);
            setStatus("Files are ready. Tap Start to open Folklore.", false);
        } else {
            validated = false;
            StringBuilder text = new StringBuilder("Check failed:");
            for (String problem : report.problems) {
                text.append("\n- ").append(problem);
            }
            setStatus(text.toString(), true);
            showMissing(report);
        }
        updateButtons();
    }

    private void showMissing(FolklorePaths.Report report) {
        if (report.missingInputs.isEmpty()) {
            missingView.setVisibility(View.GONE);
            return;
        }
        StringBuilder text = new StringBuilder("Missing inputs (relative to the folder above):");
        for (String relative : report.missingInputs) {
            text.append("\n- ").append(relative).append("\n    ").append(sourceHint(relative));
        }
        missingView.setText(text.toString());
        missingView.setVisibility(View.VISIBLE);
    }

    private static String sourceHint(String relative) {
        if (FolklorePaths.ELF_RELATIVE.equals(relative)) {
            return "Decrypted executable made from your dump's PS3_GAME/USRDIR/EBOOT.BIN.";
        }
        if (FolklorePaths.SFO_RELATIVE.equals(relative)) {
            return "Copy of your dump's PS3_GAME/PARAM.SFO.";
        }
        if (relative.startsWith(FolklorePaths.DEV_FLASH_RELATIVE)) {
            return "dev_flash from your own installed PS3 firmware; must contain sys/external.";
        }
        return "Required user-supplied input.";
    }

    private void onStartClicked() {
        if (destroyed || isFinishing() || !resumed || checking || launching || !validated) {
            return;
        }
        launching = true;
        validated = false;
        updateButtons();
        setStatus("Starting the native test session...", false);
        try {
            startActivity(new Intent(this, FolkloreActivity.class));
        } catch (ActivityNotFoundException e) {
            onStartFailed(e);
        } catch (SecurityException e) {
            onStartFailed(e);
        }
    }

    private void onStartFailed(RuntimeException e) {
        Log.e(TAG, "Could not start FolkloreActivity", e);
        launching = false;
        setStatus("Could not start the native activity: " + e + ". Tap Check files to try again.", true);
        updateButtons();
    }

    private static final class CheckTask implements Runnable {
        private final WeakReference<FolkloreLauncherActivity> owner;
        private final FolklorePaths paths;
        private final int generation;
        private final Handler handler;

        CheckTask(FolkloreLauncherActivity activity, FolklorePaths paths, int generation, Handler handler) {
            this.owner = new WeakReference<FolkloreLauncherActivity>(activity);
            this.paths = paths;
            this.generation = generation;
            this.handler = handler;
        }

        @Override
        public void run() {
            FolklorePaths.Report report;
            try {
                report = paths.validateAndPrepare();
            } catch (InterruptedIOException e) {
                return;
            } catch (RuntimeException e) {
                Log.e(TAG, "File check failed unexpectedly", e);
                report = FolklorePaths.Report.failure(paths.rootPathForDisplay(),
                        "Unexpected error while checking files: " + e);
            }
            if (Thread.currentThread().isInterrupted()) {
                return;
            }
            final FolklorePaths.Report result = report;
            final WeakReference<FolkloreLauncherActivity> target = owner;
            final int checkId = generation;
            handler.post(new Runnable() {
                @Override
                public void run() {
                    FolkloreLauncherActivity activity = target.get();
                    if (activity != null) {
                        activity.onCheckFinished(checkId, result);
                    }
                }
            });
        }
    }
}
