"""PUSH — the windowed digest and triggered alerts.

Pipeline per window:
  fetch filings (per holding, cached 20 min) → classify (rules, PDF head only when the
  filing might be material) → merge duplicates (one PDF filed under three subjects) →
  score = materiality × the investor's category weight × position size (+ price reaction,
  + network relevance) → enrich by type:
      results            → Revenue/EBITDA/PBT/PAT QoQ & YoY straight from the filing's XBRL
      management_change  → who they are (from the filing's own profile annexure) + paths to them
      material events    → the contact best placed to explain it, and why
      everything else    → a one-line summary, or collapsed into "routine"
"""
from __future__ import annotations

import concurrent.futures as cf
import datetime as dt
import re

from rapidfuzz import fuzz

from .. import store
from ..network import graph
from ..sources import docs, nse, prices
from . import llm, people, taxonomy

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

MAYBE_MATERIAL = re.compile(
    r"management|director|appointment|resign|retire|cessation|kmp|board meeting|acquisition|amalgamation|"
    r"merger|scheme|sale or disposal|restructuring|general updates|^updates$|press release|credit rating|"
    r"action|order|litigation|insolvency|issue of securities|capacity|commercial production|news verification|"
    r"rumour|financial result|integrated filing|bagging|pledge|buy ?back",
    re.I,
)


def portfolio() -> dict:
    return store.load_json("portfolio.json")


def prefs() -> dict:
    return store.load_json("prefs.json")


def now_ist() -> dt.datetime:
    """FINN_ASOF pins the clock (e.g. a hosted demo frozen to its data snapshot)."""
    import os

    asof = os.getenv("FINN_ASOF")
    if asof:
        return dt.datetime.fromisoformat(asof).replace(tzinfo=IST)
    return dt.datetime.now(IST)


def window_bounds(hours: int, end: dt.datetime | None = None) -> tuple[dt.datetime, dt.datetime]:
    end = end or now_ist()
    return end - dt.timedelta(hours=hours), end


# ------------------------------------------------------------------ classification

def _pdf_head(url: str | None) -> str:
    if not url:
        return ""
    pages = docs.pdf_pages(url, max_pages=12) or []
    return " ".join(pages[:3])[:6000]


def classify_filing(a: dict) -> dict:
    head = _pdf_head(a["pdf"]) if MAYBE_MATERIAL.search(a["nse_desc"]) else ""
    c = taxonomy.classify(a["nse_desc"], f"{a['text']} {a['summary']}", head)
    return {**a, **c, "_head": head}


def one_liner(a: dict) -> str:
    """The shortest faithful description of a filing — or, failing that, just NSE's subject."""
    line = clean_line(_one_liner_raw(a))
    if not line or len(line) < 12 or JUNK.search(line) or re.match(r"(?i)(disclosure|intimation) (under|pursuant)|regulation \d+", line):
        return a.get("nse_desc") or line
    return line


def _one_liner_raw(a: dict) -> str:
    s = a.get("summary") or ""
    if s:
        sents = docs.sentences(s)
        if sents:
            return _trim(" ".join(sents[:1]), 220)
    t = a.get("text") or ""
    t2 = re.sub(r"^.*?has (informed|intimated|submitted to) the Exchange,? (about|regarding|that)?\s*(the\s+)?", "", t, flags=re.I).strip()
    if re.search(r"\bSub\s*:", t2):
        t2 = re.split(r"\bSub\s*:\s*", t2, maxsplit=1)[1]
    if re.match(r"(?i)(disclosure|intimation) (under|pursuant to) regulation|please find (attached|enclosed)", t2):
        t2 = ""
    # NSE's text is often just "<Company> has informed the Exchange about <subject>" — no content
    if t2 and len(t2) > 45 and fuzz.token_set_ratio(t2, a.get("nse_desc", "")) < 80:
        return _trim(t2[0].upper() + t2[1:], 220)
    head = a.get("_head") or ""
    subj = subject_line(head)
    if subj:
        return _trim(subj, 200)
    if head and llm.available():
        r = llm.json_call(
            "extract",
            "Summarise an Indian exchange filing for a busy fund manager in at most 22 words. Facts only, no boilerplate.",
            head[:4000],
            {"type": "object", "properties": {"line": {"type": "string"}}, "required": ["line"], "additionalProperties": False},
        )
        if r:
            return r["line"]
    if head:
        best = _best_sentence(head)
        if best:
            return _trim(best, 220)
    return "" if re.search(r"has (informed|intimated) the Exchange", t) else _trim(t, 160)


