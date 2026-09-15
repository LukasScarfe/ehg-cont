/**
 * app.js — Phase 0 walking-skeleton shell: load one record, plot a channel with
 * its C/(c) markers, and run a real Pyodide bandpass on demand.
 */
(function () {
  const $ = (id) => document.getElementById(id);
  const statusEl = () => $("status");

  function setStatus(msg, cls) {
    const el = statusEl();
    el.textContent = msg;
    el.className = "status" + (cls ? " " + cls : "");
  }

  async function main() {
    let index, annAll, record, events, plot;
    let chIndex = 0;

    try {
      setStatus("Loading index + record…");
      index = await IO.loadIndex();
      annAll = await IO.loadAnnotations();
      const rec0 = index.records[0];
      record = await IO.loadCompactRecord(rec0.id, index);
      events = (annAll.records[record.id] || []);

      $("record-id").textContent = `${record.id} (${rec0.type}, GA ${rec0.ga_recording}, ${record.fs} Hz)`;

      const sel = $("channel-select");
      record.chNames.forEach((name, i) => {
        const o = document.createElement("option");
        o.value = String(i);
        o.textContent = name;
        sel.appendChild(o);
      });

      plot = Plots.create($("plot"), record, chIndex, events);
      const nDef = events.filter((e) => e.type === "definite").length;
      const nPos = events.filter((e) => e.type === "possible").length;
      setStatus(`Loaded ${record.id}: ${record.nSamples} samples/ch, ${record.chNames.length} channels. ` +
                `Annotations: ${nDef} C, ${nPos} (c). Bandpass runs on demand.`, "ok");

      sel.addEventListener("change", () => {
        chIndex = parseInt(sel.value, 10);
        plot.setChannel(chIndex);
      });

      $("toggle-others").addEventListener("change", (e) => plot.setShowOthers(e.target.checked));

      $("run-bandpass").addEventListener("click", async () => {
        const band = Controls.readBand();
        if (!band) { setStatus("Enter a valid band (hi > lo).", "err"); return; }
        const btn = $("run-bandpass");
        btn.disabled = true;
        try {
          await Engine.ready((m) => setStatus(m));
          setStatus(`Filtering ${record.chNames[chIndex]} @ ${band[0]}–${band[1]} Hz…`);
          const t0 = performance.now();
          const res = await Engine.process(record, [chIndex], { band, causal: false });
          plot.setFiltered(res.filtered);
          const ms = Math.round(performance.now() - t0);
          setStatus(`Bandpass ${band[0]}–${band[1]} Hz on ${record.chNames[chIndex]} — ` +
                    `real scipy via Pyodide, ${ms} ms. DC baseline removed; markers overlaid.`, "ok");
        } catch (err) {
          console.error(err);
          setStatus("Pyodide/bandpass failed: " + err.message, "err");
        } finally {
          btn.disabled = false;
        }
      });
    } catch (err) {
      console.error(err);
      setStatus("Load failed: " + err.message, "err");
    }
  }

  window.addEventListener("DOMContentLoaded", main);
})();
