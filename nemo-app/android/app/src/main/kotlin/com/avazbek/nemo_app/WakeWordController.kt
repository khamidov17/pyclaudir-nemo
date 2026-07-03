package com.avazbek.nemo_app

import android.content.Context
import android.media.AudioManager
import android.os.Build
import android.telephony.PhoneStateListener
import android.telephony.TelephonyCallback
import android.telephony.TelephonyManager
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
 *
 * Stops the engine during phone/VoIP calls to release the mic and avoid
 * interfering with call audio. Restarts automatically when the call ends.
 */
class WakeWordController(
    private val context: Context,
    private val channel: MethodChannel,
    private val onUi: (Runnable) -> Unit,
) {
    private var engine: WakeWordEngine? = null
    private var scope: CoroutineScope? = null

    // Stored so we can re-arm the engine after a call ends.
    private var lastModelAsset: String = ""
    private var lastThreshold: Float = 0f

    // Phone-state tracking — stops engine during calls, restarts on idle.
    @Suppress("DEPRECATION")
    private val legacyPhoneListener = object : PhoneStateListener() {
        @Deprecated("Deprecated in Java")
        override fun onCallStateChanged(state: Int, phoneNumber: String?) {
            handleCallState(state)
        }
    }

    private val modernCallbackHolder: Any? by lazy {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            object : TelephonyCallback(), TelephonyCallback.CallStateListener {
                override fun onCallStateChanged(state: Int) = handleCallState(state)
            }
        } else null
    }

    // True only when the engine was stopped specifically because a call started.
    // Prevents the IDLE callback from restarting after a user-initiated stop().
    private var stoppedForCall = false

    private fun handleCallState(state: Int) {
        when (state) {
            TelephonyManager.CALL_STATE_RINGING,
            TelephonyManager.CALL_STATE_OFFHOOK -> {
                if (engine != null) {
                    stoppedForCall = true
                    releaseEngine() // release mic but keep phone listener alive
                }
            }
            TelephonyManager.CALL_STATE_IDLE -> {
                if (stoppedForCall && lastModelAsset.isNotEmpty()) {
                    stoppedForCall = false
                    start(lastModelAsset, lastThreshold)
                }
            }
        }
    }

    private fun releaseEngine() {
        try { engine?.release() } catch (_: Exception) {}
        engine = null
        scope?.cancel()
        scope = null
    }

    private fun registerPhoneListener() {
        val tm = context.getSystemService(Context.TELEPHONY_SERVICE) as TelephonyManager
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            @Suppress("UNCHECKED_CAST")
            (modernCallbackHolder as? TelephonyCallback)?.let {
                tm.registerTelephonyCallback(context.mainExecutor, it)
            }
        } else {
            @Suppress("DEPRECATION")
            tm.listen(legacyPhoneListener, PhoneStateListener.LISTEN_CALL_STATE)
        }
    }

    private fun unregisterPhoneListener() {
        val tm = context.getSystemService(Context.TELEPHONY_SERVICE) as TelephonyManager
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            @Suppress("UNCHECKED_CAST")
            (modernCallbackHolder as? TelephonyCallback)?.let { tm.unregisterTelephonyCallback(it) }
        } else {
            @Suppress("DEPRECATION")
            tm.listen(legacyPhoneListener, PhoneStateListener.LISTEN_NONE)
        }
    }

    fun toast(msg: String) {
        onUi(Runnable { Toast.makeText(context, msg, Toast.LENGTH_SHORT).show() })
    }

    // True after the first successful start() — phone listener stays registered
    // for the lifetime of the controller (through call-triggered restarts).
    private var phoneListenerRegistered = false

    fun start(modelAsset: String, threshold: Float) {
        if (engine != null) return
        lastModelAsset = modelAsset
        lastThreshold = threshold
        val s = CoroutineScope(Dispatchers.Default + SupervisorJob())
        var eng: WakeWordEngine? = null
        try {
            eng = WakeWordEngine(
                context,
                listOf(WakeWordModel("nemo", modelAsset, threshold)),
            )
            s.launch {
                try {
                    eng!!.detections.collect { d ->
                        onUi(Runnable {
                            channel.invokeMethod("onWakeWord", d.score.toDouble())
                        })
                    }
                } catch (e: Throwable) {
                    // Mic permission revoked mid-session — notify Flutter so
                    // WakeWordService can set _running=false and show a snackbar.
                    onUi(Runnable {
                        channel.invokeMethod("onWakeWordError", e.message ?: "mic lost")
                    })
                    stop()
                }
            }
            eng!!.start()
            engine = eng
            scope = s
            if (!phoneListenerRegistered) {
                registerPhoneListener()
                phoneListenerRegistered = true
            }
        } catch (e: Throwable) {
            try { eng?.release() } catch (_: Exception) {}
            s.cancel()
            toast("Wake word failed: ${e.message}")
        }
    }

    fun stop() {
        stoppedForCall = false
        lastModelAsset = "" // prevent phantom restart if IDLE fires after manual stop
        if (phoneListenerRegistered) {
            unregisterPhoneListener()
            phoneListenerRegistered = false
        }
        releaseEngine()
    }
}
