#!/usr/bin/env python3
"""
scripts/selftest_engine.py — Agent ENGINE's offline numeric-parity self-test
(PLAN.md Phase 1 "Agent ENGINE" gate; CONTRACTS.md §6).

Runs in plain native CPython, independent of any browser. Puts ONLY docs/py
(and its synced docs/py/src) on sys.path — proving the browser will have
everything it needs to import and run web_engine — then:

  1. process() parity vs directly calling src.preprocessing.filters.bandpass
     (single- and multi-channel).
  2. extract_features() parity vs directly calling
     src.features.features.win_features / feature_names.
  3. For EACH whitelisted detector: list_detectors() + run_detector(...) match
     the CONTRACTS §6 shapes exactly.
  4. Byte-parity: every verbatim-copied module under docs/py/src matches its
     original ../uterine_contraction_detection/src source file exactly
     (confirms sync_src.py copied faithfully; skipped if that repo isn't
     present, e.g. in a CI checkout that only has ehg-web/).

Exit non-zero on any failure.
"""
from __future__ import annotations

import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
WEB_ROOT = os.path.dirname(HERE)
PY_DIR = os.path.join(WEB_ROOT, "docs", "py")
ORIG_SRC = os.path.abspath(os.path.join(WEB_ROOT, "..",
                                        "uterine_contraction_detection", "src"))

# Ensure ONLY docs/py is on sys.path for the import under test (proves the
# browser's synced tree is self-sufficient) — scrub anything that could shadow
# it (e.g. the original, unsynced repo's own "src" package).
sys.path = [p for p in sys.path
           if os.path.basename(p.rstrip(os.sep)) != "src"]
if PY_DIR not in sys.path:
    sys.path.insert(0, PY_DIR)

import numpy as np  # noqa: E402  (after sys.path setup, deliberately)

failures: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {label}" + (f" — {detail}" if detail else ""))
    if not cond:
        failures.append(label)


