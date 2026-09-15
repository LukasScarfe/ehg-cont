"""
DSP frequency-structure detectors for uterine contraction detection.

    zcr_burst      : windowed zero-crossing-rate of the fast-wave band
    wavelet_energy : relative DWT (SWT) energy in the contraction sub-bands

Both reduce a multichannel EHG/MMG recording to a 1-D frequency-structure
statistic and apply the shared robust-threshold event extractor.
"""
from __future__ import annotations
import numpy as np

from .base import BaseDetector, register
from ..config import EHG_BAND_FW, EHG_BAND_CONTRACTION, CANONICAL_FS
from ._dsp_common import (preprocess, fuse, robust_thr, smooth, is_degenerate,
                          EPS)


# --------------------------------------------------------------------------- #
# 3. Zero-crossing-rate burst detector
# --------------------------------------------------------------------------- #
@register
class ZCRDetector(BaseDetector):
    """Zero-crossing-rate (ZCR) contraction detector.

    Mirrors "Auto recognition of uterine contractions with EHG based on zero
    crossing rate": within the fast-wave band the ZCR of the signal shifts when
    a contraction burst appears (the burst concentrates power, changing the
    dominant frequency / crossing density relative to inter-contraction
    baseline). We track ZCR against its own robust baseline and threshold the
    deviation in the empirically dominant direction.

    Steps:
      1. Resample; band-pass the fast-wave band (0.34-1.0 Hz).
      2. Per channel, mark sign changes and count them in a sliding window
         (win_s) -> per-sample ZCR; fuse across channels.
      3. Optionally invert (contraction_lowers_zcr) so the "contraction" side is
         the high side of the statistic.
      4. Remove a slow baseline (running median over base_win_s) so only local
         ZCR excursions survive -> statistic.
      5. Robust threshold + run extraction. Strength = integrated |deviation|.
    """
    name = "zcr_burst"
    family = "dsp"
    hw_tier = "low"
    causal = False
    default_params = dict(fs_work=CANONICAL_FS, band=EHG_BAND_FW, win_s=20.0,
                          base_win_s=180.0, fuse="mean",
                          contraction_lowers_zcr=True, k=1.6,
                          min_dur_s=25.0, merge_gap_s=25.0)

    def detect(self, rec):
        p = self.params
        bp, fs = preprocess(rec, p["fs_work"], p["band"], self.causal)
        w = max(1, int(p["win_s"] * fs))
        kern = np.ones(w) / w
        zc = np.empty_like(bp)
        for c in range(bp.shape[0]):
            crossings = (np.diff(np.signbit(bp[c]).astype(int)) != 0).astype(float)
            crossings = np.concatenate([[0.0], crossings])
            zc[c] = np.convolve(crossings, kern, mode="same")
        z = fuse(zc, p["fuse"])
        if p["contraction_lowers_zcr"]:
            z = -z
        # subtract slow baseline so only local excursions remain
        base = _running_median(z, max(1, int(p["base_win_s"] * fs)))
        stat = z - base
        if is_degenerate(stat):
            return [], stat
        thr = robust_thr(stat, p["k"])
        events = self.events_from_statistic(
            stat, fs, thr, min_dur_s=p["min_dur_s"], merge_gap_s=p["merge_gap_s"],
            strength_from=np.abs(stat))
        return events, stat


def _running_median(x, w):
    """Cheap running median via percentile on a strided view fallback: uses a
    uniform-decimated median-of-blocks interpolation for speed on long signals."""
    n = len(x)
    if w <= 1 or n <= w:
        return np.full(n, np.median(x))
    hop = max(1, w // 4)
    centers, vals = [], []
    for a in range(0, n, hop):
        b = min(n, a + w)
        centers.append((a + b) // 2)
        vals.append(np.median(x[a:b]))
    centers = np.array(centers)
    vals = np.array(vals, float)
    return np.interp(np.arange(n), centers, vals)


# --------------------------------------------------------------------------- #
# 4. Wavelet relative-energy detector
# --------------------------------------------------------------------------- #
@register
class WaveletEnergyDetector(BaseDetector):
    """Discrete-wavelet relative-energy contraction detector.

    A stationary (undecimated) wavelet transform splits the signal into
    dyadic sub-bands at the recording's working rate. The detail levels whose
    pass-bands fall inside the contraction band (0.2-1.2 Hz) carry the burst;
    their share of the total wavelet energy rises during a contraction. Using
    RELATIVE energy makes the statistic robust to slow amplitude drift.

    Steps:
      1. Resample to fs_work; band-pass the wide EHG band to bound the transform.
      2. Stationary wavelet transform (pywt.swt, `wavelet`) to `level` scales
         (signal reflection-padded to a power-of-two length).
      3. For each detail level compute a smoothed instantaneous energy
         (coeff^2, moving average).
      4. Identify the levels whose nominal pass-band [fs/2^(j+1), fs/2^j]
         intersects the contraction band -> "burst" levels.
      5. Statistic = burst-level energy / total wavelet energy (relative energy),
         averaged across channels.
      6. Robust threshold + run extraction; strength = integrated burst energy.
    """
    name = "wavelet_energy"
    family = "dsp"
    hw_tier = "mid"
    causal = False
    default_params = dict(fs_work=CANONICAL_FS, band=(0.1, 3.0), wavelet="db4",
                          level=6, smooth_s=15.0, fuse="mean",
                          burst_band=EHG_BAND_CONTRACTION, k=1.8,
                          min_dur_s=25.0, merge_gap_s=25.0)

    def detect(self, rec):
        import pywt
        p = self.params
        bp, fs = preprocess(rec, p["fs_work"], p["band"], self.causal)
        n = bp.shape[-1]
        if n < 2 ** (p["level"] + 1):
            return [], np.zeros(n)
        # pad to a multiple of 2**level for swt
        L = p["level"]
        pad = (-n) % (2 ** L)
        rel_ch = []
        # which detail levels fall in the burst band
        burst_levels = []
        for j in range(1, L + 1):
            lo, hi = fs / 2 ** (j + 1), fs / 2 ** j
            if hi >= p["burst_band"][0] and lo <= p["burst_band"][1]:
                burst_levels.append(j)
        for c in range(bp.shape[0]):
            x = bp[c]
            if pad:
                x = np.pad(x, (0, pad), mode="reflect")
            coeffs = pywt.swt(x, p["wavelet"], level=L, trim_approx=False)
            # coeffs[i] = (cA, cD) with detail level = L - i
            energies = {}
            total = np.zeros_like(x)
            for i, (_cA, cD) in enumerate(coeffs):
                lvl = L - i
                e = smooth(cD ** 2, fs, p["smooth_s"])
                energies[lvl] = e
                total = total + e
            burst = np.zeros_like(x)
            for lvl in burst_levels:
                if lvl in energies:
                    burst = burst + energies[lvl]
            # Relative energy against BOTH the instantaneous total AND the channel's
            # own temporal baseline. Normalising burst energy by its running median
            # makes the statistic rise clearly during bursts (a pure burst/total
            # ratio saturates near 1 once the signal is pre-band-limited, giving no
            # threshold contrast — the cause of the earlier zero-detection failure).
            frac = burst / (total + EPS)
            rel = frac * (burst / (np.median(burst) + EPS))
            rel_ch.append(rel[:n])
        m = fuse(np.vstack(rel_ch), p["fuse"])
        if is_degenerate(m):
            return [], m
        thr = robust_thr(m, p["k"])
        events = self.events_from_statistic(
            m, fs, thr, min_dur_s=p["min_dur_s"], merge_gap_s=p["merge_gap_s"],
            strength_from=m)
        return events, m
