#!/usr/bin/env python3
"""
selftest_data.py — self-test for the committed browser dataset built by
build_web_dataset.py (Agent DATA, Phase 1).

Checks, per CONTRACTS.md §1-§3:
  1. Every record's .f16.gz gunzips to exactly n_samples*16 int16 samples
     (matching index.json).
  2. `gain` and the chosen `downsample_fs` round-trip: decoding a channel to
     mV via raw/gain gives a finite, plausible-range EHG signal.
  3. For records with >=1 annotation, a known annotation's `sample` lands
     within signal bounds and within tolerance of round(t_s * downsample_fs).
  4. Total committed size of docs/data/ is <= 150 MB.
  5. Per-record annotation_counts for C/(c) match record_summary.csv's
     definite_C/possible_c columns.

Exits non-zero on any failure; prints a summary either way.
"""
from __future__ import annotations

import csv
import gzip
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
WEB_ROOT = os.path.dirname(HERE)
DATA_DIR = os.path.join(WEB_ROOT, "docs", "data")
SUMMARY_CSV = os.path.abspath(os.path.join(
    WEB_ROOT, "..", "Icelandic16EHG_contractions", "record_summary.csv"))

BUDGET_BYTES = 150 * 1024 * 1024
N_CHANNELS = 16
# EHG signal amplitude is typically within +/-a few mV; be generous but catch
# gross gain/scale errors (raw int16 range is +/-32767 / 131.068 ~= +/-250mV).
PLAUSIBLE_MV_ABS_MAX = 260.0
SAMPLE_TOLERANCE = 1  # allowed rounding slack on annotation sample index


def fail(msg: str, errors: list[str]) -> None:
    errors.append(msg)
    print(f"FAIL: {msg}")


