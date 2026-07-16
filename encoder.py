"""encoder.py — Render plain text as a clean CW (Morse code) WAV file.

Used by SignalSentinel to generate outgoing reply/relay transmissions.
Unlike generator.py (which adds noise/jitter to simulate real conditions),
encoder.py produces a clean, ideal waveform suitable for transmission.
"""

from pathlib import Path

import numpy as np
from scipy.io import wavfile

from utils import MORSE_CODE_DICT

CW_WPM = 20
CW_TONE_HZ = 700
CW_SAMPLE_RATE = 8000


def text_to_morse_wav(
    text: str,
    output_path: Path,
    wpm: int = CW_WPM,
    tone_hz: int = CW_TONE_HZ,
    sample_rate: int = CW_SAMPLE_RATE,
) -> Path:
    """Render *text* as a keyed CW tone and write it to a WAV file.

    Standard PARIS timing: unit length (seconds) = 1.2 / wpm.
    dot = 1 unit, dash = 3 units, intra-character gap = 1 unit,
    inter-character gap = 3 units, inter-word gap = 7 units.
    """
    unit = 1.2 / wpm

    def tone(duration_s: float) -> np.ndarray:
        t = np.linspace(0, duration_s, int(sample_rate * duration_s), endpoint=False)
        envelope = np.ones_like(t)
        ramp_len = max(1, int(0.005 * sample_rate))
        envelope[:ramp_len] = np.linspace(0, 1, ramp_len)
        envelope[-ramp_len:] = np.linspace(1, 0, ramp_len)
        return 0.8 * envelope * np.sin(2 * np.pi * tone_hz * t)

    def silence(duration_s: float) -> np.ndarray:
        return np.zeros(int(sample_rate * duration_s))

    chunks = []
    words = text.upper().split()
    for w_idx, word in enumerate(words):
        for c_idx, char in enumerate(word):
            pattern = MORSE_CODE_DICT.get(char)
            if pattern is None:
                continue
            for i, symbol in enumerate(pattern):
                chunks.append(tone(unit if symbol == "." else 3 * unit))
                if i < len(pattern) - 1:
                    chunks.append(silence(unit))

            if c_idx < len(word) - 1:
                chunks.append(silence(3 * unit))  # inter-character gap

        if w_idx < len(words) - 1:
            chunks.append(silence(7 * unit))  # inter-word gap

    audio = np.concatenate(chunks) if chunks else np.zeros(1)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    wavfile.write(output_path, sample_rate, (audio * 32767).astype("int16"))
    return output_path