"""Primary documents (transcripts, annual reports, filings): download → text → page chunks."""
from __future__ import annotations

import io
import logging
import re

from pypdf import PdfReader

logging.getLogger("pypdf").setLevel(logging.ERROR)

from .. import store
from . import http

MAX_PAGES = 60  # transcripts are 15–30 pages; for ARs we only read what we need


@store.cached("pdf_text", ttl=None)  # primary documents are immutable once filed
def pdf_pages(url: str, max_pages: int = MAX_PAGES) -> list[str] | None:
    headers = {"Referer": "https://www.bseindia.com/"} if "bseindia" in url else {}
    session = http.nse() if "nseindia" in url else None
    try:
        r = http.get(url, session=session, headers=headers, timeout=60)
    except Exception:
        return None
    if not r.ok or b"%PDF" not in r.content[:1024]:
        return None
    try:
        reader = PdfReader(io.BytesIO(r.content))
        pages = []
        for p in reader.pages[:max_pages]:
            try:
                pages.append(_clean(p.extract_text() or ""))
            except Exception:
                pages.append("")
        return pages
    except Exception:
        return None


def _clean(t: str) -> str:
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


_ABBR = re.compile(r"\b(Mr|Ms|Mrs|Dr|Shri|Smt|Rs|No|Ltd|Co|Pvt|Inc|St|Sr|Jr|viz|vs|i\.e|e\.g|approx|[A-Z])\.\s")


def sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text)
    # protect "Mr. X", "V. N." — the marker must NOT be whitespace (\s matches NBSP too)
    text = _ABBR.sub(lambda m: m.group(0).replace(". ", ".\x00"), text)
    text = _ABBR.sub(lambda m: m.group(0).replace(". ", ".\x00"), text)  # overlapping runs: "V. N. Rangan"
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z\"'(])", text)
    return [p.replace("\x00", " ").strip() for p in parts if 25 < len(p.strip()) < 600]


def speaker_turns(pages: list[str]) -> list[dict]:
    """Split a concall transcript into speaker turns: [{speaker, text, page}]."""
    turns = []
    pat = re.compile(r"(?m)^([A-Z][A-Za-z.\s]{2,40}?)\s*[:\-–]\s+(?=\S)")
    for pi, page in enumerate(pages):
        idx = [(m.start(), m.group(1).strip()) for m in pat.finditer(page)]
        if not idx:
            if turns:
                turns[-1]["text"] += " " + page
            continue
        if idx[0][0] > 0 and turns:
            turns[-1]["text"] += " " + page[: idx[0][0]]
        for i, (pos, spk) in enumerate(idx):
            end = idx[i + 1][0] if i + 1 < len(idx) else len(page)
            body = page[pos:end].split(":", 1)[-1] if ":" in page[pos:end][:60] else page[pos:end]
            turns.append({"speaker": spk, "text": body.strip(), "page": pi + 1})
    return turns
