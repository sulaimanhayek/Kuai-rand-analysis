"""Run the pipeline end to end.

    python -m pipeline                 # build the database if missing, run steps 04 to 07
    python -m pipeline --rebuild       # also re-run the SQL steps 01 to 03
    python -m pipeline --export-only   # re-export from the cached results of the last run
"""

from __future__ import annotations

import argparse
import pickle
import time

from pipeline import causal, db, export, segments, tags, variants
from pipeline.config import CACHE_DIR, DB_PATH, EXPORT_DIR

RESULTS = CACHE_DIR / "results.pkl"


def step(name: str, fn, *args):
    t = time.time()
    out = fn(*args)
    print(f"{name:<12} {time.time() - t:6.1f}s", flush=True)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m pipeline")
    ap.add_argument("--rebuild", action="store_true", help="re-run SQL steps 01 to 03 even if the database exists")
    ap.add_argument("--export-only", action="store_true", help="re-export from the cached results of the last run")
    args = ap.parse_args()

    if args.export_only:
        results = pickle.loads(RESULTS.read_bytes())
    else:
        if args.rebuild or not DB_PATH.exists():
            with db.connect() as con:
                step("01-03 build", db.build, con)
        tag_table = tags.load()  # committed LLM tags (python -m pipeline.llm); the run needs no API key
        with db.connect(read_only=True) as con:
            data = step("load", causal.load, con)
            results = {
                "overview": export.overview(con, data),
                "causal": step("04 causal", causal.run, con, data),
                "variants": step("04 variants", variants.run, data),
                "segments": step("05 segments", segments.run, data,
                                 None if tag_table is None else tags.segment_columns(tag_table)),
                "llm": None if tag_table is None else step("06 llm", tags.run, data, tag_table),
            }
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        RESULTS.write_bytes(pickle.dumps(results))

    paths = step("07 export", export.write, results)
    print(f"wrote {len(paths)} files to {EXPORT_DIR.relative_to(EXPORT_DIR.parents[2])}")


if __name__ == "__main__":
    main()
