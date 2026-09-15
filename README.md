# EHG Explorer

A static web app (GitHub Pages) to browse the **Icelandic 16-electrode Electrohysterogram
(EHG) Database**, select records and channels, run signal processing in the browser, and view
the official contraction annotations overlaid on the traces.

> Status: **scaffolding**. See [PLAN.md](PLAN.md) for the execution plan and
> [CONTRACTS.md](CONTRACTS.md) for the frozen module interfaces.

## What it does (target)
- Browse records; filter by type (pregnancy/labour) and annotation content.
- Select channels via the physical 4×4 electrode grid.
- Interactive bandpass, envelope, PSD/spectrogram — running the real Python pipeline via **Pyodide**.
- Windowed feature extraction with CSV/JSON export.
- Run DSP/physio contraction detectors and overlay detected events vs. official markings.
- Read-only overlay of official `C` (definite) / `(c)` (possible) contraction annotations.

## Data
Committed data (`docs/data/`) is a **downsampled derivative** of the source database for fast,
static hosting. Full 200 Hz / 16-channel signals can be loaded in-browser from a local copy of
the original files (no upload; processed client-side).

## Attribution
Source: Alexandersson, A., Steingrimsdottir, T., Terrien, J., Marque, C., Karlsson, B.
*The Icelandic 16-electrode electrohysterogram database.* Sci. Data 2:150017 (2015).
doi:10.1038/sdata.2015.17. Redistributed data here is downsampled; see PLAN.md. License terms
of the source to be verified before redistribution.

## Layout
```
docs/     GitHub Pages root (index.html, js/, py/, data/, css/)
src/      reused signal-processing modules (filters, features, DSP/physio detectors)
scripts/  offline data builder + src sync
```

## Develop / serve locally
```
python3 -m http.server -d docs 8000   # then open http://localhost:8000
```
