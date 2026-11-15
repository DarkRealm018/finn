"""Narrative vs numbers: what management promised on the last four calls vs what was delivered.

1. Pull the last 4 concall transcripts (company PDFs linked from screener).
2. Split into speaker turns; drop the moderator and every analyst (we learn analyst names from
   "the next question is from the line of X from Y").
3. From management's own words, keep forward-looking, quantified statements about headline
   metrics (growth, margins, capex, loan growth…). Cheap model when available; otherwise a
   cue-scoring extractor. Each keeps its quote and page number.
4. Check every checkable promise against the quarters reported *after* that call, from the
   standardised quarterly P&L. Verdicts: delivered / missed / too early / not checkable.
5. Also note what analysts kept pressing on — the questions are often more telling than the answers.
"""
from __future__ import annotations

import datetime as dt
import re
from collections import Counter

from .. import store
from ..sources import docs
from . import llm

MONTHS = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}

FORWARD = re.compile(
    r"\b(we expect|we are expecting|expect(ed)? to|we anticipate|we are targeting|target(ing)?|guid(e|ance|ing)|we aim|aspir|"
    r"going forward|outlook|should be (able|in the range|around)|we will (be|deliver|achieve|grow|see)|we see|we believe we can|"
    r"next (year|quarter|fiscal)|this (year|fiscal)|by FY|for FY|in FY|for the (full )?year|medium[- ]term|over the next|"
    r"coming (quarters|years)|second half|H2|we remain confident|on track)\b",
    re.I,
)
STRONG_FWD = re.compile(
    r"\b(we expect|expect(ing|ed)? (to|a|an|that|this|the)|we are (targeting|guiding|looking at|aiming|confident of)|guid(e|ing) (for|to|of|at)|"
    r"our guidance|(the|this|that) guidance|guidance (of|is|for|at|to|from|remains|stays)|retain(ing)? (our|the) .{0,30}guidance|maintain(ing)? (our|the) .{0,30}guidance|"
    r"target(ing)? (of|to|is|a|an)|we aim|aspir\w+|we will (grow|deliver|achieve|be|see|reach)|should (be|grow|come|deliver|remain|reach|see)|"
    r"we plan|outlook (of|is|for)|on track to|committed to|we would want to|we are looking for)\b",
    re.I,
)
PAST = re.compile(
    r"\b(was|were|grew|delivered|came in|ended|reported|achieved|registered|clocked|posted|stood at|declined|we did|it says|"
    r"as against the guidance|versus guidance of|last year|previous year was|in the last)\b",
    re.I,
)
QOQ = re.compile(r"quarter[\s-]*on[\s-]*quarter|\bqoq\b|sequential|quarter \d (against|over|vs\.?) quart", re.I)
FULLYEAR = re.compile(r"\bFY\s?'?\d{2}|\bfull[- ]year\b|this year|for the year|annual|next year|medium[- ]term|cross[- ]cycle|previous year|guidance", re.I)
SEGMENT = re.compile(r"\b(this|that|the|our) (?:[\w-]+ ){1,3}(business|segment|division|vertical)\b|\bin (that|this) business\b", re.I)
# "We feel that it should be double-digit margin" — the subject was said earlier; we can't know what was promised
ANAPHOR = re.compile(r"^(?:(?:so|and|but|yes|no)[, ]+)?(?:we (?:feel|think|believe) (?:that )?)?(it|this|that|these|those)\b", re.I)

# order matters: lender metrics before the generic "growth"
METRIC = {
    "loan growth": r"\b(loan growth|aum|advances|book growth|disbursement|deposit growth)\b",
    "asset quality": r"\b(credit cost|gnpa|npa|slippage)\b",
    "margin": r"\b(margin|ebitda|opm|ebit|nim)\b",
    "capex": r"\b(capex|capital expenditure|capacity addition|investment of)\b",
    "profit": r"\b(pat|profit|earnings|eps|roe|roce|roa)\b",
    "revenue growth": r"\b(revenue|top[- ]?line|sales|growth|volume|turnover|order (book|inflow))\b",
}
NUMBER = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:%|percent|per cent|bps|basis points)|(?:₹|rs\.?|inr)\s?[\d,]+|[\d,]+\s*(?:crore|cr\b|billion|bn)|"
    r"\b(double[- ]digit|single[- ]digit|mid[- ]teens|high[- ]teens|low[- ]teens|teens|high single|mid single|low double|"
    r"twenties|20s)\b",
    re.I,
)
WORDS = {
    "high single": (7, 9), "mid single": (4, 6), "single-digit": (3, 9), "single digit": (3, 9),
    "low double": (10, 12), "double-digit": (10, None), "double digit": (10, None), "low teens": (11, 13),
    "mid-teens": (14, 16), "mid teens": (14, 16), "high teens": (17, 19), "high-teens": (17, 19), "teens": (11, 19),
    "twenties": (20, 29), "20s": (20, 29),
}


