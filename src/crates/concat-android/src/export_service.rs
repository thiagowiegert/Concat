// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 Concat contributors

use jni::objects::JString;
use jni::{jni_sig, jni_str, sys};
use slint::android::AndroidApp;
use std::sync::atomic::{AtomicBool, AtomicI64, Ordering};
use std::sync::{Arc, Mutex};

static NEXT: AtomicI64 = AtomicI64::new(1);
static ACTIVE: Mutex<Option<(i64, Arc<AtomicBool>)>> = Mutex::new(None);

pub fn install(app: &AndroidApp) {
    let start_app = app.clone();
    let progress_app = app.clone();
    let stop_app = app.clone();
    concat::install_export_service(concat::ExportService {
        start: Box::new(move |cancel| {
            let id = NEXT.fetch_add(1, Ordering::Relaxed);
            *ACTIVE.lock().map_err(|e| e.to_string())? = Some((id, cancel));
            let answer = super::java::with_class(&start_app, |env, activity, class| {
                let answer = env
                    .call_static_method(
                        class,
                        jni_str!("exportStart"),
                        jni_sig!("(Landroid/app/Activity;J)Ljava/lang/String;"),
                        &[activity.into(), id.into()],
                    )?
                    .l()?;
                if answer.is_null() {
                    return Ok(None);
                }
                let answer = env.cast_local::<JString>(answer)?;
                Ok(Some(answer.mutf8_chars(env)?.to_string()))
            });
            match answer {
                Ok(None) => Ok(id),
                other => {
                    if let Ok(mut slot) = ACTIVE.lock() {
                        *slot = None;
                    }
                    Err(match other {
                        Ok(Some(why)) => why,
                        Err(error) => format!("Could not start Android export service: {error}"),
                        _ => unreachable!(),
                    })
                }
            }
        }),
        progress: Box::new(move |id, fraction, stage| {
            let result = super::java::with_class(&progress_app, |env, _, class| {
                let stage = env.new_string(stage)?;
                env.call_static_method(
                    class,
                    jni_str!("exportProgress"),
                    jni_sig!("(JILjava/lang/String;)V"),
                    &[
                        id.into(),
                        ((fraction.clamp(0.0, 1.0) * 99.0) as i32).into(),
                        (&stage).into(),
                    ],
                )?;
                Ok(())
            });
            if let Err(error) = result {
                log::warn!("export notification: {error}");
            }
        }),
        stop: Box::new(move |id| {
            if let Ok(mut slot) = ACTIVE.lock()
                && slot.as_ref().is_some_and(|(active, _)| *active == id)
            {
                *slot = None;
            }
            let result = super::java::with_class(&stop_app, |env, _, class| {
                env.call_static_method(
                    class,
                    jni_str!("exportStop"),
                    jni_sig!("(J)V"),
                    &[id.into()],
                )?;
                Ok(())
            });
            if let Err(error) = result {
                log::warn!("stopping export service: {error}");
            }
        }),
    });
}

// SAFETY: registered with the exact Java (long) -> void signature. No UI access.
pub(super) unsafe extern "system" fn cancelled(
    _: *mut sys::JNIEnv,
    _: sys::jclass,
    id: sys::jlong,
) {
    if let Ok(slot) = ACTIVE.lock()
        && let Some((active, cancel)) = slot.as_ref()
        && *active == id
    {
        cancel.store(true, Ordering::Relaxed);
    }
}

// SAFETY: registered as () -> boolean; only reads the current atomic cancel flag.
pub(super) unsafe extern "system" fn is_cancelled(
    _: *mut sys::JNIEnv,
    _: sys::jclass,
) -> sys::jboolean {
    let cancelled = ACTIVE
        .lock()
        .map(|slot| {
            slot.as_ref()
                .is_none_or(|(_, cancel)| cancel.load(Ordering::Relaxed))
        })
        .unwrap_or(true);
    u8::from(cancelled)
}
