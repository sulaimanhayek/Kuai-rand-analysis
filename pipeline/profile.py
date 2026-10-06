"""Phase 1 data profile.

Runs the named queries in pipeline/sql/00_profile.sql plus a small variance pilot
(CUPED and clustering) and writes every result to notes/01_data_profile_tables.md.
The written findings in notes/01_data_profile.md cite these tables.

Usage: python -m pipeline.profile
"""

from __future__ import annotations

import re
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import statsmodels.api as sm

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "kuairand.duckdb"
SQL_DIR = ROOT / "pipeline" / "sql"
OUT_PATH = ROOT / "notes" / "01_data_profile_tables.md"

# Draft north-star definition used in the pilot: plays under 3s count as 0, plays capped at 180s.
MWT_SQL = "CASE WHEN {p}play_time_ms >= 3000 THEN least({p}play_time_ms, 180000) ELSE 0 END / 1000.0"
SEED = 20220422


def named_queries(path: Path) -> list[tuple[str, str]]:
    text = path.read_text()
    parts = re.split(r"^-- name: (\w+)\s*$", text, flags=re.MULTILINE)
    return [(parts[i], parts[i + 1].strip()) for i in range(1, len(parts), 2)]


def null_counts(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    rows = []
    for table in [
        "raw_log_standard_early",
        "raw_log_standard_late",
        "raw_log_random",
        "raw_user_features",
        "raw_video_basic",
        "raw_video_stats",
    ]:
        cols = [r[0] for r in con.sql(f"SELECT column_name FROM (DESCRIBE {table})").fetchall()]
        counts = con.sql(
            "SELECT " + ", ".join(f'count(*) - count("{c}") AS "{c}"' for c in cols) + f" FROM {table}"
        ).df().iloc[0]
        n = con.sql(f"SELECT count(*) FROM {table}").fetchone()[0]
        nonzero = counts[counts > 0]
        rows.append(
            {
                "table": table,
                "n_rows": n,
                "n_columns": len(cols),
                "columns_with_nulls": ", ".join(f"{c} ({v})" for c, v in nonzero.items()) or "none",
            }
        )
    return pd.DataFrame(rows)


def variance_pilot(con: duckdb.DuckDBPyConnection) -> dict[str, pd.DataFrame]:
    """Exploratory: how much would CUPED help, and which variance component dominates?"""
    con.sql(
        """
        CREATE OR REPLACE TEMP TABLE pilot_tiers AS
        WITH per_video AS (
            SELECT v.video_id, count(l.video_id) AS n
            FROM raw_video_basic v LEFT JOIN raw_log_standard_early l USING (video_id)
            GROUP BY v.video_id
        )
        SELECT video_id,
               CASE ntile(5) OVER (ORDER BY n, video_id) WHEN 1 THEN 'tail' WHEN 5 THEN 'head' ELSE 'mid' END AS tier
        FROM per_video
        """
    )
    pre = con.sql(
        f"""
        SELECT user_id,
               avg({MWT_SQL.format(p='')}) AS pre_mwt,
               avg(CASE WHEN play_time_ms < 3000 THEN 1.0 ELSE 0 END) AS pre_skip,
               avg(long_view) AS pre_long_view,
               avg(is_like) AS pre_like,
               ln(count(*)) AS pre_log_n
        FROM raw_log_standard_early WHERE tab = 1 GROUP BY user_id
        """
    ).df()

    # 1. User-level correlation between pre-period and random-period behaviour.
    post = con.sql(
        f"""
        SELECT user_id, avg({MWT_SQL.format(p='')}) AS post_mwt,
               avg(CASE WHEN play_time_ms < 3000 THEN 1.0 ELSE 0 END) AS post_skip, count(*) AS n
        FROM raw_log_random WHERE tab = 1 GROUP BY user_id
        """
    ).df()
    both = post.merge(pre, on="user_id")
    w = both["n"].to_numpy(float)

    def wcorr(a: pd.Series, b: pd.Series) -> float:
        c = np.cov(a, b, aweights=w)
        return float(c[0, 1] / np.sqrt(c[0, 0] * c[1, 1]))

    corr = pd.DataFrame(
        [
            {"pair": "pre MWT vs random-period MWT", "unweighted": both.pre_mwt.corr(both.post_mwt),
             "impression_weighted": wcorr(both.pre_mwt, both.post_mwt)},
            {"pair": "pre skip rate vs random-period skip rate", "unweighted": both.pre_skip.corr(both.post_skip),
             "impression_weighted": wcorr(both.pre_skip, both.post_skip)},
        ]
    ).round(3)
    corr["n_users"] = len(both)

    # 2. Impression-level head vs tail, with user / item / two-way clustered SEs, with and without CUPED.
    imp = con.sql(
        f"""
        SELECT r.user_id, r.video_id, CAST(t.tier = 'tail' AS INTEGER) AS treat,
               {MWT_SQL.format(p='r.')} AS y
        FROM raw_log_random r JOIN pilot_tiers t USING (video_id)
        WHERE r.tab = 1 AND t.tier IN ('head', 'tail')
        """
    ).df()
    imp = imp.merge(pre[["user_id", "pre_mwt"]], on="user_id", how="left")
    imp["x"] = imp.pre_mwt.fillna(pre.pre_mwt.mean())
    theta = np.cov(imp.y, imp.x)[0, 1] / imp.x.var()
    imp["y_cuped"] = imp.y - theta * (imp.x - imp.x.mean())
    X = sm.add_constant(imp[["treat"]].astype(float))
    groups = {
        "user": imp.user_id.to_numpy(),
        "item": imp.video_id.to_numpy(),
        "user x item (two-way)": imp[["user_id", "video_id"]].to_numpy(),
    }
    rows = []
    for outcome in ["y", "y_cuped"]:
        for name, g in groups.items():
            fit = sm.OLS(imp[outcome], X).fit(cov_type="cluster", cov_kwds={"groups": g})
            rows.append({"outcome": "MWT" if outcome == "y" else "MWT, CUPED", "clustering": name,
                         "tail_minus_head_s": fit.params["treat"], "se": fit.bse["treat"]})
    impression_level = pd.DataFrame(rows).round(4)
    impression_level["n_impressions"] = len(imp)

    # 3. Constructed user-level split: each user is coin-flipped into one arm and contributes only
    #    that arm's impressions. Ratio metric (impression-weighted), delta-method SE, user clustering only.
    agg = con.sql(
        f"""
        SELECT r.user_id, t.tier, sum({MWT_SQL.format(p='r.')}) AS s, count(*) AS n
        FROM raw_log_random r JOIN pilot_tiers t USING (video_id)
        WHERE r.tab = 1 AND t.tier IN ('head', 'tail') GROUP BY ALL
        """
    ).df()
    covs = ["pre_mwt", "pre_skip", "pre_long_view", "pre_like", "pre_log_n"]
    users = np.sort(agg.user_id.unique())  # sorted so the seeded split is reproducible
    rng = np.random.default_rng(SEED)
    split_rows = []
    for rep in range(20):
        arm = pd.Series(rng.integers(0, 2, len(users)), index=users, name="treat")
        d = agg.join(arm, on="user_id")
        d = d[((d.treat == 1) & (d.tier == "tail")) | ((d.treat == 0) & (d.tier == "head"))]
        d = d.merge(pre, on="user_id", how="left")
        d["has_pre"] = d.pre_mwt.notna().astype(float)
        for c in covs:
            d[c] = d[c].fillna(pre[c].mean())
        y = (d.s / d.n).to_numpy()
        wt = d.n.to_numpy(float)
        t = (d.treat == 1).to_numpy()

        def diff(yy: np.ndarray) -> tuple[float, float]:
            out = []
            for mask in (t, ~t):
                m = np.average(yy[mask], weights=wt[mask])
                resid = yy[mask] * wt[mask] - m * wt[mask]
                out.append((m, np.sqrt(resid.var(ddof=1) / (mask.sum() * wt[mask].mean() ** 2))))
            return out[0][0] - out[1][0], float(np.hypot(out[0][1], out[1][1]))

        Xc = d[covs + ["has_pre"]].to_numpy()
        Xc = Xc - np.average(Xc, axis=0, weights=wt)
        yc = y - np.average(y, weights=wt)
        beta = np.linalg.lstsq(Xc * np.sqrt(wt)[:, None], yc * np.sqrt(wt), rcond=None)[0]
        th1 = np.average(Xc[:, 0] * yc, weights=wt) / np.average(Xc[:, 0] ** 2, weights=wt)
        e0, s0 = diff(y)
        e1, s1 = diff(y - th1 * Xc[:, 0])
        e2, s2 = diff(y - Xc @ beta)
        split_rows.append((e0, s0, e1, s1, e2, s2, len(d)))
    sp = pd.DataFrame(split_rows, columns=["est", "se", "est_cuped1", "se_cuped1", "est_multi", "se_multi", "n_users"])
    user_split = pd.DataFrame(
        [
            {"variant": "no adjustment", "mean_estimate_s": sp.est.mean(), "mean_se": sp.se.mean(),
             "variance_reduction": 0.0},
            {"variant": "CUPED, 1 covariate (pre MWT)", "mean_estimate_s": sp.est_cuped1.mean(),
             "mean_se": sp.se_cuped1.mean(), "variance_reduction": 1 - ((sp.se_cuped1 / sp.se) ** 2).mean()},
            {"variant": "CUPED, 5 covariates + has_pre", "mean_estimate_s": sp.est_multi.mean(),
             "mean_se": sp.se_multi.mean(), "variance_reduction": 1 - ((sp.se_multi / sp.se) ** 2).mean()},
        ]
    ).round(4)
    user_split["n_splits"] = len(sp)
    user_split["mean_users_per_split"] = round(sp.n_users.mean())

    return {
        "pilot_user_correlation": corr,
        "pilot_impression_level_se": impression_level,
        "pilot_user_split_cuped": user_split,
    }


def main() -> None:
    con = duckdb.connect(str(DB_PATH))
    con.execute((SQL_DIR / "01_ingest.sql").read_text())

    sections: list[tuple[str, pd.DataFrame]] = [("null_counts", null_counts(con))]
    for name, sql in named_queries(SQL_DIR / "00_profile.sql"):
        sections.append((name, con.sql(sql).df()))
    sections.extend(variance_pilot(con).items())

    lines = [
        "# Data profile: raw query outputs",
        "",
        "Generated by `python -m pipeline.profile`. Do not edit by hand.",
        "Queries: `pipeline/sql/00_profile.sql`. Pilot sections: `pipeline/profile.py::variance_pilot`.",
        "",
    ]
    for name, df in sections:
        lines += [f"## {name}", "", df.to_markdown(index=False), ""]
    OUT_PATH.write_text("\n".join(lines))
    print(f"wrote {OUT_PATH.relative_to(ROOT)} ({len(sections)} sections)")


if __name__ == "__main__":
    main()
