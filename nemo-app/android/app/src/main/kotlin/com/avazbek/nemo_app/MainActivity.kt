package com.avazbek.nemo_app

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.PixelFormat
import android.hardware.display.DisplayManager
import android.hardware.display.VirtualDisplay
import android.media.Image
import android.media.ImageReader
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.util.DisplayMetrics
import android.view.WindowManager
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel
import java.io.ByteArrayOutputStream

class MainActivity : FlutterActivity() {

    companion object {
        const val CH_ACCESSIBILITY = "com.avazbek.nemo_app/accessibility"
        const val CH_INTENTS = "com.avazbek.nemo_app/intents"
        const val CH_SCREENSHOT = "com.avazbek.nemo_app/screenshot"
        const val CH_NOTIFICATION = "com.avazbek.nemo_app/notification"
        const val CH_VOICE = "com.avazbek.nemo_app/voice_player"
        const val CH_WAKEWORD = "com.avazbek.nemo_app/wakeword"
        const val SCREENSHOT_REQUEST = 1001
    }

    private var wakeWord: WakeWordController? = null

    // MediaProjection state
    private var mediaProjection: MediaProjection? = null
    private var pendingScreenshotResult: MethodChannel.Result? = null
    private var screenshotChannel: MethodChannel? = null

    // Continuous voice playback (native AudioTrack on the voice-comms path)
    private val voicePlayer by lazy { VoicePlayer(applicationContext) }

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)

        // ── Accessibility channel ───────────────────────────────────────
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, CH_ACCESSIBILITY)
            .setMethodCallHandler { call, result ->
                val svc = NemoAccessibilityService.instance
                when (call.method) {
                    "isEnabled" -> result.success(svc != null)
                    "tap" -> {
                        val x = (call.argument<Double>("x") ?: 0.0).toFloat()
                        val y = (call.argument<Double>("y") ?: 0.0).toFloat()
                        result.success(NemoAccessibilityService.tap(x, y))
                    }
                    "swipe" -> {
                        val x1 = (call.argument<Double>("x1") ?: 0.0).toFloat()
                        val y1 = (call.argument<Double>("y1") ?: 0.0).toFloat()
                        val x2 = (call.argument<Double>("x2") ?: 0.0).toFloat()
                        val y2 = (call.argument<Double>("y2") ?: 0.0).toFloat()
                        result.success(NemoAccessibilityService.swipe(x1, y1, x2, y2))
                    }
                    "typeText" -> {
                        val text = call.argument<String>("text") ?: ""
                        result.success(NemoAccessibilityService.typeText(text))
                    }
                    "pressButton" -> {
                        val button = call.argument<String>("button") ?: ""
                        result.success(NemoAccessibilityService.pressButton(button))
                    }
                    "getUiTree" -> result.success(NemoAccessibilityService.getUiTree())
                    "getForegroundPackage" ->
                        result.success(NemoAccessibilityService.getForegroundPackage())
                    "clickByText" -> {
                        val query = call.argument<String>("query") ?: ""
                        result.success(NemoAccessibilityService.clickByText(query))
                    }
                    "clickFirstResult" -> result.success(NemoAccessibilityService.clickFirstResult())
                    else -> result.notImplemented()
                }
            }

        // ── Intents / app launcher / alarms / contacts channel ─────────
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, CH_INTENTS)
            .setMethodCallHandler { call, result ->
                IntentActions.handle(this, call, result)
            }

        // ── Screenshot channel (MediaProjection) ───────────────────────
        screenshotChannel = MethodChannel(flutterEngine.dartExecutor.binaryMessenger, CH_SCREENSHOT)
        screenshotChannel!!.setMethodCallHandler { call, result ->
            when (call.method) {
                "capture" -> captureScreenshot(result)
                "hasPermission" -> result.success(mediaProjection != null)
                else -> result.notImplemented()
            }
        }

        // ── Remote control notification channel ────────────────────────
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, CH_NOTIFICATION)
            .setMethodCallHandler { call, result ->
                when (call.method) {
                    "showRemoteControl" -> {
                        showRemoteControlNotification()
                        result.success(true)
                    }
                    "hideRemoteControl" -> {
                        hideRemoteControlNotification()
                        result.success(true)
                    }
                    else -> result.notImplemented()
                }
            }

        // ── Voice player channel (continuous PCM16 playback) ───────────
        val voiceCh = MethodChannel(flutterEngine.dartExecutor.binaryMessenger, CH_VOICE)
        voiceCh.setMethodCallHandler { call, result ->
            when (call.method) {
                "start" -> {
                    voicePlayer.start(call.argument<Int>("sampleRate") ?: 24000)
                    result.success(true)
                }
                "write" -> {
                    val data = call.argument<ByteArray>("data")
                    if (data != null) voicePlayer.write(data)
                    result.success(true)
                }
                "flush" -> { voicePlayer.flush(); result.success(true) }
                "stop" -> { voicePlayer.stop(); result.success(true) }
                else -> result.notImplemented()
            }
        }
        // Push playback-starvation (choppy voice) events up to the Dart UI.
        voicePlayer.onUnderrun = { count ->
            runOnUiThread { voiceCh.invokeMethod("underrun", count) }
        }

        // ── Wake word channel (openWakeWord, on-device) ────────────────
        val wakeCh = MethodChannel(flutterEngine.dartExecutor.binaryMessenger, CH_WAKEWORD)
        val wake = WakeWordController(applicationContext, wakeCh) { r -> runOnUiThread(r) }
        wakeWord = wake
        wakeCh.setMethodCallHandler { call, result ->
            when (call.method) {
                "start" -> {
                    val model = call.argument<String>("model") ?: "hey_jarvis_v0.1.onnx"
                    val threshold = (call.argument<Double>("threshold") ?: 0.5).toFloat()
                    wake.start(model, threshold)
                    result.success(true)
                }
                "stop" -> { wake.stop(); result.success(true) }
                else -> result.notImplemented()
            }
        }
    }

    override fun onDestroy() {
        wakeWord?.stop()
        super.onDestroy()
    }

    // ── MediaProjection Screenshot ─────────────────────────────────────

    private fun captureScreenshot(result: MethodChannel.Result) {
        // One capture at a time: a second concurrent call would orphan the
        // first Result (hung Dart future) and double-use the projection.
        if (pendingScreenshotResult != null) {
            result.error("BUSY", "screenshot already in progress", null)
            return
        }
        if (mediaProjection != null) {
            doCapture(result)
            return
        }
        // Request permission — shows system dialog to user
        pendingScreenshotResult = result
        val mpManager = getSystemService(MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
        startActivityForResult(mpManager.createScreenCaptureIntent(), SCREENSHOT_REQUEST)
    }

    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == SCREENSHOT_REQUEST) {
            if (resultCode == Activity.RESULT_OK && data != null) {
                val mpManager = getSystemService(MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
                mediaProjection = mpManager.getMediaProjection(resultCode, data)
                pendingScreenshotResult?.let { doCapture(it) }
                pendingScreenshotResult = null
            } else {
                pendingScreenshotResult?.error("PERMISSION_DENIED", "MediaProjection denied", null)
                pendingScreenshotResult = null
            }
        }
    }

    private fun doCapture(result: MethodChannel.Result) {
        val wm = getSystemService(WINDOW_SERVICE) as WindowManager
        val metrics = DisplayMetrics()
        @Suppress("DEPRECATION")
        wm.defaultDisplay.getMetrics(metrics)
        val width = metrics.widthPixels
        val height = metrics.heightPixels
        val density = metrics.densityDpi

        val imageReader = ImageReader.newInstance(width, height, PixelFormat.RGBA_8888, 2)
        var vDisplay: VirtualDisplay? = null

        try {
            vDisplay = mediaProjection?.createVirtualDisplay(
                "NemoCapture", width, height, density,
                DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
                imageReader.surface, null, null
            )
        } catch (e: Exception) {
            result.error("CAPTURE_FAILED", "VirtualDisplay failed: ${e.message}", null)
            imageReader.close()
            mediaProjection?.stop()
            mediaProjection = null
            return
        }

        // Wait for first frame then grab it
        Handler(Looper.getMainLooper()).postDelayed({
            try {
                val image: Image? = imageReader.acquireLatestImage()
                if (image != null) {
                    val planes = image.planes
                    val buffer = planes[0].buffer
                    val pixelStride = planes[0].pixelStride
                    val rowStride = planes[0].rowStride
                    val rowPadding = rowStride - pixelStride * width

                    val bitmap = Bitmap.createBitmap(
                        width + rowPadding / pixelStride, height, Bitmap.Config.ARGB_8888
                    )
                    bitmap.copyPixelsFromBuffer(buffer)
                    image.close()

                    // Crop to actual screen width
                    val cropped = Bitmap.createBitmap(bitmap, 0, 0, width, height)
                    val baos = ByteArrayOutputStream()
                    cropped.compress(Bitmap.CompressFormat.JPEG, 80, baos)
                    result.success(baos.toByteArray())
                    cropped.recycle()
                    bitmap.recycle()
                } else {
                    result.error("NO_FRAME", "No frame captured — try again", null)
                }
            } catch (e: Exception) {
                result.error("CAPTURE_ERROR", e.message, null)
            } finally {
                vDisplay?.release()
                imageReader.close()
                // Android 14+ forbids reusing a MediaProjection for a second
                // VirtualDisplay — release it per capture or the next one
                // throws SecurityException.
                mediaProjection?.stop()
                mediaProjection = null
            }
        }, 400)
    }

    // ── Notification helpers ────────────────────────────────────────────

    private fun showRemoteControlNotification() {
        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as android.app.NotificationManager
        val channelId = "nemo_remote"
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val ch = android.app.NotificationChannel(
                channelId, "Nemo Control", android.app.NotificationManager.IMPORTANCE_LOW
            ).apply { description = "Nemo is controlling this device" }
            nm.createNotificationChannel(ch)
        }
        val n = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            android.app.Notification.Builder(this, channelId)
                .setContentTitle("Nemo Remote Control Active")
                .setContentText("Tap to manage")
                .setSmallIcon(android.R.drawable.ic_btn_speak_now)
                .setOngoing(true).build()
        } else {
            @Suppress("DEPRECATION")
            android.app.Notification.Builder(this)
                .setContentTitle("Nemo Remote Control Active")
                .setSmallIcon(android.R.drawable.ic_btn_speak_now)
                .setOngoing(true).build()
        }
        nm.notify(9001, n)
    }

    private fun hideRemoteControlNotification() {
        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as android.app.NotificationManager
        nm.cancel(9001)
    }
}
