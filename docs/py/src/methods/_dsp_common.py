"""
Shared helpers for the DSP / signal-processing detector family.

This module ADDS convenience routines (it does not modify any shared harness
file). Every DSP detector reuses these so the detectors themselves stay short
and their algorithm-step docstrings map cleanly onto a few calls:

    bp, fs      = preprocess(rec, fs_work, band, causal)   # resample + band-pass
    env         = channel_envelope(bp, fs, method, win_s)  # per-channel envelope
    m           = fuse(env, mode)                           # channel fusion -> 1-D
    thr         = robust_thr(m, k)                          # median + k*MAD gate

All routines are defensive: they accept 1-D or 2-D input, guard empty / short /
constant signals, and never divide by zero.
"""
from __future__ import annotations
import numpy as np

from ..preprocessing.filters import (bandpass, bandpass_causal, resample_to,
                                      envelope)

EPS = 1e-12

# numpy>=2 renamed trapz -> trapezoid; support either without eager-eval of a
# missing attribute.
_TRAPZ = getattr(np, "trapezoid", None) or getattr(np, "trapz", None)


def trapz(y, dx=1.0):
    """Trapezoidal integral, version-agnostic."""
    if _TRAPZ is not None:
        return _TRAPZ(y) * dx if dx != 1.0 else _TRAPZ(y)
    return float(np.sum(y))


# --------------------------------------------------------------------------- #
# Preprocessing
# --------------------------------------------------------------------------- #
def preprocess(rec, fs_work, band, causal):
    """Resample every channel to `fs_work` and band-pass (zero-phase offline,
    single-pass causal when `causal`). Returns (bp (C,n) float64, fs)."""
    x = np.atleast_2d(np.asarray(rec.signals, float))
    x, fs = resample_to(x, rec.fs, fs_work)
    x = np.atleast_2d(x)
    # guard: very short recording -> nothing to filter
    if x.shape[-1] < 16:
        return x, fs
    filt = bandpass_causal if causal else bandpass
    try:
        bp = filt(x, band, fs, order=4)
    except Exception:
        # filter can fail on degenerate length; fall back to mean-removed raw
        bp = x - x.mean(axis=-1, keepdims=True)
    return np.atleast_2d(np.asarray(bp, float)), fs


def channel_envelope(bp, fs, method="rms", win_s=15.0):
    """Per-channel amplitude envelope, shape (C, n), non-negative."""
    env = envelope(bp, fs, method=method, win_s=win_s)
    return np.atleast_2d(np.abs(np.asarray(env, float)))


# --------------------------------------------------------------------------- #
# Channel fusion
# --------------------------------------------------------------------------- #
def fuse(env, mode="mean"):
    """Fuse a (C, n) per-channel statistic to a 1-D stream.

    mode: 'mean'   - channel average (baseline behaviour)
          'median' - robust to single-channel artifacts
          'max'    - most-active channel (sensitive)
          'trimmed'- mean of the central channels (drop min/max)
          'robust_z' - average of per-channel robust z-scores (scale-free fusion)
    """
    env = np.atleast_2d(env)
    C = env.shape[0]
    if C == 1:
        return env[0].copy()
    if mode == "median":
        return np.median(env, axis=0)
    if mode == "max":
        return env.max(axis=0)
    if mode == "trimmed" and C >= 3:
        s = np.sort(env, axis=0)
        return s[1:-1].mean(axis=0)
    if mode == "robust_z":
        z = np.empty_like(env)
        for c in range(C):
            row = env[c]
            med = np.median(row)
            mad = np.median(np.abs(row - med)) + EPS
            z[c] = (row - med) / (1.4826 * mad)
        return z.mean(axis=0)
    return env.mean(axis=0)


# --------------------------------------------------------------------------- #
# Thresholds
# --------------------------------------------------------------------------- #
def robust_thr(m, k):
    """Adaptive robust threshold: median + k * 1.4826 * MAD."""
    m = np.asarray(m, float)
    med = np.median(m)
    mad = np.median(np.abs(m - med)) + EPS
    return float(med + k * 1.4826 * mad)


def smooth(x, fs, win_s):
    """Zero-lag moving-average smoother (last axis)."""
    w = max(1, int(win_s * fs))
    k = np.ones(w) / w
    x = np.asarray(x, float)
    if x.ndim == 1:
        return np.convolve(x, k, mode="same")
    return np.vstack([np.convolve(r, k, mode="same") for r in x])


def is_degenerate(m):
    """True if the statistic carries no usable dynamic range."""
    m = np.asarray(m, float)
    if m.size < 8 or not np.all(np.isfinite(m)):
        return True
    return float(np.ptp(m)) < EPS


def kalman_smooth(z, q_ratio=0.02):
    """Scalar random-walk Kalman filter (causal). Measurement noise r is the
    signal variance; process noise q = q_ratio * r sets the smoothing strength.
    Small q_ratio -> heavy smoothing, large -> tracks fast. Returns filtered z."""
    z = np.asarray(z, float)
    n = len(z)
    if n == 0:
        return z
    r = float(np.var(z)) + EPS
    q = q_ratio * r
    xhat = np.empty(n)
    x = z[0]
    P = r
    for i in range(n):
        P += q                      # predict
        K = P / (P + r)             # Kalman gain
        x += K * (z[i] - x)         # update
        P *= (1.0 - K)
        xhat[i] = x
    return xhat
