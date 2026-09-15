/**
 * controls.js — processing-panel wiring (filter / feature / detector panels).
 *
 * Phase 2 owns the full panel set. Phase 0 keeps the minimal band inputs wired
 * directly in app.js; this stub reserves the module and its contract surface.
 */
const Controls = (function () {
  /** Read the bandpass band from the Phase-0 inputs. */
  function readBand() {
    const lo = parseFloat(document.getElementById("band-lo").value);
    const hi = parseFloat(document.getElementById("band-hi").value);
    return Number.isFinite(lo) && Number.isFinite(hi) && hi > lo ? [lo, hi] : null;
  }

  return { readBand };
})();
