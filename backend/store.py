"""Tiny content-addressed disk cache.

Every external fetch and every LLM call goes through here, keyed by (namespace, key).
That single decision is most of FINN's cost story: a filing is summarised once, a
transcript is extracted once, a screener page is parsed once per day — no matter how
many times the investor opens the app.
"""
from __future__ import annotations

import functools
import hashlib
import json
import pathlib
import time
from typing import Any, Callable

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CACHE = DATA / "cache"

HOUR = 3600
DAY = 24 * HOUR


def _path(ns: str, key: str) -> pathlib.Path:
    h = hashlib.sha1(key.encode("utf-8")).hexdigest()[:24]
    d = CACHE / ns
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{h}.json"


def get(ns: str, key: str, ttl: float | None) -> Any | None:
    p = _path(ns, key)
    if not p.exists():
        return None
    if ttl is not None and time.time() - p.stat().st_mtime > ttl:
        return None
    try:
        return json.loads(p.read_text("utf-8"))["v"]
    except Exception:
        return None


def put(ns: str, key: str, value: Any) -> Any:
    p = _path(ns, key)
    p.write_text(json.dumps({"k": key, "v": value}, ensure_ascii=False, default=str), "utf-8")
    return value


def cached(ns: str, ttl: float | None):
    """Decorator: memoise a function's JSON-serialisable result on disk."""

    def deco(fn: Callable):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            key = json.dumps([fn.__name__, args, kwargs], sort_keys=True, default=str)
            hit = get(ns, key, ttl)
            if hit is not None:
                return hit
            try:
                val = fn(*args, **kwargs)
            except Exception as e:
                # stale-on-error: a blocked or flaky source serves the last good copy
                stale = get(ns, key, None)
                if stale is not None:
                    print(f"[store] {ns}: source failed ({type(e).__name__}); serving stale copy")
                    return stale
                raise
            if val is not None:
                put(ns, key, val)
            return val

        return wrapper

    return deco


def load_json(rel: str) -> Any:
    return json.loads((DATA / rel).read_text("utf-8"))


def save_json(rel: str, value: Any) -> None:
    p = DATA / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(value, indent=2, ensure_ascii=False), "utf-8")
