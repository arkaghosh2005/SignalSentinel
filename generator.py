"""generator.py — Synthesize realistic CW (Morse code) audio with configurable
impairments such as noise, timing jitter, fading, and packet loss.

Used by SignalSentinel to generate test signals that simulate real
over-the-air conditions for the decoder pipeline.
"""

import os
import time

import numpy as np
from scipy.io.wavfile import write

from utils import MORSE_CODE_DICT


class MorseCodeGenerator:
    """Render a text string as a CW audio waveform with optional channel
    impairments (noise, jitter, fading, packet loss)."""

    def __init__(
        self,
        sample_rate=8000,
        pitch=600,
        wpm=20,
        amplitude=0.8,
        noise_power=0.02,
        timing_jitter=0.15,
        packet_loss=0.0,
        fading=0.0,
        fading_rate=0.5,
        output_dir=".",
        freq_mhz=None,
    ):
        self.sample_rate = sample_rate
        self.pitch = pitch
        self.wpm = wpm
        self.amplitude = amplitude
        self.noise_power = noise_power
        self.timing_jitter = timing_jitter
        self.packet_loss = packet_loss
        self.fading = fading
        self.fading_rate = fading_rate
        self.output_dir = output_dir
        self.freq_mhz = freq_mhz

    def generate(self, text: str) -> str:
        """Generate a CW WAV file for *text* and return the absolute path."""
        dot = int((60 / self.wpm) / 50 * self.sample_rate)

        def duration(mult):
            scale = np.clip(np.random.normal(1.0, self.timing_jitter), 0.5, 2.0)
            return max(1, int(mult * dot * scale))

        signal = []
        words = text.upper().split()

        for w_idx, word in enumerate(words):
            for c_idx, ch in enumerate(word):
                if ch not in MORSE_CODE_DICT:
                    continue

                pattern = MORSE_CODE_DICT[ch]
                for s_idx, symbol in enumerate(pattern):
                    length = duration(3 if symbol == "-" else 1)

                    if np.random.rand() < self.packet_loss:
                        signal.append(np.zeros(length))
                    else:
                        signal.append(np.ones(length))

                    if s_idx < len(pattern) - 1:
                        signal.append(np.zeros(dot))

                if c_idx < len(word) - 1:
                    signal.append(np.zeros(3 * dot))

            if w_idx < len(words) - 1:
                signal.append(np.zeros(7 * dot))

        envelope = np.concatenate(signal) if signal else np.zeros(1)

        t = np.arange(len(envelope)) / self.sample_rate
        waveform = np.sin(2 * np.pi * self.pitch * t) * envelope

        if self.fading > 0:
            fade = 1 - self.fading + self.fading * (
                0.5 * (1 + np.sin(2 * np.pi * self.fading_rate * t + np.random.rand() * 2 * np.pi))
            )
            waveform *= fade

        waveform += np.random.normal(0, self.noise_power, len(waveform))
        waveform *= self.amplitude
        waveform = np.clip(waveform, -1, 1)

        # output_dir is expected to be an absolute path
        os.makedirs(self.output_dir, exist_ok=True)
        if self.freq_mhz:
            freq_str = str(self.freq_mhz).replace(" ", "")
            filename = f"{text[:8].replace(' ', '_')}_{freq_str}_{int(time.time())}.wav"
        else:
            filename = f"{text[:8].replace(' ', '_')}_{int(time.time())}.wav"
        filepath = os.path.join(self.output_dir, filename)
        write(filepath, self.sample_rate, (waveform * 32767).astype(np.int16))

        return filepath


def generate_morse(
    text,
    sample_rate=8000,
    pitch=600,
    wpm=20,
    amplitude=0.8,
    noise_power=0.02,
    timing_jitter=0.15,
    packet_loss=0.0,
    fading=0.0,
    fading_rate=0.5,
    output_dir=".",
    freq_mhz=None,
):
    """Convenience wrapper around MorseCodeGenerator."""
    generator = MorseCodeGenerator(
        sample_rate=sample_rate,
        pitch=pitch,
        wpm=wpm,
        amplitude=amplitude,
        noise_power=noise_power,
        timing_jitter=timing_jitter,
        packet_loss=packet_loss,
        fading=fading,
        fading_rate=fading_rate,
        output_dir=output_dir,
        freq_mhz=freq_mhz,
    )
    return generator.generate(text)


if __name__ == "__main__":
    path = generate_morse(
        "CQ CQ CQ DE VU2NCS VU2NCS K",
        amplitude=0.8,
        noise_power=0.2,
        output_dir="incoming_cw",
    )
    print(path)