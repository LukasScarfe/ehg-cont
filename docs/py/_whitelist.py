# Browser-safe detectors: numpy/scipy only. Extend deliberately (Phase 1, Agent
# ENGINE) — confirm each module is numpy/scipy(/scikit-learn)-only BEFORE adding,
# and add its source file to SAFE_MODULES in scripts/sync_src.py so it is served.
# NEVER `import src.methods` (that auto-loads DL/torch).
BROWSER_DETECTORS = [
    # "src.methods.baseline_rms",     # bandpass_rms
    # "src.methods.dsp_energy",       # tkeo_energy, slope_hysteresis, nonlinear_entropy
    # "src.methods.dsp_freq",         # zcr_burst, wavelet_energy
    # "src.methods.physio_spectral",  # physio_spectral
]
