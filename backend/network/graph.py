"""The Network layer: the investor's contacts as an intelligence graph.

Nodes: contacts, organisations, schools, and "people in the news" (from filings).
Edges carry a *reason* and a *strength*; every answer FINN gives is a ranked list of
(contact, why, how strong) — never just a name.

Tie strength (0–1) blends relationship type, recency and frequency, so "a classmate you
spoke to last week" beats "a dealer you met once in 2023" — but the cold-but-perfectly-placed
contact still surfaces, flagged as needing a warm-up.
"""
from __future__ import annotations

import datetime as dt
import functools
import math
import re

from rapidfuzz import fuzz

from .. import store

TODAY = dt.date.today()

REL_BASE = [
    (r"family|cousin|father|spouse", 0.65),
    (r"ex-?colleague|colleague", 0.8),
    (r"classmate|batchmate", 0.78),
    (r"school friend|friend(?! of)", 0.8),
    (r"co-?investor", 0.72),
    (r"senior", 0.62),
    (r"ex-?client", 0.5),
    (r"friend of a friend", 0.35),
    (r"met|visit|conference|trip|dealer|expert", 0.4),
]


@functools.lru_cache(maxsize=1)
def _raw() -> dict:
    return store.load_json("network/contacts.json")


@functools.lru_cache(maxsize=1)
def orgs() -> dict:
    return store.load_json("orgs.json")["orgs"]


def org_name(key: str | None) -> str:
    if not key:
        return ""
    o = orgs().get(key)
    return o["name"] if o else key


def contacts() -> list[dict]:
    out = []
    for c in _raw()["contacts"]:
        c = dict(c)
        c["strength"], c["warmth"], c["days_since"] = tie_strength(c)
        c["current"] = next((e for e in c["employers"] if e.get("to") is None), c["employers"][-1] if c["employers"] else None)
        out.append(c)
    return out


def me() -> dict:
    return _raw()["me"]


def meetings() -> list[dict]:
    return _raw().get("meetings", [])


def contact(cid: str) -> dict | None:
    return next((c for c in contacts() if c["id"] == cid), None)


def tie_strength(c: dict) -> tuple[float, str, int]:
    rel = c.get("relation", "").lower()
    base = next((v for rx, v in REL_BASE if re.search(rx, rel)), 0.45)
    try:
        days = (TODAY - dt.date.fromisoformat(c["last_spoke"])).days
    except Exception:
        days = 999
    recency = 1.0 if days < 45 else max(0.3, math.exp(-(days - 45) / 520))
    freq = min(0.15, 0.03 * c.get("touches_12m", 0))
    s = round(min(1.0, base * recency + freq), 2)
    warmth = "strong" if s >= 0.7 else "warm" if s >= 0.5 else "cooling" if s >= 0.35 else "cold"
    return s, warmth, days


def _span(e: dict) -> str:
    to = e.get("to") or "now"
    return f"{e.get('from', '?')}–{to}"


def _brief(c: dict) -> dict:
    cur = c["current"] or {}
    return {
        "id": c["id"],
        "name": c["name"],
        "relation": c["relation"],
        "now": f"{cur.get('role', '')}, {org_name(cur.get('org')) or cur.get('org', '')}".strip(", "),
        "strength": c["strength"],
        "warmth": c["warmth"],
        "days_since": c["days_since"],
        "last_spoke": c["last_spoke"],
    }


# ------------------------------------------------------------- company → who to call

