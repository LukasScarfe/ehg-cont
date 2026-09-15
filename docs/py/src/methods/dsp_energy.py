"""
DSP energy-envelope detectors for uterine contraction detection.

Contains four @register-ed BaseDetector subclasses that all share the
resample -> band-pass -> per-channel statistic -> fuse -> threshold pipeline
(helpers in `_dsp_common`) but differ in the detection statistic:

    tkeo_energy         : Teager-Kaiser energy operator (amplitude*frequency)
    slope_hysteresis    : envelope double-threshold (EMG-onset style) w/ hysteresis
    nonlinear_entropy   : nonlinear energy gated by falling sample entropy
    kalman_envelope     : Kalman-smoothed RMS envelope (vs plain RMS)

Each detector returns (events, per-sample score) as required by the harness.
"""
from __future__ import annotations
import numpy as np

from .base import BaseDetector, register
from ..preprocessing.filters import teager_kaiser
from ..features.features import sample_entropy
from ..config import EHG_BAND_CONTRACTION, EHG_BAND_FW, CANONICAL_FS
from ._dsp_common import (preprocess, channel_envelope, fuse, robust_thr,
                          smooth, is_degenerate, kalman_smooth, trapz, EPS)


# --------------------------------------------------------------------------- #
# 1. Teager-Kaiser energy operator detector
# --------------------------------------------------------------------------- #
@register
class TKEODetector(BaseDetector):
    """Teager-Kaiser energy operator (TKEO) contraction detector.

    TKEO psi[n] = x[n]^2 - x[n-1]x[n+1] tracks instantaneous energy proportional
    to amplitude^2 * frequency^2, so it reacts to the onset of fast-wave bursts
    faster than an amplitude-only RMS envelope -> aimed at earliest onset.

    Steps:
      1. Resample all channels to fs_work; band-pass the fast-wave band
         (0.34-1.0 Hz) where contraction bursts concentrate.
      2. Per channel, apply the discrete TKEO and half-wave rectify (clip<0 ->0).
      3. Smooth each channel's TKEO with a moving-average envelope (win_s).
      4. Fuse channels (default mean) into a 1-D detection statistic.
      5. Adaptive robust threshold thr = median + k*1.4826*MAD.
      6. Threshold-crossing runs -> events (min duration, gap-merge); strength =
         integrated TKEO envelope over the event.
    """
    name = "tkeo_energy"
    family = "dsp"
    hw_tier = "low"
    causal = False
    default_params = dict(fs_work=CANONICAL_FS, band=EHG_BAND_FW, win_s=12.0,
                          fuse="mean", k=2.2, min_dur_s=25.0, merge_gap_s=25.0)

    def detect(self, rec):
        p = self.params
        bp, fs = preprocess(rec, p["fs_work"], p["band"], self.causal)
        psi = np.clip(teager_kaiser(bp), 0, None)              # (C, n)
        env = smooth(psi, fs, p["win_s"])
        m = fuse(env, p["fuse"])
        if is_degenerate(m):
            return [], m
        thr = robust_thr(m, p["k"])
        events = self.events_from_statistic(
            m, fs, thr, min_dur_s=p["min_dur_s"], merge_gap_s=p["merge_gap_s"],
            strength_from=m)
        return events, m


