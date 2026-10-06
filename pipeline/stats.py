"""Estimators shared by the analysis steps: cluster-robust OLS, delta method, CUPED, BH, EB shrinkage."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats

Z95 = stats.norm.ppf(0.975)


def codes(x: np.ndarray | pd.Series | pd.DataFrame) -> np.ndarray:
    """Integer cluster codes for one key column or the intersection of several."""
    if isinstance(x, pd.DataFrame):
        return x.groupby(list(x.columns), sort=False).ngroup().to_numpy()
    return pd.factorize(np.asarray(x))[0]


def _meat(scores: np.ndarray, g: np.ndarray, n: int, k: int) -> np.ndarray:
    n_groups = int(g.max()) + 1
    s = np.column_stack([np.bincount(g, weights=scores[:, j], minlength=n_groups) for j in range(scores.shape[1])])
    correction = n_groups / (n_groups - 1) * (n - 1) / (n - k)
    return correction * (s.T @ s)


@dataclass
class Fit:
    beta: np.ndarray
    cov: np.ndarray
    n: int

    @property
    def se(self) -> np.ndarray:
        return np.sqrt(np.diag(self.cov))


def ols(y: np.ndarray, X: np.ndarray, clusters: list[np.ndarray], weights: np.ndarray | None = None) -> Fit:
    """OLS with one-way or two-way cluster-robust covariance (Cameron, Gelbach and Miller 2011).

    clusters: one code array for one-way, or [user, video, user x video] for two-way. The small-sample
    correction matches statsmodels' cov_cluster / cov_cluster_2groups.
    """
    y = np.asarray(y, float)
    X = np.asarray(X, float)
    w = np.ones(len(y)) if weights is None else np.asarray(weights, float)
    n, k = X.shape
    Xw = X * w[:, None]
    bread = np.linalg.inv(X.T @ Xw)
    beta = bread @ (Xw.T @ y)
    scores = Xw * (y - X @ beta)[:, None]
    if len(clusters) == 1:
        meat = _meat(scores, clusters[0], n, k)
    elif len(clusters) == 3:
        meat = _meat(scores, clusters[0], n, k) + _meat(scores, clusters[1], n, k) - _meat(scores, clusters[2], n, k)
    else:
        raise ValueError("pass one cluster array, or three for two-way (a, b, a x b)")
    return Fit(beta, bread @ meat @ bread, n)


def two_way(user: np.ndarray, video: np.ndarray) -> list[np.ndarray]:
    u, v = codes(user), codes(video)
    return [u, v, codes(pd.DataFrame({"u": u, "v": v}))]


def contrast(fit: Fit, i_control: int, i_effect: int) -> dict:
    """Absolute and relative effect from a regression with a control mean and an effect coefficient."""
    b0, b1 = fit.beta[i_control], fit.beta[i_effect]
    V = fit.cov[np.ix_([i_control, i_effect], [i_control, i_effect])]
    se_abs = float(np.sqrt(V[1, 1]))
    g = np.array([-b1 / b0**2, 1 / b0])
    se_rel = float(np.sqrt(g @ V @ g))
    rel = b1 / b0
    return {
        "control": float(b0),
        "treatment": float(b0 + b1),
        "abs": float(b1),
        "abs_lo": float(b1 - Z95 * se_abs),
        "abs_hi": float(b1 + Z95 * se_abs),
        "se_abs": se_abs,
        "rel": float(rel),
        "rel_lo": float(rel - Z95 * se_rel),
        "rel_hi": float(rel + Z95 * se_rel),
        "se_rel": se_rel,
        "p": float(2 * stats.norm.sf(abs(b1) / se_abs)),
        "n": fit.n,
    }


def diff_in_means(y: np.ndarray, treat: np.ndarray, clusters: list[np.ndarray]) -> dict:
    X = np.column_stack([np.ones(len(y)), treat])
    return contrast(ols(y, X, clusters), 0, 1)


def wald_equal(values: np.ndarray, cov: np.ndarray) -> tuple[float, int, float]:
    """Wald test that all entries of `values` are equal."""
    k = len(values)
    R = np.zeros((k - 1, k))
    R[:, 0] = -1
    R[np.arange(k - 1), np.arange(1, k)] = 1
    d = R @ values
    stat = float(d @ np.linalg.pinv(R @ cov @ R.T) @ d)
    return stat, k - 1, float(stats.chi2.sf(stat, k - 1))


def bh(p: np.ndarray) -> np.ndarray:
    """Benjamini-Hochberg adjusted p-values (q-values)."""
    p = np.asarray(p, float)
    m = len(p)
    order = np.argsort(p)
    ranked = p[order] * m / np.arange(1, m + 1)
    q = np.minimum.accumulate(ranked[::-1])[::-1]
    out = np.empty(m)
    out[order] = np.minimum(q, 1)
    return out


# --- user-level ratio metrics: delta method and CUPED -------------------------------------------


def ratio_arm(s: np.ndarray, n: np.ndarray) -> tuple[float, float]:
    """Mean of a ratio metric sum(s) / sum(n) over users, with its delta-method variance."""
    m = s.sum() / n.sum()
    lin = (s - m * n) / n.mean()
    return float(m), float(lin.var(ddof=1) / len(s))


def cuped_adjust(s: np.ndarray, n: np.ndarray, arm: np.ndarray, X: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """CUPED for a ratio metric: every impression of user i is shifted by theta' (X_i - Xbar).

    Xbar is the impression-weighted mean of the pre-period covariates, so the adjustment has mean zero
    over impressions. theta comes from the pooled within-arm regression of the linearised metric on the
    linearised covariates. Returns adjusted sums, theta and the centred covariates.
    """
    Xc = X - np.average(X, axis=0, weights=n)
    ly, lx = np.empty(len(s)), np.empty(X.shape)
    for a in (0, 1):
        mask = arm == a
        nbar = n[mask].mean()
        m = s[mask].sum() / n[mask].sum()
        ly[mask] = (s[mask] - m * n[mask]) / nbar
        xw = np.average(X[mask], axis=0, weights=n[mask])
        lx[mask] = n[mask, None] * (X[mask] - xw) / nbar
    theta = np.linalg.lstsq(lx, ly, rcond=None)[0]
    return s - n * (Xc @ theta), theta, Xc


def ratio_readout(s: np.ndarray, n: np.ndarray, arm: np.ndarray, X: np.ndarray | None = None) -> dict:
    """Treatment (arm 1) vs control (arm 0) for a ratio metric; X are raw pre-period user covariates."""
    if X is not None:
        s = cuped_adjust(s, n, arm, X)[0]
    (mt, vt), (mc, vc) = ratio_arm(s[arm == 1], n[arm == 1]), ratio_arm(s[arm == 0], n[arm == 0])
    diff, se = mt - mc, np.sqrt(vt + vc)
    rel = mt / mc - 1
    se_rel = np.sqrt(vt / mc**2 + mt**2 * vc / mc**4)
    return {
        "control": mc, "treatment": mt,
        "abs": diff, "abs_lo": diff - Z95 * se, "abs_hi": diff + Z95 * se, "se_abs": se,
        "rel": rel, "rel_lo": rel - Z95 * se_rel, "rel_hi": rel + Z95 * se_rel, "se_rel": se_rel,
        "p": float(2 * stats.norm.sf(abs(diff) / se)),
        "n_users_t": int((arm == 1).sum()), "n_users_c": int((arm == 0).sum()),
        "n_imps_t": int(n[arm == 1].sum()), "n_imps_c": int(n[arm == 0].sum()),
    }


# --- empirical Bayes ---------------------------------------------------------------------------------


def eb_normal(means: np.ndarray, n: np.ndarray, within_var: float) -> tuple[np.ndarray, np.ndarray, float, float]:
    """Normal-normal shrinkage of video means. Returns posterior mean, posterior sd, prior mean, prior sd."""
    noise = within_var / n
    tau2 = max(means.var(ddof=1) - noise.mean(), 1e-12)
    for _ in range(50):
        w = 1 / (tau2 + noise)
        mu = np.sum(w * means) / w.sum()
        tau2 = max(np.sum(w**2 * ((means - mu) ** 2 - noise)) / np.sum(w**2), 1e-12)
    b = tau2 / (tau2 + noise)
    return mu + b * (means - mu), np.sqrt(b * noise), float(mu), float(np.sqrt(tau2))


def eb_beta(x: np.ndarray, n: np.ndarray) -> tuple[np.ndarray, float, float]:
    """Beta-binomial shrinkage of video rates by the method of moments. Returns posterior mean, alpha, beta."""
    p = x / n
    m = np.average(p, weights=n)
    v = np.average((p - m) ** 2, weights=n)
    within = m * (1 - m) * np.mean(1 / n)
    between = max(v - within, 1e-9)
    k = m * (1 - m) / between - 1
    a, b = m * k, (1 - m) * k
    return (a + x) / (a + b + n), float(a), float(b)


def poisson_weights(rng: np.random.Generator, user: np.ndarray, video: np.ndarray) -> np.ndarray:
    """Two-way (pigeonhole) Poisson bootstrap: weight = Poisson(1) per user x Poisson(1) per video."""
    wu = rng.poisson(1.0, int(user.max()) + 1)
    wv = rng.poisson(1.0, int(video.max()) + 1)
    return (wu[user] * wv[video]).astype(float)


def mde(se: float, alpha: float = 0.05, power: float = 0.8) -> float:
    return float((stats.norm.ppf(1 - alpha / 2) + stats.norm.ppf(power)) * se)
