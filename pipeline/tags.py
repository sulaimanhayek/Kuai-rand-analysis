"""Step 06: validate the LLM tags and carry their error into the content results.

1. Agreement with the platform's own categories, mapped onto the same verticals. Both are model outputs.
2. An audit sample of about 300 videos, labelled blind from the same caption and cover text by a second model
   (Claude), weighted back to all tagged videos. Strata: tier x (LLM and platform agree or not).
3. Propagation: the within-content tail penalty and the per-vertical MWT effects, computed with platform verticals,
   with LLM verticals, and with LLM verticals corrected for misclassification. With M[t, r] = P(LLM label r | audit
   label t), assumed the same in both tiers and unrelated to the outcome, each tier's observed class totals are
   S_obs = M' S_true (watch time) and N_obs = M' N_true (impressions); solving gives the true-class means and mix.
   M is drawn from a Dirichlet posterior on the weighted audit counts inside a two-way Poisson bootstrap.

Write the audit sample (once) with: python -m pipeline.tags
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

from pipeline import db
from pipeline import stats as st
from pipeline.causal import Data
from pipeline.config import N_BOOT, ROOT, SEED
from pipeline.llm import FORMATS, PLATFORM_TO_VERTICAL, TAGS_PATH, UNCLEAR, VERTICALS

AUDIT_DIR = ROOT / "notes" / "hand_labels"
SAMPLE_PATH = AUDIT_DIR / "sample.csv"
AUDIT_SIZE = {"tail": 120, "head": 120, "mid": 60}  # per tier, split evenly between the agree and disagree strata
AUDIT_EXCLUDE_MAX_ID = 80  # tags of videos 0 to 80 were read while the prompt was being written
N_AUDIT_BOOT = 1000
TAGS = ["vertical", "format", "commercial"]
CLASSES = [*VERTICALS, UNCLEAR]
MIN_VIDEOS = 20  # per tier, for a vertical to get its own class in the propagation; the rest are pooled
REST = "Smaller verticals"
PRIOR_N = 1.0  # pseudo-observations per row of the confusion matrix, spread like the overall confusion
MAX_DRAWS = 100  # confusion-matrix draws per bootstrap replicate before giving up on a feasible one

HASHTAG = re.compile(r"[#＃][^#＃@\s]*|@[^#＃@\s]*")


def load() -> pd.DataFrame | None:
    """The committed tag table, indexed by video_id. Videos without any text have empty tags."""
    if not TAGS_PATH.exists():
        return None
    t = pd.read_csv(TAGS_PATH).set_index("video_id")
    t["commercial"] = t.commercial.map({True: True, False: False, "True": True, "False": False})
    return t


def segment_columns(tags: pd.DataFrame) -> pd.DataFrame:
    """LLM tags as content segments for step 05 ("NN|label" levels)."""
    order = sorted(v for v in VERTICALS if v != "Other") + ["Other", UNCLEAR]
    vert = {v: f"{i + 1:02d}|{v}" for i, v in enumerate(order)}
    return pd.DataFrame({
        "Content vertical (LLM)": tags.vertical.map(vert),
        "Format (LLM)": tags.format.map({"original": "01|Original", "clip_or_reupload": "02|Clip or re-upload",
                                         "unclear": "03|Unclear"}),
        "Commercial (LLM)": tags.commercial.map({True: "01|Commercial", False: "02|Not commercial"}),
    }, index=tags.index)


def video_frame(videos: pd.DataFrame, tags: pd.DataFrame) -> pd.DataFrame:
    """One row per tagged video: LLM tags, platform vertical, tier and caption features."""
    v = videos.set_index("video_id")[["tier", "l1_en", "caption", "show_cover_text", "caption_repaired"]]
    f = tags[tags.vertical.notna()].join(v, how="inner")
    f["platform"] = f.l1_en.map(PLATFORM_TO_VERTICAL)
    f["agree"] = (f.vertical == f.platform).astype(bool)
    cap = f.caption.fillna("")
    length = cap.str.len()
    stripped = cap.str.replace(HASHTAG, "", regex=True).str.replace(r"[\W_]+", "", regex=True)
    f["Caption length"] = np.select([length == 0, length <= 20, length <= 50],
                                    ["01|No caption", "02|Up to 20 characters", "03|21 to 50 characters"],
                                    "04|Over 50 characters")
    f["Hashtags and mentions only"] = np.where((length > 0) & (stripped.str.len() == 0), "01|Yes", "02|No")
    f["Repaired caption row"] = np.where(f.caption_repaired.fillna(False).astype(bool), "01|Yes", "02|No")
    return f


def kappa(a, b, w=None) -> float:
    """Cohen's kappa, optionally with row weights."""
    a, b = np.asarray(a, dtype=object), np.asarray(b, dtype=object)
    w = np.ones(len(a)) if w is None else np.asarray(w, dtype=float)
    labels = sorted(set(a) | set(b), key=str)
    ia, ib = pd.Categorical(a, labels).codes, pd.Categorical(b, labels).codes
    c = np.zeros((len(labels), len(labels)))
    np.add.at(c, (ia, ib), w)
    c /= c.sum()
    po, pe = np.trace(c), c.sum(1) @ c.sum(0)
    return float((po - pe) / (1 - pe)) if pe < 1 else float("nan")