def call_quarter(date: str) -> str | None:
    """'Oct 2026' call discusses the quarter ended Sep 2026 (screener column label 'Sep 2026')."""
    m = re.match(r"([A-Z][a-z]{2})\s+(\d{4})", date or "")
    if not m:
        return None
    mon, yr = MONTHS.get(m.group(1)), int(m.group(2))
    if not mon:
        return None
    qend = max([q for q in (3, 6, 9, 12) if q < mon] or [0])
    if qend == 0:
        qend, yr = 12, yr - 1
    return f"{['', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][qend]} {yr}"


def _q_index(cols: list[str], label: str) -> int | None:
    return cols.index(label) if label in cols else None


@store.cached("promises", ttl=7 * store.DAY)
def _transcript(url: str) -> dict:
    pages = docs.pdf_pages(url, max_pages=40) or []
    text = "\n".join(pages)
    analysts = set()
    for m in re.finditer(r"(?:question|line) (?:is |comes )?(?:from|of) (?:the line of )?(?:Mr\.?|Ms\.?)?\s*([A-Z][a-zA-Z]+(?: [A-Z][a-zA-Z]+){0,2})\s+(?:from|with|of)\s", text):
        analysts.add(m.group(1).split()[-1].lower())
    turns = docs.speaker_turns(pages)
    mgmt, asks = [], []
    for t in turns:
        spk = t["speaker"].lower()
        last = spk.split()[-1] if spk.split() else spk
        if "moderator" in spk or "operator" in spk:
            continue
        (asks if last in analysts else mgmt).append(t)
    return {"pages": len(pages), "analysts": sorted(analysts), "mgmt": mgmt, "asks": asks}


def _target(s: str) -> tuple[float | None, float | None, str | None]:
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:%|percent)?\s*(?:to|-|–|and)\s*(\d+(?:\.\d+)?)\s*(%|percent|per cent)", s, re.I)
    if m:
        return float(m.group(1)), float(m.group(2)), "%"
    m = re.search(r"(?:of|at|around|about|~|over|above|at least|upwards of|in excess of)?\s*(\d+(?:\.\d+)?)\s*(%|percent|per cent)", s, re.I)
    if m:
        v = float(m.group(1))
        lo_only = re.search(r"(at least|over|above|upwards of|in excess of|more than)\s*" + re.escape(m.group(1)), s, re.I)
        return v, (None if lo_only else v), "%"
    for w, (lo, hi) in WORDS.items():
        if w in s.lower():
            return float(lo), (float(hi) if hi else None), "%"
    return None, None, None


def _candidates(turns: list[dict]) -> list[dict]:
    out = []
    for t in turns:
        text = re.sub(r"(\d)\s+\.(\d)", r"\1.\2", t["text"])  # "14 .0%" → "14.0%" (PDF spacing)
        text = re.sub(r"\b([a-z])\s(rowth|argin|evenue)\b", r"\1\2", text)
        for s in docs.sentences(text):
            if s.endswith("?") or len(s) < 40 or re.match(r"(?i)(can you|could you|what|how|why|is it|so you)", s):
                continue
            if not STRONG_FWD.search(s) or not NUMBER.search(s):
                continue
            past = PAST.search(s)
            if past and not re.search(r"\b(will|expect\w*|should|aim|targeting|going forward|we are guiding|guide (for|to))\b", s[past.end():], re.I):
                continue  # describing what happened, not what's promised
            metric = next((k for k, rx in METRIC.items() if re.search(rx, s, re.I)), None)
            if not metric:
                continue
            lo, hi, unit = _target(s)
            score = 2 + (lo is not None) * 2 + bool(re.search(r"guid|target", s, re.I)) * 2 + (metric in ("revenue growth", "margin", "loan growth")) * 1
            out.append({
                "metric": metric, "quote": s, "page": t["page"], "speaker": t["speaker"], "lo": lo, "hi": hi, "unit": unit, "score": score,
                "basis": "qoq" if QOQ.search(s) else "yoy", "horizon": "year" if FULLYEAR.search(s) else "near",
            })
    out.sort(key=lambda x: -x["score"])
    seen, dedup = set(), []
    for c in out:
        k = (c["metric"], c["lo"], c["hi"])
        if k in seen and c["lo"] is not None:
            continue
        seen.add(k)
        dedup.append(c)
    return [c for c in dedup if c["score"] >= 3][:6]


