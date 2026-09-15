/**
 * app.js — UI shell & state for the EHG explorer (Phase 2).
 *
 * Owns: record browser (compact index + local WFDB), the 4×4 channel grid,
 * annotation toggles, and the filter / feature / detector panels — wiring them
 * to Engine (Pyodide) and Plots. Raw traces render instantly; Pyodide loads
 * lazily on the first processing/detector call.
 */
(function () {
  const $ = (id) => document.getElementById(id);
  const ALL_SYMS = ["C", "(c)", "fm", "pm", "em", "pos"];

  const S = {
    index: null,
    annAll: null,
    record: null,
    events: [],
    sel: [0, 1, 2, 3],       // selected channel indices (montage rows, top→bottom)
    montage: null,
    statPlot: null,
    psd: null,               // uPlot instance
    detectors: null,         // list from listDetectors()
    features: null,          // last feature table
    featCtx: null,           // {chIndices, winS, hopS}
  };

  function setStatus(msg, cls) {
    const el = $("status");
    el.textContent = msg;
    el.className = "status" + (cls ? " " + cls : "");
  }

  // ---- record browser ---------------------------------------------------

  function fillRecordSelect() {
    const type = $("type-filter").value;
    const sel = $("record-select");
    sel.innerHTML = "";
    S.index.records
      .filter((r) => type === "all" || r.type === type)
      .forEach((r) => {
        const a = r.annotation_counts || {};
        const o = document.createElement("option");
        o.value = r.id;
        o.textContent = `${r.id} · ${r.type} · GA ${r.ga_recording} · C${a.C || 0}/(c)${a["(c)"] || 0}`;
        sel.appendChild(o);
      });
  }

  function renderMeta() {
    const r = S.record;
    const el = $("record-meta");
    if (!r) { el.innerHTML = ""; return; }
    const dur = (r.nSamples / r.fs);
    const pills = [
      `<span class="pill">source: ${r.source}</span>`,
      `<span class="pill">${r.fs} Hz</span>`,
      `<span class="pill">${r.chNames.length} ch</span>`,
      `<span class="pill">${r.nSamples.toLocaleString()} samp</span>`,
      `<span class="pill">${(dur / 60).toFixed(1)} min</span>`,
    ];
    if (r.meta) {
      const a = r.meta.annotation_counts || {};
      pills.push(`<span class="pill">GA ${r.meta.ga_recording}</span>`);
      pills.push(`<span class="pill">C ${a.C || 0} · (c) ${a["(c)"] || 0} · fm ${a.fm || 0} · pm ${a.pm || 0} · em ${a.em || 0}</span>`);
    }
    el.innerHTML = `<strong>${r.id}</strong> ${pills.join(" ")}`;
  }

  function buildGrid() {
    const g = $("grid");
    g.innerHTML = "";
    S.record.chNames.forEach((name, k) => {
      const b = document.createElement("button");
      b.textContent = name.replace(/^EHG/, "");
      b.title = name;
      b.dataset.ch = k;
      if (S.sel.includes(k)) b.classList.add("on");
      b.addEventListener("click", () => toggleChannel(k, b));
      g.appendChild(b);
    });
  }

  function selArray() {
    return S.sel.slice().sort((a, b) => a - b);
  }

  function toggleChannel(k, btn) {
    const i = S.sel.indexOf(k);
    if (i >= 0) S.sel.splice(i, 1);
    else S.sel.push(k);
    btn.classList.toggle("on");
    S.montage.setChannels(selArray());
    afterChannelChange();
  }

  function setAllChannels(on) {
    S.sel = on ? S.record.chNames.map((_, k) => k) : [];
    document.querySelectorAll("#grid button").forEach((b) => {
      b.classList.toggle("on", S.sel.includes(+b.dataset.ch));
    });
    S.montage.setChannels(selArray());
    afterChannelChange();
  }

  // channel set changed → any filtered/detector overlays are stale.
  function afterChannelChange() {
    clearProcessing(true);
    clearDetections();
  }

  function activeSyms() {
    const set = new Set();
    document.querySelectorAll("#ann-toggles input:checked").forEach((c) => set.add(c.value));
    return set;
  }

  function buildAnnToggles() {
    const box = $("ann-toggles");
    box.innerHTML = "";
    ALL_SYMS.forEach((sym) => {
      const l = document.createElement("label");
      const c = document.createElement("input");
      c.type = "checkbox"; c.value = sym; c.checked = sym === "C" || sym === "(c)";
      c.addEventListener("change", () => S.montage.setAnnSyms(activeSyms()));
      l.appendChild(c);
      l.appendChild(document.createTextNode(" " + sym));
      box.appendChild(l);
    });
  }

  async function loadRecord(id) {
    try {
      setStatus(`Loading ${id}…`, "busy");
      const rec = await IO.loadCompactRecord(id, S.index);
      mountRecord(rec, S.annAll.records[rec.id] || []);
    } catch (err) {
      console.error(err);
      setStatus("Load failed: " + err.message, "err");
    }
  }

  function mountRecord(rec, events) {
    S.record = rec;
    S.events = events;
    // keep selection if channel count matches, else default first 4.
    if (!S.sel.length || Math.max(...S.sel, 0) >= rec.chNames.length) {
      S.sel = rec.chNames.map((_, k) => k).slice(0, 4);
    }
    renderMeta();
    buildGrid();

    if (S.montage) S.montage.destroy();
    if (S.statPlot) S.statPlot.destroy();
    $("stat-plot").style.display = "none";
    hidePSD(); hideSpec();
    S.montage = Plots.createMontage($("montage"), rec, selArray(), events);
    S.montage.setAnnSyms(activeSyms());
    S.statPlot = Plots.createStat($("stat-plot"), rec);
    $("src-raw").checked = true;

    const nDef = events.filter((e) => e.type === "definite").length;
    const nPos = events.filter((e) => e.type === "possible").length;
    setStatus(`Loaded ${rec.id}: ${rec.nSamples.toLocaleString()} samp/ch, ${rec.chNames.length} ch @ ${rec.fs} Hz. ` +
              `Annotations: ${nDef} C, ${nPos} (c). Processing runs on demand (Pyodide loads lazily).`, "ok");
  }

  // ---- processing -------------------------------------------------------

  function hidePSD() { if (S.psd) { S.psd.destroy(); S.psd = null; } $("psd").style.display = "none"; }
  function hideSpec() { $("spec").style.display = "none"; $("spec").innerHTML = ""; }

  function clearProcessing(silent) {
    if (S.montage) S.montage.clearProcessed();
    hidePSD(); hideSpec();
    $("src-raw").checked = true;
    if (!silent) setStatus("Cleared processing — showing raw traces.", "ok");
  }

  async function runProcessing() {
    if (!S.sel.length) { setStatus("Select at least one channel.", "err"); return; }
    const params = Controls.readProcParams();
    const btn = $("run-proc"); btn.disabled = true;
    try {
      await Engine.ready((m) => setStatus(m, "busy"));
      const chs = selArray();
      setStatus(`Processing ${chs.length} channel(s)…`, "busy");
      const t0 = performance.now();
      const res = await Engine.process(S.record, chs, params);
      S.montage.setProcessed(res);
      $("src-filt").checked = true;
      if (params.psd && res.psd_f) renderPSD(res.psd_f, res.psd_p); else hidePSD();
      if (params.spectrogram && res.spec_S) { $("spec").style.display = ""; Plots.renderSpectrogram($("spec"), res.spec_f, res.spec_t, res.spec_S); } else hideSpec();
      const ms = Math.round(performance.now() - t0);
      const bits = [];
      if (params.band) bits.push(`bandpass ${params.band[0]}–${params.band[1]} Hz${params.causal ? " (causal)" : ""}`);
      if (params.envelope) bits.push("RMS envelope");
      if (params.psd) bits.push("PSD");
      if (params.spectrogram) bits.push("spectrogram");
      setStatus(`${bits.join(" · ") || "passthrough"} on ${chs.length} ch — real scipy via Pyodide, ${ms} ms.`, "ok");
    } catch (err) {
      console.error(err);
      setStatus("Processing failed: " + err.message, "err");
    } finally {
      btn.disabled = false;
    }
  }

  function renderPSD(f, p) {
    hidePSD();
    $("psd").style.display = "";
    S.psd = Plots.renderPSD($("psd"), f, p);
  }

  // ---- features ---------------------------------------------------------

  async function runFeatures() {
    if (!S.sel.length) { setStatus("Select at least one channel.", "err"); return; }
    const { winS, hopS } = Controls.readFeatureWin();
    const btn = $("run-feat"); btn.disabled = true;
    try {
      await Engine.ready((m) => setStatus(m, "busy"));
      const chs = selArray();
      setStatus(`Extracting features (win ${winS}s, hop ${hopS}s)…`, "busy");
      const ft = await Engine.extractFeatures(S.record, chs, winS, hopS);
      S.features = ft;
      S.featCtx = { chIndices: chs, winS, hopS };
      renderFeatureTable(ft);
      $("export-csv").disabled = ft.matrix.length === 0;
      $("export-json").disabled = ft.matrix.length === 0;
      $("feat-summary").textContent = `${ft.matrix.length} windows × ${ft.names.length} features (channel-averaged over ${chs.length} ch).`;
      setStatus(`Extracted ${ft.matrix.length} feature windows × ${ft.names.length} features.`, "ok");
    } catch (err) {
      console.error(err);
      setStatus("Feature extraction failed: " + err.message, "err");
    } finally {
      btn.disabled = false;
    }
  }

  function renderFeatureTable(ft) {
    const box = $("feat-table");
    if (!ft.matrix.length) { box.innerHTML = "<p class='muted small'>no windows</p>"; return; }
    const maxRows = 12;
    const cols = ft.names.slice(0, 6); // preview first few features
    let html = "<table><thead><tr><th>t_s</th>" + cols.map((c) => `<th>${c}</th>`).join("") + "</tr></thead><tbody>";
    for (let i = 0; i < Math.min(maxRows, ft.matrix.length); i++) {
      html += `<tr><td>${ft.t_s[i].toFixed(1)}</td>` +
        cols.map((_, j) => `<td>${ft.matrix[i][j].toPrecision(3)}</td>`).join("") + "</tr>";
    }
    html += "</tbody></table>";
    if (ft.matrix.length > maxRows) html += `<p class='muted small'>… ${ft.matrix.length - maxRows} more rows (export for all ${ft.names.length} features)</p>`;
    box.innerHTML = html;
  }

  // ---- detectors --------------------------------------------------------

  let detLoading = null;
  async function ensureDetectors() {
    if (S.detectors) return S.detectors;
    if (detLoading) return detLoading;
    detLoading = (async () => {
      await Engine.ready((m) => setStatus(m, "busy"));
      setStatus("Enumerating browser-safe detectors…", "busy");
      const list = await Engine.listDetectors();
      S.detectors = list;
      const sel = $("det-select");
      sel.innerHTML = "";
      list.forEach((d) => {
        const o = document.createElement("option");
        o.value = d.name;
        o.textContent = `${d.name} (${d.family}${d.causal ? ", causal" : ""})`;
        sel.appendChild(o);
      });
      renderDetParams();
      setStatus(`${list.length} detectors available. Adjust params, then Run.`, "ok");
      return list;
    })();
    return detLoading;
  }

  function renderDetParams() {
    const name = $("det-select").value;
    const d = (S.detectors || []).find((x) => x.name === name);
    if (d) Controls.renderDetectorParams($("det-params"), d.params);
  }

  async function runDetector() {
    if (!S.sel.length) { setStatus("Select at least one channel.", "err"); return; }
    const btn = $("run-det"); btn.disabled = true;
    try {
      await ensureDetectors();
      const name = $("det-select").value;
      const params = Controls.readDetectorParams($("det-params"));
      const chs = selArray();
      setStatus(`Running detector ${name} on ${chs.length} ch…`, "busy");
      const t0 = performance.now();
      const res = await Engine.runDetector(name, S.record, chs, params);
      S.montage.setDetections(res);
      if (res.stat) { $("stat-plot").style.display = ""; S.statPlot.set(res); } else { $("stat-plot").style.display = "none"; S.statPlot.set(null); }
      const ms = Math.round(performance.now() - t0);
      const nTruth = S.events.filter((e) => e.type === "definite" || e.type === "possible").length;
      $("det-summary").textContent = `${res.events.length} events detected vs ${nTruth} annotated (C+(c)).`;
      setStatus(`${name}: ${res.events.length} events in ${ms} ms — green spans on the montage vs C/(c) truth lines.`, "ok");
    } catch (err) {
      console.error(err);
      setStatus("Detector failed: " + err.message, "err");
    } finally {
      btn.disabled = false;
    }
  }

  function clearDetections() {
    if (S.montage) S.montage.setDetections(null);
    if (S.statPlot) S.statPlot.set(null);
    $("stat-plot").style.display = "none";
    $("det-summary").textContent = "";
  }

  // ---- local WFDB -------------------------------------------------------

  async function loadLocal() {
    const files = $("local-files").files;
    if (!files || !files.length) { setStatus("Pick a .hea and its matching .dat first.", "err"); return; }
    try {
      setStatus("Parsing local WFDB…", "busy");
      const rec = await IO.loadLocalWFDB(files);
      // reset selection to first 4 for a fresh (possibly different) record.
      S.sel = rec.chNames.map((_, k) => k).slice(0, 4);
      mountRecord(rec, []); // no committed annotations for local records
      setStatus(`Loaded local ${rec.id}: ${rec.nSamples.toLocaleString()} samp/ch @ ${rec.fs} Hz (full-res).`, "ok");
    } catch (err) {
      console.error(err);
      setStatus("Local load failed: " + err.message, "err");
    }
  }

  // ---- wiring -----------------------------------------------------------

  function wire() {
    $("type-filter").addEventListener("change", () => { fillRecordSelect(); loadRecord($("record-select").value); });
    $("record-select").addEventListener("change", () => loadRecord($("record-select").value));
    $("prev-record").addEventListener("click", () => step(-1));
    $("next-record").addEventListener("click", () => step(1));
    $("load-local").addEventListener("click", loadLocal);

    $("ch-all").addEventListener("click", () => setAllChannels(true));
    $("ch-none").addEventListener("click", () => setAllChannels(false));

    $("run-proc").addEventListener("click", runProcessing);
    $("clear-proc").addEventListener("click", () => clearProcessing(false));
    $("src-raw").addEventListener("change", () => S.montage.setSource("raw"));
    $("src-filt").addEventListener("change", () => S.montage.setSource("filtered"));

    $("run-feat").addEventListener("click", runFeatures);
    $("export-csv").addEventListener("click", () => {
      if (!S.features) return;
      Controls.download(`${S.record.id}_features.csv`, Controls.featuresToCSV(S.features), "text/csv");
    });
    $("export-json").addEventListener("click", () => {
      if (!S.features) return;
      const c = S.featCtx;
      Controls.download(`${S.record.id}_features.json`,
        Controls.featuresToJSON(S.features, S.record, c.chIndices, c.winS, c.hopS), "application/json");
    });

    $("det-select").addEventListener("focus", ensureDetectors, { once: true });
    $("det-select").addEventListener("change", renderDetParams);
    $("run-det").addEventListener("click", runDetector);
    $("clear-det").addEventListener("click", clearDetections);
  }

  function step(dir) {
    const sel = $("record-select");
    const i = sel.selectedIndex + dir;
    if (i < 0 || i >= sel.options.length) return;
    sel.selectedIndex = i;
    loadRecord(sel.value);
  }

  async function main() {
    buildAnnToggles();
    wire();
    try {
      setStatus("Loading index + annotations…", "busy");
      S.index = await IO.loadIndex();
      S.annAll = await IO.loadAnnotations();
      fillRecordSelect();
      await loadRecord($("record-select").value);
    } catch (err) {
      console.error(err);
      setStatus("Startup failed: " + err.message, "err");
    }
  }

  window.addEventListener("DOMContentLoaded", main);
})();
