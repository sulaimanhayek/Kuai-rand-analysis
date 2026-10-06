"""Step 04: causal estimates on the random-exposure log.

Layer 1: impression-level tail vs head with two-way (user x video) cluster-robust SEs.
Layer 2: a user-level readout constructed from the slot-level randomisation, with CUPED.
Layer 3: tier gaps under recommended vs random delivery (descriptive).
Plus randomisation checks, sensitivities, time patterns, content-mix decomposition, carryover,
A/A calibration and power inputs. Layer 4 (projection) lives in pipeline/variants.py.
"""

from __future__ import annotations

from dataclasses import dataclass

import duckdb
import numpy as np
import pandas as pd
from scipy import stats

from pipeline import stats as st
from pipeline.config import ALPHA, LATE_POOL_DATE, METRICS, N_AA, N_BOOT, POWER, SEED

COVARIATES = ["pre_mwt", "pre_skip", "pre_long_view", "pre_like", "pre_log_n"]
OTHER = "All other categories"
PRE_FOR_METRIC = {
    "mwt": "pre_mwt", "early_skip": "pre_skip", "long_view": "pre_long_view", "liked": "pre_like",
    "hated": "pre_hate", "followed": "pre_follow", "profile_entered": "pre_profile",
}


@dataclass
class Data:
    imp: pd.DataFrame  # random tab-1 impressions, experiment period
    rec: pd.DataFrame  # recommended tab-1 impressions of pool videos, experiment period
    users: pd.DataFrame
    videos: pd.DataFrame
    nxt: pd.DataFrame  # next logged impression after each random impression
    cap_s: float

    @property
    def ht(self) -> pd.DataFrame:
        """Tail and head random impressions, with treat = 1 for tail."""
        d = self.imp[self.imp.tier.isin(["tail", "head"])].copy()
        d["treat"] = (d.tier == "tail").astype(float)
        return d


def load(con: duckdb.DuckDBPyConnection) -> Data:
    users = con.sql("SELECT * FROM users").df()
    users["pre_log_n"] = np.log1p(users.pre_n)
    return Data(
        imp=con.sql("SELECT * FROM imp_random").df(),
        rec=con.sql("SELECT * FROM imp WHERE src = 'rec'").df(),
        users=users,
        videos=con.sql("SELECT * FROM videos").df(),
        nxt=con.sql("SELECT * FROM imp_next").df(),
        cap_s=con.sql("SELECT cap_ms / 1000 FROM params").fetchone()[0],
    )


def tail_vs_head(d: pd.DataFrame, metric: str, cluster: str = "two_way") -> dict:
    d = d[d[metric].notna()]
    if cluster == "two_way":
        cl = st.two_way(d.user_id.to_numpy(), d.video_id.to_numpy())
    else:
        cl = [st.codes(d[cluster].to_numpy())]
    return st.diff_in_means(d[metric].to_numpy(), d.treat.to_numpy(), cl)


def group_means(d: pd.DataFrame, metric: str, group: str) -> tuple[list, st.Fit]:
    """Means of `metric` by level of `group`, with a joint two-way cluster-robust covariance."""
    d = d[d[metric].notna()]
    levels = sorted(d[group].unique())
    X = (d[group].to_numpy()[:, None] == np.array(levels)[None, :]).astype(float)
    fit = st.ols(d[metric].to_numpy(), X, st.two_way(d.user_id.to_numpy(), d.video_id.to_numpy()))
    return levels, fit


def effects_by(d: pd.DataFrame, metric: str, seg: str, scale: str = "rel") -> tuple[pd.DataFrame, dict]:
    """Tail vs head within each level of `seg`, and a Wald test that the effect is equal across levels.

    One regression: level dummies (head means) and level x tail dummies (effects), two-way clustered.
    scale: "rel" tests equal relative effects, "abs" equal absolute effects.
    """
    d = d[d[metric].notna() & d[seg].notna()]
    counts = d.groupby([seg, "tier"]).size().unstack(fill_value=0)
    levels = [lv for lv in sorted(counts.index) if counts.loc[lv].min() >= 100]
    d = d[d[seg].isin(levels)]
    lv = d[seg].to_numpy()
    D = (lv[:, None] == np.array(levels)[None, :]).astype(float)
    X = np.hstack([D, D * d.treat.to_numpy()[:, None]])
    fit = st.ols(d[metric].to_numpy(), X, st.two_way(d.user_id.to_numpy(), d.video_id.to_numpy()))
    k = len(levels)
    rows, rels, grads = [], [], []
    for j, level in enumerate(levels):
        c = st.contrast(fit, j, k + j)
        sub = d[d[seg] == level]
        c.update(level=level, n_users=sub.user_id.nunique(), n_videos=sub.video_id.nunique(), n=len(sub))
        rows.append(c)
        b0, b1 = fit.beta[j], fit.beta[k + j]
        g = np.zeros(2 * k)
        if scale == "rel":
            g[j], g[k + j] = -b1 / b0**2, 1 / b0
            rels.append(b1 / b0)
        else:
            g[k + j] = 1
            rels.append(b1)
        grads.append(g)
    G = np.array(grads)
    stat, df, p = st.wald_equal(np.array(rels), G @ fit.cov @ G.T)
    return pd.DataFrame(rows), {"segment": seg, "metric": metric, "scale": scale, "chi2": stat, "df": df, "p": p,
                                "levels": k}


