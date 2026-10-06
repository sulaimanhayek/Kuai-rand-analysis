"""CUPED for the ratio readout: unbiased with a known effect, no average shift across splits, lower variance."""

from __future__ import annotations

import numpy as np

from pipeline import stats as st

EFFECT = 0.5


def synthetic_users(n_users=4000, effect=EFFECT, seed=0):
    """Users with a pre-period covariate that predicts their outcome. Impression counts do not depend on arm."""
    rng = np.random.default_rng(seed)
    x = rng.normal(0, 1, n_users)
    level = 10 + 2 * x + rng.normal(0, 1, n_users)
    n = rng.poisson(15, n_users).astype(float) + 1
    arm = rng.integers(0, 2, n_users)
    s = n * (level + effect * arm) + np.sqrt(n) * rng.normal(0, 3, n_users)
    X = np.column_stack([x, np.log1p(n + rng.poisson(5, n_users))])
    return s, n, arm, X


def test_known_effect_recovered():
    s, n, arm, X = synthetic_users()
    r = st.ratio_readout(s, n, arm, X)
    assert r["abs_lo"] < EFFECT < r["abs_hi"]
    assert abs(r["abs"] - EFFECT) < 3 * r["se_abs"]


def test_variance_reduced():
    s, n, arm, X = synthetic_users()
    raw, cup = st.ratio_readout(s, n, arm), st.ratio_readout(s, n, arm, X)
    assert cup["se_abs"] ** 2 < 0.5 * raw["se_abs"] ** 2


def test_no_mean_shift_and_coverage_across_splits():
    s, n, arm, X = synthetic_users(effect=0.0, seed=1)
    rng = np.random.default_rng(2)
    shifts, raw_est, cup_est, covered = [], [], [], 0
    for _ in range(300):
        a = rng.integers(0, 2, len(s))
        raw, cup = st.ratio_readout(s, n, a), st.ratio_readout(s, n, a, X)
        shifts.append(cup["abs"] - raw["abs"])
        raw_est.append(raw["abs"])
        cup_est.append(cup["abs"])
        covered += cup["abs_lo"] < 0 < cup["abs_hi"]
    shifts = np.array(shifts)
    assert abs(shifts.mean()) < 4 * shifts.std(ddof=1) / np.sqrt(len(shifts))
    assert np.var(cup_est) < 0.5 * np.var(raw_est)
    assert 0.91 <= covered / 300 <= 0.98


def test_adjustment_has_mean_zero_over_impressions():
    s, n, arm, X = synthetic_users(seed=3)
    adj, _, _ = st.cuped_adjust(s, n, arm, X)
    assert np.isclose(adj.sum(), s.sum())
