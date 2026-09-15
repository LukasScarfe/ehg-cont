# Browser-safe detectors: numpy/scipy only. Extend deliberately (Phase 1, Agent
# ENGINE) — confirm each module is numpy/scipy(/scikit-learn)-only BEFORE adding,
# and add its source file to SAFE_MODULES in scripts/sync_src.py so it is served.
# NEVER `import src.methods` (that auto-loads DL/torch).
#
# Verified (Phase 1, Agent ENGINE), by actually importing each module natively
# with src/ on sys.path and inspecting sys.modules: every module below imports
# ONLY numpy/scipy/stdlib at module scope (via base.py, evaluation/metrics.py,
# config.py, methods/_dsp_common.py, methods/physio_common.py,
# features/features.py — all numpy/scipy/stdlib). Two caveats handled:
#   - evaluation/metrics.py has a LAZY `from sklearn.metrics import
#     roc_auc_score` inside sample_level(), reachable only from evaluate()
#     (the offline eval harness). web_engine.run_detector() never calls
#     evaluate()/sample_level(), so sklearn is never imported at import or
#     detector-run time — confirmed empty in sys.modules after running every
#     detector below.
#   - src.methods.dsp_freq.WaveletEnergyDetector.detect() does a RUNTIME
#     `import pywt` (not numpy/scipy). Decision: enable it anyway (option a) —
#     pywt is a real Pyodide package. Documented in scripts/sync_src.py's
#     PYODIDE_PACKAGES and recorded in docs/py/manifest.json
#     ("pyodide_packages": ["pywt"]) so Phase 2 (engine.js) knows to
#     `pyodide.loadPackage("pywt")` before calling run_detector("wavelet_energy", ...).
BROWSER_DETECTORS = [
    "src.methods.baseline_rms",     # bandpass_rms
    "src.methods.dsp_energy",       # tkeo_energy, slope_hysteresis, nonlinear_entropy,
                                     #   kalman_envelope (all numpy/scipy only)
    "src.methods.dsp_freq",         # zcr_burst (numpy/scipy only); wavelet_energy
                                     #   (ALSO needs the "pywt" Pyodide package — see
                                     #   docs/py/manifest.json "pyodide_packages")
    "src.methods.physio_spectral",  # physio_spectral
    # add physio_* / ml_* ONLY after confirming numpy/scipy(/scikit-learn)-only deps
]
