"""'Ask FINN' — one input box (typed or spoken) that routes to Push, Pull or the Network.

Routing is rules-first: the investor's questions are a small, repetitive set ("who can I call
on Dixon", "what changed this week", "dig into Kaynes"). Rules handle them instantly and for
free; only phrasing the rules don't recognise goes to the cheap model, which may only choose
from the same fixed set of tools — it never answers from its own knowledge.
"""
from __future__ import annotations

import functools
import re

from rapidfuzz import fuzz, process

from .. import store
from ..network import graph
from ..sources import screener
from . import digest, llm, research

INTENTS = ["digest", "research", "who_company", "who_person", "expertise", "reconnect", "prep", "help"]


@functools.lru_cache(maxsize=1)
def _names() -> dict[str, str]:
    """alias (lowercase) → org key"""
    out = {}
    for k, o in graph.orgs().items():
        out[k.lower()] = k
        out[o["name"].lower()] = k
        for a in o.get("aliases", []):
            out[a.lower()] = k
    return out


def resolve_company(text: str) -> str | None:
    t = text.lower()
    best = None
    for alias, key in sorted(_names().items(), key=lambda x: -len(x[0])):
        if len(alias) >= 3 and re.search(r"(?<![a-z])" + re.escape(alias) + r"(?![a-z])", t):
            best = key
            break
    if best:
        return best
    # an unknown ticker the investor typed — validate it against screener
    for tok in re.findall(r"\b[A-Z][A-Z0-9&]{2,14}\b", text):
        if tok in {"WHO", "CAN", "THE", "AND", "CFO", "CEO", "MD"}:
            continue
        if screener.company(tok):
            return tok
    return None


def people_index() -> list[dict]:
    """Everyone named in the last 30 days of portfolio filings, plus people the network knows."""
    hit = store.get("people_index", "v1", ttl=6 * store.HOUR)
    if hit is not None:
        return hit
    d = digest.build(24 * 30)
    idx = []
    for it in d["headline"] + d["brief"]:
        for p in it.get("people", []):
            idx.append({**p, "company": it["company"], "symbol": it["symbol"], "filing": it["pdf"], "ts": it["ts"]})
    store.put("people_index", "v1", idx)
    return idx


def find_person(q: str) -> dict | None:
    idx = people_index()
    names = [p["name"] for p in idx]
    for c in graph.contacts():
        for k in c.get("knows", []):
            if k["name"] not in names:
                idx.append({"name": k["name"], "career": {"orgs": [], "schools": []}, "bio": "", "company": None})
                names.append(k["name"])
    if not names:
        return None
    m = process.extractOne(q, names, scorer=fuzz.partial_token_sort_ratio)
    if m and m[1] >= 80:
        return idx[m[2]]
    return None


def route(q: str) -> dict:
    ql = q.lower().strip()
    if re.search(r"\b(what('?s| has)? (changed|happened|new)|today|this week|since|digest|catch me up|brief me|overnight)\b", ql):
        hours = 24
        if re.search(r"week|7 ?d", ql):
            hours = 168
        elif re.search(r"month|30 ?d", ql):
            hours = 720
        elif m := re.search(r"(\d+)\s*(day|d)\b", ql):
            hours = int(m.group(1)) * 24
        return {"intent": "digest", "hours": hours}
    if re.search(r"\b(reconnect|haven'?t (spoken|talked)|catch up with|warm|lost touch|stale)\b", ql):
        return {"intent": "reconnect"}
    if m := re.search(r"\b(?:expert|experts|go-?to|who understands|who knows about|who can explain|specialist)s?\b(?:\s+(?:on|in|for|about))?\s+(.+)", ql):
        topic = m.group(1).strip(" ?.")
        if not resolve_company(topic):
            return {"intent": "expertise", "topic": topic}
    if re.search(r"\b(prep|prepare|before i meet|meeting with|i'?m meeting|diligence)\b", ql):
        co = resolve_company(q)
        p = find_person(q) if not co else None
        if p:
            return {"intent": "who_person", "person": p["name"], "diligence": True}
        if co:
            return {"intent": "prep", "symbol": co}
    if re.search(r"\b(who|whom|someone|anyone)\b.*\b(know|knows|call|ask|talk|speak|reach|connect|intro)\b|\bintro to\b", ql):
        p = find_person(q)
        co = resolve_company(q)
        if p and (not co or fuzz.partial_ratio(p["name"].lower(), ql) >= 90):
            return {"intent": "who_person", "person": p["name"]}
        if co:
            return {"intent": "who_company", "symbol": co}
        topic = re.sub(r".*\b(on|about|in|for)\b", "", ql).strip(" ?.")
        if topic:
            return {"intent": "expertise", "topic": topic}
    co = resolve_company(q)
    if co:
        return {"intent": "research", "symbol": co, "focus": "promises" if re.search(r"promis|guid|deliver|credib", ql) else None}
    p = find_person(q)
    if p:
        return {"intent": "who_person", "person": p["name"], "diligence": True}
    if llm.available():
        r = llm.json_call(
            "extract",
            "Route an investor's question to exactly one tool. digest=what changed in a window; research=analyse a company; "
            "who_company=contacts who can explain a company; who_person=reach/diligence a named person; expertise=go-to people "
            "for a theme; reconnect=contacts to re-engage; prep=meeting prep for a company. Extract args.",
            q,
            {
                "type": "object",
                "properties": {
                    "intent": {"type": "string", "enum": INTENTS},
                    "company": {"type": ["string", "null"]},
                    "person": {"type": ["string", "null"]},
                    "topic": {"type": ["string", "null"]},
                    "days": {"type": ["integer", "null"]},
                },
                "required": ["intent", "company", "person", "topic", "days"],
                "additionalProperties": False,
            },
        )
        if r:
            out = {"intent": r["intent"], "via": "model"}
            if r.get("company"):
                out["symbol"] = resolve_company(r["company"]) or r["company"].upper()
            if r.get("person"):
                out["person"] = r["person"]
            if r.get("topic"):
                out["topic"] = r["topic"]
            if r.get("days"):
                out["hours"] = r["days"] * 24
            return out
    return {"intent": "help"}