# --- layer 1 -------------------------------------------------------------------------------------


def layer1(data: Data) -> dict:
    d = data.ht
    rows = []
    for m in METRICS:
        r = tail_vs_head(d, m)
        r["metric"] = m
        rows.append(r)
    main = pd.DataFrame(rows)

    # Clustering choice: the same MWT contrast under each assumption.
    se_rows = []
    for name, cl in [("none (iid)", None), ("user", "user_id"), ("video", "video_id"), ("user x video (two-way)", "two_way")]:
        if cl is None:
            dd = d[d.mwt.notna()]
            fit = st.ols(dd.mwt.to_numpy(), np.column_stack([np.ones(len(dd)), dd.treat]), [np.arange(len(dd))])
            r = st.contrast(fit, 0, 1)
        else:
            r = tail_vs_head(d, "mwt", cl)
        se_rows.append({"clustering": name, "se_abs": r["se_abs"], "abs_lo": r["abs_lo"], "abs_hi": r["abs_hi"]})

    # Dose response: every fifth vs head (fifth 5).
    dose = []
    for m in METRICS:
        levels, fit = group_means(data.imp, m, "fifth")
        k5 = levels.index(5)
        for j, f in enumerate(levels):
            b, b5 = fit.beta[j], fit.beta[k5]
            V = fit.cov[np.ix_([j, k5], [j, k5])]
            g = np.array([1 / b5, -b / b5**2])
            se_rel = float(np.sqrt(g @ V @ g)) if f != 5 else 0.0
            sub = data.imp[data.imp.fifth == f]
            dose.append({
                "metric": m, "fifth": int(f), "mean": float(b), "se": float(fit.se[j]),
                "lo": float(b - st.Z95 * fit.se[j]), "hi": float(b + st.Z95 * fit.se[j]),
                "rel_vs_head": float(b / b5 - 1), "rel_lo": float(b / b5 - 1 - st.Z95 * se_rel),
                "rel_hi": float(b / b5 - 1 + st.Z95 * se_rel),
                "n": int(sub[m].notna().sum()), "n_videos": int(sub.video_id.nunique()),
            })
    return {"main": main, "clustering": pd.DataFrame(se_rows), "dose": pd.DataFrame(dose)}


def sensitivities(data: Data) -> pd.DataFrame:
    """MWT tail vs head under alternative choices."""
    d = data.ht
    rows = [{"variant": "primary", **tail_vs_head(d, "mwt")}]
    rows.append({"variant": "cap at 180s", **tail_vs_head(d, "mwt_cap180")})

    # Date fixed effects.
    days = sorted(d.day_index.unique())
    D = (d.day_index.to_numpy()[:, None] == np.array(days)[None, 1:]).astype(float)
    X = np.column_stack([np.ones(len(d)), d.treat, D])
    fit = st.ols(d.mwt.to_numpy(), X, st.two_way(d.user_id.to_numpy(), d.video_id.to_numpy()))
    r = st.contrast(fit, 0, 1)
    # With date effects the intercept is the first day's head mean; express relative to the overall head mean.
    head_mean = d.loc[d.treat == 0, "mwt"].mean()
    r.update(control=head_mean, treatment=head_mean + r["abs"], rel=r["abs"] / head_mean,
             rel_lo=r["abs_lo"] / head_mean, rel_hi=r["abs_hi"] / head_mean)
    rows.append({"variant": "date fixed effects", **r})

    # User fixed effects: compare tail and head within the same user (the draws skip videos a user has seen,
    # which shifts head draws slightly towards users who have seen fewer head videos).
    u = st.codes(d.user_id.to_numpy())
    yd = d.mwt.to_numpy() - pd.Series(d.mwt.to_numpy()).groupby(u).transform("mean").to_numpy()
    td = d.treat.to_numpy() - pd.Series(d.treat.to_numpy()).groupby(u).transform("mean").to_numpy()
    keep = np.abs(td) > 0  # users with both tiers
    fit = st.ols(yd[keep], td[keep][:, None], st.two_way(d.user_id.to_numpy()[keep], d.video_id.to_numpy()[keep]))
    b, se = float(fit.beta[0]), float(fit.se[0])
    rows.append({"variant": "user fixed effects", "control": head_mean, "treatment": head_mean + b, "abs": b,
                 "abs_lo": b - st.Z95 * se, "abs_hi": b + st.Z95 * se, "se_abs": se, "rel": b / head_mean,
                 "rel_lo": (b - st.Z95 * se) / head_mean, "rel_hi": (b + st.Z95 * se) / head_mean,
                 "se_rel": se / head_mean, "p": float(2 * stats.norm.sf(abs(b / se))), "n": int(keep.sum())})

    late = data.videos.loc[data.videos.last_random_date >= LATE_POOL_DATE, "video_id"]
    rows.append({"variant": f"videos still drawn on or after {LATE_POOL_DATE}",
                 **tail_vs_head(d[d.video_id.isin(late)], "mwt")})
    rows.append({"variant": "videos with known duration", **tail_vs_head(d[d.long_view.notna()], "mwt")})
    return pd.DataFrame(rows)