def main() -> int:
    try:
        import web_engine
    except Exception:
        traceback.print_exc()
        check("import web_engine from docs/py alone (no repo src/ on sys.path)", False)
        return 1
    check("import web_engine from docs/py alone (no repo src/ on sys.path)", True,
          f"web_engine.__file__={web_engine.__file__}")

    rng = np.random.default_rng(0)
    fs = 20.0
    n = 20 * 600  # 600 s synthetic recording
    t = np.arange(n) / fs
    # bursty fast-wave-like signal on a noisy baseline (~contraction shape).
    sig1d = (0.02 * np.sin(2 * np.pi * 0.5 * t) * (np.sin(2 * np.pi * 0.002 * t) > 0.5)
             + 0.01 * rng.standard_normal(n))
    sig_mc = np.vstack([sig1d + 0.002 * rng.standard_normal(n) for _ in range(4)])

    # ------------------------------------------------------------------ #
    # 1. process() parity vs direct filters.bandpass
    # ------------------------------------------------------------------ #
    from src.preprocessing.filters import bandpass
    band = [0.2, 1.2]
    params = {"band": band, "causal": False, "envelope": False,
             "psd": False, "spectrogram": False}

    out = web_engine.process(sig1d, fs, params)
    direct = bandpass(sig1d.astype(np.float64), band, fs).astype(np.float32)
    diff = float(np.max(np.abs(out["filtered"] - direct)))
    check("process().filtered matches filters.bandpass (1-D, allclose)",
          np.allclose(out["filtered"], direct, atol=1e-5),
          f"max abs diff = {diff:.3e}")

    out_mc = web_engine.process(sig_mc, fs, params)
    direct_mc = bandpass(sig_mc.astype(np.float64), band, fs).astype(np.float32)
    diff_mc = float(np.max(np.abs(out_mc["filtered"] - direct_mc)))
    check("process().filtered matches filters.bandpass (multichannel, allclose)",
          np.allclose(out_mc["filtered"], direct_mc, atol=1e-5),
          f"max abs diff = {diff_mc:.3e}")

    # causal path too (bandpass_causal), since process() branches on it.
    from src.preprocessing.filters import bandpass_causal
    out_c = web_engine.process(sig1d, fs, {**params, "causal": True})
    direct_c = bandpass_causal(sig1d.astype(np.float64), band, fs).astype(np.float32)
    diff_c = float(np.max(np.abs(out_c["filtered"] - direct_c)))
    check("process().filtered matches filters.bandpass_causal (causal=True, allclose)",
          np.allclose(out_c["filtered"], direct_c, atol=1e-5),
          f"max abs diff = {diff_c:.3e}")

    # ------------------------------------------------------------------ #
    # 2. extract_features() parity vs direct win_features / feature_names
    # ------------------------------------------------------------------ #
    from src.features.features import win_features, feature_names
    win_s, hop_s = 60.0, 30.0
    feat = web_engine.extract_features(sig1d, fs, win_s, hop_s)
    names = feature_names()
    check("extract_features() names match feature_names()", feat["names"] == names,
          f"{feat['names']}")

    L = int(win_s * fs)
    direct_fv = np.array([win_features(sig1d[:L], fs)[k] for k in names], dtype=np.float32)
    got_fv = feat["matrix"][0]
    diff_feat = float(np.max(np.abs(got_fv - direct_fv)))
    check("extract_features() first window matches win_features() (allclose)",
          np.allclose(got_fv, direct_fv, atol=1e-4),
          f"max abs diff = {diff_feat:.3e}")

    # ------------------------------------------------------------------ #
    # 3. list_detectors() / run_detector() — CONTRACTS §6 shapes
    # ------------------------------------------------------------------ #
    from _whitelist import BROWSER_DETECTORS
    check("_whitelist.BROWSER_DETECTORS is non-empty", len(BROWSER_DETECTORS) > 0,
          str(BROWSER_DETECTORS))

    dets = web_engine.list_detectors()
    check("list_detectors() returns a non-empty list", isinstance(dets, list) and len(dets) > 0,
          f"{len(dets)} detectors")

    expected_names = {"bandpass_rms", "tkeo_energy", "slope_hysteresis",
                       "nonlinear_entropy", "kalman_envelope", "zcr_burst",
                       "wavelet_energy", "physio_spectral"}
    got_names = {d["name"] for d in dets}
    check("list_detectors() covers all registered detectors from whitelisted modules",
          got_names == expected_names, f"got={sorted(got_names)}")

    for d in dets:
        shape_ok = (set(d) == {"name", "family", "causal", "params"}
                    and d["family"] in ("dsp", "physio")
                    and isinstance(d["causal"], bool)
                    and isinstance(d["params"], dict))
        check(f"list_detectors() shape OK for {d['name']!r}", shape_ok, str(d))

    for d in dets:
        name = d["name"]
        try:
            res = web_engine.run_detector(name, sig_mc, fs, {})
        except Exception:
            traceback.print_exc()
            check(f"run_detector({name!r}) runs without raising", False)
            continue
        check(f"run_detector({name!r}) runs without raising", True)

        shape_ok = set(res) == {"events", "stat"} and isinstance(res["events"], list)
        check(f"run_detector({name!r}) top-level shape {{events, stat}}", shape_ok,
              str(list(res)))

        events_ok = all(
            isinstance(e, dict) and set(e) == {"onset", "offset", "peak", "score"}
            and isinstance(e["onset"], float)
            and (e["offset"] is None or isinstance(e["offset"], float))
            and (e["peak"] is None or isinstance(e["peak"], float))
            and (e["score"] is None or isinstance(e["score"], float))
            for e in res["events"]
        )
        check(f"run_detector({name!r}) events are {{onset,offset,peak,score}} floats",
              events_ok, f"n_events={len(res['events'])}")

        stat = res["stat"]
        stat_ok = stat is None or (isinstance(stat, np.ndarray)
                                   and stat.dtype == np.float32 and stat.ndim == 1)
        check(f"run_detector({name!r}) stat is float32[N] or None", stat_ok,
              "None" if stat is None else f"dtype={stat.dtype} shape={stat.shape}")

    # 1-D signal path (CONTRACTS §6: sig is 1-D or (C,N))
    try:
        res1d = web_engine.run_detector("bandpass_rms", sig1d, fs, {})
        check("run_detector() accepts 1-D sig", isinstance(res1d["events"], list))
    except Exception:
        traceback.print_exc()
        check("run_detector() accepts 1-D sig", False)

    # detector-run must never import sklearn (evaluation.metrics' lazy import
    # is only reachable via evaluate()/sample_level(), never from run_detector).
    check("sklearn was never imported by any run_detector() call",
          not any("sklearn" in m for m in sys.modules))

    # ------------------------------------------------------------------ #
    # 4. byte-parity: verbatim-copied modules match the original repo
    # ------------------------------------------------------------------ #
    verbatim = [
        "preprocessing/filters.py", "features/features.py", "evaluation/metrics.py",
        "methods/base.py", "methods/_dsp_common.py", "methods/physio_common.py",
        "methods/baseline_rms.py", "methods/dsp_energy.py", "methods/dsp_freq.py",
        "methods/physio_spectral.py",
    ]
    if os.path.isdir(ORIG_SRC):
        for rel in verbatim:
            synced = os.path.join(PY_DIR, "src", rel)
            orig = os.path.join(ORIG_SRC, rel)
            with open(synced, "rb") as f1, open(orig, "rb") as f2:
                same = f1.read() == f2.read()
            check(f"synced src/{rel} is byte-identical to the original repo source", same)
    else:
        print(f"[SKIP] byte-parity check — original repo src not found at {ORIG_SRC}")

    print()
    if failures:
        print(f"SELF-TEST FAILED: {len(failures)} check(s) failed:")
        for f in failures:
            print("  -", f)
        return 1
    print(f"SELF-TEST PASSED: all {len(dets)} whitelisted detectors + process()/"
          f"extract_features() parity checks green.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
