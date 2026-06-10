package com.avazbek.nemo_app

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import android.os.Build
import java.util.concurrent.LinkedBlockingDeque
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

/**
 * Continuous PCM16 playback of Nemo's voice on the MEDIA path.
 *
 * We play through USAGE_MEDIA (not the voice-call path) so the audio is
 * full-bandwidth, loud and clear instead of the muffled/quiet "phone call"
 * sound. The cost is that the capture-side hardware AEC may not perfectly
 * cancel speaker bleed on every device; false barge-in is handled on the
 * Dart side with a short debounce at the start of each reply.
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

    // ~1.5s ceiling. Big enough to ride out real jitter without choppiness;
    // only a severe stall ever trims it (oldest first) so latency can't run away.
    private val maxQueuedBytes get() = sampleRate * 2 * 3 / 2

    fun start(rate: Int) {
        if (track != null) return
        sampleRate = rate
        val min = AudioTrack.getMinBufferSize(
            sampleRate, AudioFormat.CHANNEL_OUT_MONO, AudioFormat.ENCODING_PCM_16BIT
        )
        val bufBytes = maxOf(min, sampleRate * 2 / 5) // >= 200ms hardware buffer

        val attrs = AudioAttributes.Builder()
            .setUsage(AudioAttributes.USAGE_MEDIA)
            .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
            .build()
        val format = AudioFormat.Builder()
            .setSampleRate(sampleRate)
            .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
            .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
            .build()
        val builder = AudioTrack.Builder()
            .setAudioAttributes(attrs)
            .setAudioFormat(format)
            .setTransferMode(AudioTrack.MODE_STREAM)
            .setBufferSizeInBytes(bufBytes)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            builder.setPerformanceMode(AudioTrack.PERFORMANCE_MODE_LOW_LATENCY)
        }
        val t = builder.build()
        t.play()
        track = t

        running = true
        worker = Thread { drain() }.apply { isDaemon = true; name = "nemo-voice-player"; start() }
    }

    private fun drain() {
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
        try {
            t?.pause(); t?.flush(); t?.stop(); t?.release()
        } catch (e: Exception) {
        }
    }
}
