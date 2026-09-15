# CONTRACTS — frozen interfaces for the EHG web explorer

> These interfaces are **frozen** before any parallel work begins. Every module builds
> against exactly these shapes. Changing a contract is an orchestrator (Opus) decision,
> recorded here with a version bump. Agents MUST NOT silently diverge.
>
> Version: 1.0.0 · Status: DRAFT (pending Phase 0 skeleton validation)

---

## 0. Repo layout & Pages

```
ehg-web/
  docs/                     ← GitHub Pages source (Pages: main branch, /docs)
    index.html
    css/app.css
    js/
      io.js                 ← data loading (compact + local WFDB)
      engine.js             ← thin JS wrapper around Pyodide
      plots.js              ← rendering + overlays
      controls.js           ← processing panel wiring
      app.js                ← UI shell, state, record browser, grid
    py/
      web_engine.py         ← Pyodide entrypoint (imports reused src/)
      _whitelist.py         ← browser-safe detector import list
    data/
      index.json
      annotations.json
      <record_id>.f16.gz    ← one per record
    vendor/                 ← pinned pyodide loader shim, gunzip, plotting lib (or CDN)
  src/                      ← REUSED, copied/synced from ../uterine_contraction_detection
    preprocessing/filters.py
    features/features.py  features/windows.py
    methods/… (DSP/physio only + base.py; NO dl_* in the browser path)
    config.py (trimmed browser-safe subset if needed)
  scripts/
    build_web_dataset.py    ← offline data builder (reproducible)
    sync_src.py             ← copies the browser-safe subset of ../src into docs/py path
  CONTRACTS.md  PLAN.md  README.md
```

Pages serves `docs/` statically. **No server-side compute.** Everything runs client-side.

---

## 1. Compact signal format — `docs/data/<record_id>.f16.gz`

- **Encoding**: gzip stream of raw `int16` little-endian samples.
- **Layout**: **channel-major** — all samples of channel 0, then all of channel 1, … channel 15.
  (Contiguous per-channel slices; shape is carried by `index.json`, file has NO header.)
- **Value → physical**: `mV = raw_int16 / gain`, where `gain = 131.068` (from source `.hea`).
- **Downsampling**: produced from the 200 Hz original by anti-aliased decimation
  (`scipy.signal.resample_poly`) to `downsample_fs` (see index). Rounded to int16.
- **Sample count** `n_samples` = per-channel length after downsampling (same for all 16 channels).
- Total decoded float array length = `n_samples * 16`.

Decode (reference):
```
bytes -> gunzip -> Int16Array(len = n_samples*16)
channel c samples = view[c*n_samples : (c+1)*n_samples]
float mV[i] = int16[i] / gain
```

---

## 2. `docs/data/index.json`

```json
{
  "schema": "ehg-index/1.0.0",
  "source": "Icelandic 16-electrode EHG Database (ice16ehgdb)",
  "generated_utc": "2026-09-15T00:00:00Z",
  "orig_fs": 200,
  "downsample_fs": 20,
  "dtype": "int16",
  "layout": "channel-major",
  "gain": 131.068,
  "channels": ["EHG1","EHG2","EHG3","EHG4","EHG5","EHG6","EHG7","EHG8",
               "EHG9","EHG10","EHG11","EHG12","EHG13","EHG14","EHG15","EHG16"],
  "grid": [[1,2,3,4],[5,6,7,8],[9,10,11,12],[13,14,15,16]],
  "records": [
    {
      "id": "ice004_p_1of1",
      "subject": "ice004",
      "type": "pregnancy",
      "n_samples": 79400,
      "duration_s": 3970.0,
      "ga_recording": "36/5",
      "annotation_counts": { "C": 0, "(c)": 1, "fm": 3, "pm": 1, "em": 2, "pos": 0 },
      "file": "ice004_p_1of1.f16.gz",
      "bytes": 512345
    }
  ]
}
```

Notes:
- `channels` order MUST match the channel-major order written to the `.f16.gz`.
- `grid` is the physical 4×4 electrode layout (electrode numbers) for the grid selector.
  NOTE: header signal order is NOT ascending (e.g. `EHG1, EHG10, EHG11, …`); the build
  script MUST reorder channels into ascending `EHG1..EHG16` before writing, so `channels`
  above is literally ascending and `grid` indexes into it directly.
- `annotation_counts` includes all symbols; UI defaults to showing `C` + `(c)`.

---

## 3. `docs/data/annotations.json`

```json
{
  "schema": "ehg-annotations/1.0.0",
  "records": {
    "ice004_p_1of1": [
      { "t_s": 1630.08, "sample": 32602, "symbol": "(c)", "type": "possible" }
    ],
    "ice002_p_2of3": [
      { "t_s": 1150.88, "sample": 23018, "symbol": "C",   "type": "definite" }
    ]
  }
}
```

- `t_s` = event time in seconds (from original `.atr`, fs=200).
- `sample` = index into the **downsampled** signal (`round(t_s * downsample_fs)`), for direct plotting.
- `type`: `"definite"` for `C`, `"possible"` for `(c)`; other symbols carry `type: "other"`.
- Full set of symbols retained: `C, (c), fm, pm, em, pos`. UI filters by symbol.

---

## 4. JS `Record` object (the shared currency between io/app/plots/engine)

