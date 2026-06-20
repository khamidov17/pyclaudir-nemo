package com.avazbek.nemo_app

import android.content.Context
import android.widget.Toast
import io.flutter.plugin.common.MethodChannel
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import com.rementia.openwakeword.lib.WakeWordEngine
import com.rementia.openwakeword.lib.model.WakeWordModel

/**
 * On-device wake word via openWakeWord (ONNX). The engine captures the mic
 * itself and emits per-frame scores + detections; we forward detections to
 * Flutter on the UI thread.
 *
 * DIAGNOSTIC MODE: because wake word can't be tested without a device, this
 * surfaces its state as on-screen toasts — "starting", a crash message, the
 * live max score (so we can see if it's even hearing the mic), and detections.
 * Once it's confirmed working the toasts can be removed.
 */
class WakeWordController(
    private val context: Context,
    private val channel: MethodChannel,
    private val onUi: (Runnable) -> Unit,
) {
    private var engine: WakeWordEngine? = null
    private var scope: CoroutineScope? = null
    private var lastToastMs = 0L
    private var maxScore = 0f

    fun toast(msg: String) {
        onUi(Runnable { Toast.makeText(context, msg, Toast.LENGTH_SHORT).show() })
    }

    fun start(modelAsset: String, threshold: Float) {
        if (engine != null) return
        toast("WW: starting ($modelAsset)…")
        try {
            val s = CoroutineScope(Dispatchers.Default + SupervisorJob())
            val eng = WakeWordEngine(
                context,
                listOf(WakeWordModel("nemo", modelAsset, threshold)),
            )
            // Raw scores → shows whether the engine is hearing the mic at all.
            s.launch {
                eng.scores.collect { sc ->
                    if (sc.score > maxScore) maxScore = sc.score
                    val now = System.currentTimeMillis()
                    if (now - lastToastMs > 2500) {
                        lastToastMs = now
                        val m = maxScore
                        maxScore = 0f
                        toast("WW hearing… max=%.2f".format(m))
                    }
                }
            }
            s.launch {
                eng.detections.collect { d ->
                    toast("WW DETECTED %.2f!".format(d.score))
                    onUi(
                        Runnable {
                            channel.invokeMethod("onWakeWord", d.score.toDouble())
                        }
                    )
                }
            }
            eng.start()
            engine = eng
            scope = s
        } catch (e: Throwable) {
            toast("WW FAILED: ${e.message}")
        }
    }

    fun stop() {
        try {
            engine?.release()
        } catch (_: Exception) {
        }
        engine = null
        scope?.cancel()
        scope = null
    }
}
