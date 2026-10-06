"""The power model's SE matches the spread of simulated tail-vs-head differences in both designs."""

import numpy as np

from pipeline.causal import model_se

VC = {"video": 16.0, "user": 30.0, "residual": 340.0}
VIDEOS, USERS, REPS = 300, 2000, 400


def simulate(split: bool, rng: np.random.Generator) -> tuple[float, float, float]:
    """One draw: users with heavy-tailed activity see random videos from each arm. Returns the difference in means,
    impressions per video and the user design effect."""
    video = rng.normal(0, np.sqrt(VC["video"]), (2, VIDEOS))
    user = rng.normal(0, np.sqrt(VC["user"]), USERS)
    per_user = rng.geometric(1 / 10, USERS)
    if split:  # each user sees one arm
        arm_of = rng.integers(0, 2, USERS)
        uid = np.repeat(np.arange(USERS), per_user)
        arm = arm_of[uid]
    else:  # each impression's arm is random
        uid = np.repeat(np.arange(USERS), per_user)
        arm = rng.integers(0, 2, len(uid))
    vid = rng.integers(0, VIDEOS, len(uid))
    y = video[arm, vid] + user[uid] + rng.normal(0, np.sqrt(VC["residual"]), len(uid))
    diff = y[arm == 1].mean() - y[arm == 0].mean()
    deff = np.mean([(n ** 2).mean() / n.mean() ** 2
                    for n in (np.bincount(uid[arm == a], minlength=USERS) for a in (0, 1))
                    for n in [n[n > 0]]])
    return diff, len(uid) / 2 / VIDEOS, deff


def test_model_se_matches_simulation():
    rng = np.random.default_rng(3)
    for split in (True, False):
        draws = [simulate(split, rng) for _ in range(REPS)]
        diffs, ipv, deff = (np.array(x) for x in zip(*draws))
        se = model_se(VC, VIDEOS, ipv.mean(), USERS if split else None, deff.mean())
        assert abs(diffs.std() / se - 1) < 0.08, (split, diffs.std(), se)


def test_cuped_only_touches_non_video_variance():
    full = model_se(VC, VIDEOS, 50, USERS, 3.0, cuped=1.0)
    assert np.isclose(full, np.sqrt(2 * VC["video"] / VIDEOS))
    assert model_se(VC, VIDEOS, 50, USERS, 3.0, cuped=0.3) < model_se(VC, VIDEOS, 50, USERS, 3.0)
    # Every user in both arms: the user count and CUPED do not enter.
    assert model_se(VC, VIDEOS, 50) < model_se(VC, VIDEOS, 50, USERS, 3.0)