# --------------------------------------------------------------------------- #
# 2. Slope / derivative double-threshold (hysteresis) detector
# --------------------------------------------------------------------------- #
@register
class SlopeHysteresisDetector(BaseDetector):
    """Envelope double-threshold detector with hysteresis (EMG-onset style).

    Classic EMG onset detection uses two thresholds: a low threshold marks the
    *start* of activity (early onset), while a high threshold *confirms* the
    burst is real (reject noise). The envelope must rise (positive slope) into
    the low threshold, so brief dips do not re-trigger. Adapted to EHG here.

    Steps:
      1. Resample; band-pass the contraction band (0.2-1.2 Hz).
      2. RMS envelope per channel, fuse to a 1-D envelope m.
      3. Smooth m and compute its slope (derivative) to require rising activity.
      4. Set a low gate thr_lo = median + k_lo*MAD and a high gate
         thr_hi = median + k_hi*MAD.
      5. Hysteresis extraction: a candidate region opens at the first sample
         where m>=thr_lo (onset, deliberately early) and closes when m<thr_lo;
         the region is KEPT only if m exceeds thr_hi somewhere inside it AND the
         entry slope was positive (confirmation) -> controls false positives.
      6. Apply min-duration and gap-merge; strength = integrated envelope.
    """
    name = "slope_hysteresis"
    family = "dsp"
    hw_tier = "low"
    causal = False
    default_params = dict(fs_work=CANONICAL_FS, band=EHG_BAND_CONTRACTION,
                          rms_win_s=18.0, fuse="mean", k_lo=1.0, k_hi=3.0,
                          slope_win_s=10.0, min_dur_s=25.0, merge_gap_s=25.0)

    def detect(self, rec):
        p = self.params
        bp, fs = preprocess(rec, p["fs_work"], p["band"], self.causal)
        env = channel_envelope(bp, fs, method="rms", win_s=p["rms_win_s"])
        m = smooth(fuse(env, p["fuse"]), fs, 3.0)
        if is_degenerate(m):
            return [], m
        sl = np.gradient(smooth(m, fs, p["slope_win_s"]))       # envelope slope
        thr_lo = robust_thr(m, p["k_lo"])
        thr_hi = robust_thr(m, p["k_hi"])
        events = self._hysteresis(m, sl, fs, thr_lo, thr_hi, p)
        return events, m

    def _hysteresis(self, m, sl, fs, thr_lo, thr_hi, p):
        from ..evaluation.metrics import DetectedEvent
        n = len(m)
        min_dur = int(p["min_dur_s"] * fs)
        gap = int(p["merge_gap_s"] * fs)
        above = m >= thr_lo
        runs, i = [], 0
        while i < n:
            if above[i]:
                j = i
                while j < n and above[j]:
                    j += 1
                # confirmation: must reach the high gate, and rise into the region
                seg = m[i:j]
                entry_slope = sl[i:min(n, i + max(1, int(2 * fs)))].mean()
                if seg.max() >= thr_hi and entry_slope >= 0:
                    runs.append([i, j])
                i = j
            else:
                i += 1
        merged = []
        for r in runs:
            if merged and r[0] - merged[-1][1] <= gap:
                merged[-1][1] = r[1]
            else:
                merged.append(r)
        events = []
        for a, b in merged:
            if b - a < min_dur:
                continue
            seg = m[a:b]
            events.append(DetectedEvent(
                onset=a / fs, offset=b / fs,
                peak=(a + int(np.argmax(seg))) / fs,
                score=float(seg.max()),
                strength=float(trapz(np.abs(seg)) / fs)))
        return events