def who_knows_org(key: str, limit: int = 5) -> list[dict]:
    """Rank contacts by how well-placed they are to explain this company.

    Link types, strongest first: works there now › alumni › peer/competitor ›
    customer/supplier › same group › theme expert.
    """
    o = orgs().get(key, {})
    peers = set(o.get("peers", []))
    for k, v in orgs().items():  # peers are symmetric
        if key in v.get("peers", []):
            peers.add(k)
    cust = set(o.get("customers", []))
    supp = set(o.get("suppliers", []))
    for k, v in orgs().items():
        if key in v.get("suppliers", []):
            cust.add(k)
        if key in v.get("customers", []):
            supp.add(k)
    themes = set(o.get("themes", []))
    group = o.get("group")
    res = []
    for c in contacts():
        best = None
        for e in c["employers"]:
            ek = e["org"]
            eo = orgs().get(ek, {})
            if ek == key and e.get("to") is None:
                cand = (1.0, "inside", f"Works there now — {e['role']}")
            elif ek == key:
                cand = (0.9, "alumni", f"{e['role']} at {org_name(key)}, {_span(e)}")
            elif ek in peers:
                cand = (0.62, "competitor", f"{e['role']} at {org_name(ek)} (competitor), {_span(e)}")
            elif ek in cust:
                cand = (0.55, "customer", f"{e['role']} at {org_name(ek)} (customer), {_span(e)}")
            elif ek in supp:
                cand = (0.5, "supplier", f"{e['role']} at {org_name(ek)} (supplier), {_span(e)}")
            elif group and eo.get("group") == group and ek != key:
                cand = (0.42, "group", f"{e['role']} at {org_name(ek)} — same group, {_span(e)}")
            else:
                continue
            if best is None or cand[0] > best[0]:
                best = cand
        overlap = themes & set(c.get("expertise", []))
        if best is None and overlap:
            best = (0.35, "expert", "Your go-to on " + ", ".join(sorted(overlap)))
        if best is None:
            continue
        place, kind, why = best
        # placement matters more than warmth, but a stone-cold tie is discounted
        score = round(place * (0.55 + 0.45 * c["strength"]), 3)
        res.append({**_brief(c), "kind": kind, "why": why, "score": score, "ask": _ask_hint(kind, key, c)})
    res.sort(key=lambda r: r["score"], reverse=True)
    return res[:limit]


def _ask_hint(kind: str, key: str, c: dict) -> str:
    n = org_name(key)
    return {
        "inside": f"Current view from inside {n} — tone, priorities, what isn't in the deck.",
        "alumni": f"How {n} actually runs: decision-making, culture, which numbers management watches.",
        "competitor": f"How {n} looks from across the table — pricing, share shifts, who's winning talent.",
        "customer": f"What it is like buying from {n}: reliability, pricing power, switching risk.",
        "supplier": f"Order patterns and payment behaviour from {n} — an early read on demand.",
        "group": f"Group-level context: capital allocation and governance norms that shape {n}.",
        "expert": f"Sector context to frame {n}'s numbers.",
    }.get(kind, "")


# ------------------------------------------------------------- person → paths

def _name_match(a: str, b: str) -> int:
    clean = lambda s: re.sub(r"\b(mr|ms|mrs|dr|shri|smt)\b\.?", "", s.lower()).replace(".", " ")
    return fuzz.token_sort_ratio(clean(a), clean(b))


def paths_to_person(person: dict, limit: int = 5) -> list[dict]:
    """How the investor can reach someone named in a filing.

    1st-degree reach via: a contact who explicitly knows them (investor's notes) ›
    worked at the same organisation (overlap likely) › studied at the same school.
    Strength = tie(investor→contact) × link(contact→person).
    """
    name = person.get("name", "")
    career = person.get("career") or {}
    porgs = [o.get("key") or o.get("org") for o in career.get("orgs", [])]
    pschools = [s.lower() for s in career.get("schools", [])]
    out = []
    for c in contacts():
        links = []
        for k in c.get("knows", []):
            if _name_match(k["name"], name) >= 85:
                links.append((0.55 + 0.09 * k.get("strength", 3), "knows", k["how"]))
        for e in c["employers"]:
            if e["org"] in porgs and e["org"]:
                links.append((0.38, "same-org", f"Both at {org_name(e['org'])} ({c['name'].split()[0]}: {_span(e)}) — may have overlapped"))
        for ed in c.get("education", []):
            if any(ed["school"].lower() in s or s in ed["school"].lower() for s in pschools):
                links.append((0.18, "same-school", f"Both studied at {ed['school']}"))
        if not links:
            continue
        links.sort(reverse=True)
        lk, kind, why = links[0]
        out.append(
            {
                **_brief(c),
                "kind": kind,
                "why": why,
                "also": [w for _, _, w in links[1:3]],
                "path_strength": round(c["strength"] * lk, 2),
                "link": round(lk, 2),
            }
        )
    out.sort(key=lambda r: r["path_strength"], reverse=True)
    return out[:limit]


def me_overlap(person: dict) -> list[str]:
    """Does the investor *personally* share history with this person?"""
    career = person.get("career") or {}
    porgs = {o.get("key") for o in career.get("orgs", [])}
    pschools = [s.lower() for s in career.get("schools", [])]
    out = []
    for e in me()["employers"]:
        if e["org"] in porgs:
            out.append(f"You were both at {org_name(e['org'])}")
    for ed in me()["education"]:
        if any(ed["school"].lower() in s for s in pschools):
            out.append(f"You both studied at {ed['school']}")
    return out


