# PLAN — Browser-based EHG dataset explorer

A static **GitHub Pages** web app to browse the Icelandic 16-electrode EHG dataset, select
records/channels, run real signal processing in the browser, and view official contraction
annotations overlaid on the traces.

- **Data**: downsampled signals committed to the repo; full 200 Hz/16 ch via in-browser local-file picker.
- **Compute**: **Pyodide** (numpy/scipy in WASM) reusing the validated `filters.py` / `features.py`
  and the DSP/physio detectors. DL (torch) detectors are out of scope in-browser.
- **Annotations**: read-only overlay of official `C` (definite) / `(c)` (possible), toggles for `fm/pm/em`.
- **Capabilities**: 4×4 channel/grid selection · bandpass + envelope + PSD/spectrogram ·
  windowed feature extraction with CSV/JSON export · run DSP/physio detectors, overlay events vs truth.

All interface shapes are frozen in **CONTRACTS.md**. This file governs *how we build it*.

---

## Guiding principle: de-risk plumbing before breadth

The risk in this project is **integration seams** and **whether Pyodide + gzip/binary parsing
work under Pages' CSP** — not the volume of feature code. So we build a **walking skeleton**
end-to-end first, deploy it, and validate the risky plumbing *before* spending agent effort on
feature breadth. Feature work fans out only after the skeleton is green.

We deliberately keep the delegated-agent count **low (2)**. The coupled frontend (IO + UI + plots
+ controls) is built as one coherent unit, because splitting it manufactures the exact seams that
cost more than parallelism saves. Only the genuinely independent, independently-verifiable pieces
(offline data build; Pyodide engine + numeric parity) are delegated to Sonnet.

---

## Roles
- **Opus 4.8 (orchestrator + integrator)**: owns contracts, the walking skeleton, the coupled
  frontend, all browser end-to-end verification (via the claude-in-chrome skill), and integration.
- **Sonnet agents (2, delegated)**: the two independent backend-ish units, each with a self-test.

---

## Phases

### Phase 0 — Skeleton & gate (Opus, no subagents)
Goal: prove the whole stack on ONE record, deployed live.
1. `git init` `ehg-web/`; set Pages source = `main` `/docs`.
2. Freeze **CONTRACTS.md** (done) + write **importable stub files** for every owned path
   (empty functions matching signatures) so later parallel work has real import targets.
3. Build a minimal `scripts/build_web_dataset.py` that emits **just one** record's `.f16.gz`
   + a 1-record `index.json` + `annotations.json` (reuse the isolated-dataset work already done).
4. Walking skeleton in `docs/`: load that record → gunzip/parse → **Pyodide bandpass** on one
   channel → plot one trace → overlay its `C`/`(c)` markers.
5. **Deploy to Pages and verify live in a real browser** (claude-in-chrome): Pyodide loads,
   gunzip works, trace + markers render.

**GATE**: if Pyodide-on-Pages is problematic, decide the fallback here (JS DSP for the interactive
path, Pyodide reserved for detectors) and amend CONTRACTS.md §6/§7 *before* Phase 1. Do not proceed
to breadth until the skeleton is green.

### Phase 1 — Independent backends (2 parallel Sonnet agents)
Run only after the gate is green and contracts are final.

- **Agent DATA** — owns `scripts/build_web_dataset.py`, `scripts/sync_src.py`, `docs/data/*`.
  - Full downsample of all 98 contraction-bearing records (all 16 ch, ascending order) per CONTRACTS §1–§3.
  - Enforce repo budget **≤ ~150 MB**: default `downsample_fs=20`; if over budget, step toward 10 Hz
    (still valid for EHG) **before** dropping channels; record the final choice in `index.json`.
  - Emit `index.json` + `annotations.json` (derive from the isolated dataset / original `.atr`).
  - **Self-test**: reload each `.f16.gz`, assert shape `= n_samples*16`, fs/gain round-trip,
    and that a known annotation `sample` lands within tolerance. Print total committed size.

