"""Step 06: LLM content tags from captions (Gemini), cached on disk.

Each video's caption and cover text get three tags: content vertical, format and commercial intent. Calls go to a
pinned model at temperature 0 with a JSON schema (enums), 20 videos per call. Every response is cached under
data/cache/llm/, keyed by a hash of model, prompt version and the batch, so a rerun costs nothing. The resulting
table is committed as data/derived/llm_tags.csv, so the rest of the pipeline runs without an API key.

Usage: python -m pipeline.llm [--limit N] [--workers N]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

from pipeline import db
from pipeline.config import CACHE_DIR, DERIVED_DIR, ROOT

MODEL = "gemini-3.1-flash-lite"
PROMPT_VERSION = "v1"
BATCH = 20
URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
LLM_CACHE = CACHE_DIR / "llm"
TAGS_PATH = DERIVED_DIR / "llm_tags.csv"

# Vertical -> the platform's top-level categories that map onto it (used for validation, not shown to the model).
VERTICALS = {
    "Film and TV": ["Film, TV and variety"],
    "Short drama": ["Short drama"],
    "Anime and comics": ["Anime and illustration"],
    "Gaming": ["Gaming"],
    "Food": ["Food"],
    "Sports and fitness": ["Sports", "Fitness"],
    "Comedy": ["Comedy"],
    "Music": ["Music"],
    "Dance": ["Dance"],
    "Celebrity and entertainment": ["Celebrity"],
    "Beauty, fashion and looks": ["Beauty", "Fashion", "Looks", "Selfies"],
    "Pets and animals": ["Pets"],
    "Family and relationships": ["Relationships", "Parenting"],
    "Everyday life and travel": ["Everyday life", "Travel", "Home", "Rural life"],
    "News and society": ["Local news", "Politics", "Military", "Finance"],
    "Knowledge and tech": ["Science and law", "Education", "Tech", "Health", "Cars", "Books", "Humanities"],
    "Arts and photography": ["Arts", "Photography"],
    "Other": ["Other", "Astrology", "Oddities"],
}
UNCLEAR = "Unclear"
FORMATS = ["original", "clip_or_reupload", "unclear"]
PLATFORM_TO_VERTICAL = {p: v for v, ps in VERTICALS.items() for p in ps}

INSTRUCTIONS = """You label short videos from a Chinese short-video app using only the creator's caption and the text \
shown on the video cover. Captions are in Chinese and often consist mostly of hashtags and @mentions. Topic hashtags \
are often the strongest signal; platform campaign and creator-programme hashtags (such as #集结吧光合创作者, \
#快手热点, #春日暴击) and @mentions of official accounts say nothing about the topic. Judge each video on its own \
text only: the videos in one request are unrelated, so never infer a label from neighbouring items. Return one \
result for every input id.

vertical: the main topic. Pick exactly one.
- Film and TV: films, TV series, variety shows, and recaps or commentary about them
- Short drama: scripted short dramas or mini-series made for short-video apps (短剧, 小剧场)
- Anime and comics: anime, manga, comics, illustration, animated stories
- Gaming: video games, esports, game commentary
- Food: cooking, eating, recipes, restaurants, food reviews
- Sports and fitness: sports, matches, athletes, workouts
- Comedy: skits, pranks, jokes, funny clips
- Music: songs, singing, instruments, covers
- Dance: dancing
- Celebrity and entertainment: celebrities, idols, entertainment news and gossip
- Beauty, fashion and looks: makeup, skincare, outfits, modelling, selfies, appearance
- Pets and animals: pets and other animals
- Family and relationships: parenting, children, couples, emotions, relationship advice
- Everyday life and travel: daily-life vlogs, home, rural life, travel, places
- News and society: news, social issues, politics, military, finance, public events
- Knowledge and tech: education, science, law, health, technology, cars, books, history and culture
- Arts and photography: painting, calligraphy, crafts, photography, traditional performance arts
- Other: a clear topic that fits none of the above
- Unclear: the text is too thin to tell the topic

format:
- original: the creator made the content themselves (filmed, performed, cooked, played, drew or wrote it)
- clip_or_reupload: built mainly from media made by others: clips, recaps or compilations of films, TV, variety \
shows, anime, sports broadcasts, music videos or other creators' videos, including commentary over such clips
- unclear: the text does not say enough to tell

commercial: true if the video promotes a product, service, shop, brand, livestream sale or purchase link (for \
example 链接, 下单, 小黄车, 店铺, 优惠, 广告). Platform campaigns, creator-programme hashtags and paid-promotion tags \
such as #快手粉条 do not count on their own. Otherwise false."""

SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "id": {"type": "INTEGER"},
            "vertical": {"type": "STRING", "enum": [*VERTICALS, UNCLEAR]},
            "format": {"type": "STRING", "enum": FORMATS},
            "commercial": {"type": "BOOLEAN"},
        },
        "required": ["id", "vertical", "format", "commercial"],
        "propertyOrdering": ["id", "vertical", "format", "commercial"],
    },
}


def api_key() -> str:
    key = os.environ.get("GEMINI_API_KEY")
    env = ROOT / ".env"
    if not key and env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("GEMINI_API_KEY="):
                key = line.split("=", 1)[1].strip().strip("'\"")
    if not key:
        raise SystemExit("Set GEMINI_API_KEY in the environment or in .env")
    return key


