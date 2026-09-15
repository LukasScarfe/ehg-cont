/**
 * plots.js — rendering for the EHG explorer (uPlot + canvas).
 *
 *  - createMontage(): multi-channel stacked traces (raw|filtered), DC-removed and
 *    offset per channel, with envelope overlay, annotation lines (C/(c)/fm/pm/em/pos),
 *    and detector event spans + peaks (events-vs-truth) drawn over the same time axis.
 *  - createStat():  a single detector detection-statistic trace under the montage.
 *  - renderPSD():   Welch PSD (log-y) for the first selected channel.
 *  - renderSpectrogram(): a canvas heatmap of the STFT.
 */
const Plots = (function () {
  const PALETTE = [
    "#38bdf8", "#a3e635", "#f472b6", "#fbbf24", "#34d399", "#c084fc",
    "#fb7185", "#22d3ee", "#facc15", "#4ade80", "#f97316", "#60a5fa",
    "#e879f9", "#2dd4bf", "#fca5a5", "#93c5fd",
  ];
  // annotation styling by MIT symbol (CONTRACTS §3).
  const SYM = {
    "C":  { color: "#ef4444", dash: [] },
    "(c)":{ color: "#f59e0b", dash: [5, 4] },
    "fm": { color: "#64748b", dash: [2, 4] },
    "pm": { color: "#64748b", dash: [2, 4] },
    "em": { color: "#64748b", dash: [2, 4] },
    "pos":{ color: "#64748b", dash: [2, 4] },
  };
  const DET_FILL = "rgba(52, 211, 153, 0.14)";
  const DET_EDGE = "rgba(52, 211, 153, 0.85)";
  const AXIS = { stroke: "#93a1bd", grid: { stroke: "#22304a" }, ticks: { stroke: "#22304a" } };

  function mean(a) {
    let s = 0;
    for (let i = 0; i < a.length; i++) s += a[i];
    return s / (a.length || 1);
  }
  function std(a, m) {
    let s = 0;
    for (let i = 0; i < a.length; i++) { const d = a[i] - m; s += d * d; }
    return Math.sqrt(s / (a.length || 1));
  }
  function median(xs) {
    if (!xs.length) return 0;
    const s = xs.slice().sort((a, b) => a - b);
    const h = s.length >> 1;
    return s.length % 2 ? s[h] : 0.5 * (s[h - 1] + s[h]);
  }

  // ---- Montage ----------------------------------------------------------

  /**
   * @param {HTMLElement} el
   * @param {Record} record
   * @param {number[]} sel      selected channel indices (top → bottom)
   * @param {Array} events      annotation events for this record
   */
  function createMontage(el, record, sel, events) {
    const st = {
      sel: sel.slice(),
      source: "raw",         // "raw" | "filtered"
      filtered: null,        // [k][N] aligned to st.sel order, or null
      envelope: null,        // [k][N] aligned to st.sel order, or null
      showEnv: false,
      events: events || [],
      annSyms: new Set(["C", "(c)"]),
      detections: null,      // { events:[{onset,offset,peak,score}] }
    };
    let u = null;
    let offsets = [];        // per-row vertical offset
    let spacing = 1;

    const t = new Float64Array(record.nSamples);
    for (let i = 0; i < record.nSamples; i++) t[i] = i / record.fs;

    function dispFor(k) {
      // k = row index into st.sel; choose filtered if requested & present.
      if (st.source === "filtered" && st.filtered && st.filtered[k]) return st.filtered[k];
      return record.data[st.sel[k]];
    }

    function computeGeometry() {
      const stds = [];
      const dcRemoved = st.sel.map((_, k) => {
        const raw = dispFor(k);
        const m = mean(raw);
        const out = new Float64Array(raw.length);
        for (let i = 0; i < raw.length; i++) out[i] = raw[i] - m;
        stds.push(std(out, 0));
        return out;
      });
      const sc = median(stds.filter((s) => s > 0)) || 1;
      spacing = sc * 8 || 1;
      offsets = st.sel.map((_, k) => (st.sel.length - 1 - k) * spacing);
      return dcRemoved;
    }

    function buildData() {
      const dc = computeGeometry();
      const series = dc.map((arr, k) => {
        const off = offsets[k];
        const out = new Float64Array(arr.length);
        for (let i = 0; i < arr.length; i++) out[i] = arr[i] + off;
        return out;
      });
      const data = [t, ...series];
      if (st.showEnv && st.envelope) {
        // envelope drawn at its channel's baseline (offset), positive-going.
        st.sel.forEach((_, k) => {
          const env = st.envelope[k];
          if (!env) { data.push(new Array(record.nSamples).fill(null)); return; }
          const off = offsets[k];
          const out = new Float64Array(env.length);
          for (let i = 0; i < env.length; i++) out[i] = off + env[i];
          data.push(out);
        });
      }
      return data;
    }

    function seriesOpts() {
      const s = [{}];
      st.sel.forEach((ci, k) => {
        s.push({
          label: record.chNames[st.sel[k]],
          stroke: PALETTE[st.sel[k] % PALETTE.length],
          width: 1,
        });
      });
      if (st.showEnv && st.envelope) {
        st.sel.forEach((ci, k) => {
          s.push({
            label: "env " + record.chNames[st.sel[k]],
            stroke: "#e2e8f0",
            width: 1,
            dash: [3, 3],
          });
        });
      }
      return s;
    }

    function overlayPlugin() {
      return {
        hooks: {
          draw: (up) => {
            const ctx = up.ctx;
            const top = up.bbox.top;
            const bot = up.bbox.top + up.bbox.height;
            const L = up.bbox.left, R = up.bbox.left + up.bbox.width;
            ctx.save();
            ctx.lineWidth = Math.max(1, devicePixelRatio || 1);

            // detector event spans + peaks (drawn first, behind ann lines).
            if (st.detections && st.detections.events) {
              for (const ev of st.detections.events) {
                const x0 = Math.round(up.valToPos(ev.onset, "x", true));
                const x1 = ev.offset != null
                  ? Math.round(up.valToPos(ev.offset, "x", true))
                  : x0 + 1;
                const a = Math.max(L, Math.min(x0, x1));
                const b = Math.min(R, Math.max(x0, x1));
                if (b < L || a > R) continue;
                ctx.fillStyle = DET_FILL;
                ctx.fillRect(a, top, Math.max(1, b - a), bot - top);
                ctx.strokeStyle = DET_EDGE;
                ctx.setLineDash([]);
                ctx.beginPath();
                ctx.moveTo(a + 0.5, top); ctx.lineTo(a + 0.5, bot);
                ctx.moveTo(b + 0.5, top); ctx.lineTo(b + 0.5, bot);
                ctx.stroke();
                if (ev.peak != null) {
                  const xp = Math.round(up.valToPos(ev.peak, "x", true));
                  if (xp >= L && xp <= R) {
                    ctx.fillStyle = DET_EDGE;
                    ctx.beginPath();
                    ctx.moveTo(xp, top); ctx.lineTo(xp - 5, top - 7); ctx.lineTo(xp + 5, top - 7);
                    ctx.closePath(); ctx.fill();
                  }
                }
              }
            }

            // annotation vertical lines by symbol.
            for (const e of st.events) {
              if (!st.annSyms.has(e.symbol)) continue;
              const style = SYM[e.symbol] || SYM["fm"];
              const x = Math.round(up.valToPos(e.t_s, "x", true));
              if (x < L || x > R) continue;
              ctx.strokeStyle = style.color;
              ctx.setLineDash(style.dash);
              ctx.beginPath();
              ctx.moveTo(x, top); ctx.lineTo(x, bot);
              ctx.stroke();
            }
            ctx.restore();
          },
        },
      };
    }

    function size() {
      const rows = Math.max(1, st.sel.length);
      return { width: el.clientWidth || 1000, height: Math.min(680, 120 + rows * 46) };
    }

    function yAxisValues() {
      // label each row baseline with its channel name.
      return (up, splits) =>
        splits.map((v) => {
          const k = offsets.findIndex((o) => Math.abs(o - v) < 1e-6);
          return k >= 0 ? record.chNames[st.sel[k]] : "";
        });
    }

    function build() {
      if (u) { u.destroy(); u = null; }
      if (!st.sel.length) { el.innerHTML = "<p class='muted' style='padding:12px'>No channels selected.</p>"; return; }
      const data = buildData();
      const ypad = spacing * 0.7;
      const opts = {
        ...size(),
        scales: {
          x: { time: false },
          y: { range: [-ypad, (st.sel.length - 1) * spacing + ypad] },
        },
        axes: [
          { label: "time (s)", ...AXIS },
          {
            ...AXIS,
            splits: () => offsets.slice().reverse(),
            values: yAxisValues(),
            size: 70,
          },
        ],
        series: seriesOpts(),
        cursor: { drag: { x: true, y: false } },
        legend: { show: false },
        plugins: [overlayPlugin()],
      };
      u = new uPlot(opts, data, el);
    }

    build();
    const onResize = () => { if (u) u.setSize(size()); };
    window.addEventListener("resize", onResize);

    return {
      setChannels(newSel) { st.sel = newSel.slice(); st.filtered = null; st.envelope = null; build(); },
      setSource(src) { st.source = src; build(); },
      setProcessed(res) {
        // res.filtered / res.envelope are [C][N] aligned to the channels we sent
        // (which are st.sel in order). null clears.
        st.filtered = res && res.filtered ? res.filtered : null;
        st.envelope = res && res.envelope ? res.envelope : null;
        if (st.filtered) st.source = "filtered";
        build();
      },
      setShowEnv(v) { st.showEnv = !!v; build(); },
      setAnnSyms(symSet) { st.annSyms = new Set(symSet); if (u) u.redraw(); },
      setDetections(det) { st.detections = det; if (u) u.redraw(); },
      clearProcessed() { st.filtered = null; st.envelope = null; st.source = "raw"; build(); },
      destroy() { window.removeEventListener("resize", onResize); if (u) u.destroy(); },
    };
  }

  // ---- Detection-statistic strip ---------------------------------------

  function createStat(el, record) {
    let u = null;
    const t = new Float64Array(record.nSamples);
    for (let i = 0; i < record.nSamples; i++) t[i] = i / record.fs;

    function size() { return { width: el.clientWidth || 1000, height: 150 }; }

    return {
      set(det) {
        if (u) { u.destroy(); u = null; }
        el.innerHTML = "";
        if (!det || !det.stat) { el.style.display = "none"; return; }
        el.style.display = "";
        // stat may be decimated: resample its x over the full duration.
        const s = det.stat;
        const tt = new Float64Array(s.length);
        const dur = record.nSamples / record.fs;
        for (let i = 0; i < s.length; i++) tt[i] = (i / Math.max(1, s.length - 1)) * dur;
        const opts = {
          ...size(),
          scales: { x: { time: false } },
          axes: [{ label: "time (s)", ...AXIS }, { label: "stat", ...AXIS, size: 70 }],
          series: [{}, { label: "detection stat", stroke: "#34d399", width: 1 }],
          legend: { show: false },
          plugins: [{
            hooks: {
              draw: (up) => {
                if (!det.events) return;
                const ctx = up.ctx, top = up.bbox.top, bot = up.bbox.top + up.bbox.height;
                const L = up.bbox.left, R = up.bbox.left + up.bbox.width;
                ctx.save();
                for (const ev of det.events) {
                  const x0 = Math.round(up.valToPos(ev.onset, "x", true));
                  const x1 = ev.offset != null ? Math.round(up.valToPos(ev.offset, "x", true)) : x0 + 1;
                  const a = Math.max(L, Math.min(x0, x1)), b = Math.min(R, Math.max(x0, x1));
                  if (b < L || a > R) continue;
                  ctx.fillStyle = DET_FILL;
                  ctx.fillRect(a, top, Math.max(1, b - a), bot - top);
                }
                ctx.restore();
              },
            },
          }],
        };
        u = new uPlot(opts, [tt, Float64Array.from(s)], el);
      },
      destroy() { if (u) u.destroy(); },
    };
  }

  // ---- PSD --------------------------------------------------------------

  function renderPSD(el, f, p) {
    el.innerHTML = "";
    if (!f || !p || !f.length) { el.style.display = "none"; return null; }
    el.style.display = "";
    const logp = p.map((v) => (v > 0 ? v : 1e-12));
    const opts = {
      width: el.clientWidth || 500,
      height: 240,
      title: "Welch PSD (ch 1)",
      scales: { x: { time: false }, y: { distr: 3 } }, // log-y
      axes: [{ label: "Hz", ...AXIS }, { label: "power", ...AXIS, size: 60 }],
      series: [{}, { label: "PSD", stroke: "#22d3ee", width: 1.3, fill: "rgba(34,211,238,0.12)" }],
      legend: { show: false },
    };
    return new uPlot(opts, [Float64Array.from(f), Float64Array.from(logp)], el);
  }

  // ---- Spectrogram (canvas heatmap) ------------------------------------

  // compact viridis-ish ramp.
  const CMAP = [
    [68, 1, 84], [59, 82, 139], [33, 145, 140], [94, 201, 98], [253, 231, 37],
  ];
  function colormap(t) {
    t = Math.max(0, Math.min(1, t));
    const x = t * (CMAP.length - 1);
    const i = Math.floor(x), frac = x - i;
    const a = CMAP[i], b = CMAP[Math.min(CMAP.length - 1, i + 1)];
    return [
      Math.round(a[0] + (b[0] - a[0]) * frac),
      Math.round(a[1] + (b[1] - a[1]) * frac),
      Math.round(a[2] + (b[2] - a[2]) * frac),
    ];
  }

  function renderSpectrogram(el, f, tArr, S) {
    el.innerHTML = "";
    if (!f || !tArr || !S || !S.length) { el.style.display = "none"; return; }
    el.style.display = "";
    const nF = S.length, nT = S[0].length;

    // log-scale power, normalise to [0,1] for the colormap.
    let lo = Infinity, hi = -Infinity;
    const logS = new Array(nF);
    for (let r = 0; r < nF; r++) {
      logS[r] = new Float32Array(nT);
      for (let c = 0; c < nT; c++) {
        const v = Math.log10((S[r][c] > 0 ? S[r][c] : 1e-12));
        logS[r][c] = v;
        if (v < lo) lo = v;
        if (v > hi) hi = v;
      }
    }
    const span = hi - lo || 1;

    const wrap = document.createElement("div");
    wrap.style.position = "relative";
    const cvs = document.createElement("canvas");
    cvs.width = nT;
    cvs.height = nF;
    cvs.style.width = "100%";
    cvs.style.height = "240px";
    cvs.style.imageRendering = "auto";
    cvs.style.borderRadius = "6px";
    const ctx = cvs.getContext("2d");
    const img = ctx.createImageData(nT, nF);
    for (let r = 0; r < nF; r++) {
      // canvas row 0 = top; put high freq at top → invert.
      const dstRow = nF - 1 - r;
      for (let c = 0; c < nT; c++) {
        const norm = (logS[r][c] - lo) / span;
        const [R, G, B] = colormap(norm);
        const o = (dstRow * nT + c) * 4;
        img.data[o] = R; img.data[o + 1] = G; img.data[o + 2] = B; img.data[o + 3] = 255;
      }
    }
    ctx.putImageData(img, 0, 0);
    wrap.appendChild(cvs);

    const cap = document.createElement("div");
    cap.className = "muted small";
    const fmax = f[f.length - 1], tmax = tArr[tArr.length - 1];
    cap.textContent = `Spectrogram (ch 1) — x: 0–${tmax.toFixed(0)} s, y: 0–${fmax.toFixed(2)} Hz (high freq at top), log power`;
    wrap.appendChild(cap);
    el.appendChild(wrap);
  }

  return { createMontage, createStat, renderPSD, renderSpectrogram };
})();
