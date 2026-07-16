"""decoder.py — Decode CW (Morse code) audio back to text.

Uses DSP (bandpass filter + envelope detection + Otsu thresholding) and
KMeans clustering to distinguish dots from dashes, then maps the pattern
sequence back to characters via the standard Morse code dictionary.
"""

import numpy as np
from scipy.io import wavfile
from scipy.signal import butter, sosfiltfilt
from scipy.ndimage import binary_closing, binary_opening, uniform_filter1d
from sklearn.cluster import KMeans
from spellchecker import SpellChecker

from utils import MORSE_CODE_DICT


_SPELL = SpellChecker()


def _otsu_threshold(values, bins=256):
    """Histogram-based Otsu threshold (same algorithm skimage.filters.threshold_otsu
    uses internally), implemented directly on top of numpy so the decoder
    doesn't need scikit-image just for this one function."""
    hist, bin_edges = np.histogram(values, bins=bins)
    bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2.0

    weight1 = np.cumsum(hist)
    weight2 = np.cumsum(hist[::-1])[::-1]

    # avoid div-by-zero for empty cumulative buckets at the ends
    with np.errstate(invalid="ignore", divide="ignore"):
        mean1 = np.cumsum(hist * bin_centers) / weight1
        mean2 = (np.cumsum((hist * bin_centers)[::-1])[::-1]) / weight2

    variance12 = weight1[:-1] * weight2[1:] * (mean1[:-1] - mean2[1:]) ** 2
    idx = np.nanargmax(variance12)
    return bin_centers[idx]


class CWDecoder:
    def __init__(self, bandwidth=200, min_element_ms=8):
        self.bandwidth = bandwidth      # bandpass half-width around the tone, Hz
        self.min_element_ms = min_element_ms  # ignore blips shorter than this

    def decode(self, filepath, debug=False, correct_spelling=False):
        sr, audio = self._load(filepath)
        tone = self._detect_tone(sr, audio)
        filtered = self._bandpass(sr, audio, tone)
        env = self._envelope(sr, filtered)
        mask = env > _otsu_threshold(env)         # adapts to noise/fading automatically
        mask = self._clean(sr, mask)
        runs = self._runs(mask)
        text, unit = self._runs_to_text(sr, runs)

        if debug:
            if unit:
                print(f"[debug] tone: {tone:.0f} Hz, unit: {unit*1000:.1f} ms")
            else:
                print("[debug] no signal found")
            print(f"[debug] raw decode: {text!r}")

        if correct_spelling:
            text = self._spellfix(text)
        return text

    # ---- audio -> envelope ----

    def _load(self, filepath):
        sr, audio = wavfile.read(filepath)
        if audio.ndim > 1:              # collapse to mono if needed
            audio = audio.mean(axis=1)
        if np.issubdtype(audio.dtype, np.integer):
            audio = audio.astype(np.float64) / np.iinfo(audio.dtype).max
        else:
            audio = audio.astype(np.float64)
        return sr, audio

    def _detect_tone(self, sr, audio):
        spectrum = np.abs(np.fft.rfft(audio * np.hanning(len(audio))))
        freqs = np.fft.rfftfreq(len(audio), d=1 / sr)
        mask = freqs > 100  # skip DC / hum
        return freqs[mask][np.argmax(spectrum[mask])]

    def _bandpass(self, sr, audio, tone):
        low = max(1, tone - self.bandwidth)
        high = min(sr / 2 - 1, tone + self.bandwidth)
        sos = butter(4, [low, high], btype="bandpass", fs=sr, output="sos")
        return sosfiltfilt(sos, audio)

    def _envelope(self, sr, filtered):
        frame = max(1, int(sr * 0.006))  # 6 ms sliding window
        return uniform_filter1d(np.abs(filtered), size=frame)

    # ---- mask -> timed elements ----

    def _clean(self, sr, mask):
        size = max(1, int(sr * self.min_element_ms / 1000))
        struct = np.ones(size, dtype=bool)
        mask = binary_closing(mask, structure=struct)  # bridge dropouts
        mask = binary_opening(mask, structure=struct)  # drop noise blips
        return mask

    @staticmethod
    def _runs(mask):
        if len(mask) == 0:
            return []
        edges = np.flatnonzero(np.diff(mask.astype(int))) + 1
        starts = np.concatenate(([0], edges))
        ends = np.concatenate((edges, [len(mask)]))
        return [(bool(mask[s]), e - s) for s, e in zip(starts, ends)]

    def _runs_to_text(self, sr, runs):
        on_durs = np.array([n / sr for v, n in runs if v])
        if len(on_durs) < 2:
            return "", None

        # learn the dot length straight from the audio via KMeans
        k = 2 if len(set(np.round(on_durs, 3))) > 1 else 1
        km = KMeans(n_clusters=k, n_init=4, random_state=0).fit(on_durs.reshape(-1, 1))
        unit = float(np.sort(km.cluster_centers_.ravel())[0])

        letters, word = [], []
        for value, length in runs:
            dur = length / sr
            if value:
                word.append('-' if dur > unit * 2 else '.')
            elif dur > unit * 5:
                letters.append(''.join(word)); word = []
                letters.append(' WORD_BREAK ')
            elif dur > unit * 2:
                letters.append(''.join(word)); word = []
        if word:
            letters.append(''.join(word))
        _PATTERN_TO_CHAR = {pattern: letter for letter, pattern in MORSE_CODE_DICT.items()}

        chars = [_PATTERN_TO_CHAR.get(l, '#') if l != ' WORD_BREAK ' else ' ' for l in letters]
        return ''.join(chars), unit

    @staticmethod
    def _spellfix(text):
        """Fixes garbled letters within a word. Cannot recover a word that
        got split apart because a fully-dropped element was misread as a
        letter/word gap -- the audio no longer contains that information."""
        if _SPELL is None:
            return text
        fixed = []
        for w in text.split(' '):
            clean = w.replace('#', '')
            correction = _SPELL.correction(clean) if clean else None
            fixed.append(correction.upper() if correction else w)
        return ' '.join(fixed)


def decode_morse(filepath, debug=False, correct_spelling=False):
    return CWDecoder().decode(filepath, debug=debug, correct_spelling=correct_spelling)