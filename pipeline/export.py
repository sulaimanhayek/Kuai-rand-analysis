"""Step 07: write pipeline results to site/src/data/ as small JSON files.

Output is deterministic: fixed seeds upstream, floats rounded to 6 significant digits (4 for per-video arrays),
no timestamps. Every number on the site is read from these files.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from pipeline.causal import Data
from pipeline.config import (BH_Q, EXP_END, EXP_START, EXPORT_DIR, METRICS, PRE_END, PRE_START, SHARES,
                             THRESHOLDS)


def clean(x, sig: int = 6):
    """Convert results to JSON-safe values. NaN and infinities become null."""
    if isinstance(x, pd.DataFrame):
        return [clean(r, sig) for r in x.to_dict("records")]
    if isinstance(x, pd.Series):
        return {str(k): clean(v, sig) for k, v in x.items()}
    if isinstance(x, dict):
        return {str(k): clean(v, sig) for k, v in x.items()}
    if isinstance(x, (list, tuple, np.ndarray)):
        return [clean(v, sig) for v in x]
    if x is None or x is pd.NA:
        return None
    if isinstance(x, (bool, np.bool_)):
        return bool(x)
    if isinstance(x, (int, np.integer)):
        return int(x)
    if isinstance(x, (float, np.floating)):
        x = float(x)
        return float(f"{x:.{sig}g}") if math.isfinite(x) else None
    if isinstance(x, str):
        return x
    raise TypeError(f"cannot export {type(x).__name__}")


def columns(df: pd.DataFrame, sig: int) -> dict:
    """Column-oriented table, smaller than a list of records for long tables."""
    return {c: clean(df[c].to_numpy(), sig) for c in df.columns}


def overview(con: duckdb.DuckDBPyConnection, data: Data) -> dict:
    t = con.sql("SELECT q20, q80 FROM tier_thresholds").df().iloc[0]
    imp = data.imp
    return {
        "periods": {"pre": [PRE_START, PRE_END], "experiment": [EXP_START, EXP_END]},
        "users": len(data.users),
        "pool_videos": len(data.videos),
        "random_impressions": len(imp),
        "random_users": imp.user_id.nunique(),
        "random_videos": imp.video_id.nunique(),
        "recommended_pool_impressions": len(data.rec),
        "impressions_by_tier": imp.tier.value_counts().sort_index().to_dict(),
        "videos_by_tier": data.videos.tier.value_counts().sort_index().to_dict(),
        "tier_thresholds": {"tail_max_pre_impressions": t.q20, "head_min_pre_impressions": t.q80 + 1},
        "mwt_cap_s": data.cap_s,
    }


def files(r: dict) -> dict[str, tuple[object, int | None]]:
    """File name -> (content, indent). Compact files hold long arrays."""
    c, v, s, llm = r["causal"], r["variants"], r["segments"], r.get("llm")
    l1 = c["layer1"]
    headline = l1["main"].set_index("metric").loc[["mwt", "early_skip", "hated"]].reset_index()
    gems = v["hidden_gems"]
    return {
        "overview.json": ({
            **r["overview"],
            "headline": headline,
            "decision_summary": v["decision_summary"],
            "max_share": v["max_share"],
        }, 1),
        "metrics.json": ({
            "definitions": [{"key": k, "label": lab, "unit": unit, "scale": sc} for k, (lab, unit, sc) in METRICS.items()],
            "thresholds": THRESHOLDS,
            "layer1": l1,
            "selection_bias": c["selection_bias"],
            "category_mix": c["category_mix"],
            "decomposition": c["decomposition"],
            "time": c["time"],
            "carryover": c["carryover"],
        }, 1),
        "experiment.json": ({
            "layer2": c["layer2"],
            "cuped_splits": c["cuped_splits"],
            "aa": c["aa"],
            "power": c["power"],
            "sensitivities": c["sensitivities"],
            "checks": c["checks"],
        }, 1),
        "segments.json": ({**s, "bh_q": BH_Q}, 1),
        "decision.json": ({
            "shares": SHARES,
            "thresholds": THRESHOLDS,
            **{k: val for k, val in v.items() if k != "hidden_gems"},
            "hidden_gems": {k: val for k, val in gems.items() if k != "videos"},
        }, 1),
        "hidden_gems_videos.json": (columns(gems["videos"], 4), None),
        **({"llm.json": (llm, 1)} if llm else {}),
    }


def write(results: dict, out_dir: Path = EXPORT_DIR) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, (content, indent) in files(results).items():
        path = out_dir / name
        sep = None if indent else (",", ":")
        path.write_text(json.dumps(clean(content), indent=indent, separators=sep, ensure_ascii=False) + "\n")
        written.append(path)
    return written