def answer(q: str) -> dict:
    r = route(q)
    it = r["intent"]
    if it == "digest":
        d = digest.build(r["hours"])
        return {**r, "say": d["lede"], "data": d}
    if it == "research":
        rr = research.build(r["symbol"])
        if not rr:
            return {**r, "intent": "help", "say": f"I couldn't find fundamentals for {r['symbol']} — try its NSE symbol."}
        return {**r, "say": f"{rr['name']}: {rr['promises'].get('read', '')}", "data": rr}
    if it == "who_company":
        calls = graph.who_knows_org(r["symbol"], limit=5) if r["symbol"] in graph.orgs() else research.network_for(r["symbol"], screener.company(r["symbol"]) or {"industry": [], "name": r["symbol"]})["calls"]
        name = graph.org_name(r["symbol"])
        say = f"{calls[0]['name']} is best placed on {name}: {calls[0]['why']}." if calls else f"No one in your network maps to {name} yet."
        return {**r, "say": say, "data": {"symbol": r["symbol"], "name": name, "calls": calls}}
    if it == "who_person":
        p = find_person(r["person"]) or {"name": r["person"], "career": {"orgs": [], "schools": []}}
        paths = graph.paths_to_person(p, limit=5)
        you = graph.me_overlap(p)
        if paths:
            top = paths[0]
            say = f"{top['name']} is your best route to {p['name']}: {top['why']}."
        else:
            say = f"No direct route to {p['name']} yet."
        return {**r, "say": say, "data": {"person": p, "paths": paths, "you": you, "questions": diligence_questions(p)}}
    if it == "expertise":
        t = graph.find_theme(r["topic"])
        say = (f"Your go-to on {t[0]['theme']}: " + ", ".join(x["name"] for x in t[0]["people"][:3]) + ".") if t else f"No one tagged for “{r['topic']}” yet."
        return {**r, "say": say, "data": {"themes": t}}
    if it == "reconnect":
        d = digest.build(24 * 14)
        w = graph.warm_reasons(d["headline"] + d["brief"])
        say = f"{len(w)} people have a natural reason to hear from you this fortnight." if w else "No timely reasons to reconnect right now."
        return {**r, "say": say, "data": {"warm": w}}
    if it == "prep":
        return {**r, **prep(r["symbol"])}
    return {
        "intent": "help",
        "say": "Try: “what changed this week”, “who can I call on Dixon”, “who knows Rajiv Dube”, “experts on specialty chemicals”, “dig into KAYNES”, “prep me for HDFC Bank”.",
    }


def diligence_questions(p: dict) -> list[str]:
    qs = []
    orgs = (p.get("career") or {}).get("orgs", [])
    if orgs:
        last = orgs[-1]["org"] if len(orgs) > 1 else orgs[0]["org"]
        qs.append(f"What did they take from {last} that they'd apply here — and what would they do differently?")
    bio = (p.get("bio") or "").lower()
    if "independent director" in (p.get("role") or "").lower() or "board" in bio:
        qs.append("How many other boards do they sit on — enough time for this one?")
    if re.search(r"compliance|risk|regulat", bio):
        qs.append("What did the last regulatory inspection flag, and what's their 100-day fix list?")
    if re.search(r"transform|turnaround|restructur", bio):
        qs.append("Which transformation did they lead, and how was success measured?")
    if p.get("action") in ("resigned", "ceased"):
        qs.append("Why now? Was the exit planned, and who inherits the role?")
    if not qs:
        qs.append("What's the mandate from the board, and over what horizon?")
    return qs[:3]


def prep(symbol: str) -> dict:
    rr = research.build(symbol)
    if not rr:
        return {"say": f"Nothing on {symbol}.", "data": {}}
    pr = rr["promises"]
    missed = [i for i in pr["items"] if i["verdict"] in ("missed", "behind")]
    asks = []
    for m in missed[:2]:
        asks.append(f"On the {m['call']} call you guided “{m['quote'][:110]}…” — {m['why']} What changed?")
    for t in pr.get("pressed", [])[:2]:
        asks.append(f"Analysts keep returning to {t['topic']} — what's the one number that tells us it's fixed?")
    b = rr["balance"]
    if b.get("ccc_trend") == "lengthening":
        asks.append("Working-capital days are stretching — which customers or products are driving it?")
    if (rr["cash"] or {}).get("verdict") == "weak":
        asks.append("Profit isn't converting to cash — when does that reverse?")
    say = f"Meeting prep for {rr['name']}: {len(asks)} questions, {len(rr['network']['calls'])} people to call first."
    return {"say": say, "data": {"research": rr, "questions": asks[:5], "calls": rr["network"]["calls"][:3]}}