- **Agent ENGINE** — owns `docs/py/web_engine.py`, `docs/py/_whitelist.py`, `scripts/sync_src.py` deps note.
  - Implement §6 API against the REAL `src/preprocessing/filters.py` / `src/features/features.py`.
  - Curate `_whitelist.py` (DSP/physio only; confirm each is numpy/scipy-only before adding).
  - **Self-test (offline, native Python — the parity gate)**: for a sample signal, assert
    `web_engine.process(...)["filtered"]` equals calling `filters.bandpass` directly (allclose),
    features match `win_features`, and each whitelisted detector runs and returns the §6 shape.
    This runs in plain CPython in CI, independent of the browser.

### Phase 2 — Frontend build-out (Opus, or ONE Sonnet agent on the proven skeleton)
On top of the green skeleton, flesh out the coupled UI as a single unit:
`io.js` (compact + local WFDB), `app.js` (record browser + filters + 4×4 grid selector + state),
`plots.js` (multi-channel traces, envelope, PSD/spectrogram, C/(c) + `fm/pm/em` overlays, detector
events vs truth), `controls.js` (filter/feature/detector panels), `engine.js` (lazy Pyodide bridge),
feature CSV/JSON export.

### Phase 3 — Integration & delivery (Opus)
- Wire DATA + ENGINE outputs into the frontend; run browser end-to-end across several records
  (pregnancy + labour, high/low annotation counts) and the local full-res picker.
