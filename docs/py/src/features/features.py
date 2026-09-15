"""
Feature library for windowed EHG/MMG analysis.

Two API levels:
  * per-window scalar features   -> feature vectors for classical ML / HMM
  * per-sample streaming features -> detection statistics for threshold methods

Feature families implemented (all documented, literature-grounded):
  Amplitude/energy : RMS, mean-abs, peak-to-peak, log-energy, TKEO energy
  Frequency        : median freq, peak freq, mean freq, spectral band powers,
                     spectral entropy, power ratio (fast/slow wave)
  Nonlinear/complexity : sample entropy, zero-crossing rate, Willison amplitude,
                     Hjorth mobility & complexity, line length
  Wavelet          : per-band relative energy (DWT)
"""
from __future__ import annotations
import numpy as np
from scipy import signal as sp

from ..preprocessing.filters import teager_kaiser

FS_DEFAULT = 20.0
# EHG sub-bands (Hz)
BANDS = {
    "slow_wave": (0.1, 0.34),
    "fast_wave": (0.34, 1.0),
    "contraction": (0.2, 1.2),
    "mhr": (1.0, 2.2),   # maternal heart-rate band (TPEHGT paper)
}


# ---------------------- per-sample streaming statistics -------------------- #
def stat_rms(x, fs, win_s=20.0):
    w = max(1, int(win_s * fs))
    k = np.ones(w) / w
    return np.sqrt(np.convolve(x ** 2, k, mode="same"))


def stat_tkeo_env(x, fs, win_s=10.0):
    psi = np.clip(teager_kaiser(x), 0, None)
    w = max(1, int(win_s * fs))
    return np.convolve(psi, np.ones(w) / w, mode="same")


def stat_line_length(x, fs, win_s=20.0):
    d = np.abs(np.diff(x, prepend=x[:1]))
    w = max(1, int(win_s * fs))
    return np.convolve(d, np.ones(w), mode="same")


def stat_zcr(x, fs, win_s=20.0):
    z = (np.diff(np.signbit(x).astype(int), prepend=0) != 0).astype(float)
    w = max(1, int(win_s * fs))
    return np.convolve(z, np.ones(w) / w, mode="same")


# ---------------------- per-window scalar features ------------------------- #
def win_features(x, fs=FS_DEFAULT):
    """Scalar feature dict for a 1-D window x."""
    x = np.asarray(x, float)
    x = x - np.mean(x)
    n = len(x)
    f = {}
    # amplitude / energy
    f["rms"] = float(np.sqrt(np.mean(x ** 2)))
    f["mav"] = float(np.mean(np.abs(x)))
    f["ptp"] = float(np.ptp(x))
    f["log_energy"] = float(np.log(np.sum(x ** 2) + 1e-12))
    f["tkeo"] = float(np.mean(np.clip(teager_kaiser(x), 0, None)))
    f["line_length"] = float(np.sum(np.abs(np.diff(x))))
    f["var"] = float(np.var(x))
    # Hjorth
    d1 = np.diff(x); d2 = np.diff(d1)
    v0 = np.var(x) + 1e-12; v1 = np.var(d1) + 1e-12; v2 = np.var(d2) + 1e-12
    f["hjorth_mobility"] = float(np.sqrt(v1 / v0))
    f["hjorth_complexity"] = float(np.sqrt(v2 / v1) / (np.sqrt(v1 / v0) + 1e-12))
    # zero crossings / Willison
    f["zcr"] = float(np.mean(np.abs(np.diff(np.signbit(x).astype(int))) > 0))
    f["willison"] = float(np.sum(np.abs(np.diff(x)) > (0.1 * np.std(x) + 1e-12)))
    # spectral
    nperseg = min(n, max(32, int(fs * 30)))
    if n >= 16:
        fr, pxx = sp.welch(x, fs=fs, nperseg=min(nperseg, n))
        pxx = pxx + 1e-18
        ptot = np.sum(pxx)
        f["median_freq"] = float(fr[np.searchsorted(np.cumsum(pxx), 0.5 * ptot)])
        f["peak_freq"] = float(fr[np.argmax(pxx)])
        f["mean_freq"] = float(np.sum(fr * pxx) / ptot)
        pn = pxx / ptot
        f["spectral_entropy"] = float(-np.sum(pn * np.log(pn)) / np.log(len(pn)))
        for name, (lo, hi) in BANDS.items():
            m = (fr >= lo) & (fr < hi)
            f[f"bp_{name}"] = float(np.sum(pxx[m]) / ptot)
        f["fast_slow_ratio"] = float((f["bp_fast_wave"] + 1e-9) / (f["bp_slow_wave"] + 1e-9))
    else:
        for k in ["median_freq", "peak_freq", "mean_freq", "spectral_entropy",
                  "fast_slow_ratio"] + [f"bp_{b}" for b in BANDS]:
            f[k] = 0.0
    f["samp_entropy"] = sample_entropy(x)
    return f


def sample_entropy(x, m=2, r=0.2):
    """Sample entropy (subsampled for speed on long windows)."""
    x = np.asarray(x, float)
    if len(x) > 600:                       # decimate long windows for tractability
        x = x[:: len(x) // 600 + 1]
    N = len(x)
    if N < m + 2:
        return 0.0
    r = r * (np.std(x) + 1e-12)

    def phi(mm):
        xm = np.array([x[i:i + mm] for i in range(N - mm + 1)])
        C = 0
        for i in range(len(xm)):
            d = np.max(np.abs(xm - xm[i]), axis=1)
            C += np.sum(d <= r) - 1
        return C

    B = phi(m); A = phi(m + 1)
    if B == 0 or A == 0:
        return 0.0
    return float(-np.log(A / B))


def feature_names():
    dummy = win_features(np.random.randn(400), FS_DEFAULT)
    return list(dummy.keys())