# --- randomisation checks ---------------------------------------------------------------------------


def checks(data: Data) -> dict:
    imp, v = data.imp, data.videos
    # SRM: random impressions per fifth vs each fifth's share of pool videos.
    obs = imp.groupby("fifth").size()
    share = v.groupby("fifth").size() / len(v)
    exp = share * obs.sum()
    chi2, p = stats.chisquare(obs.to_numpy(), exp.to_numpy())
    srm = pd.DataFrame({"fifth": obs.index, "observed": obs.to_numpy(), "expected": exp.to_numpy().round(1),
                        "ratio": (obs / exp).to_numpy()})
    srm_test = {"chi2": float(chi2), "df": len(obs) - 1, "p": float(p)}

    # The same test among videos still in the pool late in the window.
    late_ids = v.loc[v.last_random_date >= LATE_POOL_DATE, ["video_id", "fifth"]]
    obs_l = imp[imp.video_id.isin(late_ids.video_id)].groupby("fifth").size()
    exp_l = late_ids.groupby("fifth").size() / len(late_ids) * obs_l.sum()
    chi2_l, p_l = stats.chisquare(obs_l.to_numpy(), exp_l.to_numpy())

    dropout = v.groupby("fifth").agg(
        n_videos=("video_id", "size"),
        last_drawn_before_late=("last_random_date", lambda s: int((s < LATE_POOL_DATE).sum())),
        mean_random_impressions=("random_impressions", "mean"),
    ).reset_index()

    # Balance: user covariates of tail vs head impressions (standardised mean differences).
    d = data.ht.merge(data.users[["user_id", "pre_mwt", "pre_skip", "pre_n", "has_pre", "register_days"]],
                      on="user_id", how="left")
    bal = []
    for c in ["pre_mwt", "pre_skip", "pre_n", "has_pre", "register_days"]:
        a, b = d.loc[d.treat == 1, c].dropna(), d.loc[d.treat == 0, c].dropna()
        sd = np.sqrt((a.var() + b.var()) / 2)
        bal.append({"covariate": c, "tail_mean": a.mean(), "head_mean": b.mean(), "smd": (a.mean() - b.mean()) / sd})

    # Tail share of random impressions by user activity and by date.
    act = imp.merge(data.users[["user_id", "user_active_degree"]], on="user_id")
    tab = pd.crosstab(act.user_active_degree, act.tier)
    chi_act = stats.chi2_contingency(tab)
    by_date = pd.crosstab(imp.date, imp.tier)
    chi_date = stats.chi2_contingency(by_date)
    tail_share = pd.DataFrame({
        "date": by_date.index.astype(str),
        "tail_share": (by_date["tail"] / by_date.sum(axis=1)).to_numpy(),
        "head_share": (by_date["head"] / by_date.sum(axis=1)).to_numpy(),
        "impressions": by_date.sum(axis=1).to_numpy(),
    })
    # Head draws per tail draw by the user's pre-period volume. Under a uniform draw this is flat;
    # a "skip videos already seen" rule lowers it for heavy users, who have seen more head videos.
    cnt = data.ht.groupby(["user_id", "treat"]).size().unstack(fill_value=0)
    u = data.users.set_index("user_id").loc[cnt.index]
    cnt["pre_volume_fifth"] = pd.qcut(u.pre_n.rank(method="first"), 5, labels=False).to_numpy() + 1
    by_vol = cnt.groupby("pre_volume_fifth")[[0.0, 1.0]].sum().rename(columns={0.0: "head_draws", 1.0: "tail_draws"})
    by_vol["head_per_tail"] = by_vol.head_draws / by_vol.tail_draws
    by_vol["median_pre_impressions"] = u.pre_n.groupby(cnt.pre_volume_fifth.to_numpy()).median().to_numpy()

    return {
        "srm": srm, "srm_test": srm_test, "draws_by_pre_volume": by_vol.reset_index(),
        "srm_late_pool": {"chi2": float(chi2_l), "p": float(p_l), "n_videos": len(late_ids),
                          "ratio_by_fifth": (obs_l / exp_l).round(4).to_dict()},
        "dropout": dropout,
        "balance": pd.DataFrame(bal),
        "tier_by_activity": {"chi2": float(chi_act.statistic), "df": int(chi_act.dof), "p": float(chi_act.pvalue),
                             "cramers_v": float(np.sqrt(chi_act.statistic / tab.to_numpy().sum() / (min(tab.shape) - 1)))},
        "tier_by_date": {"chi2": float(chi_date.statistic), "df": int(chi_date.dof), "p": float(chi_date.pvalue),
                         "cramers_v": float(np.sqrt(chi_date.statistic / by_date.to_numpy().sum() / (min(by_date.shape) - 1)))},
        "tail_share_by_date": tail_share,
    }


