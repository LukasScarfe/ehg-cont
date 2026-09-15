#!/usr/bin/env python3
"""
build_web_dataset.py — offline builder for the committed browser dataset.

PHASE 0 SCOPE: emits *one* record end-to-end so the walking skeleton has real
data to load. Phase 1 (Agent DATA) generalises the RECORDS loop to all 98
contraction-bearing records and enforces the repo size budget.

For each record it:
  1. reads the WFDB triplet (.hea/.dat/.atr) from the isolated dataset,
  2. reorders the 16 channels into ascending EHG1..EHG16 (header order is NOT
     ascending — see CONTRACTS §2),
  3. anti-alias decimates 200 Hz -> DOWNSAMPLE_FS via resample_poly,
  4. converts back to int16 (raw = round(mV * gain)),
  5. writes channel-major gzip (.f16.gz) per CONTRACTS §1,
  6. accumulates index.json + annotations.json entries per CONTRACTS §2/§3.

Reproducible: pure function of the source files + the constants below.
"""
from __future__ import annotations

import gzip
import json
import os
from collections import Counter
from datetime import datetime, timezone

import numpy as np
import wfdb
from scipy.signal import resample_poly

HERE = os.path.dirname(os.path.abspath(__file__))
WEB_ROOT = os.path.dirname(HERE)
DATA_OUT = os.path.join(WEB_ROOT, "docs", "data")
SRC_SIGNALS = os.path.abspath(os.path.join(
    WEB_ROOT, "..", "Icelandic16EHG_contractions", "signals"))

# --- build parameters (locked by CONTRACTS; Phase 1 may lower fs for budget) ---
ORIG_FS = 200
DOWNSAMPLE_FS = 20
GAIN = 131.068
CHANNELS = [f"EHG{i}" for i in range(1, 17)]        # ascending target order
GRID = [[1, 2, 3, 4], [5, 6, 7, 8], [9, 10, 11, 12], [13, 14, 15, 16]]

# Phase 0: just one record. Phase 1 replaces this with all 98.
RECORDS = ["ice002_p_2of3"]

DEFINITE = {"C"}
POSSIBLE = {"(c)"}
KEEP_SYMBOLS = {"C", "(c)", "fm", "pm", "em", "pos"}


def _symbol_type(sym: str) -> str:
    if sym in DEFINITE:
        return "definite"
    if sym in POSSIBLE:
        return "possible"
    return "other"


def _ascending_order(sig_names: list[str]) -> list[int]:
    """Column indices that reorder header signal order into EHG1..EHG16."""
    idx = {name: i for i, name in enumerate(sig_names)}
    missing = [c for c in CHANNELS if c not in idx]
    if missing:
        raise ValueError(f"record missing channels {missing}; has {sig_names}")
    return [idx[c] for c in CHANNELS]


def build_record(rec_id: str) -> tuple[dict, list[dict], int]:
    path = os.path.join(SRC_SIGNALS, rec_id)
    rec = wfdb.rdrecord(path)                         # physical mV, (n, 16)
    order = _ascending_order(list(rec.sig_name))
    x = rec.p_signal[:, order].T.astype(np.float64)   # (16, n) ascending, mV
    n_orig = x.shape[1]

    # anti-aliased decimation 200 -> DOWNSAMPLE_FS
    from math import gcd
    g = gcd(ORIG_FS, DOWNSAMPLE_FS)
    y = resample_poly(x, DOWNSAMPLE_FS // g, ORIG_FS // g, axis=-1)  # (16, n2) mV
    n_samples = y.shape[1]

    raw = np.rint(y * GAIN).astype(np.int16)          # back to int16
    raw = np.ascontiguousarray(raw)                   # channel-major (C order)

    gz_path = os.path.join(DATA_OUT, f"{rec_id}.f16.gz")
    with gzip.open(gz_path, "wb", compresslevel=9) as fh:
        fh.write(raw.tobytes(order="C"))
    n_bytes = os.path.getsize(gz_path)

    # annotations from the .atr (full symbol set retained)
    ann = wfdb.rdann(path, "atr")
    events: list[dict] = []
    counts: Counter = Counter()
    for s, sym in zip(ann.sample, ann.symbol):
        sym = sym.strip()
        if sym not in KEEP_SYMBOLS:
            continue
        t_s = s / ORIG_FS
        events.append({
            "t_s": round(t_s, 3),
            "sample": int(round(t_s * DOWNSAMPLE_FS)),
            "symbol": sym,
            "type": _symbol_type(sym),
        })
        counts[sym] += 1
    events.sort(key=lambda e: e["t_s"])

    comments = {c.split(":", 1)[0].strip("# ").lower(): c.split(":", 1)[1].strip()
                for c in rec.comments if ":" in c}
    index_entry = {
        "id": rec_id,
        "subject": rec_id.split("_")[0],
        "type": comments.get("record type", "unknown"),
        "n_samples": int(n_samples),
        "duration_s": round(n_orig / ORIG_FS, 3),
        "ga_recording": comments.get("gestational age at recording(w/d)", ""),
        "annotation_counts": {s: int(counts.get(s, 0)) for s in
                              ["C", "(c)", "fm", "pm", "em", "pos"]},
        "file": f"{rec_id}.f16.gz",
        "bytes": n_bytes,
    }
    return index_entry, events, n_bytes


def main() -> None:
    os.makedirs(DATA_OUT, exist_ok=True)
    index_records = []
    annotations = {}
    total_bytes = 0
    for rec_id in RECORDS:
        entry, events, nb = build_record(rec_id)
        index_records.append(entry)
        annotations[rec_id] = events
        total_bytes += nb
        print(f"  {rec_id}: n={entry['n_samples']} bytes={nb} "
              f"events={sum(entry['annotation_counts'].values())}")

    index = {
        "schema": "ehg-index/1.0.0",
        "source": "Icelandic 16-electrode EHG Database (ice16ehgdb)",
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "orig_fs": ORIG_FS,
        "downsample_fs": DOWNSAMPLE_FS,
        "dtype": "int16",
        "layout": "channel-major",
        "gain": GAIN,
        "channels": CHANNELS,
        "grid": GRID,
        "records": index_records,
    }
    with open(os.path.join(DATA_OUT, "index.json"), "w") as fh:
        json.dump(index, fh, indent=2)
    with open(os.path.join(DATA_OUT, "annotations.json"), "w") as fh:
        json.dump({"schema": "ehg-annotations/1.0.0", "records": annotations},
                  fh, indent=2)

    print(f"wrote {len(index_records)} record(s), total {total_bytes} bytes "
          f"({total_bytes/1e6:.2f} MB) -> {DATA_OUT}")


if __name__ == "__main__":
    main()
