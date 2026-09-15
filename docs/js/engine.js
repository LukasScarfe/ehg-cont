/**
 * engine.js — main-thread JS ↔ Pyodide bridge (CONTRACTS §7).
 *
 * Pyodide actually runs in a Web Worker (engine.worker.js) so heavy DSP, feature
 * extraction and detectors never freeze the UI. This module is a thin, promise-
 * based proxy: same public API, result shapes mirror §6 verbatim. Pyodide is
 * loaded lazily inside the worker on the first call.
 */
const Engine = (function () {
  const PYODIDE_VERSION = "0.26.4";
  let worker = null;
  let seq = 0;
  const pending = new Map();     // id → {resolve, reject}
  let onProgress = () => {};

  function ensureWorker() {
    if (worker) return worker;
    worker = new Worker("js/engine.worker.js");
    worker.onmessage = (e) => {
      const d = e.data;
      if (d.progress !== undefined) { onProgress(d.progress); return; }
      const p = pending.get(d.id);
      if (!p) return;
      pending.delete(d.id);
      if (d.ok) p.resolve(d.result);
      else p.reject(new Error(d.error));
    };
    worker.onerror = (e) => {
      // reject everything in flight on a fatal worker error.
      const msg = e.message || "worker error";
      for (const [, p] of pending) p.reject(new Error(msg));
      pending.clear();
    };
    return worker;
  }

  function call(op, payload) {
    ensureWorker();
    const id = ++seq;
    return new Promise((resolve, reject) => {
      pending.set(id, { resolve, reject });
      worker.postMessage({ id, op, payload });
    });
  }

  /** stack selected channels into a plain array-of-Float32Array (→ (C,N)). */
  function stack(record, chIndices) {
    return chIndices.map((i) => record.data[i]);
  }
  function as2d(a) {
    if (a == null) return null;
    return Array.isArray(a[0]) ? a : [a];
  }

  /** Lazily spin up Pyodide in the worker; onProgress gets status strings. */
  function ready(progressCb) {
    if (progressCb) onProgress = progressCb;
    return call("ready", {});
  }

  async function process(record, chIndices, params) {
    if (params && typeof params === "object") ready();
    const r = await call("process", { sig: stack(record, chIndices), fs: record.fs, params });
    r.filtered = as2d(r.filtered);
    r.envelope = as2d(r.envelope);
    return r;
  }

  function extractFeatures(record, chIndices, winS, hopS) {
    return call("extractFeatures", { sig: stack(record, chIndices), fs: record.fs, winS, hopS });
  }

  function listDetectors() {
    return call("listDetectors", {});
  }

  function runDetector(name, record, chIndices, params) {
    return call("runDetector", { name, sig: stack(record, chIndices), fs: record.fs, params: params || {} });
  }

  return { ready, process, extractFeatures, listDetectors, runDetector, PYODIDE_VERSION };
})();