def _llm_extract(turns: list[dict], name: str, quarter: str) -> list[dict] | None:
    if not llm.available():
        return None
    body = "\n".join(f"[p{t['page']}] {t['speaker']}: {t['text']}" for t in turns)[:60000]
    res = llm.json_call(
        "extract",
        "From management's remarks on an Indian company's earnings call, extract explicit forward-looking GUIDANCE on headline "
        "numbers (revenue/volume growth, margins, capex, loan growth, credit cost, profit). Only statements management made about "
        "the future, with a number or numeric range. Quote verbatim. Ignore statements about the past.",
        f"Company: {name}\nCall discussing quarter ended {quarter}\n\n{body}",
        {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "metric": {"type": "string", "enum": list(METRIC)},
                            "quote": {"type": "string"},
                            "page": {"type": "integer"},
                            "lo": {"type": ["number", "null"]},
                            "hi": {"type": ["number", "null"]},
                            "period": {"type": "string"},
                        },
                        "required": ["metric", "quote", "page", "lo", "hi", "period"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["items"],
            "additionalProperties": False,
        },
        max_tokens=3000,
    )
    if not res:
        return None
    for it in res["items"]:
        it["unit"] = "%"
        it["speaker"] = "Management"
        it["basis"] = "qoq" if QOQ.search(it["quote"]) else "yoy"
        it["horizon"] = "year" if FULLYEAR.search(it["quote"] + " " + it.get("period", "")) else "near"
    return res["items"][:6]


def _check(item: dict, qtable: list[dict], q_label: str, financial: bool = False) -> dict:
    """Compare a promise with the quarters reported after the call."""
    labels = [q["q"] for q in qtable]
    i = _q_index(labels, q_label)
    after = qtable[i + 1 :] if i is not None else []
    metric, lo, hi = item["metric"], item.get("lo"), item.get("hi")
    if lo is None:
        return {"verdict": "not checkable", "why": "An absolute or qualitative target — nothing in the quarterly P&L to hold it to."}
    if metric == "margin" and financial:
        return {"verdict": "not checkable", "why": "NIM/spread isn't in the standardised P&L — check the investor presentation."}
    if metric not in ("revenue growth", "margin"):
        where = {"loan growth": "AUM/loan book is in the investor presentation", "asset quality": "credit cost is in the investor presentation"}.get(metric, "check the next annual report")
        return {"verdict": "not checkable", "why": f"{metric.capitalize()} isn't in the quarterly P&L — {where}."}
    if ANAPHOR.search(item["quote"].strip()):
        return {"verdict": "not checkable", "why": "The promise refers back to something said earlier (“it/this”) — open the transcript for context."}
    if SEGMENT.search(item["quote"]):
        return {"verdict": "not checkable", "why": "Segment-level guidance — the consolidated P&L can't confirm it; see the segment note in the results filing."}
    if not after:
        return {"verdict": "too early", "why": "No quarter reported since this call."}
    partial = item.get("horizon") == "year" and len(after) < 4
    if metric == "revenue growth":
        if lo > 60:
            return {"verdict": "not checkable", "why": "Looks like a level, not a growth rate."}
        key = "rev_qoq" if item.get("basis") == "qoq" else "rev_yoy"
        vals = [q[key] for q in (after[:1] if key == "rev_qoq" else after) if q.get(key) is not None]
        if not vals:
            return {"verdict": "too early", "why": "No comparison yet."}
        actual = round(sum(vals) / len(vals), 1)
        ok = actual >= lo - 1.0
        verdict = ("on track" if ok else "behind") if partial else ("delivered" if ok else "missed")
        span = after[:1] if key == "rev_qoq" else after
        return {
            "verdict": verdict,
            "actual": actual,
            "why": f"Revenue {'QoQ' if key == 'rev_qoq' else 'YoY'} {actual:+.1f}% "
                   f"{'next quarter' if key == 'rev_qoq' else 'on average'} ({', '.join(q['q'] for q in span)}) vs {lo:g}{'–' + format(hi, 'g') if hi and hi != lo else ''}% promised"
                   + (f" — {len(after)} of 4 quarters in." if partial else "."),
            "caveat": "Company may guide in constant currency or on a segment; this checks reported consolidated revenue." if re.search(r"constant currency|cc\b|segment|business", item["quote"], re.I) else None,
        }
    vals = [q["opm"] for q in after if q.get("opm") is not None]
    if not vals:
        return {"verdict": "too early", "why": "No margin reported since."}
    actual = round(sum(vals) / len(vals), 1)
    band_lo, band_hi = lo - 0.5, (hi if hi is not None else 999) + 0.5
    if lo > 60:
        return {"verdict": "not checkable", "why": "Number doesn't look like a margin."}
    verdict = "delivered" if actual >= band_lo else "missed"
    if partial:
        verdict = "on track" if verdict == "delivered" else "behind"
    return {
        "verdict": verdict,
        "actual": actual,
        "why": f"Operating margin averaged {actual}% in the {len(vals)} quarter{'s' if len(vals) > 1 else ''} since (guided {lo:g}{'–' + format(hi, 'g') if hi and hi != lo else ''}%).",
        "caveat": "Screener's OPM is EBITDA-based; if management guided EBIT/operating margin the bands differ." if re.search(r"\bebit\b|operating margin", item["quote"], re.I) else None,
    }


