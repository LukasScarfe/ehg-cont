#!/usr/bin/env python3
"""
sync_src.py — copy the *browser-safe* subset of the reused signal-processing
code into docs/py/src/ so Pyodide can import it, and emit docs/py/manifest.json
(the file list engine.js fetches into the Pyodide virtual filesystem).

Browser-safe = numpy/scipy only. We deliberately DO NOT copy the original
package __init__.py files (they pull in config/methods, some of which import
torch); instead we write empty __init__.py shims so `import src...` resolves to
just the whitelisted leaf modules.

Phase 0 scope: filters.py + features.py (enough for process() / extract_features()).
Phase 1 (Agent ENGINE) extends SAFE_MODULES with the DSP/physio detector modules
listed in docs/py/_whitelist.py, after confirming each is numpy/scipy-only.

Owner: Agent DATA/ENGINE (Phase 1). This Phase-0 version is written by the
orchestrator to make the walking skeleton importable.
"""
from __future__ import annotations

import json
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
WEB_ROOT = os.path.dirname(HERE)                                  # ehg-web/
SRC_ROOT = os.path.abspath(os.path.join(WEB_ROOT, "..",
                                        "uterine_contraction_detection", "src"))
PY_DIR = os.path.join(WEB_ROOT, "docs", "py")
DST_SRC = os.path.join(PY_DIR, "src")

# Modules copied verbatim from the reused project (paths relative to src/).
# Each MUST be numpy/scipy-only. Confirm before extending.
SAFE_MODULES = [
    "preprocessing/filters.py",
    "features/features.py",
]

# Package dirs that need an (empty) __init__.py so imports resolve.
PACKAGE_DIRS = ["", "preprocessing", "features"]

# Extra py entrypoints already living in docs/py that the browser must load.
ENTRYPOINTS = ["web_engine.py", "_whitelist.py"]


def main() -> None:
    if not os.path.isdir(SRC_ROOT):
        raise SystemExit(f"reused src/ not found at {SRC_ROOT}")

    # Fresh copy of the synced tree.
    if os.path.isdir(DST_SRC):
        shutil.rmtree(DST_SRC)
    os.makedirs(DST_SRC, exist_ok=True)

    manifest_files: list[str] = list(ENTRYPOINTS)

    for pkg in PACKAGE_DIRS:
        pkg_dir = os.path.join(DST_SRC, pkg) if pkg else DST_SRC
        os.makedirs(pkg_dir, exist_ok=True)
        init_path = os.path.join(pkg_dir, "__init__.py")
        with open(init_path, "w") as fh:
            fh.write("# synced by scripts/sync_src.py — browser-safe shim\n")
        rel = os.path.join("src", pkg, "__init__.py") if pkg else os.path.join("src", "__init__.py")
        manifest_files.append(rel.replace(os.sep, "/"))

    for rel in SAFE_MODULES:
        src_file = os.path.join(SRC_ROOT, rel)
        if not os.path.isfile(src_file):
            raise SystemExit(f"safe module missing: {src_file}")
        dst_file = os.path.join(DST_SRC, rel)
        os.makedirs(os.path.dirname(dst_file), exist_ok=True)
        shutil.copyfile(src_file, dst_file)
        manifest_files.append(("src/" + rel).replace(os.sep, "/"))

    # Verify entrypoints exist (written by hand / earlier steps).
    for ep in ENTRYPOINTS:
        if not os.path.isfile(os.path.join(PY_DIR, ep)):
            raise SystemExit(f"entrypoint missing in docs/py: {ep}")

    manifest = {
        "schema": "ehg-pymanifest/1.0.0",
        "root": "py",                       # fetched relative to docs/ as py/<file>
        "sys_path": "/py",                  # mount point inside Pyodide FS
        "entry": "web_engine",              # module to import
        "files": manifest_files,
    }
    manifest_path = os.path.join(PY_DIR, "manifest.json")
    with open(manifest_path, "w") as fh:
        json.dump(manifest, fh, indent=2)

    print(f"synced {len(SAFE_MODULES)} module(s) + shims into {DST_SRC}")
    print(f"manifest -> {manifest_path} ({len(manifest_files)} files)")


if __name__ == "__main__":
    main()