def main() -> int:
    errors: list[str] = []

    if not os.path.isdir(DATA_DIR):
        print(f"FAIL: {DATA_DIR} does not exist")
        return 1

    with open(os.path.join(DATA_DIR, "index.json")) as fh:
        index = json.load(fh)
    with open(os.path.join(DATA_DIR, "annotations.json")) as fh:
        ann_doc = json.load(fh)

    downsample_fs = index["downsample_fs"]
    gain = index["gain"]
    channels = index["channels"]
    records = index["records"]

    assert channels == [f"EHG{i}" for i in range(1, 17)], \
        f"channels not ascending EHG1..EHG16: {channels}"
    assert len(channels) == N_CHANNELS

    print(f"index.json: {len(records)} records, downsample_fs={downsample_fs}, "
          f"gain={gain}")

    # --- load record_summary.csv for cross-check ---
    summary = {}
    with open(SUMMARY_CSV, newline="") as fh:
        for row in csv.DictReader(fh):
            summary[row["record"]] = {
                "definite_C": int(row["definite_C"]),
                "possible_c": int(row["possible_c"]),
            }

    if len(records) != 98:
        fail(f"expected 98 records, index.json has {len(records)}", errors)

    missing_from_summary = [r["id"] for r in records if r["id"] not in summary]
    if missing_from_summary:
        fail(f"{len(missing_from_summary)} record(s) not in record_summary.csv: "
             f"{missing_from_summary[:5]}...", errors)

    n_checked_shape = 0
    n_checked_gain = 0
    n_checked_ann = 0
    n_ann_events_checked = 0

    for entry in records:
        rec_id = entry["id"]
        n_samples = entry["n_samples"]
        gz_path = os.path.join(DATA_DIR, entry["file"])

        if not os.path.isfile(gz_path):
            fail(f"{rec_id}: missing file {gz_path}", errors)
            continue

        with open(gz_path, "rb") as fh:
            raw_bytes = gzip.decompress(fh.read())

        arr = np.frombuffer(raw_bytes, dtype="<i2")  # int16 LE
        expected_len = n_samples * N_CHANNELS
        if arr.shape[0] != expected_len:
            fail(f"{rec_id}: decoded length {arr.shape[0]} != "
                 f"n_samples*16 ({expected_len})", errors)
            continue
        n_checked_shape += 1

        # channel-major: reshape (16, n_samples)
        chmat = arr.reshape(N_CHANNELS, n_samples)

        # --- gain / fs round-trip sanity ---
        ch0_mv = chmat[0].astype(np.float64) / gain
        if not np.all(np.isfinite(ch0_mv)):
            fail(f"{rec_id}: decoded mV contains non-finite values", errors)
        elif ch0_mv.size and np.max(np.abs(ch0_mv)) > PLAUSIBLE_MV_ABS_MAX:
            fail(f"{rec_id}: decoded mV out of plausible EHG range "
                 f"(max abs {np.max(np.abs(ch0_mv)):.2f} mV)", errors)
        else:
            n_checked_gain += 1

        # duration/fs consistency: n_samples should be ~ duration_s * downsample_fs
        # (allow rounding from resample_poly's exact output length)
        expected_n = entry["duration_s"] * downsample_fs
        if abs(n_samples - expected_n) > max(2, 0.01 * expected_n):
            fail(f"{rec_id}: n_samples={n_samples} inconsistent with "
                 f"duration_s={entry['duration_s']} * downsample_fs={downsample_fs} "
                 f"(expected ~{expected_n:.1f})", errors)

        # --- annotation sample bounds + tolerance check ---
        events = ann_doc["records"].get(rec_id, [])
        if events:
            for ev in events:
                expected_sample = round(ev["t_s"] * downsample_fs)
                if abs(ev["sample"] - expected_sample) > SAMPLE_TOLERANCE:
                    fail(f"{rec_id}: annotation sample {ev['sample']} not within "
                         f"tolerance of round(t_s*fs)={expected_sample} "
                         f"(t_s={ev['t_s']}, symbol={ev['symbol']})", errors)
                if not (0 <= ev["sample"] < n_samples):
                    fail(f"{rec_id}: annotation sample {ev['sample']} out of "
                         f"signal bounds [0, {n_samples})", errors)
                n_ann_events_checked += 1
            n_checked_ann += 1

        # --- annotation_counts cross-check vs record_summary.csv ---
        if rec_id in summary:
            exp = summary[rec_id]
            got_C = entry["annotation_counts"].get("C", 0)
            got_c = entry["annotation_counts"].get("(c)", 0)
            if got_C != exp["definite_C"]:
                fail(f"{rec_id}: annotation_counts C={got_C} != "
                     f"record_summary definite_C={exp['definite_C']}", errors)
            if got_c != exp["possible_c"]:
                fail(f"{rec_id}: annotation_counts (c)={got_c} != "
                     f"record_summary possible_c={exp['possible_c']}", errors)

    # --- total committed size ---
    total_size = 0
    for fn in os.listdir(DATA_DIR):
        total_size += os.path.getsize(os.path.join(DATA_DIR, fn))
    total_mb = total_size / 1e6
    print(f"total docs/data/ size: {total_mb:.2f} MB (budget 150 MB)")
    if total_size > BUDGET_BYTES:
        fail(f"docs/data/ total size {total_mb:.2f} MB exceeds 150 MB budget", errors)

    print()
    print("--- self-test summary ---")
    print(f"records in index.json:        {len(records)}")
    print(f"records shape-checked OK:     {n_checked_shape}")
    print(f"records gain/fs-checked OK:   {n_checked_gain}")
    print(f"records with annotations:     {n_checked_ann}")
    print(f"annotation events checked:    {n_ann_events_checked}")
    print(f"downsample_fs (final):        {downsample_fs}")
    print(f"total docs/data size:         {total_mb:.2f} MB")
    print(f"errors:                       {len(errors)}")

    if errors:
        print()
        print(f"SELF-TEST FAILED with {len(errors)} error(s).")
        return 1

    print()
    print("SELF-TEST PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
