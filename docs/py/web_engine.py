"""
web_engine.py — Pyodide entrypoint. Reuses the REAL src/ signal-processing code
(CONTRACTS §6). Kept pure (numpy in, JSON-serialisable/ndarray out) so it is
callable both from the browser (via engine.js) and from native CPython (Agent
ENGINE's offline numeric-parity self-test in Phase 1).

Phase 0 scope: process() + extract_features() are real (they exercise the
filters/features reuse the skeleton must prove). list_detectors()/run_detector()
are stubs owned by Agent ENGINE in Phase 1 (they read _whitelist.py).
"""
from __future__ import annotations

import numpy as np
from scipy import signal as sp

from src.preprocessing.filters import bandpass, bandpass_causal, envelope as _envelope


def process(sig, fs, params) -> dict:
    """params: {band:[lo,hi]|None, causal:bool, envelope:bool, psd:bool, spectrogram:bool}."""
    x = np.asarray(sig, dtype=np.float64)
    params = params or {}
    band = params.get("band")
    causal = bool(params.get("causal", False))

    if band:
        bp = bandpass_causal(x, band, fs) if causal else bandpass(x, band, fs)
    else:
        bp = x.copy()
    bp = bp.astype(np.float32)

    out: dict = {
        "filtered": bp,
        "envelope": None,
        "psd_f": None, "psd_p": None,
        "spec_f": None, "spec_t": None, "spec_S": None,
    }

    ref = bp if bp.ndim == 1 else bp[0]      # single-channel views for 1-D outputs

    if params.get("envelope"):
        out["envelope"] = _envelope(bp, fs, method="rms", win_s=20.0).astype(np.float32)

    if params.get("psd"):
        nperseg = int(min(len(ref), max(64, fs * 60)))
        f, pxx = sp.welch(ref, fs=fs, nperseg=nperseg)
        out["psd_f"] = f.astype(np.float32)
        out["psd_p"] = pxx.astype(np.float32)

    if params.get("spectrogram"):
        nperseg = int(min(len(ref), max(64, fs * 30)))
        f, t, S = sp.spectrogram(ref, fs=fs, nperseg=nperseg,
                                 noverlap=int(nperseg * 0.5))
        out["spec_f"] = f.astype(np.float32)
        out["spec_t"] = t.astype(np.float32)
        out["spec_S"] = S.astype(np.float32)

    return out


def extract_features(sig, fs, win_s, hop_s) -> dict:
    """Windowed scalar features via src.features.features (channel-averaged)."""
    from src.features.features import win_features, feature_names

    x = np.atleast_2d(np.asarray(sig, dtype=np.float64))   # (C, N)
    names = feature_names()
    L = max(1, int(win_s * fs))
    hop = max(1, int(hop_s * fs))
    n = x.shape[1]

    rows, t_s = [], []
    for a in range(0, max(1, n - L + 1), hop):
        seg = x[:, a:a + L]
        if seg.shape[1] < L:
            break
        fv = np.mean([[win_features(ch, fs)[k] for k in names] for ch in seg], axis=0)
        rows.append(fv.astype(np.float32))
        t_s.append((a + L / 2) / fs)

    matrix = np.vstack(rows).astype(np.float32) if rows else np.zeros((0, len(names)), np.float32)
    return {"names": names, "t_s": np.asarray(t_s, np.float32), "matrix": matrix}


def list_detectors() -> list:
    """Phase 1 (Agent ENGINE): enumerate browser-safe detectors from _whitelist.py."""
    return []


def run_detector(name, sig, fs, params) -> dict:
    """Phase 1 (Agent ENGINE): dispatch to a whitelisted detector."""
    raise NotImplementedError("run_detector is implemented in Phase 1 (Agent ENGINE)")
