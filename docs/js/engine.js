/**
 * engine.js — thin JS ↔ Pyodide bridge (CONTRACTS §7).
 *
 * Pyodide + numpy/scipy + the synced src/ are loaded LAZILY on the first call so
 * raw traces render instantly. web_engine.py stays pure (numpy in / ndarray out);
 * the JSON marshalling glue lives here.
 */
const Engine = (function () {
  const PYODIDE_VERSION = "0.26.4";
  const PYODIDE_URL = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;
  let pyodide = null;
  let readyP = null;

  function loadScript(src) {
    return new Promise((res, rej) => {
      const s = document.createElement("script");
      s.src = src;
      s.onload = res;
      s.onerror = () => rej(new Error(`failed to load ${src}`));
      document.head.appendChild(s);
    });
  }

  async function init(onProgress) {
    const say = onProgress || (() => {});
    say("loading Pyodide runtime…");
    await loadScript(PYODIDE_URL + "pyodide.js");
    pyodide = await loadPyodide({ indexURL: PYODIDE_URL });

    say("loading numpy + scipy (WASM)…");
    await pyodide.loadPackage(["numpy", "scipy"]);

    say("mounting signal-processing code…");
    const man = await fetch("py/manifest.json").then((r) => r.json());
    const base = man.root + "/";
    for (const rel of man.files) {
      const text = await fetch(base + rel).then((r) => {
        if (!r.ok) throw new Error(`${base + rel} ${r.status}`);
        return r.text();
      });
      const full = man.sys_path + "/" + rel;
      const dir = full.slice(0, full.lastIndexOf("/"));
      mkdirp(dir);
      pyodide.FS.writeFile(full, text);
    }
    pyodide.runPython(
      `import sys\nif ${JSON.stringify(man.sys_path)} not in sys.path:\n    sys.path.insert(0, ${JSON.stringify(man.sys_path)})\nimport ${man.entry}`
    );
    say("Pyodide ready.");
  }

  function mkdirp(dir) {
    const parts = dir.split("/").filter(Boolean);
    let cur = "";
    for (const p of parts) {
      cur += "/" + p;
      try {
        pyodide.FS.mkdir(cur);
      } catch (e) {
        /* exists */
      }
    }
  }

  function ready(onProgress) {
    if (!readyP) readyP = init(onProgress);
    return readyP;
  }

  /** Call web_engine.process on one channel; returns §6 shape (arrays as JS arrays). */
  async function process(record, chIndices, params) {
    await ready();
    const ch = record.data[chIndices[0]];
    pyodide.globals.set("_sig", ch);
    pyodide.globals.set("_fs", record.fs);
    pyodide.globals.set("_params", JSON.stringify(params));
    const js = pyodide.runPython(`
import json, numpy as np, web_engine
def _to(v):
    return v.tolist() if isinstance(v, np.ndarray) else v
_r = web_engine.process(np.asarray(_sig.to_py(), dtype=float), _fs, json.loads(_params))
json.dumps({k: _to(v) for k, v in _r.items()})
`);
    return JSON.parse(js);
  }

  async function extractFeatures(record, chIndices, winS, hopS) {
    await ready();
    const stacked = chIndices.map((i) => Array.from(record.data[i]));
    pyodide.globals.set("_sig", stacked);
    pyodide.globals.set("_fs", record.fs);
    pyodide.globals.set("_win", winS);
    pyodide.globals.set("_hop", hopS);
    const js = pyodide.runPython(`
import json, numpy as np, web_engine
_r = web_engine.extract_features(np.asarray(_sig.to_py(), dtype=float), _fs, _win, _hop)
json.dumps({"names": _r["names"], "t_s": _r["t_s"].tolist(), "matrix": _r["matrix"].tolist()})
`);
    return JSON.parse(js);
  }

  async function listDetectors() {
    await ready();
    return JSON.parse(pyodide.runPython("import json, web_engine\njson.dumps(web_engine.list_detectors())"));
  }

  return { ready, process, extractFeatures, listDetectors, PYODIDE_VERSION };
})();