TOPICS = {
    "margins": r"margin|pricing|price hike|discount",
    "demand": r"demand|volume|growth|slowdown|pickup",
    "competition": r"competit|market share|new entrant",
    "capex & capacity": r"capex|capacity|utili[sz]ation|plant",
    "costs": r"raw material|input cost|commodity|wage|employee cost",
    "working capital": r"working capital|receivable|inventory|cash flow",
    "asset quality": r"npa|credit cost|slippage|collection|delinquen",
    "guidance": r"guidance|outlook|visibility",
    "M&A": r"acqui|inorganic|merger",
    "AI & tech": r"\bai\b|gen ?ai|automation|digital",
    "exports & US": r"export|us market|tariff|usfda|fda",
}


def _pressed(asks: list[dict]) -> list[dict]:
    c = Counter()
    for t in asks:
        for k, rx in TOPICS.items():
            if re.search(rx, t["text"], re.I):
                c[k] += 1
    return [{"topic": k, "n": n} for k, n in c.most_common(4) if n >= 2]


def build(symbol: str, co: dict, n_calls: int = 4) -> dict:
    calls = [c for c in co["docs"]["concalls"] if c.get("transcript")][:n_calls]
    from .research import financials  # local import to avoid a cycle

    fin = financials(co)
    qtable = fin["quarters"]
    # need all quarters, not just the last 8, to locate older calls
    full = co["quarters"]
    if full:
        qtable = financials({**co, "quarters": full})["quarters"]
        allq = []
        cols = full["cols"]
        top = next((v for k, v in full["rows"].items() if k.startswith(("Sales", "Revenue"))), [])
        opm = next((v for k, v in full["rows"].items() if k.startswith(("OPM", "Financing Margin"))), [])
        for i, cl in enumerate(cols):
            y = top[i - 4] if i >= 4 and i - 4 < len(top) else None
            p = top[i - 1] if i >= 1 and i - 1 < len(top) else None
            cur = top[i] if i < len(top) else None
            allq.append({"q": cl, "revenue": cur, "opm": opm[i] if i < len(opm) else None,
                         "rev_yoy": round((cur - y) / abs(y) * 100, 1) if y and cur is not None else None,
                         "rev_qoq": round((cur - p) / abs(p) * 100, 1) if p and cur is not None else None})
        qtable = allq
    financial = fin.get("is_financial") or bool(re.search(r"bank|nbfc|finance|microfinance", co.get("about", "")[:300], re.I))
    items, pressed, used, silent = [], Counter(), [], 0
    for c in calls:
        q = call_quarter(c.get("date"))
        try:
            tr = _transcript(c["transcript"])
        except Exception as e:
            print("[promises] transcript failed", e)
            continue
        if not tr or not tr["mgmt"]:
            continue
        used.append({"date": c["date"], "quarter": q, "url": c["transcript"], "pages": tr["pages"]})
        ext = _llm_extract(tr["mgmt"], co["name"], q) or _candidates(tr["mgmt"])
        if not any(e.get("lo") is not None for e in ext):
            silent += 1
        call_items = []
        for it in ext:
            chk = _check(it, qtable, q, financial)
            call_items.append({**it, **chk, "call": c["date"], "quarter": q, "url": c["transcript"]})
        # checkable promises first; at most two "not checkable" per call
        call_items.sort(key=lambda x: (x["verdict"] == "not checkable", x["lo"] is None, -x.get("score", 0)))
        nc = 0
        for ci in call_items:
            if ci["verdict"] == "not checkable":
                nc += 1
                if nc > 2:
                    continue
            items.append(ci)
            if len([x for x in items if x["call"] == c["date"]]) >= 4:
                break
        for p in _pressed(tr["asks"]):
            pressed[p["topic"]] += p["n"]
    if llm.available() and items:
        items = _judge(co["name"], items, qtable) or items
    good, bad = ("delivered", "on track"), ("missed", "behind")
    checked = [i for i in items if i["verdict"] in good + bad]
    sc = {
        "checked": len(checked),
        "delivered": sum(1 for i in checked if i["verdict"] in good),
        "missed": sum(1 for i in checked if i["verdict"] in bad),
        "early": sum(1 for i in items if i["verdict"] == "too early"),
        "other": sum(1 for i in items if i["verdict"] == "not checkable"),
        "silent_calls": silent,
    }
    return {
        "calls": used,
        "n_calls": len(used),
        "items": items,
        "scorecard": sc,
        "read": _read(sc, len(used), pressed),
        "pressed": [{"topic": k, "n": n} for k, n in pressed.most_common(5)],
        "method": "model-extracted" if llm.available() else "cue-scored extraction (no model)",
    }


