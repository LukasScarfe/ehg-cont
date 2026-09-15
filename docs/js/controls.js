/**
 * controls.js — reads the processing panels (filter / feature / detector) and
 * builds the detector parameter form. Pure helpers; app.js owns wiring & state.
 */
const Controls = (function () {
  const $ = (id) => document.getElementById(id);

  /** bandpass band from the filter panel; null if invalid or "no band". */
  function readBand() {
    if ($("filt-enable") && !$("filt-enable").checked) return null;
    const lo = parseFloat($("band-lo").value);
    const hi = parseFloat($("band-hi").value);
    return Number.isFinite(lo) && Number.isFinite(hi) && hi > lo ? [lo, hi] : null;
  }

  /** full CONTRACTS §6 process() params from the filter panel. */
  function readProcParams() {
    return {
      band: readBand(),
      causal: !!($("filt-causal") && $("filt-causal").checked),
      envelope: !!($("filt-env") && $("filt-env").checked),
      psd: !!($("filt-psd") && $("filt-psd").checked),
      spectrogram: !!($("filt-spec") && $("filt-spec").checked),
    };
  }

  function readFeatureWin() {
    const winS = parseFloat($("feat-win").value);
    const hopS = parseFloat($("feat-hop").value);
    return {
      winS: Number.isFinite(winS) && winS > 0 ? winS : 30,
      hopS: Number.isFinite(hopS) && hopS > 0 ? hopS : 15,
    };
  }

  // ---- detector parameter form -----------------------------------------

  /** classify a default value → the input kind used to render/read it. */
  function kind(v) {
    if (typeof v === "boolean") return "bool";
    if (typeof v === "number") return "num";
    if (Array.isArray(v) && v.length === 2 && v.every((x) => typeof x === "number")) return "pair";
    return "str";
  }

  /**
   * Render editable inputs for a detector's default params into `container`.
   * The original template is stashed so readDetectorParams can restore types.
   */
  function renderDetectorParams(container, params) {
    container.innerHTML = "";
    container._tmpl = params;
    Object.keys(params).forEach((key) => {
      const v = params[key];
      const row = document.createElement("label");
      row.className = "param-row";
      const name = document.createElement("span");
      name.className = "param-name";
      name.textContent = key;
      row.appendChild(name);

      const k = kind(v);
      if (k === "bool") {
        const cb = document.createElement("input");
        cb.type = "checkbox"; cb.checked = v; cb.dataset.key = key; cb.dataset.kind = k;
        row.appendChild(cb);
      } else if (k === "pair") {
        ["0", "1"].forEach((idx) => {
          const inp = document.createElement("input");
          inp.type = "number"; inp.step = "any"; inp.value = v[+idx];
          inp.dataset.key = key; inp.dataset.kind = k; inp.dataset.idx = idx;
          inp.style.width = "5em";
          row.appendChild(inp);
        });
      } else {
        const inp = document.createElement("input");
        inp.type = k === "num" ? "number" : "text";
        if (k === "num") inp.step = "any";
        inp.value = v; inp.dataset.key = key; inp.dataset.kind = k;
        inp.style.width = k === "num" ? "6em" : "8em";
        row.appendChild(inp);
      }
      container.appendChild(row);
    });
  }

  /** read the detector form back into a params object with original types. */
  function readDetectorParams(container) {
    const tmpl = container._tmpl || {};
    const out = {};
    for (const key of Object.keys(tmpl)) out[key] = tmpl[key]; // defaults
    const inputs = container.querySelectorAll("input[data-key]");
    inputs.forEach((inp) => {
      const key = inp.dataset.key, k = inp.dataset.kind;
      if (k === "bool") out[key] = inp.checked;
      else if (k === "pair") {
        if (!Array.isArray(out[key])) out[key] = [0, 0];
        out[key][+inp.dataset.idx] = parseFloat(inp.value);
      } else if (k === "num") out[key] = parseFloat(inp.value);
      else out[key] = inp.value;
    });
    return out;
  }

  // ---- feature export ---------------------------------------------------

  function featuresToCSV(ft) {
    const header = ["t_s", ...ft.names].join(",");
    const rows = ft.t_s.map((t, i) =>
      [t, ...ft.matrix[i]].map((x) => (typeof x === "number" ? x : "")).join(",")
    );
    return header + "\n" + rows.join("\n") + "\n";
  }

  function featuresToJSON(ft, record, chIndices, winS, hopS) {
    return JSON.stringify(
      {
        record: record.id,
        fs: record.fs,
        source: record.source,
        channels: chIndices.map((i) => record.chNames[i]),
        win_s: winS,
        hop_s: hopS,
        names: ft.names,
        t_s: ft.t_s,
        matrix: ft.matrix,
      },
      null,
      1
    );
  }

  function download(filename, text, mime) {
    const blob = new Blob([text], { type: mime || "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = filename;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  return {
    readBand,
    readProcParams,
    readFeatureWin,
    renderDetectorParams,
    readDetectorParams,
    featuresToCSV,
    featuresToJSON,
    download,
  };
})();
