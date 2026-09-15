/**
 * io.js — data loading for the EHG explorer.
 *
 * Two sources produce the SAME `Record` shape (CONTRACTS §4):
 *   - compact committed records: channel-major int16 gzip (CONTRACTS §1).
 *   - local full-res WFDB (.hea/.dat, format 16, interleaved) (CONTRACTS §5).
 *
 * @typedef {Object} Record
 * @property {string} id
 * @property {number} fs
 * @property {string[]} chNames
 * @property {Float32Array[]} data
 * @property {number} nSamples
 * @property {"compact"|"local"} source
 * @property {object} meta
 */
const IO = (function () {
  const DATA_BASE = "data/";

  async function loadIndex() {
    const r = await fetch(DATA_BASE + "index.json");
    if (!r.ok) throw new Error(`index.json ${r.status}`);
    return r.json();
  }

  async function loadAnnotations() {
    const r = await fetch(DATA_BASE + "annotations.json");
    if (!r.ok) throw new Error(`annotations.json ${r.status}`);
    return r.json();
  }

  /** gunzip an ArrayBuffer → Uint8Array (native DecompressionStream). */
  async function gunzip(buf) {
    if (typeof DecompressionStream === "undefined") {
      throw new Error("DecompressionStream unavailable (this browser lacks gzip streams)");
    }
    const ds = new DecompressionStream("gzip");
    const stream = new Response(buf).body.pipeThrough(ds);
    const out = await new Response(stream).arrayBuffer();
    return new Uint8Array(out);
  }

  /**
   * Load one committed record into a Record. `index` may be passed to avoid a
   * refetch; otherwise it is loaded.
   * @returns {Promise<Record>}
   */
  async function loadCompactRecord(id, index) {
    index = index || (await loadIndex());
    const meta = index.records.find((r) => r.id === id);
    if (!meta) throw new Error(`record ${id} not in index`);

    const r = await fetch(DATA_BASE + meta.file);
    if (!r.ok) throw new Error(`${meta.file} ${r.status}`);
    const bytes = await gunzip(await r.arrayBuffer());

    const n = meta.n_samples;
    const nCh = index.channels.length;
    const expected = n * nCh * 2;
    if (bytes.byteLength !== expected) {
      throw new Error(`decoded ${bytes.byteLength} bytes, expected ${expected}`);
    }
    const i16 = new Int16Array(bytes.buffer, bytes.byteOffset, n * nCh);
    const gain = index.gain;

    // channel-major: channel c occupies [c*n, (c+1)*n)
    const data = [];
    for (let c = 0; c < nCh; c++) {
      const ch = new Float32Array(n);
      const base = c * n;
      for (let i = 0; i < n; i++) ch[i] = i16[base + i] / gain;
      data.push(ch);
    }

    return {
      id: meta.id,
      fs: index.downsample_fs,
      chNames: index.channels.slice(),
      data,
      nSamples: n,
      source: "compact",
      meta,
    };
  }

  // ---- Local full-res WFDB (CONTRACTS §5) --------------------------------

  /** numeric suffix of an "EHG12" style label, for ascending reorder. */
  function chOrderKey(label, fallbackIdx) {
    const m = /(\d+)\s*$/.exec(label || "");
    return m ? parseInt(m[1], 10) : 1000 + fallbackIdx;
  }

  /**
   * Parse a WFDB `.hea` header (format 16). Returns
   * { name, nsig, fs, nsamp, datFile, signals:[{gain, baseline, label, fmt}] }.
   */
  function parseHea(text) {
    const lines = text
      .split(/\r?\n/)
      .map((l) => l.trim())
      .filter((l) => l && !l.startsWith("#"));
    if (!lines.length) throw new Error("empty .hea");

    const rec = lines[0].split(/\s+/);
    const name = rec[0];
    const nsig = parseInt(rec[1], 10);
    // fs token may be "200" or "200/…"; take the leading number.
    const fs = parseFloat(rec[2]);
    const nsamp = rec[3] ? parseInt(rec[3], 10) : null;
    if (!Number.isFinite(nsig) || nsig < 1) throw new Error("bad nsig in .hea");

    const signals = [];
    let datFile = null;
    for (let s = 0; s < nsig; s++) {
      const parts = (lines[1 + s] || "").split(/\s+/);
      if (parts.length < 2) throw new Error(`bad signal line ${s} in .hea`);
      const file = parts[0];
      // format may be "16" or "16x1"; take the leading integer.
      const fmt = parseInt(parts[1], 10);
      if (fmt !== 16) throw new Error(`only WFDB format 16 supported (got ${parts[1]})`);
      // gain token: "<gain>(<baseline>)/<units>" — parseFloat gets the gain;
      // baseline (ADC value of 0 physical) is in parens if present.
      const gainTok = parts[2] || "131.068";
      const gain = parseFloat(gainTok) || 131.068;
      const bm = /\(([-\d.]+)\)/.exec(gainTok);
      // adczero is field index 4 (0-based within the signal line's own columns):
      // file fmt gain adcres adczero initval checksum blocksize label
      const adczero = parts[4] !== undefined ? parseInt(parts[4], 10) : 0;
      const baseline = bm ? parseFloat(bm[1]) : (Number.isFinite(adczero) ? adczero : 0);
      const label = parts.slice(8).join(" ") || `sig${s}`;
      signals.push({ file, fmt, gain, baseline, label });
      if (datFile === null) datFile = file;
    }
    return { name, nsig, fs, nsamp, datFile, signals };
  }

  /**
   * Parse a local .hea/.dat pair (a FileList or array of File) into a Record.
   * WFDB format 16: .dat is int16 LE, sample-interleaved across signals
   * (s0c0,s0c1,…,s0c15,s1c0,…). Channels reordered to ascending EHG1..16.
   * @returns {Promise<Record>}
   */
  async function loadLocalWFDB(files) {
    files = Array.from(files || []);
    const byExt = (ext) =>
      files.find((f) => f.name.toLowerCase().endsWith(ext));
    const heaFile = byExt(".hea");
    if (!heaFile) throw new Error("pick a .hea file (plus its matching .dat)");

    const hdr = parseHea(await heaFile.text());

    // Find the .dat: prefer the exact name referenced by the header.
    const wantDat = (hdr.datFile || "").toLowerCase();
    let datFile =
      files.find((f) => f.name.toLowerCase() === wantDat) || byExt(".dat");
    if (!datFile) throw new Error(`.dat file (${hdr.datFile}) not selected`);

    const nsig = hdr.nsig;
    const buf = await datFile.arrayBuffer();
    const raw = new Int16Array(buf); // LE on all target platforms
    const total = raw.length;
    let n = Math.floor(total / nsig);
    if (hdr.nsamp && hdr.nsamp <= n) n = hdr.nsamp;
    if (n < 1) throw new Error("empty/short .dat");

    // De-interleave → per-signal Float32 (mV = (raw - baseline)/gain).
    const perSig = [];
    for (let s = 0; s < nsig; s++) {
      const g = hdr.signals[s].gain || 131.068;
      const b = hdr.signals[s].baseline || 0;
      const ch = new Float32Array(n);
      for (let i = 0; i < n; i++) ch[i] = (raw[i * nsig + s] - b) / g;
      perSig.push(ch);
    }

    // Reorder to ascending EHG1..EHG16 to match the compact contract.
    const order = hdr.signals
      .map((sig, i) => ({ i, key: chOrderKey(sig.label, i) }))
      .sort((a, b) => a.key - b.key);
    const data = order.map((o) => perSig[o.i]);
    const chNames = order.map((o) => hdr.signals[o.i].label);

    return {
      id: hdr.name || datFile.name.replace(/\.dat$/i, ""),
      fs: hdr.fs || 200,
      chNames,
      data,
      nSamples: n,
      source: "local",
      meta: null,
    };
  }

  return {
    loadIndex,
    loadAnnotations,
    loadCompactRecord,
    loadLocalWFDB,
    parseHea,
    gunzip,
  };
})();
