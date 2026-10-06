"""The misclassification correction recovers known class means; kappa matches statsmodels."""

import numpy as np
import pandas as pd
from statsmodels.stats.inter_rater import cohens_kappa

from pipeline import tags


def test_kappa_matches_statsmodels():
    rng = np.random.default_rng(1)
    a = rng.choice(list("abcd"), 2000)
    b = np.where(rng.random(2000) < 0.7, a, rng.choice(list("abcd"), 2000))
    table = pd.crosstab(a, b).to_numpy()
    assert np.isclose(tags.kappa(a, b), cohens_kappa(table).kappa)
    # Integer weights equal repeated rows.
    w = rng.integers(1, 4, 2000)
    assert np.isclose(tags.kappa(a, b, w), tags.kappa(np.repeat(a, w), np.repeat(b, w)))


def test_correction_recovers_true_means():
    rng = np.random.default_rng(2)
    n, K = 400_000, 4
    tail_share, head_share = np.array([0.4, 0.3, 0.2, 0.1]), np.array([0.1, 0.2, 0.3, 0.4])
    head_mean, tail_mean = np.array([10.0, 20.0, 30.0, 40.0]), np.array([6.0, 15.0, 27.0, 20.0])
    t = (rng.random(n) < 0.5).astype(float)
    true = np.where(t == 1, rng.choice(K, n, p=tail_share), rng.choice(K, n, p=head_share))
    y = np.where(t == 1, tail_mean[true], head_mean[true]) + rng.normal(0, 5, n)
    # Labels are right 75% of the time, otherwise uniform over the other classes, in both tiers.
    wrong = rng.random(n) < 0.25
    obs = np.where(wrong, (true + rng.integers(1, K, n)) % K, true)
    M = pd.crosstab(true, obs, normalize="index").to_numpy()  # P(observed | true), as the audit estimates it

    sums = tags._class_sums(obs, y, t, np.ones(n), K)
    naive, _ = tags._summary(sums)
    fixed, dec = tags._summary(tags._correct(sums, M))
    truth = tail_mean / head_mean - 1
    assert np.abs(fixed - truth).max() < 0.02
    assert np.abs(naive - truth).max() > 0.1  # uncorrected, the class effects are visibly pulled together
    m_tail, m_head = y[t == 1].mean(), y[t == 0].mean()
    assert np.isclose(dec["within_rel"], (m_tail - tail_share @ head_mean) / m_head, atol=0.01)


def test_correction_rejects_infeasible_matrices():
    # Observed: 100 impressions of each class, mean 10 in class 0 and 2 in class 1, in both tiers.
    sums = (np.array([1000.0, 200.0]), np.array([100.0, 100.0])) * 2
    assert tags._correct(sums, np.eye(2), y_max=50) is not None
    # 60% of true class 0 tagged as 1 would put more than all of observed class 1 in class 0: negative impressions.
    assert tags._correct(sums, np.array([[0.4, 0.6], [0.0, 1.0]]), y_max=50) is None
    # Feasible impressions (true 0: 125, true 1: 75) but a negative outcome total, so a negative mean, for class 1.
    M = np.array([[0.8, 0.2], [0.0, 1.0]])
    assert np.linalg.solve(M.T, sums[1]).min() > 0
    assert tags._correct(sums, M, y_max=50) is None
    # A mean above the cap is impossible too.
    assert tags._correct(sums, np.eye(2), y_max=5) is None


def test_confusion_posterior():
    classes = ["a", "b", "rest"]
    m = pd.DataFrame({"vertical": ["a"] * 50 + ["b"] * 50, "audit_vertical": ["a"] * 50 + ["b"] * 45 + ["a"] * 5,
                      "w": 1.0})
    M = tags._misclassification(m, classes)  # rows: audit class; columns: LLM class
    assert np.allclose(M.sum(1), 1)
    assert 0.87 < M[0, 0] < 0.93 and M[0, 1] > 0.07  # 5 of 55 audit "a" were tagged "b"
    assert M[1, 1] > 0.97
    assert M[2, 2] > 0.9  # no audit rows: the prior
    draw = tags._misclassification(m, classes, np.random.default_rng(0))
    assert np.allclose(draw.sum(1), 1)


def test_codes_pool_unknown_labels():
    assert tags._codes(["a", "x", None, "b"], ["a", "b", "rest"]).tolist() == [0, 2, 2, 1]
