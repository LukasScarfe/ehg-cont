"""
Preprocessing primitives shared by all methods: band-pass filtering, resampling,
bipolar (spatial-difference) derivations, envelope extraction, maternal-ECG-band
handling, and artifact masking.

Design notes
------------
* Zero-phase (filtfilt) filtering is used for OFFLINE analysis (the project's
  stated priority). A causal single-pass variant (`bandpass_causal`) is provided so
  online-latency variants of each method can be evaluated without look-ahead.
* All filters are Butterworth; default orders follow the EHG literature
  (4th order, band 0.08-5 Hz wide; 0.34-1 Hz for the fast-wave contraction band).
"""
from __future__ import annotations
import numpy as np
from scipy import signal as sp


def _sos(band, fs, order=4, btype="band"):
    ny = 0.5 * fs
    if btype == "band":
        wn = [max(1e-6, band[0] / ny), min(0.999, band[1] / ny)]
    else:
        wn = min(0.999, band / ny)
    return sp.butter(order, wn, btype=btype, output="sos")


def bandpass(x, band, fs, order=4):
    """Zero-phase band-pass (offline). x: (..., n) along last axis."""
    lo, hi = band
    if hi >= 0.5 * fs:
        sos = _sos(lo, fs, order, "high")
    else:
        sos = _sos(band, fs, order, "band")
    return sp.sosfiltfilt(sos, x, axis=-1)


def bandpass_causal(x, band, fs, order=4):
    """Causal single-pass band-pass (online; introduces phase lag by design)."""
    lo, hi = band
    if hi >= 0.5 * fs:
        sos = _sos(lo, fs, order, "high")
    else:
        sos = _sos(band, fs, order, "band")
    return sp.sosfilt(sos, x, axis=-1)


def notch(x, fs, f0=50.0, q=30.0):
    if f0 >= 0.5 * fs:
        return x
    b, a = sp.iirnotch(f0, q, fs)
    return sp.filtfilt(b, a, x, axis=-1)


def resample_to(x, fs_in, fs_out):
    """Polyphase resample along last axis. Returns (y, fs_out)."""
    if abs(fs_in - fs_out) < 1e-6:
        return x, fs_in
    from math import gcd
    fi, fo = int(round(fs_in)), int(round(fs_out))
    g = gcd(fi, fo)
    up, down = fo // g, fi // g
    y = sp.resample_poly(x, up, down, axis=-1)
    return y, fs_out


def bipolar_grid(signals, ch_names):
    """Monopolar -> bipolar (adjacent-difference) derivations.
    Generic fallback: consecutive channel differences. Returns (bip, names)."""
    bip, names = [], []
    for i in range(len(ch_names) - 1):
        bip.append(signals[i + 1] - signals[i])
        names.append(f"{ch_names[i+1]}-{ch_names[i]}")
    return np.asarray(bip), names


def envelope(x, fs, method="rms", win_s=2.0):
    """Amplitude envelope of a (possibly multichannel) signal along last axis.
    method: 'rms' (moving RMS), 'hilbert' (analytic magnitude), 'abs' (rectify+LP)."""
    if method == "hilbert":
        return np.abs(sp.hilbert(x, axis=-1))
    w = max(1, int(win_s * fs))
    if method == "abs":
        rect = np.abs(x)
        k = np.ones(w) / w
        return _movavg(rect, k)
    # rms
    k = np.ones(w) / w
    return np.sqrt(_movavg(x ** 2, k))


def _movavg(x, k):
    if x.ndim == 1:
        return np.convolve(x, k, mode="same")
    return np.vstack([np.convolve(row, k, mode="same") for row in x])


def teager_kaiser(x):
    """Discrete Teager-Kaiser Energy Operator: psi[n] = x[n]^2 - x[n-1]x[n+1].
    Emphasises instantaneous energy (amplitude*frequency); strong EHG onset cue."""
    x = np.asarray(x, float)
    psi = np.empty_like(x)
    psi[..., 1:-1] = x[..., 1:-1] ** 2 - x[..., :-2] * x[..., 2:]
    psi[..., 0] = psi[..., 1]
    psi[..., -1] = psi[..., -2]
    return psi


def zscore(x, axis=-1, eps=1e-9):
    m = np.mean(x, axis=axis, keepdims=True)
    s = np.std(x, axis=axis, keepdims=True)
    return (x - m) / (s + eps)


def artifact_mask(n, fs, artifacts, guard_s=5.0):
    """Boolean mask (True = keep) that zeroes-out +/- guard_s around artifact events.
    artifacts: list[(t_seconds, symbol)]."""
    mask = np.ones(n, dtype=bool)
    g = int(guard_s * fs)
    for t, _sym in artifacts:
        c = int(t * fs)
        mask[max(0, c - g): min(n, c + g)] = False
    return mask
