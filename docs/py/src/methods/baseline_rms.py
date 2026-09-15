"""
Reference baseline: band-pass RMS envelope + adaptive threshold.

Steps (fully specified, reproducible):
  1. Resample each channel to `fs_work` (default 20 Hz).
  2. Band-pass 0.2-1.2 Hz (contraction band), zero-phase (offline) or causal.
  3. For multichannel input, form the mean RMS envelope across channels
     (win = `rms_win_s`).
  4. Adaptive threshold: thr = median(env) + k * MAD(env)   (robust baseline).
  5. Threshold-crossing runs -> events (min duration, gap-merge).
  6. Strength = integrated envelope energy over the event.

This is the sanity-check method and the floor other methods must beat.
"""
from __future__ import annotations
import numpy as np

from .base import BaseDetector, register
from ..preprocessing.filters import bandpass, bandpass_causal, resample_to, envelope
from ..config import EHG_BAND_CONTRACTION, CANONICAL_FS


@register
class BandpassRMS(BaseDetector):
    name = "bandpass_rms"
    family = "dsp"
    hw_tier = "low"
    causal = False
    default_params = dict(fs_work=CANONICAL_FS, band=EHG_BAND_CONTRACTION,
                          rms_win_s=20.0, k=3.0, min_dur_s=30.0, merge_gap_s=25.0)

    def detect(self, rec):
        p = self.params
        x = rec.signals
        x, fs = resample_to(x, rec.fs, p["fs_work"])
        bp = (bandpass_causal if self.causal else bandpass)(x, p["band"], fs, order=4)
        env = envelope(bp, fs, method="rms", win_s=p["rms_win_s"])  # (ch, n)
        env = np.atleast_2d(env)
        m = env.mean(axis=0)                    # channel-averaged envelope
        med = np.median(m)
        mad = np.median(np.abs(m - med)) + 1e-12
        thr = med + p["k"] * 1.4826 * mad
        events = self.events_from_statistic(
            m, fs, thr, min_dur_s=p["min_dur_s"], merge_gap_s=p["merge_gap_s"],
            strength_from=m)
        return events, m
