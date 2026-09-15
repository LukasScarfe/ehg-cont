/**
 * engine.worker.js — Pyodide runs here, OFF the main thread, so heavy DSP,
 * feature extraction (samp_entropy over the whole record), and detectors never
 * freeze the UI. Mirrors CONTRACTS §6 exactly; the main-thread Engine (engine.js)
 * is a thin postMessage proxy over this worker.
 *
 * Protocol:
 *   main → worker: { id, op, payload }
 *   worker → main: { id, ok, result } | { id, ok:false, error } | { progress }
 */
/* global loadPyodide */
const PYODIDE_VERSION = "0.26.4";
const PYODIDE_URL = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;

let pyodide = null;
let initP = null;

function progress(msg) {
  self.postMessage({ progress: msg });
}

function mkdirp(dir) {
  const parts = dir.split("/").filter(Boolean);
  let cur = "";
  for (const p of parts) {
    cur += "/" + p;
    try { pyodide.FS.mkdir(cur); } catch (e) { /* exists */ }
  }
}

async function init() {
  progress("loading Pyodide runtime…");
  importScripts(PYODIDE_URL + "pyodide.js");
  pyodide = await loadPyodide({ indexURL: PYODIDE_URL });

  // manifest + py files live at /py/ (one level up from /js/).
  const manifest = await fetch("../py/manifest.json").then((r) => r.json());

  progress("loading numpy, scipy (WASM)…");
  await pyodide.loadPackage(["numpy", "scipy"]);

  // Optional extras (pywavelets → import pywt, only for wavelet_energy).
  for (const pkg of manifest.pyodide_packages || []) {
    try {
      progress(`loading ${pkg} (WASM)…`);
      await pyodide.loadPackage(pkg);
    } catch (e) {
      console.warn(`optional package '${pkg}' failed to load`, e);
    }
  }

  progress("mounting signal-processing code…");
  const base = "../" + manifest.root + "/";
  for (const rel of manifest.files) {
    const text = await fetch(base + rel).then((r) => {
      if (!r.ok) throw new Error(`${base + rel} ${r.status}`);
      return r.text();
    });
    const full = manifest.sys_path + "/" + rel;
    mkdirp(full.slice(0, full.lastIndexOf("/")));
    pyodide.FS.writeFile(full, text);
  }
  pyodide.runPython(
    `import sys\nif ${JSON.stringify(manifest.sys_path)} not in sys.path:\n    sys.path.insert(0, ${JSON.stringify(manifest.sys_path)})\nimport ${manifest.entry}`
  );
  progress("Pyodide ready.");
}

function ready() {
  if (!initP) initP = init();
  return initP;
}

// ---- ops (mirror CONTRACTS §6; return JSON-parseable structures) ---------

function opProcess({ sig, fs, params }) {
  pyodide.globals.set("_sig", sig);
  pyodide.globals.set("_fs", fs);
  pyodide.globals.set("_params", JSON.stringify(params));
  const js = pyodide.runPython(`
import json, numpy as np, web_engine
def _to(v):
    return v.tolist() if isinstance(v, np.ndarray) else v
_x = np.asarray([np.asarray(a.to_py(), dtype=float) for a in _sig])
_r = web_engine.process(_x, _fs, json.loads(_params))
json.dumps({k: _to(v) for k, v in _r.items()})
`);
  return JSON.parse(js);
}

function opExtractFeatures({ sig, fs, winS, hopS }) {
  pyodide.globals.set("_sig", sig);
  pyodide.globals.set("_fs", fs);
  pyodide.globals.set("_win", winS);
  pyodide.globals.set("_hop", hopS);
  const js = pyodide.runPython(`
import json, numpy as np, web_engine
_x = np.asarray([np.asarray(a.to_py(), dtype=float) for a in _sig])
_r = web_engine.extract_features(_x, _fs, _win, _hop)
json.dumps({"names": _r["names"], "t_s": _r["t_s"].tolist(), "matrix": _r["matrix"].tolist()})
`);
  return JSON.parse(js);
}

function opListDetectors() {
  return JSON.parse(
    pyodide.runPython("import json, web_engine\njson.dumps(web_engine.list_detectors())")
  );
}

function opRunDetector({ name, sig, fs, params }) {
  pyodide.globals.set("_name", name);
  pyodide.globals.set("_sig", sig);
  pyodide.globals.set("_fs", fs);
  pyodide.globals.set("_params", JSON.stringify(params || {}));
  const js = pyodide.runPython(`
import json, numpy as np, web_engine
_x = np.asarray([np.asarray(a.to_py(), dtype=float) for a in _sig])
_r = web_engine.run_detector(_name, _x, _fs, json.loads(_params))
_stat = None if _r["stat"] is None else np.asarray(_r["stat"], dtype=float).tolist()
json.dumps({"events": _r["events"], "stat": _stat})
`);
  return JSON.parse(js);
}

const OPS = {
  ready: () => ({}),
  process: opProcess,
  extractFeatures: opExtractFeatures,
  listDetectors: opListDetectors,
  runDetector: opRunDetector,
};

self.onmessage = async (e) => {
  const { id, op, payload } = e.data;
  try {
    await ready();
    const fn = OPS[op];
    if (!fn) throw new Error(`unknown op ${op}`);
    const result = fn(payload || {});
    self.postMessage({ id, ok: true, result });
  } catch (err) {
    self.postMessage({ id, ok: false, error: (err && err.message) || String(err) });
  }
};
