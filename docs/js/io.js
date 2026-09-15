/**
 * io.js — data loading for the EHG explorer.
 *
 * Phase 0: compact committed records (channel-major int16 gzip, CONTRACTS §1/§4).
 * The local full-res WFDB picker (CONTRACTS §5) is a Phase 2 stub below.
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
      throw new Error("DecompressionStream unavailable (gzip fallback is Phase 3)");
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

  /** Phase 2: parse a local .hea/.dat (WFDB fmt 16) → Record. */
  async function loadLocalWFDB(_files) {
    throw new Error("local WFDB picker is implemented in Phase 2");
  }

  return { loadIndex, loadAnnotations, loadCompactRecord, loadLocalWFDB, gunzip };
})();
