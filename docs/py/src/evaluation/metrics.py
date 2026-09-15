"""
Evaluation framework — the single scoring contract every method is judged by.

A method produces, for a Recording, a list of *detected events*:
    DetectedEvent(onset, offset=None, peak=None, score=None)
We score these against the harmonised ground-truth Intervals.

Metrics
-------
1. Event detection (per-contraction):
     - a detection matches a truth interval if their [onset,offset] overlap OR the
       detection onset falls within `tol_s` of the truth onset/interval.
     - TP / FP / FN -> Sensitivity (recall), PPV (precision), F1.
   Greedy one-to-one matching, nearest-onset first.
2. Onset latency (the headline): for each matched TP, signed latency =
       detected_onset - reference_onset
   reported against EVERY available reference clock present on the truth interval
   (mechanical TOCO onset, annotated electrical begin, manual-log start). Negative
   latency = detected before that reference (early = good).
3. Magnitude / strength: Spearman & Pearson correlation between a method's per-event
   strength estimate and the truth strength (toco_amp / pain_score) where available.
4. Sample-level (optional): point-wise sensitivity/specificity over a contraction
   mask, plus AUC when a continuous score is provided.

All metrics degrade gracefully when a reference is absent for a dataset.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
import numpy as np


@dataclass
class DetectedEvent:
    onset: float
    offset: Optional[float] = None
    peak: Optional[float] = None
    score: Optional[float] = None
    strength: Optional[float] = None


def _overlap(a0, a1, b0, b1):
    return max(0.0, min(a1, b1) - max(a0, b0))


# Maximum plausible duration (s) of a single detected contraction. Detections longer
# than this are "non-specific" blobs (e.g. a model that flags most of the recording);
# they cannot serve as a clean localized true positive and are counted as false
# positives instead. This closes the loophole (found in adversarial review) whereby a
# recording-spanning detection scored 1 TP / 0 FP and an artificially early latency.
MAX_EVENT_S = 240.0


def match_events(dets, truth, tol_s=60.0, max_event_s=MAX_EVENT_S):
    """Greedy one-to-one match. Returns (matches, fp_idx, fn_idx).
    matches: list of (det_index, truth_index). A match requires interval overlap OR
    |det.onset - truth.onset| <= tol_s (covers point-labelled truth). A detection whose
    duration exceeds `max_event_s` is deemed non-specific: it is excluded from matching
    and forced to a false positive."""
    used_t = set()
    matches = []
    toolong = set()
    # sort detections by onset for stable, nearest-first assignment
    order = sorted(range(len(dets)), key=lambda i: dets[i].onset)
    for di in order:
        d = dets[di]
        d_off = d.offset if d.offset is not None else d.onset
        if max_event_s is not None and (d_off - d.onset) > max_event_s:
            toolong.add(di)               # non-specific blob -> cannot be a TP
            continue
        best, best_cost = None, None
        for ti, t in enumerate(truth):
            if ti in used_t:
                continue
            ov = _overlap(d.onset, d_off, t.onset, t.offset)
            near = abs(d.onset - t.onset)
            ok = (ov > 0) or (near <= tol_s)
            if ok:
                cost = near  # prefer closest onset
                if best_cost is None or cost < best_cost:
                    best, best_cost = ti, cost
        if best is not None:
            used_t.add(best)
            matches.append((di, best))
    matched_d = {m[0] for m in matches}
    fp = [i for i in range(len(dets)) if i not in matched_d]  # includes toolong
    fn = [i for i in range(len(truth)) if i not in used_t]
    return matches, fp, fn


def detection_scores(dets, truth, tol_s=60.0, max_event_s=MAX_EVENT_S):
    matches, fp, fn = match_events(dets, truth, tol_s, max_event_s)
    TP, FP, FN = len(matches), len(fp), len(fn)
    sens = TP / (TP + FN) if (TP + FN) else 0.0
    ppv = TP / (TP + FP) if (TP + FP) else 0.0
    f1 = 2 * sens * ppv / (sens + ppv) if (sens + ppv) else 0.0
    return dict(TP=TP, FP=FP, FN=FN, sensitivity=sens, ppv=ppv, f1=f1,
                _matches=matches)


def latency_stats(dets, truth, matches):
    """Signed onset latency (detected - reference) for each matched pair, keyed by
    the reference type available on that truth interval."""
    lat = {"annot_elec": [], "annot_point": [], "manual_log": [], "toco_mech": [],
           "toco_peak": []}
    for di, ti in matches:
        d, t = dets[di], truth[ti]
        lat[t.ref_type].append(d.onset - t.onset)
        if t.peak is not None and t.strength_kind == "toco_amp":
            # electrical onset relative to mechanical peak (informational)
            lat["toco_peak"].append(d.onset - t.peak)
    out = {}
    for k, v in lat.items():
        if v:
            arr = np.array(v, float)
            out[k] = dict(n=len(arr), mean=float(arr.mean()), median=float(np.median(arr)),
                          std=float(arr.std()), p25=float(np.percentile(arr, 25)),
                          p75=float(np.percentile(arr, 75)),
                          values=[float(x) for x in arr])   # raw per-TP for correct pooling
    return out


def magnitude_corr(dets, truth, matches):
    xs, ys = [], []
    for di, ti in matches:
        d, t = dets[di], truth[ti]
        if d.strength is not None and t.strength is not None:
            xs.append(d.strength); ys.append(t.strength)
    if len(xs) < 4:
        return {}
    xs, ys = np.array(xs), np.array(ys)
    from scipy.stats import pearsonr, spearmanr
    pr = pearsonr(xs, ys); sr = spearmanr(xs, ys)
    return dict(n=len(xs), pearson=float(pr[0]), pearson_p=float(pr[1]),
                spearman=float(sr[0]), spearman_p=float(sr[1]),
                strength_kind=truth[matches[0][1]].strength_kind)


def sample_level(score, fs, truth, n, thr=None):
    """Point-wise metrics over a contraction mask built from truth intervals.
    If truth is point-labelled, a +/-60 s window is used around each point."""
    y = np.zeros(n, dtype=bool)
    for t in truth:
        a = int(t.onset * fs)
        b = int(t.offset * fs) if t.offset > t.onset else int((t.onset + 60) * fs)
        if t.offset == t.onset:            # point label -> window
            a, b = int((t.onset - 60) * fs), int((t.onset + 60) * fs)
        y[max(0, a):min(n, b)] = True
    out = {"positive_frac": float(y.mean())}
    if score is not None:
        s = np.asarray(score, float)[:n]
        from sklearn.metrics import roc_auc_score
        if y.any() and not y.all():
            try:
                out["auc"] = float(roc_auc_score(y, s))
            except Exception:
                pass
    return out


def evaluate(dets, rec, tol_s=60.0, score=None):
    """Full per-recording evaluation bundle."""
    ds = detection_scores(dets, rec.intervals, tol_s)
    matches = ds.pop("_matches")
    res = dict(rec_id=rec.rec_id, dataset=rec.dataset, modality=rec.modality,
               n_truth=len(rec.intervals), **ds)
    res["latency"] = latency_stats(dets, rec.intervals, matches)
    mc = magnitude_corr(dets, rec.intervals, matches)
    if mc:
        res["magnitude"] = mc
    if score is not None:
        res["sample"] = sample_level(score, rec.fs, rec.intervals, rec.n_samples, )
    return res


def aggregate(results):
    """Pool per-recording results -> dataset-level and overall micro/macro stats."""
    import collections
    by_ds = collections.defaultdict(list)
    for r in results:
        by_ds[r["dataset"]].append(r)
    agg = {}
    for ds, rs in list(by_ds.items()) + [("ALL", results)]:
        TP = sum(r["TP"] for r in rs); FP = sum(r["FP"] for r in rs); FN = sum(r["FN"] for r in rs)
        sens = TP / (TP + FN) if (TP + FN) else 0.0
        ppv = TP / (TP + FP) if (TP + FP) else 0.0
        f1 = 2 * sens * ppv / (sens + ppv) if (sens + ppv) else 0.0
        # Pool TRUE per-TP latencies (not per-recording medians). The headline
        # "onset latency" is measured against the human-marked contraction onset
        # (annot_elec / annot_point / manual_log). Mechanical (toco_mech/toco_peak)
        # references are pooled separately so clocks are never mixed.
        anno_ref = {"annot_elec", "annot_point", "manual_log"}
        lat_anno, lat_mech = [], []
        for r in rs:
            for k, v in r.get("latency", {}).items():
                vals = v.get("values", [v["median"]] * v["n"])
                if k in anno_ref:
                    lat_anno += vals
                elif k == "toco_mech":
                    lat_mech += vals
        agg[ds] = dict(n_recs=len(rs), TP=TP, FP=FP, FN=FN,
                       sensitivity=round(sens, 4), ppv=round(ppv, 4), f1=round(f1, 4),
                       median_onset_latency_s=round(float(np.median(lat_anno)), 2) if lat_anno else None,
                       n_latency=len(lat_anno),
                       median_onset_latency_vs_toco_s=round(float(np.median(lat_mech)), 2) if lat_mech else None)
    return agg
