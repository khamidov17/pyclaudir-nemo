# Training the "hey nemo" wake word

The app ships a **placeholder** wake word — `hey_jarvis_v0.1.onnx` — so today you
must say **"hey jarvis"**, not "hey nemo", to wake the assistant
(`nemo-app/lib/services/wake_word_service.dart`). This folder trains a custom
**"hey nemo"** model that drops straight into the app.

## Why it's drop-in

The app uses **openWakeWord** (`xyz.rementia:openwakeword:0.1.5`,
`WakeWordController.kt`). openWakeWord splits a wake word into three pieces:

| Asset (already in `nemo-app/.../assets/`) | Role | Changes per phrase? |
|---|---|---|
| `melspectrogram.onnx` | audio → mel features | no (shared) |
| `embedding_model.onnx` | features → embeddings | no (shared) |
| `hey_jarvis_v0.1.onnx` | embeddings → "is this the phrase?" | **yes — this is what we retrain** |

Only the last, tiny classifier is phrase-specific. Training a `hey_nemo.onnx`
that uses the same two shared models means it loads with **zero code changes**
beyond swapping one filename.

## Steps (the GPU training runs in Colab — ~1 hour)

Training needs a GPU and generates thousands of synthetic utterances, so it runs
in Google Colab, not on your Mac. This folder prepares the inputs and tells you
exactly what to run.

1. **Generate the training config** (locally):

   ```bash
   cd scripts/train_wakeword
   python3 make_config.py            # writes hey_nemo.yaml
   ```

   Edit `hey_nemo.yaml` if you want extra pronunciations (e.g. "hey nemo",
   "hey, nemo", "hey neemo") — more variants = more robust detection.

2. **Open openWakeWord's automatic training notebook** in Colab (GPU runtime):

   <https://github.com/dscripka/openWakeWord> →
   `notebooks/automatic_model_training.ipynb`

   It will:
   - synthesize positive samples for the phrase with Piper TTS (many voices),
   - mix in openWakeWord's negative/background + noise/RIR augmentation,
   - train the small classifier on top of the shared feature models,
   - export `hey_nemo.onnx`.

   Upload `hey_nemo.yaml` and point the notebook's `config` at it (or paste its
   fields into the notebook's config cell).

3. **Download `hey_nemo.onnx`** and drop it into the app:

   ```
   nemo-app/android/app/src/main/assets/hey_nemo.onnx
   ```

4. **Point the app at it** — change ONE constant in
   `nemo-app/lib/services/wake_word_service.dart`:

   ```dart
   static const _model = 'hey_nemo.onnx';   // was 'hey_jarvis_v0.1.onnx'
   ```

5. **Tune the threshold.** The placeholder peaks ~0.39 for the owner's voice, so
   `_threshold = 0.3`. A purpose-trained model usually scores higher and more
   separably; start at `0.5` and adjust:
   - misses your "hey nemo" → lower it (0.4, 0.35);
   - fires on random speech → raise it (0.6, 0.7).

6. **Rebuild + enable.** `scripts/build_apk.sh`, install, and turn the wake word
   ON in Settings (it's OFF by default).

## Validating before you ship

Use openWakeWord's Python API on a laptop to sanity-check the model on a few of
your own "hey nemo" recordings vs. random speech BEFORE rebuilding the app:

```bash
pip install openwakeword
python3 -c "
import openwakeword, numpy as np, wave
m = openwakeword.Model(wakeword_models=['hey_nemo.onnx'])
# feed 16kHz mono PCM frames from a wav of you saying 'hey nemo' and print scores
"
```

You want clear separation: high scores on "hey nemo", near-zero on everything
else. If not, add more pronunciation variants in `hey_nemo.yaml` and retrain.
