package com.avazbek.nemo_app

import android.content.Context
import io.flutter.plugin.common.MethodChannel
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import com.rementia.openwakeword.lib.WakeWordEngine
import com.rementia.openwakeword.lib.model.WakeWordModel

/**
 * On-device wake word via openWakeWord (ONNX). A purpose-built keyword spotter
 * — far lighter and more accurate than the old Vosk STT, fully offline with no
 * vendor key. The engine captures the mic itself; we forward each detection to
 * Flutter on the UI thread.
 *
 * start()/stop() mirror the Dart WakeWordService so mic contention with a live
 * voice session is handled identically: stop() releases the engine (and the
 * mic) before a voice session opens, and start() re-arms it afterwards.
 *
 * The melspectrogram.onnx and embedding_model.onnx feature models live at the
 * assets root (loaded by the library); [modelAsset] names the wake-word model.
 */
class WakeWordController(
    private val context: Context,
    private val channel: MethodChannel,
    private val onUi: (Runnable) -> Unit,
) {
    private var engine: WakeWordEngine? = null
    private var scope: CoroutineScope? = null

    fun start(modelAsset: String, threshold: Float) {
        if (engine != null) return
        val s = CoroutineScope(Dispatchers.Default + SupervisorJob())
        val eng = WakeWordEngine(
            context,
            listOf(WakeWordModel("nemo", modelAsset, threshold)),
        )
        s.launch {
            eng.detections.collect { detection ->
                onUi(
                    Runnable {
                        channel.invokeMethod("onWakeWord", detection.score.toDouble())
                    }
                )
            }
        }
        eng.start()
        engine = eng
        scope = s
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