# --- layer 2: constructed user-level readout -----------------------------------------------------------


def user_covariates(users: pd.DataFrame, ids: np.ndarray, metric: str) -> np.ndarray:
    """CUPED covariates for the given users: pre-period metric, the five core covariates and has_pre.
    Users without pre-period data get the mean plus the flag."""
    cols = list(dict.fromkeys([PRE_FOR_METRIC[metric], *COVARIATES]))
    u = users.set_index("user_id").loc[ids]
    X = u[cols].copy()
    for c in cols:
        X[c] = X[c].fillna(users[c].mean())
    X["has_pre"] = u.has_pre.to_numpy()
    return X.to_numpy(float)


def assign_users(user_ids: np.ndarray, seed: int) -> pd.Series:
    users = np.sort(np.unique(user_ids))
    rng = np.random.default_rng(seed)
    return pd.Series(rng.integers(0, 2, len(users)), index=users, name="arm")


def split_sample(d: pd.DataFrame, arm: pd.Series) -> pd.DataFrame:
    """Each user contributes only their arm's impressions: tail if arm 1, head if arm 0."""
    a = arm.reindex(d.user_id).to_numpy()
    return d[((a == 1) & (d.treat == 1).to_numpy()) | ((a == 0) & (d.treat == 0).to_numpy())]


def readout_metric(s: pd.DataFrame, users: pd.DataFrame, metric: str, cuped: bool) -> dict:
    s = s[s[metric].notna()]
    agg = s.groupby("user_id").agg(S=(metric, "sum"), N=(metric, "size"), arm=("treat", "first"))
    S, N, arm = agg.S.to_numpy(), agg.N.to_numpy().astype(float), agg.arm.to_numpy()
    X = user_covariates(users, agg.index.to_numpy(), metric) if cuped else None
    r = st.ratio_readout(S, N, arm, X)

    # Two-way SE for the same contrast: impression level, video clustering included. With CUPED each
    # impression carries its user's adjustment, so the sums match the user-level adjusted metric.
    y = s[metric].to_numpy().copy()
    if cuped:
        _, theta, Xc = st.cuped_adjust(S, N, arm, X)
        y = y - pd.Series(Xc @ theta, index=agg.index).reindex(s.user_id).to_numpy()
    tw = st.diff_in_means(y, s.treat.to_numpy(), st.two_way(s.user_id.to_numpy(), s.video_id.to_numpy()))
    r.update(se_abs_two_way=tw["se_abs"], se_rel_two_way=tw["se_rel"],
             rel_lo_two_way=r["rel"] - st.Z95 * tw["se_rel"], rel_hi_two_way=r["rel"] + st.Z95 * tw["se_rel"],
             abs_lo_two_way=r["abs"] - st.Z95 * tw["se_abs"], abs_hi_two_way=r["abs"] + st.Z95 * tw["se_abs"],
             p_two_way=tw["p"])
    return r


def layer2(data: Data, seed: int = SEED) -> dict:
    d = data.ht
    arm = assign_users(d.user_id.to_numpy(), seed)
    s = split_sample(d, arm)
    rows = []
    for m in METRICS:
        for cuped in (False, True):
            r = readout_metric(s, data.users, m, cuped)
            r.update(metric=m, cuped=cuped)
            rows.append(r)
    res = pd.DataFrame(rows)
    n_t, n_c = int((arm == 1).sum()), int((arm == 0).sum())
    users_in = s.groupby("treat").user_id.nunique()
    srm = stats.binomtest(n_t, n_t + n_c, 0.5)
    vr = []
    for m in METRICS:
        a = res[(res.metric == m) & ~res.cuped].iloc[0]
        b = res[(res.metric == m) & res.cuped].iloc[0]
        vr.append({"metric": m, "variance_reduction_user": 1 - (b.se_abs / a.se_abs) ** 2,
                   "variance_reduction_two_way": 1 - (b.se_abs_two_way / a.se_abs_two_way) ** 2,
                   "equivalent_extra_users": 1 / (1 - (1 - (b.se_abs / a.se_abs) ** 2)) - 1})
    return {
        "readout": res,
        "variance_reduction": pd.DataFrame(vr),
        "srm": {"users_assigned_t": n_t, "users_assigned_c": n_c, "p": float(srm.pvalue),
                "users_with_data_t": int(users_in.get(1.0, 0)), "users_with_data_c": int(users_in.get(0.0, 0))},
        "pre_coverage": float(data.users.set_index("user_id").loc[arm.index, "has_pre"].mean()),
    }