def subject_line(head: str) -> str | None:
    """Every exchange letter has a 'Sub:' line — usually the best one-line title there is."""
    m = re.search(
        r"\bSub(?:ject)?\s*[:.\-–]+\s*(.{12,260}?)(?=\s(?:Ref(?:erence)?\s*[:.]|Dear\b|Sir\b|Madam\b|Pursuant\b|In terms\b|"
        r"With reference\b|Further to\b|This is to\b|We wish\b|We would\b|In furtherance\b|In compliance\b|The Board\b|We hereby\b|We refer\b|In continuation\b)|$)",
        head,
    )
    if not m:
        return None
    s = re.sub(r"\s+", " ", m.group(1)).strip(" .-–:")
    s = re.sub(r"^(Intimation|Disclosure|Submission|Information)\s+(under|pursuant to|in terms of)\s+Regulation[^–\-:]*[–\-:]\s*", "", s, flags=re.I)
    s = re.sub(r"^(Intimation|Disclosure|Submission)( of| regarding| about| on| for)?\s+", "", s, flags=re.I)
    if re.match(r"(?i)^(disclosure )?under regulation \d+|^regulation \d+|^outcome of (the )?board meeting( held on [\w\s,]+)?$", s) or len(s) < 12:
        return None
    return s[0].upper() + s[1:]


JUNK = re.compile(
    r"(?i)not debarred|not related to any|is not disqualified|thanking you|yours (faithfully|truly|sincerely)|company secretary|\bCIN\b|regd\.? office|membership no|"
    r"name of the|designation of|contact details|meeting (commenced|concluded)|commenced at|concluded at|"
    r"please find (attached|enclosed)|for your (information|records)|kindly take|take (the same )?on record|"
    r"^(sir|madam|dear)\b|^about [A-Z]"
)
VERB = re.compile(r"(?i)\b(is|are|has|have|had|was|were|will|shall|approv\w*|announc\w*|receiv\w*|enter\w*|acquir\w*|award\w*|"
                  r"secur\w*|sign\w*|launch\w*|impos\w*|pass\w*|declar\w*|allot\w*|appoint\w*|complet\w*|incorporat\w*)\b")


def _best_sentence(text: str) -> str | None:
    body = re.split(r"(?i)dear sir|dear madam|sir/madam|sub\s*:", text, maxsplit=1)[-1]
    cands = docs.sentences(body)
    best, bs = None, 0
    for s in cands[:40]:
        if JUNK.search(s) or not VERB.search(s):
            continue
        sc = 0
        sc += 3 * bool(re.search(r"₹|rs\.?\s?\d|inr|crore|million|billion|%", s, re.I))
        sc += 2 * bool(re.search(r"approv|award|receiv|acquir|appoint|resign|enter|sign|launch|order|rating|penalt|imposed|fine", s, re.I))
        sc -= 3 * bool(re.search(r"regulation 30|listing obligations|pursuant to", s, re.I))
        if sc > bs:
            best, bs = s, sc
    return best


def clean_line(s: str) -> str:
    """Strip letter furniture that leaks from PDFs: '/Madam, S Sub: …', trailing 'This is further to…'."""
    s = re.sub(r"\s+", " ", s or "").strip()
    if re.search(r"\b(?:Sub(?:ject)?|Re)\s*[:.]", s[:80]):
        s = re.split(r"\b(?:Sub(?:ject)?|Re)\s*[:.]\s*", s, maxsplit=1)[1]
    s = re.split(r"\s(?:We enclose|Please find|Enclosed)\b", s)[0]
    s = re.split(r"\s(?:This is further to|Further to our|Dear Sir|We wish to inform|Pursuant to)\b", s)[0]
    s = re.sub(r"^(Intimation|Disclosure|Submission)\s+(of|regarding|about|on|for|under)\s+", "", s, flags=re.I)
    s = s.strip(" '\".,;:-–")
    return s[:1].upper() + s[1:] if s else s