# --------------------------------------------------------------------------- #
# 5. Nonlinear-energy gated by sample entropy
# --------------------------------------------------------------------------- #
@register
class NonlinearEntropyDetector(BaseDetector):
    """Nonlinear-energy stream gated by a fall in windowed sample entropy.

    Contraction bursts are both more energetic (nonlinear/TKEO energy up) and
    more *organized* / periodic than baseline noise (sample entropy down). The
    detection statistic multiplies rising energy by falling entropy so both cues
    must agree, which suppresses high-energy but disorganized artifacts.

    Steps:
      1. Resample; band-pass the contraction band.
      2. Nonlinear energy = smoothed, rectified TKEO, fused across channels.
      3. Windowed sample entropy of the fused band-passed signal (window
         `se_win_s`, hop `se_hop_s`), linearly interpolated to per-sample; low
         entropy = organized activity.
      4. Convert entropy to an "organization" score org = zscore(max-entropy).
      5. Statistic s = zscore(nonlinear_energy) + w_ent * org  (both must rise).
      6. Robust threshold + run extraction; strength = integrated nonlinear
         energy over the event.
    """
    name = "nonlinear_entropy"
    family = "dsp"
    hw_tier = "mid"
    causal = False
    default_params = dict(fs_work=CANONICAL_FS, band=EHG_BAND_CONTRACTION,
                          ne_win_s=12.0, fuse="mean", se_win_s=30.0, se_hop_s=15.0,
                          w_ent=0.7, k=1.3, min_dur_s=25.0, merge_gap_s=25.0)

    def detect(self, rec):
        p = self.params
        bp, fs = preprocess(rec, p["fs_work"], p["band"], self.causal)
        # nonlinear energy
        ne = smooth(np.clip(teager_kaiser(bp), 0, None), fs, p["ne_win_s"])
        ne = fuse(ne, p["fuse"])
        # fused band signal for entropy (channel average of the band-passed signal)
        sig = bp.mean(axis=0)
        org = self._entropy_org(sig, fs, p["se_win_s"], p["se_hop_s"])
        if is_degenerate(ne):
            return [], ne
        s = _z(ne) + p["w_ent"] * org
        thr = robust_thr(s, p["k"])
        events = self.events_from_statistic(
            s, fs, thr, min_dur_s=p["min_dur_s"], merge_gap_s=p["merge_gap_s"],
            strength_from=ne)
        return events, s

    @staticmethod
    def _entropy_org(sig, fs, win_s, hop_s):
        n = len(sig)
        w = max(8, int(win_s * fs))
        hop = max(1, int(hop_s * fs))
        if n < w:
            return np.zeros(n)
        centers, vals = [], []
        for a in range(0, n - w + 1, hop):
            # decimate the window 4x before sample entropy: SampEn is O(N^2), and the
            # organization cue survives coarse sampling, so this is ~16x faster with
            # negligible effect on the detection statistic.
            se = sample_entropy(sig[a:a + w][::4])
            centers.append(a + w // 2)
            vals.append(se)
        centers = np.array(centers)
        vals = np.array(vals, float)
        if len(vals) < 2 or np.ptp(vals) < EPS:
            return np.zeros(n)
        full = np.interp(np.arange(n), centers, vals)
        # organization = z-score of (max - entropy): high when entropy is low
        return _z(np.max(full) - full)


def _z(x):
    x = np.asarray(x, float)
    s = np.std(x)
    return (x - np.mean(x)) / (s + EPS)


# --------------------------------------------------------------------------- #
# 8. Kalman-smoothed envelope detector
# --------------------------------------------------------------------------- #
@register
class KalmanEnvelopeDetector(BaseDetector):
    """Kalman-smoothed energy envelope + adaptive threshold.

    A scalar random-walk Kalman filter smooths the RMS energy envelope before
    thresholding. Compared to a fixed moving-average (baseline RMS), the Kalman
    gain adapts: it responds quickly to a real rise (preserving onset timing)
    yet averages out measurement noise in the plateau, giving a cleaner statistic
    and fewer threshold-chatter false positives. Fully causal.

    Steps:
      1. Resample; band-pass the contraction band.
      2. Short-window RMS envelope per channel, fuse to a 1-D measurement stream.
      3. Kalman filter the fused envelope (process/measurement variance ratio
         q_ratio sets smoothing strength) -> denoised envelope.
      4. Adaptive robust threshold on the Kalman envelope.
      5. Run extraction; strength = integrated Kalman envelope over the event.
    """
    name = "kalman_envelope"
    family = "dsp"
    hw_tier = "low"
    causal = True
    default_params = dict(fs_work=CANONICAL_FS, band=EHG_BAND_CONTRACTION,
                          rms_win_s=8.0, fuse="mean", q_ratio=0.01, k=2.5,
                          min_dur_s=25.0, merge_gap_s=25.0)

    def detect(self, rec):
        p = self.params
        bp, fs = preprocess(rec, p["fs_work"], p["band"], self.causal)
        env = channel_envelope(bp, fs, method="rms", win_s=p["rms_win_s"])
        m0 = fuse(env, p["fuse"])
        if is_degenerate(m0):
            return [], m0
        m = kalman_smooth(m0, q_ratio=p["q_ratio"])
        thr = robust_thr(m, p["k"])
        events = self.events_from_statistic(
            m, fs, thr, min_dur_s=p["min_dur_s"], merge_gap_s=p["merge_gap_s"],
            strength_from=m)
        return events, m