def cuped_across_splits(data: Data, n_splits: int = 100, metric: str = "mwt") -> dict:
    """CUPED should cut variance split after split without moving the estimate much on average.

    The draws skip videos a user has already seen, so heavy users get relatively fewer head draws and the
    impression weights differ slightly by arm. CUPED corrects that imbalance, so a small average shift is
    expected; `expected_shift` computes it from all users' tail and head draw counts.
    """
    d = data.ht
    rows = []
    for i in range(n_splits):
        arm = assign_users(d.user_id.to_numpy(), SEED + 1000 + i)
        s = split_sample(d, arm)
        s = s[s[metric].notna()]
        agg = s.groupby("user_id").agg(S=(metric, "sum"), N=(metric, "size"), arm=("treat", "first"))
        S, N, a = agg.S.to_numpy(), agg.N.to_numpy().astype(float), agg.arm.to_numpy()
        X = user_covariates(data.users, agg.index.to_numpy(), metric)
        raw, cup = st.ratio_readout(S, N, a), st.ratio_readout(S, N, a, X)
        rows.append({"split": i, "raw": raw["abs"], "cuped": cup["abs"], "se_raw": raw["se_abs"], "se_cuped": cup["se_abs"]})
    sp = pd.DataFrame(rows)

    cnt = d.groupby(["user_id", "treat"]).size().unstack(fill_value=0)
    X = user_covariates(data.users, cnt.index.to_numpy(), metric)
    arm = assign_users(d.user_id.to_numpy(), SEED)
    s = split_sample(d, arm)
    agg = s.groupby("user_id").agg(S=(metric, "sum"), N=(metric, "size"), arm=("treat", "first"))
    _, theta, _ = st.cuped_adjust(agg.S.to_numpy(), agg.N.to_numpy().astype(float), agg.arm.to_numpy(),
                                  user_covariates(data.users, agg.index.to_numpy(), metric))
    expected = -float(theta @ (np.average(X, axis=0, weights=cnt[1.0]) - np.average(X, axis=0, weights=cnt[0.0])))
    shift = sp.cuped - sp.raw
    return {
        "splits": sp,
        "summary": {
            "n_splits": n_splits, "mean_raw": float(sp.raw.mean()), "mean_cuped": float(sp.cuped.mean()),
            "mean_shift": float(shift.mean()), "mc_se_shift": float(shift.std(ddof=1) / np.sqrt(n_splits)),
            "expected_shift": expected,
            "sd_raw_across_splits": float(sp.raw.std(ddof=1)), "sd_cuped_across_splits": float(sp.cuped.std(ddof=1)),
            "mean_se_raw": float(sp.se_raw.mean()), "mean_se_cuped": float(sp.se_cuped.mean()),
            "variance_reduction": float(1 - (sp.se_cuped**2 / sp.se_raw**2).mean()),
            "empirical_variance_reduction": float(1 - sp.cuped.var() / sp.raw.var()),
        },
    }


# --- A/A calibration ---------------------------------------------------------------------------------


def aa_tests(data: Data, n_reps: int = N_AA, seed: int = SEED) -> dict:
    """Null comparisons inside the head tier, where the true effect is zero by construction.

    user split: arms are random halves of users, both see the same head videos.
    video split: arms are random halves of head videos, every user in both.
    user x video split: users and videos both halved, as in the constructed readout.
    Each p-value comes from user-clustered and from two-way clustered SEs.
    """
    d = data.imp[data.imp.tier == "head"]
    y = d.mwt.to_numpy()
    u, v = st.codes(d.user_id.to_numpy()), st.codes(d.video_id.to_numpy())
    rng = np.random.default_rng(seed)
    rows = []
    for rep in range(n_reps):
        au = rng.integers(0, 2, u.max() + 1)
        av = rng.integers(0, 2, v.max() + 1)
        for design in ("user split", "video split", "user x video split"):
            if design == "user split":
                keep, t = np.ones(len(y), bool), au[u]
            elif design == "video split":
                keep, t = np.ones(len(y), bool), av[v]
            else:
                keep = au[u] == av[v]
                t = au[u]
            yy, tt = y[keep], t[keep].astype(float)
            uu, vv = st.codes(u[keep]), st.codes(v[keep])
            for name, cl in (("user", [uu]), ("two-way", st.two_way(uu, vv))):
                r = st.diff_in_means(yy, tt, cl)
                rows.append({"rep": rep, "design": design, "se": name, "p": r["p"]})
    res = pd.DataFrame(rows)
    summary = []
    for (design, se), g in res.groupby(["design", "se"]):
        k, n = int((g.p < 0.05).sum()), len(g)
        ci = stats.binomtest(k, n).proportion_ci()
        summary.append({"design": design, "se": se, "reps": n, "false_positive_rate": k / n,
                        "fpr_lo": ci.low, "fpr_hi": ci.high, "ks_p_uniform": float(stats.kstest(g.p, "uniform").pvalue)})
    hist = (res.assign(bin=np.minimum((res.p * 20).astype(int), 19))
            .groupby(["design", "se", "bin"]).size().rename("count").reset_index())
    return {"summary": pd.DataFrame(summary), "p_hist": hist}