def _trim(s: str, n: int) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= n else s[: n - 1].rsplit(" ", 1)[0] + "…"


# ------------------------------------------------------------------ titles

def title_for(item: dict) -> str:
    cat = item["category"]
    if cat == "results" and item.get("results"):
        return f"{item['results']['quarter']} results"
    if cat == "management_change" and item.get("people"):
        p = item["people"][0]
        verb = {"appointed": "joins", "assumed office": "takes office", "resigned": "resigns", "retired": "retires",
                "ceased": "steps down", "re-appointed": "re-appointed"}.get(p.get("action"), "—")
        role = (p.get("role") or "").replace("Non- Executive", "Non-Executive")
        return f"{p['name']} {verb}" + (f" · {role}" if role else "")
    line = item.get("line") or item.get("nse_desc")
    return _trim(line, 96)


# ------------------------------------------------------------------ digest

def _fetch_symbol(sym: str, start: str, end: str) -> list[dict]:
    try:
        return nse.announcements(sym, start, end)
    except Exception as e:
        print("[digest] fetch failed", sym, e)
        return []


def build(hours: int = 24, end: dt.datetime | None = None, symbols: list[str] | None = None) -> dict:
    start_dt, end_dt = window_bounds(hours, end)
    pf = portfolio()
    pr = prefs()
    weights = {h["symbol"]: h["weight"] for h in pf["holdings"]}
    syms = symbols or list(weights)
    s_iso, e_iso = start_dt.date().isoformat(), end_dt.date().isoformat()

    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        raw = sum(ex.map(lambda s: _fetch_symbol(s, s_iso, e_iso), syms), [])
    lo, hi = start_dt.strftime("%Y-%m-%d %H:%M:%S"), end_dt.strftime("%Y-%m-%d %H:%M:%S")
    raw = [a for a in raw if lo <= a["ts"] <= hi]

    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        classified = list(ex.map(classify_filing, raw))

    items = _merge(classified)
    px = _prices(syms)
    with cf.ThreadPoolExecutor(max_workers=4) as ex:
        items = list(ex.map(lambda it: _enrich(it, weights, pr, px), items))
    items.sort(key=lambda x: (-x["rank"], x["ts"]), reverse=False)

    head = [i for i in items if i["tier"] == "headline"]
    brief = [i for i in items if i["tier"] == "brief"]
    routine = [i for i in items if i["tier"] == "routine"]
    for i in items:
        i.pop("_head", None)
    return {
        "window": {"hours": hours, "start": start_dt.isoformat(), "end": end_dt.isoformat()},
        "counts": {"total": len(items), "raw_filings": len(raw), "headline": len(head), "brief": len(brief), "routine": len(routine)},
        "headline": head,
        "brief": brief,
        "routine": routine,
        "lede": lede(head, brief, routine, hours),
        "model": "rules+model" if llm.available() else "rules",
    }