def load_items(con) -> list[dict]:
    """Videos with any caption or cover text, in video_id order."""
    df = con.sql("SELECT video_id, caption, show_cover_text FROM videos ORDER BY video_id").df()
    items = []
    for r in df.itertuples():
        cap = (r.caption or "").strip() if isinstance(r.caption, str) else ""
        cover = (r.show_cover_text or "").strip() if isinstance(r.show_cover_text, str) else ""
        if cap or cover:
            items.append({"id": int(r.video_id), "caption": cap, "cover_text": cover})
    return items


def request_body(batch: list[dict]) -> dict:
    lines = "\n".join(json.dumps(it, ensure_ascii=False) for it in batch)
    return {
        "systemInstruction": {"parts": [{"text": INSTRUCTIONS}]},
        "contents": [{"role": "user", "parts": [{"text": f"Label these {len(batch)} videos:\n{lines}"}]}],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
            "responseSchema": SCHEMA,
            "thinkingConfig": {"thinkingLevel": "low"},
        },
    }


class QuotaExhausted(RuntimeError):
    """The daily request quota is used up; the run can resume from the cache once it resets."""


def _retry_delay(err: urllib.error.HTTPError, attempt: int) -> float:
    try:
        for d in json.loads(err.read()).get("error", {}).get("details", []):
            m = re.match(r"([\d.]+)s", d.get("retryDelay", ""))
            if m:
                return float(m.group(1)) + random.uniform(0, 2)
    except (ValueError, AttributeError):
        pass
    return min(2 ** attempt, 60) + random.uniform(0, 2)


def post(body: dict, key: str, max_attempts: int = 8) -> dict:
    data = json.dumps(body, ensure_ascii=False).encode()
    for attempt in range(max_attempts):
        req = urllib.request.Request(URL, data=data, headers={"x-goog-api-key": key, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504) or attempt == max_attempts - 1:
                raise
            delay = _retry_delay(e, attempt)
            if delay > 300:
                raise QuotaExhausted(f"HTTP {e.code}, retry in {delay / 3600:.1f}h") from None
            print(f"HTTP {e.code}, retrying in {delay:.0f}s", file=sys.stderr, flush=True)
            time.sleep(delay)
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt == max_attempts - 1:
                raise
            print(f"{type(e).__name__}, retrying", file=sys.stderr, flush=True)
            time.sleep(min(2 ** attempt, 60))
    raise RuntimeError("unreachable")


def cache_path(batch: list[dict]):
    h = hashlib.sha256(json.dumps([MODEL, PROMPT_VERSION, INSTRUCTIONS, SCHEMA, batch], ensure_ascii=False,
                                  sort_keys=True).encode()).hexdigest()[:24]
    return LLM_CACHE / f"{h}.json"


def tag_batch(batch: list[dict], key: str) -> list[dict]:
    """Tag one batch, from the cache if present. Splits the batch if the ids in the answer don't match."""
    path = cache_path(batch)
    if path.exists():
        return json.loads(path.read_text())["labels"]
    ids = [it["id"] for it in batch]
    for _ in range(2):
        resp = post(request_body(batch), key)
        try:
            labels = json.loads(resp["candidates"][0]["content"]["parts"][0]["text"])
        except (KeyError, IndexError, ValueError):
            continue
        if sorted(lab["id"] for lab in labels) == sorted(ids):
            path.write_text(json.dumps({"model": MODEL, "model_version": resp.get("modelVersion"),
                                        "prompt_version": PROMPT_VERSION, "usage": resp.get("usageMetadata"),
                                        "labels": labels}, ensure_ascii=False))
            return labels
    if len(batch) == 1:
        raise RuntimeError(f"no valid answer for video {ids[0]}")
    mid = len(batch) // 2
    return tag_batch(batch[:mid], key) + tag_batch(batch[mid:], key)


def tag_all(limit: int | None = None, workers: int = 4) -> pd.DataFrame:
    """Tags for every pool video; videos without caption or cover text get empty tags."""
    key = api_key()
    LLM_CACHE.mkdir(parents=True, exist_ok=True)
    with db.connect(read_only=True) as con:
        items = load_items(con)
        ids = con.sql("SELECT video_id FROM videos ORDER BY video_id").df().video_id
    if limit:
        items = items[:limit]
        ids = ids[ids <= items[-1]["id"]]
    batches = [items[i:i + BATCH] for i in range(0, len(items), BATCH)]
    labels, done, t0 = [], 0, time.time()
    with ThreadPoolExecutor(workers) as ex:
        futures = [ex.submit(tag_batch, b, key) for b in batches]
        try:
            for fut in as_completed(futures):
                labels += fut.result()
                done += 1
                if done % 20 == 0 or done == len(batches):
                    print(f"{done}/{len(batches)} batches, {time.time() - t0:.0f}s", flush=True)
        except QuotaExhausted as e:
            ex.shutdown(cancel_futures=True)
            raise SystemExit(f"Daily quota used up after {done}/{len(batches)} batches ({e}). "
                             "Rerun later; finished batches are cached.") from None
    tags = pd.DataFrame(labels).rename(columns={"id": "video_id"}).set_index("video_id")
    return tags.reindex(ids).reset_index()


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m pipeline.llm")
    ap.add_argument("--limit", type=int, help="tag only the first N videos with text (for a trial run)")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()
    tags = tag_all(args.limit, args.workers)
    if args.limit:
        print(tags.to_string())
        return
    DERIVED_DIR.mkdir(parents=True, exist_ok=True)
    tags.assign(model=MODEL, prompt_version=PROMPT_VERSION).to_csv(TAGS_PATH, index=False)
    print(f"wrote {len(tags)} rows ({tags.vertical.notna().sum()} tagged) to {TAGS_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
