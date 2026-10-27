// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 Concat contributors
package org.standalones.concat;

import android.Manifest;
import android.app.*;
import android.content.*;
import android.content.pm.PackageManager;
import android.content.pm.ServiceInfo;
import android.os.*;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

/** Protects one user-started Rust export, including its MediaStore copy. */
public final class ConcatExportService extends Service {
    private static final String CHANNEL = "exports";
    private static final int NOTIFICATION = 0xC0C6;
    private static final Handler MAIN = new Handler(Looper.getMainLooper());
    private static volatile Session current;
    private static ConcatExportService instance;
    private PowerManager.WakeLock wakeLock;
    private boolean stopping;

    private static final class Session {
        final long id;
        final CountDownLatch ready = new CountDownLatch(1);
        volatile String error;
        volatile boolean cancelled;
        int percent;
        String stage = "Preparing export";
        long updated;
        Session(long id) { this.id = id; }
    }

    /** Called on Slint's thread, never on Android's main looper. */
    public static String start(Activity activity, long id) {
        if (Looper.myLooper() == Looper.getMainLooper()) return "Export must start off the Android UI thread";
        Session session = new Session(id);
        MAIN.post(() -> {
            if (current != null || (instance != null && instance.stopping)) {
                session.error = "An export is already running";
                session.ready.countDown();
                return;
            }
            current = session;
            try {
                if (Build.VERSION.SDK_INT >= 33 && activity.checkSelfPermission(
                        Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
                    // Denial does not prohibit an FGS; Android lists it in Active apps.
                    activity.requestPermissions(new String[] {Manifest.permission.POST_NOTIFICATIONS}, 0xC0C6);
                }
                activity.startForegroundService(new Intent(activity, ConcatExportService.class).putExtra("id", id));
            } catch (RuntimeException e) {
                session.error = e.toString();
                current = null;
                session.ready.countDown();
            }
        });
        try {
            if (!session.ready.await(5, TimeUnit.SECONDS)) {
                session.error = "Android did not start the export service in time";
                stop(id);
            }
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            session.error = "Interrupted while starting export service";
            stop(id);
        }
        return session.cancelled && session.error == null ? "Export cancelled" : session.error;
    }

    public static boolean cancelled() {
        Session session = current;
        return session == null || session.cancelled || ConcatFiles.exportIsCancelled();
    }

    public static void progress(long id, int percent, String stage) {
        MAIN.post(() -> {
            Session session = current;
            if (session == null || session.id != id || instance == null) return;
            long now = SystemClock.elapsedRealtime();
            if (now - session.updated < 500 && stage.equals(session.stage)) return;
            session.updated = now;
            session.percent = Math.max(0, Math.min(99, percent));
            session.stage = stage;
            instance.getSystemService(NotificationManager.class).notify(NOTIFICATION, instance.notification(session));
        });
    }

    public static void stop(long id) {
        MAIN.post(() -> {
            Session session = current;
            if (session == null || session.id != id) return;
            current = null;
            session.cancelled = true;
            session.ready.countDown();
            if (instance != null) instance.finish();
        });
    }

    private Notification notification(Session session) {
        Intent open = getPackageManager().getLaunchIntentForPackage(getPackageName());
        PendingIntent content = PendingIntent.getActivity(this, 0, open,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        Intent cancel = new Intent(this, ConcatExportService.class).setAction("cancel").putExtra("id", session.id);
        PendingIntent action = PendingIntent.getService(this, (int) session.id, cancel,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        return new Notification.Builder(this, CHANNEL)
                .setSmallIcon(android.R.drawable.stat_sys_upload)
                .setContentTitle("Concat � Exporting video").setContentText(session.stage)
                .setContentIntent(content).setOngoing(true).setOnlyAlertOnce(true)
                .setCategory(Notification.CATEGORY_PROGRESS)
                .setProgress(100, session.percent, session.percent == 0)
                .addAction(new Notification.Action.Builder(null, "Cancel", action).build()).build();
    }

    @Override public void onCreate() {
        super.onCreate();
        instance = this;
        getSystemService(NotificationManager.class).createNotificationChannel(
                new NotificationChannel(CHANNEL, "Video exports", NotificationManager.IMPORTANCE_LOW));
    }

    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        Session session = current;
        if (session == null) {
            // Satisfy a delayed startForegroundService even when Rust already gave up.
            try { promote(new Session(0)); } catch (RuntimeException ignored) {
                // No worker exists. A denied stale start still needs to stop.
            } finally { finish(); }
            return START_NOT_STICKY;
        }
        if (intent != null && "cancel".equals(intent.getAction())) {
            if (intent.getLongExtra("id", -1) == session.id) cancel();
            return START_NOT_STICKY;
        }
        try {
            promote(session);
            wakeLock = getSystemService(PowerManager.class).newWakeLock(
                    PowerManager.PARTIAL_WAKE_LOCK, "Concat:export");
            wakeLock.acquire(6 * 60 * 60 * 1000L);
            // Bound older Android versions too, which have no onTimeout callback.
            MAIN.postDelayed(() -> { if (current == session) cancel(); }, 6 * 60 * 60 * 1000L);
        } catch (RuntimeException e) {
            session.error = e.toString();
            current = null;
            finish();
        } finally {
            session.ready.countDown();
        }
        return START_NOT_STICKY;
    }

    private void promote(Session session) {
        if (Build.VERSION.SDK_INT >= 35) {
            startForeground(NOTIFICATION, notification(session), ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROCESSING);
        } else if (Build.VERSION.SDK_INT >= 29) {
            startForeground(NOTIFICATION, notification(session), ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC);
        } else {
            startForeground(NOTIFICATION, notification(session));
        }
    }

    private void cancel() {
        Session session = current;
        if (session != null) {
            session.cancelled = true;
            ConcatFiles.exportCancelled(session.id);
            current = null;
            session.ready.countDown();
        }
        finish();
    }

    private void finish() {
        stopping = true;
        if (wakeLock != null && wakeLock.isHeld()) wakeLock.release();
        wakeLock = null;
        stopForeground(STOP_FOREGROUND_REMOVE);
        stopSelf();
    }

    @Override public void onTimeout(int startId, int fgsType) { cancel(); }
    @Override public void onDestroy() {
        if (instance == this) { cancel(); instance = null; }
        super.onDestroy();
    }
    @Override public IBinder onBind(Intent intent) { return null; }
}
