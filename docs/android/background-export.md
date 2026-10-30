# Android foreground export

The Slint APK uses `ConcatExportService` for a user-started export. Rust starts
it while the editor is visible and waits for `startForeground` before queuing
rendering. Progress goes directly from the worker to the notification. The same
worker publishes to MediaStore before releasing the service and export job.
Slint only receives the final presentation result; a paused UI cannot defer the
copy or service cleanup. Desktop and iOS publication paths are unchanged.

Android 15 uses `mediaProcessing`; API 29–34 uses `dataSync` for local file
processing, and API 26–28 uses the untyped foreground API. The manifest declares
both type permissions, `FOREGROUND_SERVICE`, `POST_NOTIFICATIONS` and `WAKE_LOCK`.
The notification permission is requested from the visible activity on export.
Denying it does not prevent export, but Android hides the notification from the
notification drawer (the service remains visible in Active apps). A partial wake
lock covers rendering and publication, with a six-hour maximum. The system's
Android 15 `onTimeout` cancels the current job and stops immediately; a six-hour
timer also bounds older systems. Starts denied by Android (including exhaustion
of the shared time budget) fail before rendering. No boot receiver or background
restart is used. Cancellation does not delete the completed source video; an
incomplete MediaStore row is removed.

This is not resumable work. Force-stop, process death, explicit exit/Back that
destroys the NativeActivity, and device reboot can interrupt an export. A
foreground service does not guarantee survival of those events. Android 15's
six-hour budget is shared across all services of the same type in the app.

## Build

Build the native APK using the existing mobile environment and `cargo apk build`
(see `.github/workflows/mobile.yml`), then from the repository root run:

```
python3 scripts/ci/package-android-service.py src/target/release/apk/Concat.apk
```

The packaging step requires Java, SDK platform 35, build-tools 35.0.0,
`CARGO_APK_RELEASE_KEYSTORE` and `CARGO_APK_RELEASE_KEYSTORE_PASSWORD`. It preserves
cargo-apk's generated manifest, native libraries and assets, adds the service
and Java DEX, aligns and signs the APK. **The intermediate cargo-apk APK alone is
not the installable deliverable for this feature.** Android creates services
through the application's class loader; an embedded in-memory DEX alone cannot
provide the service. The final APK puts both Java classes in the main DEX, so
the existing parent-first loader registers native callbacks on those classes.
The license and existing copyright notices are preserved.

## Validation status and device acceptance

No background export has yet been verified on an Android device. CI's existing
launch smoke test is expanded to API 33, 34 and 35 (also keeping 30); a launch
smoke pass is not evidence that background rendering works. Run the following
on Android 13, 14 and 15 before claiming support:

1. Import video and audio, start an export long enough to outlast these steps.
   Grant notification permission; verify ongoing progress and the service type
   with `adb shell dumpsys activity services org.standalones.concat`.
2. Press Home, open another application, then lock the screen. Observe continued
   progress and verify a playable full-duration output in Movies/Concat while
   the editor remains backgrounded. Check audio/video synchronization.
3. Repeat with notifications denied. Confirm the export still completes and
   Android lists the active foreground service in its task manager.
4. Cancel separately from the editor and notification, including during a large
   MediaStore copy. Confirm no partial gallery entry, retained source when the
   copy is interrupted, no service, notification or wake lock afterward. Start
   another export and ensure stale callbacks do not cancel it.
5. Exercise render failure, storage-full publication failure, and immediate
   cancellation. Verify the original error/result and cleanup, then retry.
6. On API 35 shorten the budget with
   `adb shell device_config put activity_manager media_processing_fgs_timeout_duration 10000`.
   Start a long export and background it. Confirm timeout cancellation, no ANR,
   and service removal. Restore with
   `adb shell device_config delete activity_manager media_processing_fgs_timeout_duration`.
7. Save logcat, Android version, device model, output metadata and timings.
   Test foreground editing/playback, picker, microphone and subtitle export for
   regressions. Do not report background support based only on compilation.

References: [service types](https://developer.android.com/develop/background-work/services/fgs/service-types),
[timeouts](https://developer.android.com/develop/background-work/services/fgs/timeout),
[notification permission](https://developer.android.com/develop/ui/views/notifications/notification-permission).