def _merge(items: list[dict]) -> list[dict]:
    """Same PDF filed under several NSE subjects → one item; results-day companions fold into results."""
    by_pdf: dict[str, dict] = {}
    for a in items:
        # same PDF, or same company filing at the same minute (one letter, several subjects)
        k = a["pdf"] or a["id"]
        same_minute = next((kk for kk, m in by_pdf.items() if m["symbol"] == a["symbol"] and m["ts"][:16] == a["ts"][:16]), None)
        k = same_minute or k
        if k in by_pdf:
            m = by_pdf[k]
            m["also_filed_as"] = sorted(set(m.get("also_filed_as", []) + [a["nse_desc"]]) - {m["nse_desc"]})
            m["cats"] = sorted(set(m.get("cats", [m["category"]])) | {a["category"]})
            if a["score"] > m["score"]:
                for f in ("category", "score", "tier", "flags", "via"):
                    m[f] = a[f]
            continue
        by_pdf[k] = dict(a)
    # near-duplicates: the same event filed twice an hour apart (board approval, then allotment)
    out = []
    for a in sorted(by_pdf.values(), key=lambda x: x["ts"]):
        sig = f"{a['summary'] or a['text']}"
        twin = next(
            (m for m in out if m["symbol"] == a["symbol"] and m["category"] == a["category"]
             and _is_mgmt_desc(m) == _is_mgmt_desc(a)  # a board-change letter never folds into a results card
             and abs((dt.date.fromisoformat(m["ts"][:10]) - dt.date.fromisoformat(a["ts"][:10])).days) <= 2
             and ((fuzz.token_set_ratio(sig, m["summary"] or m["text"]) >= 75 and len(sig) > 40)
                  or (_big_numbers(sig) & _big_numbers(m["summary"] or m["text"]))  # same "5,74,25,740 warrants"
                  or (a.get("_head") and m.get("_head") and abs(_mins(a["ts"]) - _mins(m["ts"])) <= 90
                      and fuzz.ratio(a["_head"][:1500], m["_head"][:1500]) >= 90))),
            None,
        )
        if twin:
            twin.setdefault("updates", []).append({"ts": a["ts"], "desc": a["nse_desc"], "pdf": a["pdf"], "text": a["summary"] or a["text"]})
            continue
        out.append(dict(a))
    results_days = {(a["symbol"], a["ts"][:10]) for a in out if a["category"] == "results" and a["tier"] == "headline"}
    final = []
    for a in out:
        is_companion = (a["symbol"], a["ts"][:10]) in results_days and a["category"] in ("results", "press_release", "investor_meetings", "earnings_call", "unclassified") and a["tier"] != "headline"
        if is_companion:
            host = next(x for x in out if x["symbol"] == a["symbol"] and x["ts"][:10] == a["ts"][:10] and x["category"] == "results" and x["tier"] == "headline")
            host.setdefault("companions", []).append({"desc": a["nse_desc"], "pdf": a["pdf"]})
            continue
        final.append(a)
    # keep only the strongest results card per company (board outcome + XBRL integrated filing etc.)
    seen, dedup = set(), []
    for a in sorted(final, key=lambda x: -x["score"]):
        key = (a["symbol"], "results") if a["category"] == "results" and a["tier"] == "headline" and not _is_mgmt_desc(a) else id(a)
        if key in seen:
            continue
        seen.add(key)
        dedup.append(a)
    return dedup


def _is_mgmt_desc(a: dict) -> bool:
    return bool(re.search(r"management|director|appointment|resign|retire|cessation|kmp", a.get("nse_desc", ""), re.I))


def _big_numbers(s: str) -> set[str]:
    """Distinctive figures (≥6 digits once commas are dropped) — a strong 'same event' signal."""
    return {n.replace(",", "") for n in re.findall(r"\d[\d,]{5,}\d", s or "") if len(n.replace(",", "")) >= 6 and not re.match(r"20\d\d", n)}


def _mins(ts: str) -> float:
    try:
        return dt.datetime.strptime(ts[:16], "%Y-%m-%d %H:%M").timestamp() / 60
    except Exception:
        return 0


def _prices(syms):
    out = {}
    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        for s, p in zip(syms, ex.map(lambda s: prices.eod(s), syms)):
            if p:
                out[s] = {"bars": p["bars"], "moves": {m["d"]: m for m in prices.day_moves(p["bars"])}}
    return out


def _reaction(sym: str, ts: str, px: dict) -> dict | None:
    """The first trading session on/after the filing (after 15:30 → next session)."""
    p = px.get(sym)
    if not p:
        return None
    d = ts[:10]
    after_close = ts[11:16] >= "15:30"
    for b in p["bars"]:
        if b["d"] > d or (b["d"] == d and not after_close):
            m = p["moves"].get(b["d"])
            return m and {"date": m["d"], "chg_pct": m["chg_pct"], "vol_x": m["vol_x"]}
    return None