# --- descriptives and agreement with the platform ---------------------------------------------------


def distribution(f: pd.DataFrame, n_videos: int) -> dict:
    out = {"videos": n_videos, "tagged": len(f), "no_text": n_videos - len(f)}
    for tag in TAGS:
        c = pd.crosstab(f[tag].astype(str), f.tier)
        rows = pd.DataFrame({"n": c.sum(1), "share": c.sum(1) / len(f),
                             "share_tail": c["tail"] / c["tail"].sum(), "share_head": c["head"] / c["head"].sum()})
        out[tag] = rows.rename_axis("level").reset_index()
    return out


def platform_agreement(f: pd.DataFrame) -> dict:
    g = f[f.platform.notna()]
    per_class = []
    for c in VERTICALS:
        n_llm, n_plat = int((g.vertical == c).sum()), int((g.platform == c).sum())
        both = int(((g.vertical == c) & (g.platform == c)).sum())
        per_class.append({"vertical": c, "n_llm": n_llm, "n_platform": n_plat, "n_both": both,
                          "precision": both / n_llm if n_llm else None, "recall": both / n_plat if n_plat else None})
    conf = g.groupby(["vertical", "platform"]).size().rename("n").reset_index()
    return {
        "n": len(g),
        "agreement": g.agree.mean(),
        "kappa": kappa(g.vertical, g.platform),
        "unclear_share": (g.vertical == UNCLEAR).mean(),
        "agreement_excluding_unclear": g[g.vertical != UNCLEAR].agree.mean(),
        "by_tier": g.groupby("tier").agree.mean(),
        "per_class": pd.DataFrame(per_class),
        "confusion": conf,
    }


# --- audit --------------------------------------------------------------------------------------------


def _audit_frame(f: pd.DataFrame) -> pd.DataFrame:
    f = f[f.index > AUDIT_EXCLUDE_MAX_ID]
    return f.assign(stratum=f.tier + "|" + np.where(f.agree, "agree", "disagree"))


