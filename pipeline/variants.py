"""Step 04, layer 4: feed-level projections for variants A, B and C, plus hidden gems and the gate simulation.

Everything here is a projection: per-impression causal inputs from the random log combined with stated
assumptions about delivery. Uncertainty comes from a two-way (user x video) Poisson bootstrap that re-runs the
whole procedure, including the gate decisions and the category choice.

Assumptions (also stated on the site):
- A boosted slot displaces an average recommended tab-1 slot (the baseline). Pure only logs recommended
  impressions of pool videos, so the baseline is the average recommended slot among pool videos.
- Pessimistic: boosted videos perform as under random delivery.
- Optimistic: boosted videos get the tail's targeting lift, i.e. random-delivery means x (recommended tail mean /
  random tail mean), for MWT and early skip. Hates keep the random-delivery rate: recommended tail impressions
  hold 2 hates, too few to estimate a lift.
- Variant B spends k test impressions on every tail video. With about B = 158 boosted impressions per tail video
  (the random log's rate), test slots are k / B of the boost budget.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

from pipeline import stats as st
from pipeline.causal import Data
from pipeline.config import GATE_DEFAULT_K, GATE_K, N_BOOT, SEED, SHARES, THRESHOLDS

PROJ = ["mwt", "early_skip", "hated"]
MIN_TAIL_VIDEOS_C = 20  # categories with fewer tail videos are not eligible for variant C
MIN_LATER_IMPS = 20  # videos need this many post-gate impressions to score the gate


def _wmean(idx: np.ndarray, y: np.ndarray, w: np.ndarray) -> float:
    ww = w[idx]
    tot = ww.sum()
    return float((ww * y[idx]).sum() / tot) if tot > 0 else np.nan


class Inputs:
    """Numpy views of the random and recommended logs with shared user and video codes."""

    def __init__(self, data: Data):
        users = pd.Index(np.union1d(data.imp.user_id.unique(), data.rec.user_id.unique()))
        self.video_ids = data.videos.video_id.to_numpy()
        vidx = pd.Index(self.video_ids)
        self.n_users, self.n_videos = len(users), len(vidx)
        r, c = data.imp, data.rec
        self.r_u, self.r_v = users.get_indexer(r.user_id), vidx.get_indexer(r.video_id)
        self.c_u, self.c_v = users.get_indexer(c.user_id), vidx.get_indexer(c.video_id)
        self.r_y = {m: r[m].to_numpy(float) for m in PROJ}
        self.c_y = {m: c[m].to_numpy(float) for m in PROJ}

        tier = data.videos.tier.to_numpy()
        self.is_tail_v = tier == "tail"
        self.is_head_v = tier == "head"
        self.r_tail = np.flatnonzero(self.is_tail_v[self.r_v])
        self.r_head = np.flatnonzero(self.is_head_v[self.r_v])
        self.c_all = np.arange(len(c))
        self.c_tail = np.flatnonzero(self.is_tail_v[self.c_v])
        # The gate's bar: median per-video random-delivery MWT among head videos (fixed policy constant).
        head = r[r.tier == "head"].groupby("video_id").mwt.mean()
        self.bar = float(head.median())
        self.head_median_skip = float(r[r.tier == "head"].groupby("video_id").early_skip.mean().median())

        # Gate decisions are a property of each video's own first k impressions, so they are fixed here and
        # travel with the video when the bootstrap resamples videos. Re-deciding on reweighted impressions
        # would make the gate noisier than it is and bias the passers' later performance down.
        nth = r.nth_for_video.to_numpy()
        self.gate_pass = {}
        for k in GATE_K:
            first = r[(r.tier == "tail") & (r.nth_for_video <= k)].groupby("video_id").mwt.mean()
            self.gate_pass[k] = np.isin(self.video_ids, first[first >= self.bar].index)
        self.gate_first = {k: self.r_tail[nth[self.r_tail] <= k] for k in GATE_K}
        self.gate_later = {k: self.r_tail[nth[self.r_tail] > k] for k in GATE_K}
        self.tail_imps_per_video = len(self.r_tail) / self.is_tail_v.sum()

        # Variant C: category choice on the first half, evaluation on the second half.
        cats = data.videos.l1_en.fillna("Unknown")
        tail_counts = cats[self.is_tail_v].value_counts()
        self.cat_names = np.array(sorted(cats.unique()))
        cat_code_v = pd.Index(self.cat_names).get_indexer(cats)
        self.eligible = np.isin(self.cat_names, tail_counts[tail_counts >= MIN_TAIL_VIDEOS_C].index)
        self.r_cat = cat_code_v[self.r_v]
        half = r.half.to_numpy()
        self.r_half1 = np.flatnonzero(half == 1)
        self.tail_h1 = self.r_tail[half[self.r_tail] == 1]
        self.tail_h2 = self.r_tail[half[self.r_tail] == 2]
        self.cat_code_v = cat_code_v
        self.tail_counts = tail_counts



def estimate(inp: Inputs, w_r: np.ndarray, w_c: np.ndarray, w_v: np.ndarray) -> dict:
    """Every input to the projections for one set of impression weights (w_v: video weights)."""
    out: dict = {}
    for m in PROJ:
        out[f"base_{m}"] = _wmean(inp.c_all, inp.c_y[m], w_c)
        out[f"tail_rand_{m}"] = _wmean(inp.r_tail, inp.r_y[m], w_r)
        out[f"head_rand_{m}"] = _wmean(inp.r_head, inp.r_y[m], w_r)
        out[f"tail_rec_{m}"] = _wmean(inp.c_tail, inp.c_y[m], w_c)
    for m in ("mwt", "early_skip"):
        out[f"mult_{m}"] = out[f"tail_rec_{m}"] / out[f"tail_rand_{m}"]
    out["mult_hated"] = 1.0

    # Variant A: every tail video, untargeted.
    for m in PROJ:
        out[f"A_{m}"] = out[f"tail_rand_{m}"]

    # Variant B: gate on the first k random impressions, score on later ones.
    for k in GATE_K:
        first, later = inp.gate_first[k], inp.gate_later[k]
        passed = inp.gate_pass[k]
        later_pass = later[passed[inp.r_v[later]]]
        f_test = k / inp.tail_imps_per_video
        out[f"B{k}_pass_rate"] = (w_v * passed).sum() / (w_v * inp.is_tail_v).sum()
        out[f"B{k}_f_test"] = f_test
        for m in PROJ:
            test_m = _wmean(first, inp.r_y[m], w_r)
            later_m = _wmean(later_pass, inp.r_y[m], w_r)
            out[f"B{k}_test_{m}"] = test_m
            out[f"B{k}_passers_later_{m}"] = later_m
            out[f"B{k}_{m}"] = f_test * test_m + (1 - f_test) * later_m

    # Variant C: categories whose tail does at least as well as the average random slot (first half).
    avg_h1 = _wmean(inp.r_half1, inp.r_y["mwt"], w_r)
    cw = np.bincount(inp.r_cat[inp.tail_h1], weights=w_r[inp.tail_h1], minlength=len(inp.cat_names))
    cy = np.bincount(inp.r_cat[inp.tail_h1], weights=w_r[inp.tail_h1] * inp.r_y["mwt"][inp.tail_h1],
                     minlength=len(inp.cat_names))
    with np.errstate(invalid="ignore", divide="ignore"):
        cat_mean = cy / cw
    chosen = inp.eligible & (cw > 0) & (cat_mean >= avg_h1)
    eval_idx = inp.tail_h2[chosen[inp.r_cat[inp.tail_h2]]]
    out["C_threshold"] = avg_h1
    out["C_n_categories"] = int(chosen.sum())
    out["C_tail_video_share"] = float(inp.is_tail_v[chosen[inp.cat_code_v]].sum() / inp.is_tail_v.sum())
    out["_C_chosen"] = chosen
    out["_C_cat_mean_h1"] = cat_mean
    for m in PROJ:
        out[f"C_{m}"] = _wmean(eval_idx, inp.r_y[m], w_r) if len(eval_idx) else np.nan
        out[f"C_all_tail_h2_{m}"] = _wmean(inp.tail_h2, inp.r_y[m], w_r)
    return out


def bootstrap(inp: Inputs, n_boot: int = N_BOOT, seed: int = SEED) -> tuple[dict, pd.DataFrame]:
    point = estimate(inp, np.ones(len(inp.r_u)), np.ones(len(inp.c_u)), np.ones(inp.n_videos))
    rng = np.random.default_rng(seed)
    reps = []
    for _ in range(n_boot):
        wu = rng.poisson(1.0, inp.n_users).astype(float)
        wv = rng.poisson(1.0, inp.n_videos).astype(float)
        e = estimate(inp, wu[inp.r_u] * wv[inp.r_v], wu[inp.c_u] * wv[inp.c_v], wv)
        reps.append({k: v for k, v in e.items() if not k.startswith("_")})
    return point, pd.DataFrame(reps)


def _ci(reps: pd.Series) -> tuple[float, float]:
    return float(np.nanquantile(reps, 0.025)), float(np.nanquantile(reps, 0.975))


VARIANTS = [("A", "A. Blanket boost")] + [(f"B{k}", f"B. Gated boost (k = {k})") for k in GATE_K] + [
    ("C", "C. Content-aware boost")]


def _boost(src: dict | pd.DataFrame, key: str, m: str, scenario: str):
    b = src[f"{key}_{m}"]
    return b * src[f"mult_{m}"] if scenario == "optimistic" else b


def _change(boost, base, s: float, m: str):
    """Feed-level change at reallocation share s: relative for MWT and hates, percentage points for skip."""
    return s * (boost - base) if m == "early_skip" else s * (boost - base) / base


def _passes(value: float, m: str) -> bool:
    if m == "mwt":
        return value >= THRESHOLDS["mwt_rel_min"]
    if m == "early_skip":
        return value <= THRESHOLDS["skip_pp_max"]
    return value <= THRESHOLDS["hate_rel_max"]


def projections(point: dict, reps: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = []
    for key, label in VARIANTS:
        for scenario in ("pessimistic", "optimistic"):
            for s in SHARES:
                for m in PROJ:
                    est = _change(_boost(point, key, m, scenario), point[f"base_{m}"], s, m)
                    lo, hi = _ci(_change(_boost(reps, key, m, scenario), reps[f"base_{m}"], s, m))
                    ok = _passes(est, m)
                    robust = _passes(lo, m) and _passes(hi, m) if ok else not (_passes(lo, m) or _passes(hi, m))
                    rows.append({"variant": key, "label": label, "scenario": scenario, "share": s, "metric": m,
                                 "estimate": est, "lo": lo, "hi": hi, "passes": ok, "robust": robust,
                                 "boost_slot": float(_boost(point, key, m, scenario)), "baseline": point[f"base_{m}"]})
    proj = pd.DataFrame(rows)

    dec = []
    for (key, label, s), g in proj.groupby(["variant", "label", "share"], sort=False):
        pess = g[g.scenario == "pessimistic"]
        opt = g[g.scenario == "optimistic"]
        p_ok, o_ok = bool(pess.passes.all()), bool(opt.passes.all())
        verdict = "ship" if p_ok else "A/B test" if o_ok else "don't ship"
        deciding = pess if p_ok else opt if o_ok else pess
        failing = ", ".join(pess.loc[~pess.passes, "metric"]) if not p_ok else ""
        dec.append({"variant": key, "label": label, "share": s, "pessimistic_passes": p_ok, "optimistic_passes": o_ok,
                    "verdict": verdict, "robust": bool(deciding.robust.all()),
                    "pessimistic_fails_on": failing})
    return proj, pd.DataFrame(dec)


def decision_summary(dec: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (key, label), g in dec.groupby(["variant", "label"], sort=False):
        ship = g.loc[g.pessimistic_passes, "share"]
        test = g.loc[g.optimistic_passes, "share"]
        rows.append({"variant": key, "label": label,
                     "max_share_ship": float(ship.max()) if len(ship) else None,
                     "max_share_ab_test": float(test.max()) if len(test) else None})
    return pd.DataFrame(rows)


def max_safe_share(point: dict, reps: pd.DataFrame, key: str, scenario: str) -> dict:
    """Largest share that keeps every guardrail at its threshold (continuous, not limited to the grid)."""
    def smax(src):
        caps = []
        for m, thr, rel in (("mwt", THRESHOLDS["mwt_rel_min"], True), ("early_skip", THRESHOLDS["skip_pp_max"], False),
                            ("hated", THRESHOLDS["hate_rel_max"], True)):
            per_s = _change(_boost(src, key, m, scenario), src[f"base_{m}"], 1.0, m)  # change at s = 1
            caps.append(np.where((per_s * np.sign(thr)) > 0, thr / per_s, np.inf))
        return np.minimum.reduce(caps)
    est = float(smax(point))
    lo, hi = _ci(pd.Series(np.minimum(smax(reps), 1.0)))
    return {"variant": key, "scenario": scenario, "max_share": min(est, 1.0), "lo": lo, "hi": hi}


def inputs_table(point: dict, reps: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for m in PROJ:
        for name, key in (("Average recommended slot (baseline)", "base"), ("Head, random delivery", "head_rand"),
                          ("Tail, random delivery", "tail_rand"), ("Tail, recommended delivery", "tail_rec")):
            lo, hi = _ci(reps[f"{key}_{m}"])
            rows.append({"metric": m, "quantity": name, "estimate": point[f"{key}_{m}"], "lo": lo, "hi": hi})
        if m != "hated":
            lo, hi = _ci(reps[f"mult_{m}"])
            rows.append({"metric": m, "quantity": "Targeting lift for tail (recommended / random)",
                         "estimate": point[f"mult_{m}"], "lo": lo, "hi": hi})
    return pd.DataFrame(rows)


# --- gate simulation and hidden gems ------------------------------------------------------------------


def gate_table(data: Data, inp: Inputs, point: dict, reps: pd.DataFrame) -> pd.DataFrame:
    """Precision and recall of the gate against each video's later random-delivery MWT (point estimates),
    and the bootstrap intervals for pass rate and the passers' later performance."""
    r = data.imp[data.imp.tier == "tail"]
    rows = []
    for k in GATE_K:
        first = r[r.nth_for_video <= k].groupby("video_id").mwt.mean()
        later = r[r.nth_for_video > k].groupby("video_id").mwt.agg(["mean", "size"])
        later = later[later["size"] >= MIN_LATER_IMPS]
        df = later.join(first.rename("first"), how="inner")
        passed, good = df["first"] >= inp.bar, df["mean"] >= inp.bar
        row = {
            "k": k, "default": k == GATE_DEFAULT_K, "videos_scored": len(df),
            "pass_rate": point[f"B{k}_pass_rate"], "test_slot_share": point[f"B{k}_f_test"],
            "precision": float((passed & good).sum() / passed.sum()),
            "recall": float((passed & good).sum() / good.sum()),
            "share_good": float(good.mean()),
            "passers_later_mwt_simple": float(df.loc[passed, "mean"].mean()),
            "failers_later_mwt_simple": float(df.loc[~passed, "mean"].mean()),
        }
        for m in PROJ:
            for part in ("passers_later", "test"):
                key = f"B{k}_{part}_{m}"
                row[f"{part}_{m}"] = point[key]
                row[f"{part}_{m}_lo"], row[f"{part}_{m}_hi"] = _ci(reps[key])
            row[f"boost_slot_{m}"] = point[f"B{k}_{m}"]
            row[f"boost_slot_{m}_lo"], row[f"boost_slot_{m}_hi"] = _ci(reps[f"B{k}_{m}"])
        row["pass_rate_lo"], row["pass_rate_hi"] = _ci(reps[f"B{k}_pass_rate"])
        rows.append(row)
    return pd.DataFrame(rows)


