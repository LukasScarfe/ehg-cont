#!/usr/bin/env python3
"""
build_web_dataset.py — offline builder for the committed browser dataset.

PHASE 1 SCOPE (Agent DATA): builds ALL 98 contraction-bearing records and
enforces the repo size budget (<= ~150 MB total for docs/data/).

For each record it:
  1. reads the WFDB triplet (.hea/.dat/.atr) from the isolated dataset,
  2. reorders the 16 channels into ascending EHG1..EHG16 (header order is NOT
     ascending — see CONTRACTS §2),
  3. anti-alias decimates 200 Hz -> DOWNSAMPLE_FS via resample_poly,
  4. converts back to int16 (raw = round(mV * gain)),
  5. writes channel-major gzip (.f16.gz) per CONTRACTS §1,
  6. accumulates index.json + annotations.json entries per CONTRACTS §2/§3.

Budget enforcement: builds the full 98-record set at a candidate
`downsample_fs`, sums the gz bytes, and if the total exceeds the ~150 MB
budget, retries at the next lower candidate rate (20 -> 15 -> 10 Hz) BEFORE
ever considering dropping channels (channels are never dropped). The final
chosen rate is what ends up committed to docs/data/ and recorded in
index.json's `downsample_fs` field.

Reproducible: pure function of the source files + the constants below.
"""
from __future__ import annotations

import glob
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

# --- build parameters ---
ORIG_FS = 200
GAIN = 131.068
CHANNELS = [f"EHG{i}" for i in range(1, 17)]        # ascending target order
GRID = [[1, 2, 3, 4], [5, 6, 7, 8], [9, 10, 11, 12], [13, 14, 15, 16]]

# Candidate downsample rates to try, in order, until total gz size <= BUDGET.
# Never drop channels to save space — only lower the rate.
CANDIDATE_FS = [20, 15, 10]
BUDGET_BYTES = 150 * 1024 * 1024  # ~150 MB

DEFINITE = {"C"}
POSSIBLE = {"(c)"}
KEEP_SYMBOLS = {"C", "(c)", "fm", "pm", "em", "pos"}


def discover_records() -> list[str]:
    """All *.hea basenames under SRC_SIGNALS, sorted."""
    heas = sorted(glob.glob(os.path.join(SRC_SIGNALS, "*.hea")))
    return [os.path.splitext(os.path.basename(h))[0] for h in heas]


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


def build_record(rec_id: str, downsample_fs: int) -> tuple[dict, list[dict], int, bytes]:
    path = os.path.join(SRC_SIGNALS, rec_id)
    rec = wfdb.rdrecord(path)                         # physical mV, (n, 16)
    order = _ascending_order(list(rec.sig_name))
    x = rec.p_signal[:, order].T.astype(np.float64)   # (16, n) ascending, mV
    n_orig = x.shape[1]

    # anti-aliased decimation 200 -> downsample_fs
    from math import gcd
    g = gcd(ORIG_FS, downsample_fs)
    y = resample_poly(x, downsample_fs // g, ORIG_FS // g, axis=-1)  # (16, n2) mV
    n_samples = y.shape[1]

    raw = np.rint(y * GAIN).astype(np.int16)          # back to int16
    raw = np.ascontiguousarray(raw)                   # channel-major (C order)
    payload = gzip.compress(raw.tobytes(order="C"), compresslevel=9)
    n_bytes = len(payload)

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
            "sample": int(round(t_s * downsample_fs)),
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
    return index_entry, events, n_bytes, payload


def build_all(record_ids: list[str], downsample_fs: int):
    """Build every record at a given rate; return (index_records, annotations,
    total_bytes, payloads) without writing anything to disk yet."""
    index_records = []
    annotations = {}
    payloads = {}
    total_bytes = 0
    for rec_id in record_ids:
        entry, events, nb, payload = build_record(rec_id, downsample_fs)
        index_records.append(entry)
        annotations[rec_id] = events
        payloads[rec_id] = payload
        total_bytes += nb
    return index_records, annotations, total_bytes, payloads


def main() -> None:
    os.makedirs(DATA_OUT, exist_ok=True)
    record_ids = discover_records()
    print(f"discovered {len(record_ids)} record(s) in {SRC_SIGNALS}")

    chosen_fs = None
    index_records = annotations = payloads = None
    total_bytes = 0
    for i, fs_candidate in enumerate(CANDIDATE_FS):
        print(f"building all records at downsample_fs={fs_candidate} Hz "
              f"({'candidate ' + str(i + 1) + '/' + str(len(CANDIDATE_FS))})...")
        index_records, annotations, total_bytes, payloads = build_all(
            record_ids, fs_candidate)
        print(f"  -> total gz bytes at {fs_candidate} Hz: {total_bytes} "
              f"({total_bytes / 1e6:.2f} MB), budget {BUDGET_BYTES / 1e6:.0f} MB")
        if total_bytes <= BUDGET_BYTES:
            chosen_fs = fs_candidate
            print(f"under budget at {fs_candidate} Hz -> choosing this rate "
                  f"(no channel drop needed).")
            break
        print(f"over budget at {fs_candidate} Hz; "
              f"{'trying lower rate' if i + 1 < len(CANDIDATE_FS) else 'no lower candidate left'}...")

    if chosen_fs is None:
        # Exhausted all candidates; still under budget requirement not met.
        # Per instructions: never drop channels. Use the lowest candidate
        # rate anyway (best effort) and flag loudly.
        chosen_fs = CANDIDATE_FS[-1]
        print(f"WARNING: budget not reachable even at {chosen_fs} Hz "
              f"({total_bytes / 1e6:.2f} MB > {BUDGET_BYTES / 1e6:.0f} MB budget). "
              f"Proceeding with {chosen_fs} Hz as the lowest allowed rate; "
              f"channels were NOT dropped per instructions.")

    # write payloads for the chosen rate
    for rec_id, payload in payloads.items():
        gz_path = os.path.join(DATA_OUT, f"{rec_id}.f16.gz")
        with open(gz_path, "wb") as fh:
            fh.write(payload)

    for entry in index_records:
        nb = entry["bytes"]
        print(f"  {entry['id']}: n={entry['n_samples']} bytes={nb} "
              f"events={sum(entry['annotation_counts'].values())}")

    index = {
        "schema": "ehg-index/1.0.0",
        "source": "Icelandic 16-electrode EHG Database (ice16ehgdb)",
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "orig_fs": ORIG_FS,
        "downsample_fs": chosen_fs,
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

    print(f"chosen downsample_fs = {chosen_fs} Hz")
    print(f"wrote {len(index_records)} record(s), total {total_bytes} bytes "
          f"({total_bytes/1e6:.2f} MB) -> {DATA_OUT}")


if __name__ == "__main__":
    main()
