/**
 * plots.js — trace rendering + annotation overlays (uPlot).
 *
 * Phase 0: one channel, raw + optional filtered series, with C/(c)/other event
 * markers drawn as vertical lines. Multi-channel grid, PSD/spectrogram: Phase 2.
 */
const Plots = (function () {
  const COLORS = { raw: "#3b82f6", filt: "#ef4444" };
  const MARK = {
    definite: { color: "#ef4444", dash: [] },
    possible: { color: "#f59e0b", dash: [5, 4] },
    other: { color: "#64748b", dash: [2, 4] },
  };

  /** vertical-line overlay for annotation events, styled by type. */
  function annotationPlugin(getEvents, getShowOthers) {
    return {
      hooks: {
        draw: (u) => {
          const ctx = u.ctx;
          const events = getEvents() || [];
          const showOthers = getShowOthers();
          const top = u.bbox.top;
          const bot = u.bbox.top + u.bbox.height;
          ctx.save();
          ctx.lineWidth = Math.max(1, devicePixelRatio || 1);
          for (const e of events) {
            if (e.type === "other" && !showOthers) continue;
            const style = MARK[e.type] || MARK.other;
            const x = Math.round(u.valToPos(e.t_s, "x", true));
            if (x < u.bbox.left || x > u.bbox.left + u.bbox.width) continue;
            ctx.strokeStyle = style.color;
            ctx.setLineDash(style.dash);
            ctx.beginPath();
            ctx.moveTo(x, top);
            ctx.lineTo(x, bot);
            ctx.stroke();
          }
          ctx.restore();
        },
      },
    };
  }

  /**
   * Create a plot bound to a record channel.
   * @returns {{ setFiltered:Function, setChannel:Function, setShowOthers:Function, destroy:Function }}
   */
  function create(el, record, chIndex, events) {
    let showOthers = false;
    let ch = chIndex;

    const n = record.nSamples;
    const t = new Float64Array(n);
    for (let i = 0; i < n; i++) t[i] = i / record.fs;

    let data = [t, record.data[ch], new Array(n).fill(null)];

    function size() {
      return { width: el.clientWidth || 1000, height: 420 };
    }

    const opts = {
      ...size(),
      scales: { x: { time: false } },
      axes: [
        { label: "time (s)", stroke: "#93a1bd", grid: { stroke: "#22304a" }, ticks: { stroke: "#22304a" } },
        { label: "mV", stroke: "#93a1bd", grid: { stroke: "#22304a" }, ticks: { stroke: "#22304a" } },
      ],
      series: [
        {},
        { label: "raw", stroke: COLORS.raw, width: 1 },
        { label: "filtered", stroke: COLORS.filt, width: 1.2, spanGaps: false },
      ],
      plugins: [annotationPlugin(() => events, () => showOthers)],
    };

    let u = new uPlot(opts, data, el);

    window.addEventListener("resize", () => u.setSize(size()));

    return {
      setFiltered(arr) {
        data = [data[0], data[1], arr];
        u.setData(data);
      },
      setChannel(newIdx) {
        ch = newIdx;
        data = [t, record.data[ch], new Array(n).fill(null)];
        u.setData(data);
      },
      setShowOthers(v) {
        showOthers = !!v;
        u.redraw();
      },
      destroy() {
        u.destroy();
      },
    };
  }

  return { create };
})();