def _enrich(it: dict, weights: dict, pr: dict, px: dict) -> dict:
    sym = it["symbol"]
    cat = it["category"]
    it["label"] = taxonomy.LABELS.get(cat, cat)
    it["company"] = graph.org_name(sym) if sym in graph.orgs() else (it.get("company") or sym)
    it["weight"] = weights.get(sym)
    it["line"] = one_liner(it)
    boost, why_rank = 0.0, []

    if it["tier"] == "headline" or cat in ("results", "management_change"):
        # one letter can carry both results and a board change (Dixon, 30 Oct) — show both
        if cat == "results" or "results" in it.get("cats", []) or cat == "management_change" and re.search(
            r"(un-?audited|audited)[^.]{0,60}financial results[^.]{0,40}(quarter|half[- ]year)", (it.get("_head") or "")[:3000], re.I
        ):
            try:
                r = nse.headline_results(sym)
            except Exception as e:
                r = None
                print("[digest] results failed", sym, e)
            if r and _results_fresh(r, it["ts"]):
                it["results"] = r
                it["results_read"] = results_read(r)
                it["line"] = it["results_read"] or it["line"]
        mgmt_letter = cat == "management_change" or "management_change" in it.get("cats", []) or re.search(
            r"management|director|appointment|resign|retire|kmp", it.get("nse_desc", ""), re.I)
        if mgmt_letter and it.get("pdf"):
            try:
                ppl = people.people_in_filing(it["pdf"], sym, it["ts"])
            except Exception as e:
                ppl = []
                print("[digest] people failed", sym, e)
            for p in ppl:
                p["paths"] = graph.paths_to_person(p, limit=3)
                p["you"] = graph.me_overlap(p)
            it["people"] = ppl[:4]
            if ppl and cat == "results" and _is_mgmt_desc(it):
                # e.g. Dixon's board letter carried results *and* a new director: the numbers already have
                # their own card (the board-outcome filing), so this one is about the people.
                cat = it["category"] = "management_change"
                it["label"] = taxonomy.LABELS[cat]
                for k in ("results", "results_read"):
                    it.pop(k, None)
            if ppl and not it.get("results"):
                it["line"] = "; ".join(
                    f"{p['name']} {p['action'] or 'named'}" + (f" as {p['role']}" if p.get("role") and p["action"] in ("appointed", "assumed office", "re-appointed") else "")
                    for p in ppl[:3]
                ) + "."
            if ppl and any(p.get("paths") for p in ppl):
                boost += 4
                why_rank.append("someone in your network knows them")
            if ppl and ppl[0].get("action") == "re-appointed" and it["score"] > 40:
                it["score"] -= 20
        if it["tier"] == "headline" or cat in ("M&A", "capex", "litigation", "fund_raise", "credit_rating"):
            it["call"] = graph.who_knows_org(sym, limit=2)

    react = _reaction(sym, it["ts"], px)
    if react:
        it["reaction"] = react
        if abs(react["chg_pct"]) >= pr["price_move_pct"] or (react["vol_x"] or 0) >= pr["volume_multiple"]:
            boost += 8
            why_rank.append(f"stock moved {react['chg_pct']:+.1f}% on {react['vol_x']}× volume")

    w = weights.get(sym, 2.0)
    size = 0.75 + 0.05 * w  # 3% → 0.90, 9% → 1.20
    it["rank"] = round(it["score"] * pr["category_weight"].get(cat, 1.0) * size + boost, 1)
    it["tier"] = "headline" if it["rank"] >= 62 else "brief" if it["rank"] >= 30 else "routine"
    if w >= 7:
        why_rank.append(f"top position ({w:.1f}%)")
    it["why_rank"] = why_rank
    it["title"] = title_for(it)
    it["alert"] = cat in pr["alert_categories"] and it["tier"] == "headline"
    return it


def _results_fresh(r: dict, filed_ts: str) -> bool:
    try:
        return 0 <= (dt.date.fromisoformat(filed_ts[:10]) - dt.date.fromisoformat(r["period_end"])).days <= 75
    except Exception:
        return False


def results_read(r: dict) -> str:
    rev, mid, _, pat = (r["metrics"] + [{}] * 4)[:4]
    bits = []
    if rev.get("yoy") is not None:
        bits.append(f"{rev['label']} {rev['yoy']:+.0f}% YoY")
    if pat.get("yoy") is not None:
        bits.append(f"PAT {pat['yoy']:+.0f}% YoY")
    if mid.get("margin") is not None and mid.get("margin_yago") is not None:
        bp = round((mid["margin"] - mid["margin_yago"]) * 100)
        bits.append(f"{mid['label']} margin {mid['margin']:.1f}% ({bp:+d} bp YoY)")
    elif mid.get("yoy") is not None:
        bits.append(f"{mid['label']} {mid['yoy']:+.0f}% YoY")
    if rev.get("yoy") is not None and pat.get("yoy") is not None:
        gap = pat["yoy"] - rev["yoy"]
        if gap > 8:
            bits.append("operating leverage showing")
        elif gap < -8:
            bits.append("profit lagging sales")
    return " · ".join(bits)