# --- layer 3: selection bias ----------------------------------------------------------------------------


def selection_bias(data: Data) -> pd.DataFrame:
    rows = []
    for src, df in (("random", data.imp), ("recommended", data.rec)):
        levels, fit = group_means(df, "mwt", "fifth")
        b5 = fit.beta[levels.index(5)]
        for j, f in enumerate(levels):
            rows.append({"delivery": src, "fifth": int(f), "mwt": float(fit.beta[j]),
                         "lo": float(fit.beta[j] - st.Z95 * fit.se[j]), "hi": float(fit.beta[j] + st.Z95 * fit.se[j]),
                         "rel_vs_head": float(fit.beta[j] / b5 - 1), "n": int((df.fifth == f).sum())})
    return pd.DataFrame(rows)


# --- time ------------------------------------------------------------------------------------------------


def time_patterns(data: Data) -> dict:
    d = data.ht
    by_day = []
    for day, g in d.groupby("date"):
        r = tail_vs_head(g, "mwt")
        by_day.append({"date": str(day), **r})
    # Trend: does the gap change across the window? treat x day interaction, two-way clustered.
    X = np.column_stack([np.ones(len(d)), d.treat, d.day_index, d.treat * d.day_index])
    fit = st.ols(d.mwt.to_numpy(), X, st.two_way(d.user_id.to_numpy(), d.video_id.to_numpy()))
    slope, se = fit.beta[3], fit.se[3]
    trend = {"gap_change_per_day_s": float(slope), "se": float(se), "p": float(2 * stats.norm.sf(abs(slope / se)))}

    bins = [0, 5, 20, 50, 100, np.inf]
    labels = ["1-5", "6-20", "21-50", "51-100", "101+"]
    d = d.assign(nth_bucket=pd.cut(d.nth_random, bins=bins, labels=labels).astype(str))
    order = {lab: i for i, lab in enumerate(labels)}
    fatigue, fatigue_test = effects_by(d.assign(nth_bucket=d.nth_bucket.map(lambda x: f"{order[x] + 1}. {x}")),
                                       "mwt", "nth_bucket")
    halves, halves_test = effects_by(d.assign(half=d.half.map({1: "04-22 to 04-30", 2: "05-01 to 05-08"})), "mwt", "half")
    return {"by_day": pd.DataFrame(by_day), "trend": trend, "fatigue": fatigue, "fatigue_test": fatigue_test,
            "halves": halves, "halves_test": halves_test}


# --- content-mix decomposition --------------------------------------------------------------------------


def top_categories(videos: pd.DataFrame, n: int = 10) -> list[str]:
    return videos.l1_en.value_counts().head(n).index.tolist()


def add_cells(d: pd.DataFrame, videos: pd.DataFrame) -> pd.DataFrame:
    top = top_categories(videos)
    cat = d.l1_en.where(d.l1_en.isin(top), OTHER)
    return d.assign(cat=cat, cell=cat + " | " + d.duration_bucket)


def _decompose(y: np.ndarray, treat: np.ndarray, cat: np.ndarray, cell: np.ndarray, w: np.ndarray) -> dict:
    """Reweight head impressions to the tail's content mix. Cells without head data fall back to the category."""
    out = {}
    tail, head = treat == 1, treat == 0
    m_tail = np.average(y[tail], weights=w[tail])
    m_head = np.average(y[head], weights=w[head])
    for name, key in (("category", cat), ("category x duration", cell)):
        df = pd.DataFrame({"k": key, "c": cat, "y": y * w, "w": w, "t": treat})
        hw = df[df.t == 0].groupby("k")[["y", "w"]].sum()
        head_cell = (hw.y / hw.w).where(hw.w > 0)
        hc = df[df.t == 0].groupby("c")[["y", "w"]].sum()
        head_cat = hc.y / hc.w
        tw = df[df.t == 1].groupby(["k", "c"]).w.sum().reset_index()
        tw["p"] = tw.w / tw.w.sum()
        tw["m"] = tw.k.map(head_cell).fillna(tw.c.map(head_cat))
        m_rw = float((tw.p * tw.m).sum())
        out[name] = {
            "tail": m_tail, "head": m_head, "head_reweighted": m_rw,
            "raw_rel": m_tail / m_head - 1,
            "mix_rel": m_rw / m_head - 1,
            "within_rel": (m_tail - m_rw) / m_head,
            "within_vs_reweighted": m_tail / m_rw - 1,
            "mix_share_of_gap": (m_rw - m_head) / (m_tail - m_head),
        }
    return out