- Polish: loading states, error handling (missing Pyodide, bad local file), responsive layout.
- README with usage + **PhysioNet attribution** and a note that committed data is a downsampled
  derivative (verify the source's license terms before asserting one).
- Confirm Pages build; deliver the live URL.

---

## Orchestration mechanics (for the delegated agents)
- **Disjoint file ownership** (table below) → no shared files → no merge conflicts.
- Each agent prompt includes: its file list, the relevant CONTRACTS.md sections, "touch only your
  files," and its required self-test. An agent is not "done" until its self-test passes.
- Agents run against the Phase-0 stubs, so imports resolve from the start.
- The orchestrator reviews each diff and runs the **browser** end-to-end — that verification cannot
  be delegated (a Sonnet subagent can't reliably validate Pyodide-in-a-real-browser).

| Owner | Files | Verifies by |
|---|---|---|
| Opus | `docs/index.html`, `docs/css/*`, `docs/js/*`, skeleton, integration | live browser (claude-in-chrome) |
| Agent DATA | `scripts/build_web_dataset.py`, `scripts/sync_src.py`, `docs/data/*` | reload/round-trip self-test + size report |
| Agent ENGINE | `docs/py/web_engine.py`, `docs/py/_whitelist.py` | offline numeric parity vs `src/` in CI |

---

## Key decisions (locked unless the gate reopens them)
- **Compute = Pyodide** (reuse real pipeline + run detectors). Fallback path defined at the Phase-0 gate.
- **Committed data** = downsampled (default 20 Hz), all 16 ch, int16, gzip, channel-major, budget ≤150 MB.
- **Full-res** = optional in-browser local-file picker (no hosting of 2.1 GB).
- **Annotations** = read-only overlay from committed JSON.
- **Detectors in browser** = DSP/physio (numpy/scipy) only; DL stays offline.
- **Pages** = `main` `/docs`, no server, no required build step.

## Open risks & mitigations
| Risk | Mitigation |
|---|---|
| Pyodide won't load / CSP blocks it on Pages | Phase-0 gate proves it before any breadth; JS-DSP fallback ready |
| Repo size over budget | build-script rule: lower rate before dropping channels; report size |
| Detector registry pulls in torch | `_whitelist.py` explicit imports; never `import src.methods` |
| Compact vs WFDB layout mismatch (channel-major vs interleaved) | codified in CONTRACTS §1/§5; io.js self-checks on one sample |
| Pyodide round-trip per slider tweak feels slow | debounce; lazy-load; optional JS instant-preview later |

## Changelog
- Phase 2 complete (2026-09-15): coupled frontend built on the green skeleton (Opus, no
  subagents) and verified headless (Playwright/Chromium) end-to-end, zero console errors.
  Delivered: record browser (98 records, type filter, prev/next); local full-res WFDB picker
  (§5 — format-16 de-interleave + ascending EHG1..16 reorder, verified on a real 200 Hz
  746k-sample pair); 4×4 grid channel selector; multi-channel montage (DC-removed stacked
  traces, raw/filtered toggle, RMS-envelope overlay, C/(c)/fm/pm/em/pos annotation lines);
  Welch PSD (log-y) + STFT spectrogram (canvas heatmap); windowed feature extraction with
  CSV/JSON export; all 8 detectors with editable params, events-vs-truth spans + peaks +
  detection-stat strip. Three findings fixed during integration:
  1. **Pyodide moved into a Web Worker** (`engine.worker.js`; `engine.js` is now a proxy).
     Main-thread latency during a full-record feature crunch measured **1 ms** — the UI
     never freezes. This is the right home for all heavy compute (CONTRACTS §7 clarified).
  2. **`web_engine.extract_features` was ~21× too slow**: it re-invoked `win_features`
     once per (channel, feature-name) instead of once per channel (samp_entropy is O(L²)).
     Full-record extraction went from >8 min → ~25 s native; offline parity gate still green.
  3. **manifest package name `pywt` → `pywavelets`** (`loadPackage("pywt")` errors); extras
     now load best-effort so a missing one only disables its detector (CONTRACTS §8).
  Known remaining polish (Phase 3): full-record feature extraction is still ~1–2 min in
  WASM (non-freezing but slow — candidate for per-window progress / region-limited extract);
  PSD log-axis tick labels are coarse; `.atr` local-annotation parsing still deferred (§5).
- 1.0.0 (2026-09-15): initial plan; leaner 2-agent + skeleton-first execution shape.
- Phase 1 complete (2026-09-15): two parallel Sonnet agents, disjoint files, both self-tests green
  and orchestrator-reverified. **Agent DATA**: all 98 records built at `downsample_fs=20`
  (budget loop `[20,15,10]` in place but not needed), total `docs/data/` = 88.42 MB (< 150 MB),
  no channel drops; reload/round-trip + `record_summary.csv` C/(c) cross-check pass (933 events).
  **Agent ENGINE**: `list_detectors()`/`run_detector()` implemented against 8 browser-safe detectors
  (baseline_rms · dsp_energy ×4 · dsp_freq: zcr_burst + wavelet_energy · physio_spectral); offline
  numeric-parity gate exact (max abs diff 0.0 vs `src/`); synced modules byte-identical to source.
  Ownership note: `scripts/sync_src.py` assigned solely to ENGINE (DATA didn't need it) → zero shared
  files. Two follow-ups for Phase 2 `engine.js`: (1) `manifest.json.pyodide_packages=["pywt"]` — must
  `pyodide.loadPackage("pywt")` before `runDetector("wavelet_energy")`; (2) `config.py` is a trimmed
  browser-safe subset (original's import-time `os.makedirs` stripped), per §0. Headless browser
  regression (Playwright/Chromium): skeleton still boots, all 18 py files load, bandpass green.
- Phase 0 complete (2026-09-15): walking skeleton green on `ice002_p_2of3`.
  **GATE PASSED** — Pyodide 0.26.4 + numpy/scipy load and run a real `filters.bandpass`
  in-browser (~114 ms), `DecompressionStream` gunzips the channel-major `.f16.gz`, uPlot
  renders the trace with C/(c)/other overlays. No JS-DSP fallback needed; CONTRACTS §1–§7
  stand. One finding: uPlot vendored to `docs/vendor/` (not on cdnjs) → CONTRACTS 1.0.1 §8.
  Verified headless (Playwright/Chromium) since the claude-in-chrome extension was unavailable.
  Known Phase-2 nit: raw + filtered share one y-axis, so the ~0.09 mV filtered trace looks
  flat against the 0–25 mV raw DC — needs separate/normalised axes in the build-out.
