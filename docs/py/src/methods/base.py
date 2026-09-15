"""
Method interface contract. Every detection algorithm subclasses BaseDetector.

Lifecycle
---------
    det = MyDetector(**params)
    det.fit(train_recs)            # optional; no-op for unsupervised threshold methods
    events, score = det.detect(rec)  # events: list[DetectedEvent]; score: per-sample stat or None

`detect` must be causal-optional: if self.causal is True it may not use future
samples (for online-latency evaluation). Offline methods ignore it.

Each detector declares:
    name          : unique short id
    family        : 'dsp' | 'ml' | 'dl' | 'physio'
    hw_tier       : 'low' (MCU/wearable) | 'mid' (embedded Linux) | 'high' (server)
    causal        : bool (whether detect() is online-causal)
"""
from __future__ import annotations
from ..evaluation.metrics import DetectedEvent

REGISTRY = {}


def register(cls):
    REGISTRY[cls.name] = cls
    return cls


class BaseDetector:
    name = "base"
    family = "dsp"
    hw_tier = "low"
    causal = False
    default_params: dict = {}

    def __init__(self, **params):
        self.params = {**self.default_params, **params}

    def fit(self, recs):
        return self

    def detect(self, rec):
        raise NotImplementedError

    # ---- shared helpers for event extraction from a 1-D detection statistic ---- #
    @staticmethod
    def events_from_statistic(stat, fs, thr, min_dur_s=30.0, merge_gap_s=20.0,
                              strength_from=None):
        """Threshold-crossing -> events. Returns list[DetectedEvent].
        strength_from: optional array to integrate/peak for a strength estimate."""
        import numpy as np
        above = stat >= thr
        events = []
        i, n = 0, len(stat)
        min_dur = int(min_dur_s * fs)
        gap = int(merge_gap_s * fs)
        runs = []
        while i < n:
            if above[i]:
                j = i
                while j < n and above[j]:
                    j += 1
                runs.append([i, j])
                i = j
            else:
                i += 1
        # merge close runs
        merged = []
        for r in runs:
            if merged and r[0] - merged[-1][1] <= gap:
                merged[-1][1] = r[1]
            else:
                merged.append(r)
        for a, b in merged:
            if b - a < min_dur:
                continue
            onset, offset = a / fs, b / fs
            seg = stat[a:b]
            peak = (a + int(np.argmax(seg))) / fs
            strength = None
            if strength_from is not None:
                s = strength_from[a:b]
                _trapz = getattr(np, "trapezoid", getattr(np, "trapz", None))
                strength = float(_trapz(np.abs(s)) / fs)  # energy-like
            events.append(DetectedEvent(onset=onset, offset=offset, peak=peak,
                                        score=float(seg.max()), strength=strength))
        return events
