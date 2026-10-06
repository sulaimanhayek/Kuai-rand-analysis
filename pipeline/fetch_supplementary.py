"""Fetch captions and categories for the 7,583 Pure videos.

The supplementary files (Zenodo 10.5281/zenodo.18159199, CC BY 4.0) cover all 32M KuaiRand
videos (about 7 GB). They are sorted by final_video_id and the Pure pool is ids 0 to 7582, so a
4 MB range request of each file is enough. The full-file md5 cannot be checked on a partial
download; the profile instead checks that caption durations match video_features_basic.

Usage: python -m pipeline.fetch_supplementary
"""

from __future__ import annotations

import io
import re
import urllib.request
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "raw" / "supplementary"
BASE_URL = "https://zenodo.org/records/18159199/files"
N_PURE_VIDEOS = 7583
HEAD_BYTES = 4_000_000
NUMBER = re.compile(r"^\d+(\.\d+)?$")


def fetch_pure_lines(name: str) -> tuple[str, list[str]]:
    req = urllib.request.Request(f"{BASE_URL}/{name}.csv", headers={"Range": f"bytes=0-{HEAD_BYTES}"})
    with urllib.request.urlopen(req) as resp:
        lines = resp.read().decode("utf-8", errors="replace").split("\n")
    header, body = lines[0], lines[1:-1]  # the range ends mid-row; drop the partial last line
    pure = [ln for ln in body if int(ln.split(",", 1)[0]) < N_PURE_VIDEOS]
    if len(pure) != N_PURE_VIDEOS:
        raise RuntimeError(f"{name}: expected {N_PURE_VIDEOS} Pure rows, got {len(pure)}")
    return header, pure


def parse_captions(lines: list[str]) -> pd.DataFrame:
    """The file is unquoted and, for a few rows, the duration sits in a text column.

    Every Pure row has exactly four fields. When the last field is not numeric, the numeric field
    is taken as the duration and the remaining text fields are joined into the caption.
    """
    rows = []
    for ln in lines:
        fields = ln.split(",")
        if len(fields) != 4:
            raise RuntimeError(f"unexpected field count: {ln[:80]!r}")
        vid, caption, cover, duration = fields
        repaired = not (duration == "" or NUMBER.match(duration))
        if repaired:
            texts = [f for f in (caption, cover, duration) if not NUMBER.match(f.strip())]
            numbers = [f for f in (caption, cover, duration) if NUMBER.match(f.strip())]
            caption, cover, duration = " ".join(t for t in texts if t.strip()), "", (numbers[0] if numbers else "")
        rows.append({
            "video_id": int(vid),
            "caption": caption.strip() or None,
            "show_cover_text": cover.strip() or None,
            "duration": float(duration) if duration else None,
            "parse_repaired": repaired,
        })
    return pd.DataFrame(rows)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    _, lines = fetch_pure_lines("kuairand_video_captions")
    captions = parse_captions(lines)
    captions.to_parquet(OUT_DIR / "captions_pure.parquet", index=False)
    print(f"wrote captions_pure.parquet: {len(captions)} rows, {captions.parse_repaired.sum()} repaired")

    header, lines = fetch_pure_lines("kuairand_video_categories")
    categories = pd.read_csv(io.StringIO("\n".join([header, *lines])))
    categories = categories.rename(columns={"final_video_id": "video_id"})
    categories.to_parquet(OUT_DIR / "categories_pure.parquet", index=False)
    print(f"wrote categories_pure.parquet: {len(categories)} rows")


if __name__ == "__main__":
    main()
