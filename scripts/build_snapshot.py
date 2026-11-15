"""Freeze the live API into static JSON so the prototype runs on any static host (GitHub Pages)
with zero backend. The client falls back to these files automatically when /api is unreachable.

    python scripts/build_snapshot.py
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from backend.app import app  # noqa: E402
from backend.engine import digest  # noqa: E402

OUT = ROOT / "web" / "snapshot"
ASKS = [
    "What changed this week?", "Who knows Rajiv Dube?", "Who can I call on HDFC Bank?", "Who should I reconnect with?",
    "Who can I call on CDSL?", "What changed in the last 3 days?", "Experts on specialty chemicals", "Who knows V. N. Srivatsan?",
    "Who understands rural lending?", "Prep me for Dixon", "Who knows Kalpana Merchant?", "Who knows Leo Puri?",
]


def key(path: str) -> str:
    """Must match snapKey() in web/app.js."""
    return re.sub(r"_+$", "", re.sub(r"[^\w]+", "_", path.lstrip("/")))


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    c = TestClient(app)
    pf = digest.portfolio()
    syms = [h["symbol"] for h in pf["holdings"]] + pf["research"]
    paths = [f"/digest?hours={h}" for h in (24, 72, 168, 720)] + [
        "/alerts?hours=168", "/portfolio", "/prefs", "/network/expertise", "/network/warm", "/network/graph", "/network/contacts",
    ]
    paths += [f"/network/company/{s}" for s in syms] + [f"/research/{s}" for s in syms]
    for p in paths:
        t = time.time()
        r = c.get("/api" + p)
        if r.status_code != 200:
            print("skip", p, r.status_code)
            continue
        (OUT / f"{key(p)}.json").write_text(json.dumps(r.json(), ensure_ascii=False, separators=(",", ":")), "utf-8")
        print(f"{p:40} {len(r.content):>8}B {time.time() - t:5.1f}s")
    for q in ASKS:
        r = c.post("/api/ask", json={"q": q})
        name = key("/ask_" + key(q.lower()))
        (OUT / f"{name}.json").write_text(json.dumps(r.json(), ensure_ascii=False, separators=(",", ":")), "utf-8")
        print("ask", q, "->", r.json().get("intent"))


if __name__ == "__main__":
    main()