# ------------------------------------------------------------- expertise map

def expertise_map() -> list[dict]:
    themes: dict[str, list] = {}
    for c in contacts():
        for t in c.get("expertise", []):
            themes.setdefault(t, []).append(c)
        # employers imply expertise too (weaker)
        for e in c["employers"]:
            for t in orgs().get(e["org"], {}).get("themes", []):
                if t not in c.get("expertise", []):
                    themes.setdefault(t, []).append({**c, "_implied": True})
    out = []
    for t, cs in themes.items():
        seen, ranked = set(), []
        for c in sorted(cs, key=lambda c: (not c.get("_implied"), c["strength"]), reverse=True):
            if c["id"] in seen:
                continue
            seen.add(c["id"])
            ranked.append({**_brief(c), "implied": bool(c.get("_implied"))})
        depth = sum(1 for r in ranked if not r["implied"])
        out.append({"theme": t, "people": ranked[:6], "depth": depth, "count": len(ranked)})
    out.sort(key=lambda x: (x["depth"], x["count"]), reverse=True)
    return out


def find_theme(q: str) -> list[dict]:
    q = q.lower()
    best = []
    for t in expertise_map():
        s = max(fuzz.partial_ratio(q, t["theme"]), fuzz.token_set_ratio(q, t["theme"]))
        if s >= 70:
            best.append((s, t))
    best.sort(key=lambda x: (x[0], x[1]["depth"]), reverse=True)
    return [t for _, t in best[:3]]


# ------------------------------------------------------------- keep warm

def warm_reasons(events: list[dict], min_days: int = 100) -> list[dict]:
    """Contacts you haven't spoken to in a while whose (former) employer just did something.

    `events` are digest items: {symbol, category, title, score}.
    """
    by_org: dict[str, list] = {}
    for ev in events:
        if ev.get("rank", ev.get("score", 0)) >= 55:
            by_org.setdefault(ev["symbol"], []).append(ev)
    out = []
    for c in contacts():
        if c["days_since"] < min_days:
            continue
        for e in c["employers"]:
            evs = by_org.get(e["org"])
            if not evs:
                continue
            ev = max(evs, key=lambda x: x["score"])
            former = e.get("to") is not None
            out.append(
                {
                    **_brief(c),
                    "org": e["org"],
                    "org_name": org_name(e["org"]),
                    "event": ev["title"],
                    "event_id": ev.get("id"),
                    "category": ev.get("category"),
                    "why": f"{'Ex-' if former else ''}{org_name(e['org'])} · {ev['title']}",
                    "opener": _opener(c, e, ev, former),
                }
            )
            break
    out.sort(key=lambda r: (-(r["days_since"] > 300), -r["strength"]))
    return out


def _opener(c, e, ev, former) -> str:
    first = c["name"].replace("Dr. ", "").split()[0]
    n = org_name(e["org"])
    cat = ev.get("category")
    if cat == "management_change":
        hook = f"saw the leadership news at {n}"
    elif cat == "results":
        hook = f"just went through {n}'s numbers"
    elif cat in ("M&A", "capex"):
        hook = f"saw {n}'s announcement"
    else:
        hook = f"saw the update from {n}"
    tail = "— curious how it looks to someone who's been inside." if former else "— would love your read when you have 15 min."
    return f"Hi {first}, it's been a while! {hook[0].upper() + hook[1:]} {tail}"


# ------------------------------------------------------------- graph for UI

def graph(focus_org: str | None = None) -> dict:
    nodes = [{"id": "me", "label": "You", "type": "me"}]
    edges = []
    for c in contacts():
        nodes.append({"id": c["id"], "label": c["name"], "type": "contact", "strength": c["strength"], "warmth": c["warmth"]})
        edges.append({"a": "me", "b": c["id"], "w": c["strength"]})
        for e in c["employers"]:
            if e["org"] in orgs():
                edges.append({"a": c["id"], "b": e["org"], "w": 0.5 if e.get("to") else 0.9})
        for k in c.get("knows", []):
            pid = "p:" + k["name"]
            if not any(n["id"] == pid for n in nodes):
                nodes.append({"id": pid, "label": k["name"], "type": "person"})
            edges.append({"a": c["id"], "b": pid, "w": k.get("strength", 3) / 5})
    used = {e["b"] for e in edges} | {e["a"] for e in edges}
    for k, o in orgs().items():
        if k in used:
            nodes.append({"id": k, "label": o["name"], "type": "org"})
    return {"nodes": nodes, "edges": edges}
