package com.avazbek.nemo_app

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import android.util.Log
import java.util.concurrent.LinkedBlockingDeque
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

/**
 * Continuous PCM16 playback of Nemo's voice on the ASSISTANT path.
 *
 * We play through USAGE_ASSISTANT (not USAGE_MEDIA) so the system identifies
 * this stream as the assistant's voice and mixes it correctly over ducked
 * music. Full-bandwidth loudspeaker route — same quality as MEDIA, louder
 * than the muffled phone-call path. The cost is that hardware AEC may not
 * perfectly cancel speaker bleed on every device; false barge-in is handled
 * on the Dart side with a short debounce at the start of each reply.
 *
 * Audio streams chunk-by-chunk as it arrives. A generous ~1.5s jitter buffer
 * absorbs network bursts so playback is smooth; mid-reply chunks are never
 * dropped (only flush() on a real barge-in clears it). Writes run on a
 * dedicated thread so they never block the platform channel.
 */
class VoicePlayer(private val context: Context) {
    private var track: AudioTrack? = null
    private var worker: Thread? = null
    @Volatile private var running = false
    private val queue = LinkedBlockingDeque<ByteArray>()
    private val queuedBytes = AtomicInteger(0)
    private var sampleRate = 24000

    /** Reported when playback starves (choppy voice). Set by MainActivity. */
    var onUnderrun: ((Int) -> Unit)? = null

    // ~3s ceiling. Larger so a lossy long-haul link (China↔Europe↔Singapore)
    // can burst without the queue overflowing into dropped audio (choppiness);
    // only a severe stall ever trims it (oldest first) so latency can't run away.
    private val maxQueuedBytes get() = sampleRate * 2 * 3

    fun start(rate: Int) {
        if (track != null) return
        sampleRate = rate
        val min = AudioTrack.getMinBufferSize(
            sampleRate, AudioFormat.CHANNEL_OUT_MONO, AudioFormat.ENCODING_PCM_16BIT
        )
        // ~800ms hardware buffer (NOT low-latency): streamed-over-network audio
        // (esp. on mobile 4G) arrives in bursts, and a small buffer underruns on
        // every hiccup = choppy speech. A bigger buffer rides out the jitter.
        // NOTE: this latency is matched by the +1100ms mic-mute tail in the
        // screen so Nemo's buffered tail can't echo into a false barge-in.
        val bufBytes = maxOf(min, sampleRate * 2 * 6 / 5)

        // USAGE_ASSISTANT: same full-bandwidth loudspeaker route as MEDIA, but
        // identifies this stream as the assistant's voice so the system mixes
        // it correctly over ducked music (the session-level transient focus in
        // voice_chat_service is what makes other apps duck and resume).
        val attrs = AudioAttributes.Builder()
            .setUsage(AudioAttributes.USAGE_ASSISTANT)
            .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
            .build()
        val format = AudioFormat.Builder()
            .setSampleRate(sampleRate)
            .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
            .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
            .build()
        val t = AudioTrack.Builder()
            .setAudioAttributes(attrs)
            .setAudioFormat(format)
            .setTransferMode(AudioTrack.MODE_STREAM)
            .setBufferSizeInBytes(bufBytes)
            .build()
        // Start PAUSED — play() called on first chunk so underruns don't fire
        // before any audio has been queued (B-17).
        t.pause()
        track = t

        running = true
        worker = Thread { drain() }.apply { isDaemon = true; name = "nemo-voice-player"; start() }
    }

    private fun drain() {
        var lastUnderrun = 0
        while (running) {
            val chunk = try {
                queue.pollFirst(200, TimeUnit.MILLISECONDS)
            } catch (e: InterruptedException) {
                null
            } ?: continue
            queuedBytes.addAndGet(-chunk.size)
            val t = track ?: continue
            try {
                t.write(chunk, 0, chunk.size, AudioTrack.WRITE_BLOCKING)
                // Underruns = the speaker starved (choppy voice). Log when it
                // climbs so logcat shows whether the buffer needs to be bigger.
                val u = t.underrunCount
                if (u > lastUnderrun) {
                    Log.w("NemoVoicePlayer", "playback underruns: $u (queued=${queuedBytes.get()}B)")
                    lastUnderrun = u
                    onUnderrun?.invoke(u)
                }
            } catch (e: Exception) {
                // track torn down mid-write; loop exits on running=false
            }
        }
    }

    fun write(bytes: ByteArray) {
        if (!running) return
        while (queuedBytes.get() > maxQueuedBytes) {
            val old = queue.pollFirst() ?: break
            queuedBytes.addAndGet(-old.size)
        }
        queue.offerLast(bytes)
        queuedBytes.addAndGet(bytes.size)
        // First chunk: transition from PAUSED → PLAYING (B-17 — no underrun before data arrives).
        val t = track ?: return
        if (t.playState == AudioTrack.PLAYSTATE_PAUSED) {
            try { t.play() } catch (_: Exception) {}
        }
    }

    /** Barge-in: drop everything queued and clear the track immediately. */
    fun flush() {
        queue.clear()
        queuedBytes.set(0)
        val t = track ?: return
        try {
            t.pause(); t.flush(); t.play()
        } catch (e: Exception) {
        }
    }

    fun stop() {
        running = false
        worker?.interrupt()
        worker = null
        queue.clear()
        queuedBytes.set(0)
        val t = track
        track = null
        try { t?.pause() } catch (_: Exception) {}
        try { t?.flush() } catch (_: Exception) {}
        try { t?.stop() } catch (_: Exception) {}
        try { t?.release() } catch (_: Exception) {}
    }
}