def lede(head, brief, routine, hours) -> str:
    span = "today" if hours <= 24 else f"in the last {hours // 24} days"
    if not head and not brief:
        if not routine:
            return f"Quiet {span} — no new filings from your holdings."
        return f"Quiet {span}: {len(routine)} routine filing{'s' if len(routine) != 1 else ''}, nothing that needs you."
    names, seen = [], set()
    for h in head:
        if h["company"] in seen:
            continue
        seen.add(h["company"])
        names.append(f"{h['company']} ({h['label'].lower()})")
        if len(names) == 3:
            break
    s = f"{len(head)} thing{'s' if len(head) != 1 else ''} worth your attention {span}"
    if names:
        s += ": " + ", ".join(names)
    s += f". {len(routine)} routine filings collapsed."
    return s


# ------------------------------------------------------------------ alerts

def alerts(hours: int = 24 * 7, end: dt.datetime | None = None, price_move_pct: float | None = None,
           volume_multiple: float | None = None, digest: dict | None = None) -> dict:
    pr = prefs()
    pm = price_move_pct if price_move_pct is not None else pr["price_move_pct"]
    vm = volume_multiple if volume_multiple is not None else pr["volume_multiple"]
    start_dt, end_dt = window_bounds(hours, end)
    pf_ = portfolio()
    held = [h["symbol"] for h in pf_["holdings"]]
    syms = held + [s for s in pf_.get("research", []) if s not in held]  # holdings + watchlist
    px = _prices(syms)
    d = digest or build(hours, end)
    filings_by = {}
    for it in d["headline"] + d["brief"]:
        filings_by.setdefault(it["symbol"], []).append(it)
    market = []
    s_d = start_dt.date().isoformat()
    for sym, p in px.items():
        bars = p["bars"]
        # always look at least at the last 2 sessions so a weekend window isn't empty
        recent = [b["d"] for b in bars if b["d"] >= s_d] or [b["d"] for b in bars[-2:]]
        for day in recent:
            m = p["moves"].get(day)
            if not m:
                continue
            hits = []
            if abs(m["chg_pct"]) >= pm:
                hits.append("price")
            if (m["vol_x"] or 0) >= vm:
                hits.append("volume")
            if not hits:
                continue
            explained = [f for f in filings_by.get(sym, []) if f["ts"][:10] <= day and (dt.date.fromisoformat(day) - dt.date.fromisoformat(f["ts"][:10])).days <= 3]
            market.append(
                {
                    "symbol": sym,
                    "held": sym in held,
                    "company": graph.org_name(sym),
                    "date": day,
                    "close": round(m["c"], 2),
                    "chg_pct": m["chg_pct"],
                    "vol_x": m["vol_x"],
                    "hits": hits,
                    "explained_by": [{"id": f["id"], "title": f["title"], "label": f["label"]} for f in explained[:2]],
                    "spark": [round(b["c"], 2) for b in bars if b["d"] <= day][-30:],
                    "call": graph.who_knows_org(sym, limit=1) if not explained else [],
                }
            )
    market.sort(key=lambda x: (x["date"], abs(x["chg_pct"]) + 2 * (x["vol_x"] or 0)), reverse=True)
    filing_alerts = [
        {k: it[k] for k in ("id", "symbol", "company", "ts", "title", "label", "category", "rank", "line")}
        for it in d["headline"] if it["category"] in pr["alert_categories"]
    ]
    return {
        "thresholds": {"price_move_pct": pm, "volume_multiple": vm, "lookback": pr["volume_lookback_days"], "categories": pr["alert_categories"]},
        "market": market,
        "filings": filing_alerts,
        "last_session": max((p["bars"][-1]["d"] for p in px.values()), default=None),
    }
