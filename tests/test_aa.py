"""A/A calibration on synthetic data with user and video effects, and the cluster-robust SEs against statsmodels."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
import statsmodels.api as sm
from statsmodels.stats import sandwich_covariance as sw
from statsmodels.stats.multitest import multipletests

from pipeline import stats as st
from pipeline.causal import Data, aa_tests


def synthetic_log(n_users=300, n_videos=200, per_user=40, seed=0) -> pd.DataFrame:
    """Each user sees a random sample of videos. Outcome = user effect + video effect + noise; no treatment."""
    rng = np.random.default_rng(seed)
    user = np.repeat(np.arange(n_users), per_user)
    video = np.concatenate([rng.choice(n_videos, per_user, replace=False) for _ in range(n_users)])
    y = rng.normal(0, 1, n_users)[user] + rng.normal(0, 1, n_videos)[video] + rng.normal(0, 2, len(user))
    return pd.DataFrame({"user_id": user, "video_id": video, "mwt": y + 10, "tier": "head"})


@pytest.fixture(scope="module")
def aa():
    d = synthetic_log()
    data = Data(imp=d, rec=None, users=None, videos=None, nxt=None, cap_s=0.0)
    return aa_tests(data, n_reps=200, seed=1)["summary"].set_index(["design", "se"])


def test_user_split_is_calibrated(aa):
    for se in ("user", "two-way"):
        row = aa.loc[("user split", se)]
        assert 0.01 <= row.false_positive_rate <= 0.10
        assert row.ks_p_uniform > 0.01


def test_video_split_needs_video_clustering(aa):
    assert aa.loc[("video split", "user"), "false_positive_rate"] > 0.30
    two_way = aa.loc[("video split", "two-way")]
    assert 0.01 <= two_way.false_positive_rate <= 0.10
    assert aa.loc[("user x video split", "two-way"), "false_positive_rate"] <= 0.10


def test_cluster_robust_matches_statsmodels():
    d = synthetic_log(n_users=120, n_videos=80, per_user=15, seed=2)
    rng = np.random.default_rng(3)
    treat = rng.integers(0, 2, len(d)).astype(float)
    X = np.column_stack([np.ones(len(d)), treat])
    u, v = st.codes(d.user_id.to_numpy()), st.codes(d.video_id.to_numpy())
    ref = sm.OLS(d.mwt.to_numpy(), X).fit()

    one = st.ols(d.mwt.to_numpy(), X, [u])
    np.testing.assert_allclose(one.beta, ref.params, rtol=1e-10)
    np.testing.assert_allclose(one.cov, sw.cov_cluster(ref, u), rtol=1e-8)

    two = st.ols(d.mwt.to_numpy(), X, st.two_way(u, v))
    np.testing.assert_allclose(two.cov, sw.cov_cluster_2groups(ref, u, v)[0], rtol=1e-8)


def test_bh_matches_statsmodels():
    p = np.random.default_rng(4).uniform(0, 0.2, 25)
    np.testing.assert_allclose(st.bh(p), multipletests(p, method="fdr_bh")[1], rtol=1e-12)
