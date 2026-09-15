"""
Normalized power-spectrum / spectral-moment tracker (physio family).

Physiological rationale
-----------------------
During a contraction the myometrium fires faster, more-synchronised action
potentials, so EHG spectral content shifts UP: the fast-wave (0.34-1.0 Hz)
component grows relative to the slow wave and the median / peak frequency of the
normalised power spectrum rises. Tracking the spectral moments (rather than raw
amplitude alone) makes the detector robust to low-frequency baseline/motion
artifacts, which are energetic but spectrally low — they do not raise the median
frequency. We flag intervals where an elevated band energy *coincides* with a
sustained upward shift of the median frequency.

Numbered algorithm
------------------
 1. Resample to ``fs_work`` (20 Hz); zero-phase band-pass to the wide EHG band
    (0.1-3 Hz) so the whole slow+fast spectrum is present.
 2. Slide a window (``win_s`` s, hop ``step_s`` s).
 3. Per window: Welch PSD (normalised to unit total power over the analysis
    band); compute median frequency, peak frequency, and contraction-band
    (0.2-1.2 Hz) energy fraction, plus physical in-band RMS energy.
 4. Establish a robust per-recording baseline median frequency (its median over
    all windows) and express each window's median-frequency shift in MAD units,
    clipped at >= 0 (only upward shifts count).
 5. Detection statistic ``S = band_energy * (0.5 + medfreq_shift)`` — energetic
    AND spectrally-shifted-upward.
 6. Interpolate to per-sample, robust-threshold ``median + k*MAD``, extract
    events; strength = integrated band energy. The per-sample median-frequency
    track is available on ``self.last_medfreq`` for reporting.

Graceful degradation
---------------------
 * Single-channel and any grid size supported (channels averaged).
 * Very short windows fall back to a zeroed spectral shift (energy-only).
"""
from __future__ import annotations
import numpy as np
from scipy import signal as sp

from .base import BaseDetector, register
from .physio_common import (prep, window_bounds, interp_to_samples, robust_thr,
                            to_native, EPS)
from ..config import CANONICAL_FS, EHG_BAND_CONTRACTION

WIDE = (0.1, 3.0)


@register
class SpectralMomentTracker(BaseDetector):
    name = "physio_spectral"
    family = "physio"
    hw_tier = "low"
    causal = False
    default_params = dict(fs_work=CANONICAL_FS, win_s=30.0, step_s=5.0,
                          band=EHG_BAND_CONTRACTION, k=2.5,
                          min_dur_s=30.0, merge_gap_s=25.0)

    def detect(self, rec):
        p = self.params
        bp, fs = prep(rec, p["fs_work"], WIDE, causal=self.causal)
        C, N = bp.shape
        xm = np.mean(bp, axis=0)                 # channel-averaged signal
        lo, hi = p["band"]
        nperseg = min(N, max(64, int(fs * p["win_s"])))

        wins = window_bounds(N, fs, p["win_s"], p["step_s"])
        centers, medf, pkf, energy = [], [], [], []
        for c, a, b in wins:
            seg = xm[a:b]
            npg = min(nperseg, len(seg))
            e = float(np.sqrt(np.mean(seg ** 2)))
            if npg < 16:
                centers.append(c); medf.append(0.0); pkf.append(0.0); energy.append(e); continue
            fr, pxx = sp.welch(seg, fs=fs, nperseg=npg)
            m = (fr >= 0.05) & (fr <= min(3.0, 0.49 * fs))
            fr, pxx = fr[m], pxx[m] + EPS
            ptot = np.sum(pxx)
            cs = np.cumsum(pxx)
            mf = float(fr[np.searchsorted(cs, 0.5 * ptot)])
            pf = float(fr[np.argmax(pxx)])
            centers.append(c); medf.append(mf); pkf.append(pf); energy.append(e)

        medf = np.asarray(medf); energy = np.asarray(energy)
        base = np.median(medf[medf > 0]) if np.any(medf > 0) else 0.0
        mad = np.median(np.abs(medf - base)) + EPS
        shift = np.clip((medf - base) / (1.4826 * mad), 0, None)
        S_win = energy * (0.5 + shift)

        S = interp_to_samples(centers, S_win, N)
        E_samp = interp_to_samples(centers, energy, N)
        self.last_medfreq = interp_to_samples(centers, medf, N)
        thr = robust_thr(S, k=p["k"])
        events = self.events_from_statistic(
            S, fs, thr, min_dur_s=p["min_dur_s"], merge_gap_s=p["merge_gap_s"],
            strength_from=E_samp)
        return events, to_native(S, rec.n_samples)
