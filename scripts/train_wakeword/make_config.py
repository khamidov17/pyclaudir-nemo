#!/usr/bin/env python3
"""Generate an openWakeWord training config for the "hey nemo" wake word.

openWakeWord's automatic training notebook
(github.com/dscripka/openWakeWord → notebooks/automatic_model_training.ipynb)
consumes a YAML config describing the target phrase and training budget. This
writes a sensible one for "hey nemo" so you don't hand-craft it.

The GPU training itself runs in Colab (see README.md); this step is local and
deterministic — it only emits the config.

Usage:
    python3 make_config.py [--out hey_nemo.yaml]
"""

from __future__ import annotations

import argparse

# Pronunciation variants Piper TTS will synthesize as positives. More, varied
# spellings/spacings = a model that catches how the phrase is really said.
TARGET_PHRASES = [
    "hey nemo",
    "hey, nemo",
    "hey neemo",
    "hey nimo",
    "ey nemo",
]

# A compact, readable YAML writer so this script has NO third-party deps (the
# heavy libs only need to exist in Colab, not on your Mac).
def _yaml(config: dict) -> str:
    lines: list[str] = []

    def emit(key: str, val, indent: int = 0) -> None:
        pad = "  " * indent
        if isinstance(val, list):
            lines.append(f"{pad}{key}:")
            for item in val:
                lines.append(f"{pad}  - {item}")
        elif isinstance(val, bool):
            lines.append(f"{pad}{key}: {str(val).lower()}")
        elif val is None:
            lines.append(f"{pad}{key}: null")
        else:
            lines.append(f"{pad}{key}: {val}")

    for k, v in config.items():
        emit(k, v)
    return "\n".join(lines) + "\n"


def build_config() -> dict:
    return {
        "model_name": "hey_nemo",
        # The phrase(s) Piper synthesizes as positive examples.
        "target_phrase": TARGET_PHRASES,
        # Synthetic-sample budget. ~30-50k positives is plenty for one phrase;
        # bump if recall is weak, lower to train faster while iterating.
        "n_samples": 30000,
        "n_samples_val": 2000,
        # Standard openWakeWord augmentation: room impulse responses + noise so
        # the model is robust to real rooms, not just clean TTS.
        "augmentation_rounds": 1,
        "augmentation_batch_size": 16,
        # Output a phone-compatible ONNX (matches the existing assets) — NOT just
        # tflite. The app loads .onnx via xyz.rementia:openwakeword.
        "model_type": "onnx",
        "output_dir": "./hey_nemo_out",
        # Leave these to the notebook's downloaded datasets; the placeholders are
        # filled in by the Colab notebook (negative/background clips, validation
        # negatives, room impulse responses).
        "background_paths": ["FILL_IN_negative_background_clips"],
        "false_positive_validation_data_path": "FILL_IN_validation_negatives",
        "rir_paths": ["FILL_IN_room_impulse_responses"],
        # Target operating point. Train for high recall; you set the runtime
        # threshold in wake_word_service.dart afterward (see README step 5).
        "target_accuracy": 0.7,
        "target_recall": 0.5,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="hey_nemo.yaml", help="output YAML path")
    args = ap.parse_args()

    text = _yaml(build_config())
    with open(args.out, "w") as f:
        f.write(text)
    print(f"Wrote {args.out} for phrases: {', '.join(TARGET_PHRASES)}")
    print("Next: open openWakeWord's automatic_model_training.ipynb in Colab")
    print("(GPU runtime), point its config at this file, and run. See README.md.")


if __name__ == "__main__":
    main()
