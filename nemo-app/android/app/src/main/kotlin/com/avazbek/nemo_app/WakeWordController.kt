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
 * itself and emits detections; we forward them to Flutter on the UI thread.
 * Silent in normal operation — only a toast if the engine fails to start.
 */
class WakeWordController(
    private val context: Context,
    private val channel: MethodChannel,
    private val onUi: (Runnable) -> Unit,
) {
    private var engine: WakeWordEngine? = null
    private var scope: CoroutineScope? = null

    fun toast(msg: String) {
        onUi(Runnable { Toast.makeText(context, msg, Toast.LENGTH_SHORT).show() })
    }

    fun start(modelAsset: String, threshold: Float) {
        if (engine != null) return
        try {
            val s = CoroutineScope(Dispatchers.Default + SupervisorJob())
            val eng = WakeWordEngine(
                context,
                listOf(WakeWordModel("nemo", modelAsset, threshold)),
            )
            s.launch {
                eng.detections.collect { d ->
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
            toast("Wake word failed: ${e.message}")
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
