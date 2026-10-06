"""Step 05: heterogeneous effects. Tail vs head within pre-registered user and content segments.

Each segment level gets a two-way clustered estimate; each segment gets a Wald test that the effect is the same at
every level (relative for MWT, absolute for early skip, matching the decision thresholds). Benjamini-Hochberg runs across all of those tests (segments x metrics) at q = 0.10.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from pipeline import stats as st
from pipeline.causal import OTHER, Data, effects_by, top_categories
from pipeline.config import BH_Q

SEG_METRICS = {"mwt": "rel", "early_skip": "abs"}  # the scale each decision threshold uses

ACTIVITY = {"full_active": "01|Full", "high_active": "02|High", "middle_active": "03|Middle"}
FOLLOWING = {"0": "01|0 to 10", "(0,10]": "01|0 to 10", "(10,50]": "02|11 to 50", "(50,100]": "03|51 to 100",
             "(100,150]": "04|101 to 150", "(150,250]": "05|151 to 250", "(250,500]": "06|251 to 500",
             "500+": "07|Over 500"}
DURATION = {"1. under 15s": "01|Under 15s", "2. 15-30s": "02|15 to 30s", "3. 30-60s": "03|30 to 60s",
            "4. 1-3 min": "04|1 to 3 min", "5. 3 min+": "05|Over 3 min", "missing": "06|Unknown"}


def _terciles(users: pd.DataFrame, col: str) -> pd.Series:
    """Terciles over users with pre-period history; users without it get their own level."""
    has = (users.has_pre == 1).to_numpy()
    x = users[col].astype(float).to_numpy()
    cuts = np.quantile(x[has], [1 / 3, 2 / 3])
    lab = np.select([x <= cuts[0], x <= cuts[1]], ["01|Low", "02|Middle"], "03|High")
    return pd.Series(np.where(has, lab, "04|No pre-period history"), index=users.user_id)


def user_segments(users: pd.DataFrame) -> pd.DataFrame:
    u = users.set_index("user_id")
    days = u.register_days
    seg = pd.DataFrame(index=u.index)
    seg["Activity level"] = u.user_active_degree.map(ACTIVITY).fillna("04|Other")
    seg["Tenure"] = np.select([days <= 180, days <= 365, days <= 730],
                              ["01|Up to 180 days", "02|181 to 365 days", "03|1 to 2 years"], "04|Over 2 years")
    seg["Pre-period MWT"] = _terciles(users, "pre_mwt")
    seg["Pre-period skip rate"] = _terciles(users, "pre_skip")
    seg["Pre-period volume"] = _terciles(users, "pre_n")
    seg["Creator"] = np.where(u.is_video_author == 1, "01|Creator", "02|Not a creator")
    seg["Following count"] = u.follow_user_num_range.map(FOLLOWING).fillna("08|Unknown")
    return seg


def content_segments(d: pd.DataFrame, videos: pd.DataFrame) -> pd.DataFrame:
    top = sorted(top_categories(videos))
    order = {c: f"{i + 1:02d}|{c}" for i, c in enumerate(top)}
    out = pd.DataFrame(index=d.index)
    out["Category"] = d.l1_en.map(order).fillna(f"{len(top) + 1:02d}|{OTHER}")
    out["Duration"] = d.duration_bucket.map(DURATION)
    return out


SIDE = {"Category": "content", "Duration": "content"}


def run(data: Data, extra_content: pd.DataFrame | None = None) -> dict:
    """extra_content: optional per-video segment columns (indexed by video_id), e.g. LLM tags."""
    d = data.ht
    useg = user_segments(data.users)
    d = d.join(useg, on="user_id")
    d = pd.concat([d, content_segments(d, data.videos)], axis=1)
    names = list(useg.columns) + ["Category", "Duration"]
    if extra_content is not None:
        d = d.join(extra_content, on="video_id")
        names += list(extra_content.columns)
    rows, tests = [], []
    for m, scale in SEG_METRICS.items():
        overall = st.diff_in_means(d[m].to_numpy(), d.treat.to_numpy(), st.two_way(d.user_id.to_numpy(), d.video_id.to_numpy()))
        for seg in names:
            est, test = effects_by(d, m, seg, scale)
            est["segment"], est["metric"] = seg, m
            est["side"] = SIDE.get(seg, "content" if extra_content is not None and seg in extra_content.columns else "user")
            est["order"] = est.level.str.split("|").str[0].astype(int)
            est["level"] = est.level.str.split("|").str[1]
            est["overall_rel"], est["overall_abs"] = overall["rel"], overall["abs"]
            rows.append(est)
            tests.append({**test, "side": est.side.iloc[0]})
    est = pd.concat(rows, ignore_index=True)
    tests = pd.DataFrame(tests)
    tests["q"] = st.bh(tests.p.to_numpy())
    tests["heterogeneous"] = tests.q < BH_Q
    return {"estimates": est, "tests": tests, "bh_q": BH_Q}