def decomposition(data: Data, n_boot: int = N_BOOT) -> pd.DataFrame:
    d = add_cells(data.ht, data.videos)
    y, t = d.mwt.to_numpy(), d.treat.to_numpy()
    cat, cell = d.cat.to_numpy(), d.cell.to_numpy()
    base = _decompose(y, t, cat, cell, np.ones(len(y)))
    u, v = st.codes(d.user_id.to_numpy()), st.codes(d.video_id.to_numpy())
    rng = np.random.default_rng(SEED)
    boots = [_decompose(y, t, cat, cell, st.poisson_weights(rng, u, v)) for _ in range(n_boot)]
    rows = []
    for name, est in base.items():
        for k, val in est.items():
            b = np.array([bb[name][k] for bb in boots])
            rows.append({"cells": name, "quantity": k, "estimate": val,
                         "lo": float(np.quantile(b, 0.025)), "hi": float(np.quantile(b, 0.975))})
    return pd.DataFrame(rows)


def category_mix(data: Data) -> pd.DataFrame:
    """Share of tail and head videos by category, and random-delivery MWT by category and tier."""
    d = add_cells(data.ht, data.videos)
    v = data.videos[data.videos.tier.isin(["tail", "head"])]
    top = top_categories(data.videos)
    v = v.assign(cat=v.l1_en.where(v.l1_en.isin(top), OTHER))
    share = pd.crosstab(v.cat, v.tier, normalize="columns").rename(columns={"tail": "tail_video_share", "head": "head_video_share"})
    mwt = d.groupby(["cat", "tier"]).mwt.mean().unstack().rename(columns={"tail": "tail_mwt", "head": "head_mwt"})
    n = d.groupby(["cat", "tier"]).video_id.nunique().unstack().rename(columns={"tail": "tail_videos", "head": "head_videos"})
    all_mwt = add_cells(data.imp, data.videos).groupby("cat").mwt.mean().rename("all_tiers_mwt")
    return share.join(mwt).join(n).join(all_mwt).reset_index().rename(columns={"cat": "category"})


# --- carryover (exploratory) ------------------------------------------------------------------------


def carryover(data: Data) -> dict:
    n = data.nxt
    gaps = n.gap_s.dropna()
    feas = {
        "median_gap_s": float(gaps.median()),
        "share_next_within_60s": float((n.gap_s <= 60).mean()),
        "share_next_within_300s": float((n.gap_s <= 300).mean()),
        "share_next_is_random": float((n.next_src == "random").mean()),
        "share_without_next": float(n.next_time_ms.isna().mean()),
    }
    rows = []
    for window in (60, 300):
        d = n[n.tier.isin(["tail", "head"]) & (n.gap_s <= window) & (n.next_src == "random")].copy()
        d["treat"] = (d.tier == "tail").astype(float)
        for m in ("next_mwt", "next_skip"):
            r = tail_vs_head(d, m)
            r.update(window_s=window, metric=m)
            rows.append(r)
    return {"feasibility": feas, "estimates": pd.DataFrame(rows)}


# --- power -------------------------------------------------------------------------------------------


def variance_components(d: pd.DataFrame, metric: str = "mwt") -> dict:
    """Moment estimates of user, video and residual variance of a per-impression metric (head and tail pooled,
    tier means removed)."""
    d = d[d[metric].notna()]
    y = d[metric] - d.groupby("tier")[metric].transform("mean")
    total = float(y.var())

    def between(key: str) -> float:
        g = y.groupby(d[key].to_numpy())
        means, n, var_in = g.mean(), g.size(), g.var().fillna(0)
        within = float(np.average(var_in, weights=np.maximum(n - 1, 0)))
        return max(float(means.var() - np.mean(within / n)), 0.0)

    s_v, s_u = between("video_id"), between("user_id")
    return {"total": total, "video": s_v, "user": s_u, "residual": max(total - s_v - s_u, 0.0)}


def model_se(vc: dict, videos: float, imps_per_video: float, users: float | None = None, user_deff: float = 1.0,
             cuped: float = 0.0) -> float:
    """SE of a tail-vs-head difference in means with `videos` per arm and `imps_per_video` impressions each.

    users=None: every user is in both arms, so user effects mostly cancel and count as impression noise.
    Otherwise a user split with `users` split evenly: each arm's mean carries its users' effects, weighted by their
    impressions (user_deff = mean(n^2) / mean(n)^2 over impressions per user), and CUPED removes a share `cuped` of
    the non-video variance. The site's power calculator implements the same formula."""
    n = videos * imps_per_video
    if users is None:
        return float(np.sqrt(2 * (vc["video"] / videos + (vc["user"] + vc["residual"]) / n)))
    non_video = vc["user"] * user_deff / (users / 2) + vc["residual"] / n
    return float(np.sqrt(2 * (vc["video"] / videos + (1 - cuped) * non_video)))