```js
/**
 * @typedef {Object} Record
 * @property {string} id
 * @property {number} fs            // Hz of the data currently held (20 for compact, 200 for local)
 * @property {string[]} chNames     // ascending ["EHG1"... "EHG16"]
 * @property {Float32Array[]} data  // one Float32Array (mV) per channel, length n_samples
 * @property {number} nSamples
 * @property {"compact"|"local"} source
 * @property {object} meta          // passthrough of the index.json record entry (may be null for local)
 */
```

Every producer (io.js) and every consumer (app/plots/engine) uses exactly this shape.

---

## 5. Local full-res files (WFDB) — parsing contract for `io.js`

The local-file picker accepts a `.hea` + matching `.dat` (optionally `.atr`).

`.hea` (WFDB, format 16), first line: `<name> <nsig> <fs> <nsamp>`; then one line per signal:
`<file> <format> <gain>/<units> <adcres> <adczero> <initval> <checksum> <blocksize> <label>`

- `format` = 16 → `.dat` is int16 LE, **sample-interleaved across signals**
  (s0ch0, s0ch1, …, s0ch15, s1ch0, …). NOTE: interleaved on disk, unlike our channel-major compact.
- `gain` = 131.068 → mV = raw/gain.
- Reorder parsed channels to ascending `EHG1..EHG16` to match the compact contract.
- Emits a `Record` with `fs=200`, `source:"local"`.

`.atr` parsing is OPTIONAL for v1 (annotations come from committed JSON for repo records).
If a local `.atr` is provided, parse via the same MIT annotation format; otherwise no overlay.

---

## 6. Pyodide engine API — `docs/py/web_engine.py`

Called from `engine.js`. All functions take/return JSON-serializable data or typed arrays
marshalled by Pyodide. `sig` is a 1-D or (C,N) numpy array built from the `Record`.

```python
def process(sig, fs, params) -> dict:
    """
    params = {
      "band": [lo_hz, hi_hz] | null,   # bandpass; null = skip
      "causal": bool,                  # bandpass_causal vs zero-phase
      "envelope": bool,                # add analytic/RMS envelope
      "psd": bool,                     # add Welch PSD
      "spectrogram": bool
    }
    returns {
      "filtered": float32[N] | float32[C][N],
      "envelope": float32[N] | null,
      "psd_f": float32[] | null, "psd_p": float32[] | null,
      "spec_f": float32[] | null, "spec_t": float32[] | null, "spec_S": float32[][] | null
    }
    """

def extract_features(sig, fs, win_s, hop_s) -> dict:
    """ returns { "names": string[], "t_s": float32[], "matrix": float32[nWin][nFeat] }
        Uses src.features.features.win_features / feature_names. """

def list_detectors() -> list[dict]:
    """ [{ "name": str, "family": "dsp"|"physio", "causal": bool, "params": {..defaults} }]
        Only browser-safe (numpy/scipy) detectors. """

def run_detector(name, sig, fs, params) -> dict:
    """ returns {
          "events": [{ "onset": s, "offset": s, "peak": s, "score": float }],
          "stat":   float32[N] | null   # per-sample detection statistic (may be decimated)
        } """
```

Rules:
- `web_engine.py` imports the REAL `src/preprocessing/filters.py` and `src/features/features.py`.
- Detector imports come from `_whitelist.py` ONLY — never `import src.methods` (that auto-loads DL/torch).
- Multichannel input for detectors: reduce per the detector's own contract (most take (C,N)).

`_whitelist.py`:
```python
# Browser-safe detectors: numpy/scipy only. Extend deliberately.
BROWSER_DETECTORS = [
    "src.methods.baseline_rms",     # bandpass_rms
    "src.methods.dsp_energy",       # tkeo_energy, slope_hysteresis, nonlinear_entropy
    "src.methods.dsp_freq",         # zcr_burst, wavelet_energy
    "src.methods.physio_spectral",  # physio_spectral
    # add physio_* / ml_* ONLY after confirming numpy/scipy(/scikit-learn)-only deps
]
```

---

## 7. `engine.js` — JS ↔ Pyodide bridge

```js
await Engine.ready()                              // lazy-loads Pyodide + src on first call
Engine.process(record, chIndices, params) -> Promise<ProcessResult>
Engine.extractFeatures(record, chIndices, winS, hopS) -> Promise<FeatureTable>
Engine.listDetectors() -> Promise<DetectorInfo[]>
Engine.runDetector(name, record, chIndices, params) -> Promise<DetectionResult>
```

- Pyodide is loaded **lazily** (only on first processing call) so raw traces render instantly.
- Result shapes mirror §6 verbatim.

---

## 8. External libraries / CSP (GitHub Pages)

- Pyodide: pinned version from the official CDN (jsDelivr). Loaded lazily.
- gunzip: `DecompressionStream('gzip')` where available; else a small pinned pure-JS fallback.
- Plotting: one pinned library (uPlot preferred for large time series; Plotly acceptable). Pin exact version.
- No build step required to *serve*; a bundler is optional. Keep imports CDN-pinned or vendored in `docs/vendor/`.

---

## 9. Change control
Any change to §1–§7 = bump `Version` at top, note it in PLAN.md changelog, and notify affected agents.