def _read(sc: dict, n: int, pressed: Counter) -> str:
    if not n:
        return "No concall transcripts found."
    parts = []
    if sc["checked"]:
        parts.append(f"{sc['delivered']} of {sc['checked']} checkable promises met or on track")
    if sc["silent_calls"] >= max(2, n - 1):
        parts.append(f"management gave no numeric guidance on {sc['silent_calls']} of {n} calls")
    if pressed:
        top = pressed.most_common(1)[0][0]
        parts.append(f"analysts kept pressing on {top}")
    return ("; ".join(parts) + ".").capitalize() if parts else "Nothing checkable yet."


def _judge(name: str, items: list[dict], qtable: list[dict]) -> list[dict] | None:
    """Stronger model only for the judgement call — numbers are already computed."""
    table = [{k: q.get(k) for k in ("q", "revenue", "rev_yoy", "opm")} for q in qtable[-10:]]
    res = llm.json_call(
        "judge",
        "You audit management credibility. For each guidance item, decide delivered / missed / too early / not checkable using ONLY "
        "the quarterly table given (reported after the call's quarter). One sentence of reasoning citing numbers. No outside knowledge.",
        f"Company: {name}\nQuarterly table (₹ cr, rev_yoy %, opm %): {table}\nItems: {[{k: i.get(k) for k in ('metric', 'quote', 'lo', 'hi', 'quarter', 'verdict', 'why')} for i in items]}",
        {
            "type": "object",
            "properties": {
                "verdicts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"i": {"type": "integer"}, "verdict": {"type": "string", "enum": ["delivered", "missed", "on track", "behind", "too early", "not checkable"]}, "why": {"type": "string"}},
                        "required": ["i", "verdict", "why"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["verdicts"],
            "additionalProperties": False,
        },
        effort="medium",
    )
    if not res:
        return None
    for v in res["verdicts"]:
        if 0 <= v["i"] < len(items):
            items[v["i"]].update({"verdict": v["verdict"], "why": v["why"], "judged_by": "model"})
    return items