def write_sample(seed: int = SEED) -> None:
    """Draw the audit sample. The sheet shows only id, caption and cover text, in id order."""
    if SAMPLE_PATH.exists():
        raise SystemExit(f"{SAMPLE_PATH.relative_to(ROOT)} exists; delete it to draw a new sample")
    with db.connect(read_only=True) as con:
        videos = con.sql("SELECT * FROM videos").df()
    f = _audit_frame(video_frame(videos, load()))
    rng = np.random.default_rng(seed)
    picks = []
    for tier, n in AUDIT_SIZE.items():
        for stratum in (f"{tier}|agree", f"{tier}|disagree"):
            g = f[f.stratum == stratum]
            k = min(n // 2, len(g))
            # Systematic sample after sorting by LLM vertical: every class shows up in proportion, equal probabilities.
            g = g.assign(r=rng.random(len(g))).sort_values(["vertical", "r"])
            step = len(g) / k
            picks.append(g.iloc[(rng.uniform(0, step) + step * np.arange(k)).astype(int)])
    s = pd.concat(picks).sort_index()
    sheet = pd.DataFrame({"video_id": s.index, "caption": s.caption.fillna(""), "cover_text": s.show_cover_text.fillna(""),
                          "vertical": "", "format": "", "commercial": ""})
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    sheet.to_csv(SAMPLE_PATH, index=False)
    print(f"wrote {len(sheet)} rows to {SAMPLE_PATH.relative_to(ROOT)}")


def read_audit(f: pd.DataFrame) -> pd.DataFrame | None:
    """Labelled audit rows joined to the tags, with weights = stratum population / labelled rows in the stratum."""
    if not SAMPLE_PATH.exists():
        return None
    a = pd.read_csv(SAMPLE_PATH, dtype=str, keep_default_na=False)
    a = a.set_index(a.video_id.astype(int))
    a = a[a.vertical != ""]
    if a.empty:
        return None
    bad = (~a.vertical.isin(CLASSES)) | (~a.format.isin(FORMATS)) | (~a.commercial.str.lower().isin(["true", "false"]))
    if bad.any():
        raise ValueError(f"invalid audit labels for videos {a.index[bad].tolist()[:10]}")
    a = pd.DataFrame({"audit_vertical": a.vertical, "audit_format": a.format,
                      "audit_commercial": a.commercial.str.lower() == "true"})
    frame = _audit_frame(f)
    m = a.join(frame, how="inner")
    m["w"] = m.stratum.map(frame.stratum.value_counts() / m.stratum.value_counts())
    return m


def _agreement(m: pd.DataFrame, a: str, b: str) -> tuple[float, float]:
    g = m[m[a].notna() & m[b].notna()]
    x, y = g[a].astype(str).to_numpy(), g[b].astype(str).to_numpy()
    return float(np.average(x == y, weights=g.w)), kappa(x, y, g.w)


def _audit_stats(m: pd.DataFrame) -> dict:
    out = {}
    for name, a, b in [("vertical: LLM vs audit", "vertical", "audit_vertical"),
                       ("vertical: platform vs audit", "platform", "audit_vertical"),
                       ("vertical: LLM vs platform", "vertical", "platform"),
                       ("format: LLM vs audit", "format", "audit_format"),
                       ("commercial: LLM vs audit", "commercial", "audit_commercial")]:
        out[f"{name}|agreement"], out[f"{name}|kappa"] = _agreement(m, a, b)
    out["commercial: audit share|share"] = float(np.average(m.audit_commercial, weights=m.w))
    out["commercial: LLM share|share"] = float(np.average(m.commercial.astype(bool), weights=m.w))
    return out


def audit(m: pd.DataFrame) -> dict:
    est = _audit_stats(m)
    rng = np.random.default_rng(SEED)
    groups = list(m.groupby("stratum").indices.values())
    boots = [_audit_stats(m.iloc[np.concatenate([rng.choice(ix, len(ix)) for ix in groups])])
             for _ in range(N_AUDIT_BOOT)]
    stats = []
    for k, v in est.items():
        b = np.array([bb[k] for bb in boots])
        pair, quantity = k.split("|")
        stats.append({"comparison": pair, "quantity": quantity, "estimate": v,
                      "lo": float(np.nanquantile(b, 0.025)), "hi": float(np.nanquantile(b, 0.975))})
    errors = []
    for by in ["vertical", "Caption length", "Hashtags and mentions only", "Repaired caption row"]:
        for level, g in m.groupby(by):
            row = {"by": "LLM vertical" if by == "vertical" else by, "level": str(level).split("|")[-1],
                   "n": len(g), "weight_share": g.w.sum() / m.w.sum()}
            for tag in TAGS:
                row[f"{tag}_error"] = float(np.average(g[tag].astype(str) != g[f"audit_{tag}"].astype(str), weights=g.w))
            errors.append(row)
    conf = m.groupby(["vertical", "audit_vertical"]).agg(n=("w", "size"), weight=("w", "sum")).reset_index()
    conf["weight"] /= m.w.sum()
    strata = m.groupby("stratum").agg(n=("w", "size"), weight=("w", "first")).reset_index()
    return {"n": len(m), "strata": strata, "stats": pd.DataFrame(stats), "errors": pd.DataFrame(errors),
            "confusion": conf}


# --- propagation ----------------------------------------------------------------------------------------


def _class_sums(k: np.ndarray, y: np.ndarray, t: np.ndarray, w: np.ndarray, K: int) -> tuple[np.ndarray, ...]:
    """Outcome totals and impression counts by class: tail (S, N), then head (S, N)."""
    tail = t == 1
    return (np.bincount(k[tail], (y * w)[tail], K), np.bincount(k[tail], w[tail], K),
            np.bincount(k[~tail], (y * w)[~tail], K), np.bincount(k[~tail], w[~tail], K))


def _summary(sums: tuple[np.ndarray, ...]) -> tuple[np.ndarray, dict]:
    """Per-class relative effects, and the category-level decomposition (head reweighted to the tail's mix)."""
    st_, nt, sh, nh = sums
    with np.errstate(invalid="ignore", divide="ignore"):
        mt, mh = st_ / nt, sh / nh
    m_tail, m_head = st_.sum() / nt.sum(), sh.sum() / nh.sum()
    m_rw = float(nt / nt.sum() @ mh)
    return mt / mh - 1, {"raw_rel": m_tail / m_head - 1, "mix_rel": m_rw / m_head - 1,
                         "within_rel": (m_tail - m_rw) / m_head, "mix_share_of_gap": (m_rw - m_head) / (m_tail - m_head)}


def _correct(sums: tuple[np.ndarray, ...], M: np.ndarray, y_max: float = np.inf) -> tuple[np.ndarray, ...] | None:
    """True-class totals from observed ones: x_obs = M' x_true. None if a class ends up with no impressions or a
    mean outside [0, y_max], which means M cannot have produced the observed labels and outcomes."""
    st_, nt, sh, nh = out = tuple(np.linalg.solve(M.T, x) for x in sums)
    ok = all((n > 0).all() and (s >= 0).all() and (s <= y_max * n).all() for s, n in ((st_, nt), (sh, nh)))
    return out if ok else None


def _misclassification(m: pd.DataFrame, classes: list[str], rng: np.random.Generator | None = None) -> np.ndarray:
    """M[t, r] = P(LLM class r | audit class t): posterior mean, or a Dirichlet draw when rng is given."""
    K = len(classes)
    true, obs = _codes(m.audit_vertical, classes), _codes(m.vertical, classes)
    w = m.w.to_numpy()
    counts = np.zeros((K, K))
    np.add.at(counts, (true, obs), w)
    wsum, w2 = np.bincount(true, w, K), np.bincount(true, w ** 2, K)
    n_eff = np.divide(wsum ** 2, w2, out=np.zeros(K), where=w2 > 0)
    share = np.divide(counts, wsum[:, None], out=np.zeros((K, K)), where=wsum[:, None] > 0)
    acc = np.average(obs == true, weights=w)
    prior = np.full((K, K), (1 - acc) / (K - 1))
    np.fill_diagonal(prior, acc)
    alpha = n_eff[:, None] * share + PRIOR_N * prior
    if rng is None:
        return alpha / alpha.sum(1, keepdims=True)
    return np.vstack([rng.dirichlet(a) for a in alpha])


def _codes(labels, classes: list[str]) -> np.ndarray:
    """Index into classes; anything not listed (or missing) goes to the last class, the pooled rest."""
    idx = {c: i for i, c in enumerate(classes[:-1])}
    return np.array([idx.get(x, len(classes) - 1) for x in labels], dtype=np.int64)


def propagation(data: Data, f: pd.DataFrame, m: pd.DataFrame | None, n_boot: int = N_BOOT) -> dict:
    d = data.ht
    d = d[d.video_id.isin(f.index)]
    vids = f[f.tier.isin(["tail", "head"])]
    big = [c for c in VERTICALS
           if min((vids[vids.vertical == c].tier.value_counts().reindex(["tail", "head"], fill_value=0)).min(),
                  (vids[vids.platform == c].tier.value_counts().reindex(["tail", "head"], fill_value=0)).min())
           >= MIN_VIDEOS]
    classes = [*big, REST]
    K = len(classes)
    k_llm = _codes(d.video_id.map(f.vertical), classes)
    k_plat = _codes(d.video_id.map(f.platform), classes)
    y, t = d.mwt.to_numpy(), d.treat.to_numpy()
    y_max = float(y.max())  # the cap
    u, v = st.codes(d.user_id.to_numpy()), st.codes(d.video_id.to_numpy())

    rejected = []

    def one(w, draw):
        out = {"Platform verticals": _summary(_class_sums(k_plat, y, t, w, K))}
        s_llm = _class_sums(k_llm, y, t, w, K)
        out["LLM verticals"] = _summary(s_llm)
        if m is not None:
            # Draws of M that imply negative impressions or impossible means are incompatible with the observed data:
            # reject and redraw, so the interval comes from the posterior restricted to feasible confusion matrices.
            fixed, tries = None, 0
            while fixed is None and tries < MAX_DRAWS:
                fixed, tries = _correct(s_llm, draw(), y_max), tries + 1
            rejected.append(tries - (fixed is not None))
            out["LLM verticals, corrected"] = _summary(fixed) if fixed else (np.full(K, np.nan), {})
        return out

    base = one(np.ones(len(d)), lambda: _misclassification(m, classes))
    rng = np.random.default_rng(SEED)
    boots = [one(st.poisson_weights(rng, u, v), lambda: _misclassification(m, classes, rng)) for _ in range(n_boot)]

    effects, decomp = [], []
    for labels, (eff, dec) in base.items():
        b_eff = np.array([bb[labels][0] for bb in boots])
        lo, hi = np.nanquantile(b_eff, 0.025, axis=0), np.nanquantile(b_eff, 0.975, axis=0)
        for j, c in enumerate(big):
            effects.append({"vertical": c, "labels": labels, "estimate": eff[j], "lo": lo[j], "hi": hi[j]})
        for q, val in dec.items():
            b = np.array([bb[labels][1].get(q, np.nan) for bb in boots])
            decomp.append({"labels": labels, "quantity": q, "estimate": val,
                           "lo": float(np.nanquantile(b, 0.025)), "hi": float(np.nanquantile(b, 0.975))})
    counts = pd.DataFrame({
        "vertical": classes,
        "llm_tail_videos": [int(((vids.tier == "tail") & (_codes(vids.vertical, classes) == i)).sum()) for i in range(K)],
        "llm_head_videos": [int(((vids.tier == "head") & (_codes(vids.vertical, classes) == i)).sum()) for i in range(K)],
        "platform_tail_videos": [int(((vids.tier == "tail") & (_codes(vids.platform, classes) == i)).sum()) for i in range(K)],
        "platform_head_videos": [int(((vids.tier == "head") & (_codes(vids.platform, classes) == i)).sum()) for i in range(K)],
    })
    draws = None
    if m is not None:
        boot_rejected = rejected[1:]
        draws = {"accepted": sum(bool(bb["LLM verticals, corrected"][1]) for bb in boots),
                 "rejected": int(sum(boot_rejected)), "replicates": n_boot, "max_per_replicate": MAX_DRAWS}
    return {"classes": classes, "min_videos_per_tier": MIN_VIDEOS, "impressions": len(d), "counts": counts,
            "effects": pd.DataFrame(effects), "decomposition": pd.DataFrame(decomp), "correction_draws": draws}


def run(data: Data, tags: pd.DataFrame) -> dict:
    f = video_frame(data.videos, tags)
    m = read_audit(f)
    return {
        "model": tags.model.dropna().iloc[0],
        "prompt_version": tags.prompt_version.dropna().iloc[0],
        "distribution": distribution(f, len(data.videos)),
        "platform_agreement": platform_agreement(f),
        "audit": audit(m) if m is not None else None,
        "propagation": propagation(data, f, m),
    }


if __name__ == "__main__":
    write_sample()