def hidden_gems(data: Data, inp: Inputs) -> dict:
    """Empirical Bayes shrinkage of per-video random-delivery MWT and skip rate, by tier."""
    r = data.imp
    out, dist = {}, []
    for tier in ("tail", "head"):
        d = r[r.tier == tier]
        g = d.groupby("video_id").agg(mwt=("mwt", "mean"), n=("mwt", "size"), skips=("early_skip", "sum"))
        within = float(((d.mwt - d.groupby("video_id").mwt.transform("mean")) ** 2).sum() / (len(d) - len(g)))
        post, post_sd, mu, tau = st.eb_normal(g.mwt.to_numpy(), g.n.to_numpy(float), within)
        p_above = stats.norm.sf((inp.bar - post) / post_sd)
        skip_post, a, b = st.eb_beta(g.skips.to_numpy(float), g.n.to_numpy(float))
        out[tier] = {
            "videos": len(g), "prior_mean": mu, "prior_sd": tau, "within_sd": float(np.sqrt(within)),
            "mean_impressions": float(g.n.mean()),
            "share_posterior_above_bar": float((post >= inp.bar).mean()),
            "share_prob_above_bar_90": float((p_above >= 0.9).mean()),
            "share_raw_above_bar": float((g.mwt >= inp.bar).mean()),
            "share_skip_below_head_median": float((skip_post <= inp.head_median_skip).mean()),
            "skip_prior_a": a, "skip_prior_b": b,
        }
        dist.append(pd.DataFrame({"tier": tier, "video_id": g.index, "raw_mwt": g.mwt.to_numpy(),
                                  "posterior_mwt": post, "posterior_sd": post_sd, "n": g.n.to_numpy(),
                                  "posterior_skip": skip_post}))
    return {"summary": out, "bar": inp.bar, "head_median_skip": inp.head_median_skip,
            "videos": pd.concat(dist, ignore_index=True)}


