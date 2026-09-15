"""
web_engine.py — Pyodide entrypoint. Reuses the REAL src/ signal-processing code
(CONTRACTS §6). Kept pure (numpy in, JSON-serialisable/ndarray out) so it is
callable both from the browser (via engine.js) and from native CPython (Agent
ENGINE's offline numeric-parity self-test in Phase 1).

process() + extract_features() (Phase 0) exercise the filters/features reuse.
list_detectors()/run_detector() (Phase 1, Agent ENGINE) dispatch to the REAL
detector REGISTRY for exactly the modules listed in _whitelist.py — never
`import src.methods` directly, which would auto-load DL/torch detectors.
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


def _load_registry() -> dict:
    """Import ONLY the modules listed in _whitelist.BROWSER_DETECTORS (never
    `import src.methods`, which would auto-load DL/torch modules), then return
    the REAL detector REGISTRY (methods/base.py), filtered to just those
    whitelisted modules (defensive — REGISTRY is a shared module-level dict)."""
    import importlib
    from _whitelist import BROWSER_DETECTORS

    for mod_name in BROWSER_DETECTORS:
        importlib.import_module(mod_name)

    from src.methods.base import REGISTRY

    return {n: c for n, c in REGISTRY.items() if c.__module__ in BROWSER_DETECTORS}


def _jsonable(obj):
    """Recursively convert tuples -> lists and numpy scalars -> python scalars
    so a detector's default_params dict is JSON-serialisable."""
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, np.floating):
        return float(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    return obj


def _event_to_dict(ev) -> dict:
    """Map a src.evaluation.metrics.DetectedEvent onto the CONTRACTS §6 shape
    (onset/offset/peak in seconds, score float; the event's own `strength`
    field is not part of the frozen shape and is dropped here)."""
    return {
        "onset": float(ev.onset),
        "offset": float(ev.offset) if ev.offset is not None else None,
        "peak": float(ev.peak) if ev.peak is not None else None,
        "score": float(ev.score) if ev.score is not None else None,
    }


def list_detectors() -> list:
    """Enumerate browser-safe detectors from _whitelist.py (CONTRACTS §6)."""
    registry = _load_registry()
    out = []
    for name in sorted(registry):
        cls = registry[name]
        out.append({
            "name": cls.name,
            "family": cls.family,
            "causal": bool(cls.causal),
            "params": _jsonable(cls.default_params),
        })
    return out


def run_detector(name, sig, fs, params) -> dict:
    """Dispatch to a whitelisted detector and return the CONTRACTS §6 shape.

    `sig` may be 1-D or (C, N); each whitelisted detector reduces multichannel
    input itself (np.atleast_2d + its own fuse step), per CONTRACTS §6's rule
    ("reduce per the detector's own contract").
    """
    registry = _load_registry()
    if name not in registry:
        raise ValueError(f"unknown or non-whitelisted detector: {name!r}")
    cls = registry[name]

    from src.config import Recording

    x = np.atleast_2d(np.asarray(sig, dtype=np.float64))
    rec = Recording(rec_id="web", dataset="web", modality="EHG",
                    fs=float(fs), signals=x)

    det = cls(**(params or {}))
    events, stat = det.detect(rec)

    stat_arr = None if stat is None else np.asarray(stat, dtype=np.float32)
    return {
        "events": [_event_to_dict(e) for e in events],
        "stat": stat_arr,
    }
