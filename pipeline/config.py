"""Constants fixed before the analysis ran (see notes/02_analysis_plan.md)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "kuairand.duckdb"
SQL_DIR = ROOT / "pipeline" / "sql"
EXPORT_DIR = ROOT / "site" / "src" / "data"
DERIVED_DIR = ROOT / "data" / "derived"  # committed derived data (LLM tags)
CACHE_DIR = ROOT / "data" / "cache"  # gitignored

SEED = 20220422

# Periods (yyyymmdd ints, as in the logs).
PRE_START, PRE_END = 20220409, 20220421
EXP_START, EXP_END = 20220422, 20220508
LATE_POOL_DATE = 20220505  # sensitivity: videos still drawn on or after this date

# North-star: meaningful watch time per impression.
SKIP_MS = 3000
CAP_QUANTILE = 0.999  # cap = pre-period p99.9 of tab-1 recommended play time (approved: 373.5s)

# Decision thresholds at the feed level (approved).
THRESHOLDS = {
    "mwt_rel_min": -0.010,  # MWT change >= -1.0%
    "skip_pp_max": 0.010,  # early-skip increase <= +1.0 pp
    "hate_rel_max": 0.050,  # hates per 1K <= +5% relative
}
SHARES = [0.01, 0.02, 0.05]  # share of tab-1 slots reallocated to the boost
GATE_K = [5, 10, 20, 50]
GATE_DEFAULT_K = 10

ALPHA = 0.05
POWER = 0.80
BH_Q = 0.10
N_BOOT = 400
N_AA = 300

METRICS = {
    # key: (label, unit, scale). scale multiplies the per-impression mean for display.
    "mwt": ("Meaningful watch time", "s", 1.0),
    "early_skip": ("Early-skip rate", "rate", 1.0),
    "hated": ("Hates per 1K", "per1k", 1000.0),
    "long_view": ("Long-view rate", "rate", 1.0),
    "liked": ("Likes per 1K", "per1k", 1000.0),
    "followed": ("Follows per 1K", "per1k", 1000.0),
    "profile_entered": ("Profile enters per 1K", "per1k", 1000.0),
}

# Platform top-level categories (Chinese names in the source) to English labels.
L1_ENGLISH = {
    "影视综": "Film, TV and variety",
    "二次元": "Anime and illustration",
    "游戏": "Gaming",
    "美食": "Food",
    "运动": "Sports",
    "喜剧": "Comedy",
    "音乐": "Music",
    "短剧": "Short drama",
    "舞蹈": "Dance",
    "明星娱乐": "Celebrity",
    "艺术": "Arts",
    "宠物": "Pets",
    "美妆": "Beauty",
    "颜值": "Looks",
    "生活": "Everyday life",
    "民生资讯": "Local news",
    "情感": "Relationships",
    "时尚": "Fashion",
    "汽车": "Cars",
    "旅游": "Travel",
    "科学与法律": "Science and law",
    "房产家居": "Home",
    "读书": "Books",
    "摄影": "Photography",
    "高新数码": "Tech",
    "亲子": "Parenting",
    "自拍": "Selfies",
    "人文": "Humanities",
    "军事": "Military",
    "教育": "Education",
    "三农": "Rural life",
    "财经": "Finance",
    "星座命理": "Astrology",
    "奇人异象": "Oddities",
    "其他": "Other",
    "健身": "Fitness",
    "健康": "Health",
    "时政资讯": "Politics",
}