def variant_c_table(inp: Inputs, point: dict, data: Data) -> pd.DataFrame:
    r = data.imp[data.imp.tier == "tail"]
    h2 = r[r.half == 2].groupby(r.l1_en.fillna("Unknown")).mwt.agg(["mean", "size"])
    df = pd.DataFrame({
        "category": inp.cat_names, "eligible": inp.eligible, "first_half_tail_mwt": point["_C_cat_mean_h1"],
        "chosen": point["_C_chosen"], "tail_videos": inp.tail_counts.reindex(inp.cat_names).fillna(0).astype(int).to_numpy(),
    })
    df["second_half_tail_mwt"] = df.category.map(h2["mean"])
    df["second_half_tail_impressions"] = df.category.map(h2["size"]).fillna(0).astype(int)
    return df[df.eligible].sort_values("first_half_tail_mwt", ascending=False).reset_index(drop=True)


# --- distribution breadth ---------------------------------------------------------------------------------


def _breadth(p: np.ndarray, is_tail: np.ndarray) -> dict:
    p = p / p.sum()
    nz = p[p > 0]
    top = np.sort(p)[::-1][: int(round(0.2 * len(p)))].sum()
    return {"tail_share": float(p[is_tail].sum()), "effective_videos": float(np.exp(-(nz * np.log(nz)).sum())),
            "top20_share": float(top), "videos_reached": int((p > 0).sum())}


