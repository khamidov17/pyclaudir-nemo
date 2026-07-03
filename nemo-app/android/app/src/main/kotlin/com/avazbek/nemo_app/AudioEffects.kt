package com.avazbek.nemo_app

import android.content.Context
import android.media.AudioManager
import android.media.audiofx.AcousticEchoCanceler
import android.media.audiofx.AutomaticGainControl
import android.media.audiofx.NoiseSuppressor
import android.util.Log

/**
 * Full-duplex echo control for the Nemo voice chat.
 *
 * Two responsibilities, both driven from Dart over the
 * `com.avazbek.nemo_app/audio_effects` MethodChannel:
 *
 *  1. ROUTING — put the platform into MODE_IN_COMMUNICATION and force the
 *     loudspeaker on while Nemo is speaking. This is what gives the SoC's
 *     hardware AEC an echo *reference*: it now knows the loudspeaker signal and
 *     can subtract it from the mic, so Nemo's own voice doesn't reach the
 *     server's VAD and self-trigger a barge-in. The mic is captured by the
 *     `record` plugin with the VOICE_COMMUNICATION source, which engages that
 *     hardware AEC on most devices.
 *
 *  2. EFFECTS (best-effort) — additionally attach the software
 *     AcousticEchoCanceler / NoiseSuppressor / AutomaticGainControl effects.
 *
 *     LIMITATION: the `record` Flutter plugin does NOT expose the audio session
 *     id of its internal AudioRecord, so we cannot bind these effects to the
 *     exact capture session. We attach to session id 0 (the global/output mix)
 *     as a belt-and-suspenders. On many devices the primary, reliable echo
 *     cancellation comes from step (1) (the voiceCommunication source + comm
 *     routing); these effects are an opportunistic extra. If a device needs a
 *     bound session id, the proper fix is a native AudioRecord capture path that
 *     owns the session id — see README / TODO.
 *
 * All operations are guarded and never throw across the channel.
 */
class AudioEffects(private val context: Context) {

    private val tag = "NemoAudioEffects"

    private var aec: AcousticEchoCanceler? = null
    private var ns: NoiseSuppressor? = null
    private var agc: AutomaticGainControl? = null

    private var previousMode: Int = AudioManager.MODE_NORMAL
    private var previousSpeakerphone: Boolean = false
    private var engaged = false

    private val audioManager: AudioManager
        get() = context.getSystemService(Context.AUDIO_SERVICE) as AudioManager

    /**
     * Engage communication routing + AEC effects. Returns a status map so Dart
     * can log what actually took (useful for the device test checklist).
     */
    fun enable(): Map<String, Any> {
        val am = audioManager
        if (!engaged) {
            previousMode = am.mode
            @Suppress("DEPRECATION")
            previousSpeakerphone = am.isSpeakerphoneOn
        }
        engaged = true

        var modeOk = false
        var speakerOk = false
        try {
            am.mode = AudioManager.MODE_IN_COMMUNICATION
            modeOk = true
        } catch (e: Exception) {
            Log.w(tag, "could not set MODE_IN_COMMUNICATION: ${e.message}")
        }
        try {
            // Keep Nemo's reply on the loud speaker (not the earpiece) even in
            // communication mode. setSpeakerphoneOn is deprecated on API 31+ but
            // still functions; the modern AudioManager.setCommunicationDevice
            // path is left as a follow-up to avoid an API-level branch here.
            @Suppress("DEPRECATION")
            am.isSpeakerphoneOn = true
            speakerOk = true
        } catch (e: Exception) {
            Log.w(tag, "could not force speakerphone: ${e.message}")
        }

        // Capture-side effects require the AudioRecord session ID, not session 0.
        // Session 0 is a no-op for AEC/NS/AGC on most devices. The Flutter `record`
        // plugin doesn't expose audioSessionId; until it does (or the path is replaced
        // with native AudioRecord), we skip software effects and rely on hardware AEC
        // that MODE_IN_COMMUNICATION engages on the audio path automatically.
        // TODO: pass capture session ID via MethodChannel when plugin exposes it.
        val aecAvailable = AcousticEchoCanceler.isAvailable()
        val nsAvailable = NoiseSuppressor.isAvailable()
        val agcAvailable = AutomaticGainControl.isAvailable()
        if (aecAvailable || nsAvailable || agcAvailable) {
            Log.d(tag, "AEC/NS/AGC available but skipped — capture session ID unknown")
        }

        val status = mapOf(
            "modeInCommunication" to modeOk,
            "speakerphone" to speakerOk,
            "aecAvailable" to aecAvailable,
            "aecAttached" to false, // software AEC skipped until capture session ID is available
            "nsAvailable" to nsAvailable,
            "nsAttached" to (ns != null),
            "agcAvailable" to agcAvailable,
            "agcAttached" to (agc != null),
        )
        Log.i(tag, "enable → $status")
        return status
    }

    /** Release effects and restore the prior audio mode/routing. */
    fun disable() {
        aec?.let { safeRelease("AEC") { it.enabled = false; it.release() } }
        ns?.let { safeRelease("NS") { it.enabled = false; it.release() } }
        agc?.let { safeRelease("AGC") { it.enabled = false; it.release() } }
        aec = null
        ns = null
        agc = null

        if (engaged) {
            val am = audioManager
            try {
                am.mode = previousMode
            } catch (e: Exception) {
                Log.w(tag, "could not restore audio mode: ${e.message}")
            }
            try {
                @Suppress("DEPRECATION")
                am.isSpeakerphoneOn = previousSpeakerphone
            } catch (e: Exception) {
                Log.w(tag, "could not restore speakerphone: ${e.message}")
            }
            engaged = false
        }
        Log.i(tag, "disable → restored mode=$previousMode")
    }

    private fun <T> tryCreate(name: String, create: () -> T?): T? = try {
        create()
    } catch (e: Exception) {
        Log.w(tag, "could not create $name effect: ${e.message}")
        null
    }

    private inline fun safeRelease(name: String, block: () -> Unit) = try {
        block()
    } catch (e: Exception) {
        Log.w(tag, "could not release $name effect: ${e.message}")
    }
}