def power(data: Data, l1: dict, l2: dict) -> dict:
    d = data.ht
    vc = variance_components(d)
    t, c = d[d.treat == 1], d[d.treat == 0]
    design = {
        "videos_t": int(t.video_id.nunique()), "videos_c": int(c.video_id.nunique()),
        "imps_t": len(t), "imps_c": len(c), "users": int(d.user_id.nunique()),
        "head_mean": float(c.mwt.mean()),
    }
    # Both designs at this dataset's size, arms averaged, to check the model against the measured SEs.
    videos = (design["videos_t"] + design["videos_c"]) / 2
    se_model = model_se(vc, videos, (design["imps_t"] + design["imps_c"]) / 2 / videos)
    s = split_sample(d, assign_users(d.user_id.to_numpy(), SEED))
    s = s[s.mwt.notna()]
    arms = [s[s.treat == a] for a in (1.0, 0.0)]
    per_user = [a.groupby("user_id").size().to_numpy() for a in arms]
    split = {
        "videos": float(np.mean([a.video_id.nunique() for a in arms])),
        "users": int(sum(len(n) for n in per_user)),
        "user_deff": float(np.mean([(n ** 2).mean() / n.mean() ** 2 for n in per_user])),
    }
    split["imps_per_video"] = len(s) / 2 / split["videos"]
    split["se_model"] = model_se(vc, split["videos"], split["imps_per_video"], split["users"], split["user_deff"])
    se_obs = float(l1["main"].set_index("metric").loc["mwt", "se_abs"])
    ro = l2["readout"].set_index(["metric", "cuped"])
    split["se_observed"] = float(ro.loc[("mwt", False), "se_abs_two_way"])
    table = []
    for name, se in [
        ("Impression level, two-way clustered", se_obs),
        ("User split, user-clustered", float(ro.loc[("mwt", False), "se_abs"])),
        ("User split, user-clustered, CUPED", float(ro.loc[("mwt", True), "se_abs"])),
        ("User split, two-way clustered", float(ro.loc[("mwt", False), "se_abs_two_way"])),
        ("User split, two-way clustered, CUPED", float(ro.loc[("mwt", True), "se_abs_two_way"])),
    ]:
        m = st.mde(se, ALPHA, POWER)
        table.append({"design": name, "se": se, "mde_abs": m, "mde_rel_to_head": m / design["head_mean"]})

    # Hates are rare: what can this design detect?
    hate = l1["main"].set_index("metric").loc["hated"]
    hate_mde = st.mde(hate.se_abs, ALPHA, POWER)
    return {
        "components": vc, "design": design, "se_model": se_model, "se_observed": se_obs, "user_split": split,
        "table": pd.DataFrame(table),
        "hate": {"mde_abs_per_1k": 1000 * hate_mde, "head_rate_per_1k": 1000 * hate.control,
                 "mde_rel": hate_mde / hate.control},
        "alpha": ALPHA, "power": POWER,
    }


def seen_filter(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Do random draws skip videos the user has already seen?

    expected_if_unfiltered: share of each fifth's videos the average drawn-for user had already seen in the
    standard logs (pre-period plus experiment period, an upper bound). observed: share of random impressions
    where the user had any earlier logged impression of the drawn video.
    """
    return con.sql("""
        WITH seen AS (
            SELECT DISTINCT user_id, video_id FROM (
                SELECT user_id, video_id FROM log_pre UNION ALL SELECT user_id, video_id FROM log_rec)),
        per AS (SELECT s.user_id, v.fifth, count(*) AS n_seen FROM seen s JOIN videos v USING (video_id) GROUP BY ALL),
        nf AS (SELECT fifth, count(*) AS n_v FROM videos GROUP BY 1),
        ru AS (SELECT user_id, count(*) AS n_r FROM imp_random GROUP BY 1),
        expected AS (
            SELECT nf.fifth, sum(ru.n_r * coalesce(per.n_seen, 0) / nf.n_v) / sum(ru.n_r) AS expected_if_unfiltered
            FROM ru CROSS JOIN nf LEFT JOIN per ON per.user_id = ru.user_id AND per.fifth = nf.fifth
            GROUP BY 1),
        first_seen AS (
            SELECT user_id, video_id, min(time_ms) AS first_ms FROM (
                SELECT user_id, video_id, time_ms FROM log_pre
                UNION ALL SELECT user_id, video_id, time_ms FROM log_rec
                UNION ALL SELECT user_id, video_id, time_ms FROM log_rand) GROUP BY ALL),
        observed AS (
            SELECT r.fifth, avg(CASE WHEN f.first_ms < r.time_ms THEN 1.0 ELSE 0.0 END) AS observed
            FROM imp_random r LEFT JOIN first_seen f USING (user_id, video_id) GROUP BY 1)
        SELECT fifth, expected_if_unfiltered, observed FROM expected JOIN observed USING (fifth) ORDER BY fifth
    """).df()


def run(con: duckdb.DuckDBPyConnection, data: Data) -> dict:
    l1 = layer1(data)
    l2 = layer2(data)
    return {
        "layer1": l1,
        "sensitivities": sensitivities(data),
        "checks": {**checks(data), "seen_filter": seen_filter(con)},
        "layer2": l2,
        "cuped_splits": cuped_across_splits(data),
        "aa": aa_tests(data),
        "selection_bias": selection_bias(data),
        "time": time_patterns(data),
        "decomposition": decomposition(data),
        "category_mix": category_mix(data),
        "carryover": carryover(data),
        "power": power(data, l1, l2),
    }