def breadth(data: Data, inp: Inputs, point: dict) -> pd.DataFrame:
    """Mechanical projection of exposure concentration among pool videos (tab 1, experiment period)."""
    base = np.bincount(inp.c_v, minlength=inp.n_videos).astype(float)
    base /= base.sum()
    tail = inp.is_tail_v
    q = {"A": tail / tail.sum()}
    for k in GATE_K:
        passed = inp.gate_pass[k]
        f = point[f"B{k}_f_test"]
        q[f"B{k}"] = f * tail / tail.sum() + (1 - f) * passed / passed.sum()
    chosen_v = tail & point["_C_chosen"][inp.cat_code_v]
    q["C"] = chosen_v / chosen_v.sum()
    rows = [{"variant": "baseline", "share": 0.0, **_breadth(base, tail)}]
    for key, _ in VARIANTS:
        for s in SHARES:
            rows.append({"variant": key, "share": s, **_breadth((1 - s) * base + s * q[key], tail)})
    return pd.DataFrame(rows)


def run(data: Data, n_boot: int = N_BOOT) -> dict:
    inp = Inputs(data)
    point, reps = bootstrap(inp, n_boot)
    proj, dec = projections(point, reps)
    return {
        "inputs": inputs_table(point, reps),
        "projection": proj,
        "decision": dec,
        "decision_summary": decision_summary(dec),
        "max_share": pd.DataFrame([max_safe_share(point, reps, key, sc) for key, _ in VARIANTS
                                   for sc in ("pessimistic", "optimistic")]),
        "gate": gate_table(data, inp, point, reps),
        "hidden_gems": hidden_gems(data, inp),
        "variant_c": variant_c_table(inp, point, data),
        "variant_c_summary": {"threshold_first_half_avg_random_mwt": point["C_threshold"],
                              "n_categories": point["C_n_categories"], "tail_video_share": point["C_tail_video_share"],
                              "min_tail_videos": MIN_TAIL_VIDEOS_C},
        "breadth": breadth(data, inp, point),
        "assumptions": {"tail_imps_per_video": inp.tail_imps_per_video, "bar_mwt": inp.bar,
                        "head_median_skip": inp.head_median_skip, "n_boot": n_boot, "shares": SHARES,
                        "thresholds": THRESHOLDS, "gate_k": GATE_K, "gate_default_k": GATE_DEFAULT_K},
    }
