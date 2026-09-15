"""
Shared helpers for the physiological / biophysical-model detector family.

These utilities are used by the physio_* detectors. They centralise the
resample -> band-pass -> (multichannel) windowing pipeline so each detector's
own file only expresses the *physiological statistic* it computes.

Nothing here mutates shared harness files; this is an additive helper module
owned by the physio family.
"""
from __future__ import annotations
import numpy as np
from scipy import signal as sp

from ..preprocessing.filters import bandpass, bandpass_causal, resample_to

EPS = 1e-12


def prep(rec, fs_work, band, causal=False, order=4):
    """Resample all channels to fs_work and zero-phase (or causal) band-pass.

    Returns (bp, fs) where bp is (C, N) float64. Guards against fs_work above
    Nyquist and against recordings too short for the filter.
    """
    x = np.atleast_2d(np.asarray(rec.signals, float))
    # Do not up-sample beyond the native rate (wastes compute, adds nothing).
    fs_target = min(fs_work, rec.fs)
    x, fs = resample_to(x, rec.fs, fs_target)
    # Clamp band to below Nyquist.
    hi = min(band[1], 0.49 * fs)
    lo = min(band[0], 0.5 * hi)
    b = (lo, hi)
    filt = bandpass_causal if causal else bandpass
    if x.shape[1] < 3 * order + 10:            # too short to filter safely
        return x - x.mean(axis=-1, keepdims=True), fs
    try:
        bp = filt(x, b, fs, order=order)
    except Exception:
        bp = x - x.mean(axis=-1, keepdims=True)
    return np.atleast_2d(bp), fs


def window_bounds(n, fs, win_s, step_s):
    """Yield (center_sample, a, b) for sliding windows over [0, n)."""
    w = max(4, int(win_s * fs))
    s = max(1, int(step_s * fs))
    out = []
    a = 0
    while a < n:
        b = min(n, a + w)
        if b - a >= max(4, w // 2):            # keep last partial window if half-full
            out.append(((a + b) // 2, a, b))
        if b >= n:
            break
        a += s
    if not out:
        out = [(n // 2, 0, n)]
    return out


def interp_to_samples(centers, values, n):
    """Piecewise-linear interpolate a per-window statistic to a per-sample array."""
    centers = np.asarray(centers, float)
    values = np.asarray(values, float)
    if len(centers) == 0:
        return np.zeros(n)
    if len(centers) == 1:
        return np.full(n, values[0])
    xi = np.arange(n)
    return np.interp(xi, centers, values, left=values[0], right=values[-1])


def to_native(stat, n_native):
    """Resample a per-sample statistic computed at the working rate up to the
    recording's native sample count, so the harness's sample-level AUC (which
    indexes at rec.fs) lines up. Events are unaffected (they are in seconds)."""
    stat = np.asarray(stat, float)
    if len(stat) == n_native or len(stat) == 0:
        return stat
    xo = np.linspace(0, 1, len(stat))
    xn = np.linspace(0, 1, n_native)
    return np.interp(xn, xo, stat)


def rms_window(seg):
    """Root-mean-square amplitude of a (C, w) or (w,) segment (per row mean)."""
    seg = np.atleast_2d(seg)
    return float(np.sqrt(np.mean(seg ** 2)))


def robust_thr(stat, k=3.0):
    """median + k * 1.4826 * MAD robust threshold for a 1-D statistic."""
    med = np.median(stat)
    mad = np.median(np.abs(stat - med)) + EPS
    return med + k * 1.4826 * mad


def moving_average(x, w):
    w = max(1, int(w))
    if w == 1:
        return np.asarray(x, float)
    k = np.ones(w) / w
    return np.convolve(np.asarray(x, float), k, mode="same")


def xcorr_peak(a, b, maxlag):
    """Normalised cross-correlation peak (signed) and its lag (samples) within
    +/- maxlag. Returns (peak_corr in [-1,1], lag). A traveling wave between two
    electrodes shows a high peak at a consistent non-zero lag."""
    a = np.asarray(a, float); b = np.asarray(b, float)
    a = a - a.mean(); b = b - b.mean()
    na = np.sqrt(np.sum(a * a)); nb = np.sqrt(np.sum(b * b))
    if na < EPS or nb < EPS:
        return 0.0, 0
    c = sp.correlate(a, b, mode="full", method="auto") / (na * nb)
    mid = len(a) - 1
    lo = max(0, mid - maxlag); hi = min(len(c), mid + maxlag + 1)
    seg = c[lo:hi]
    k = int(np.argmax(np.abs(seg)))
    lag = (lo + k) - mid
    return float(seg[k]), int(lag)


def onset_from_foot(env, fs, peak_idx, baseline, frac=0.2, search_s=180.0):
    """Walk backward from a peak to the 'foot' where the envelope first exceeds
    baseline + frac*(peak-baseline). Robust onset estimator (seconds)."""
    peak_idx = int(np.clip(peak_idx, 0, len(env) - 1))
    peak_val = env[peak_idx]
    thr = baseline + frac * (peak_val - baseline)
    lo = max(0, peak_idx - int(search_s * fs))
    i = peak_idx
    while i > lo and env[i] > thr:
        i -= 1
    return i / fs
