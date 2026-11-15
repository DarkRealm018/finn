"""People in filings → structured profiles.

SEBI requires a "Brief Profile" annexure for every appointment, so the filing itself is the
most reliable bio of an incoming director/KMP. We:
  1. find every named person and what happened to them (appointed / resigned / retired …),
  2. lift their profile paragraph from the annexure,
  3. turn it into a career graph (orgs, roles, schools) — cheap model if available, else a
     gazetteer + pattern extractor,
  4. if this filing has no profile (e.g. a shareholder-approval notice), look back through the
     company's earlier filings for one that does.
"""
from __future__ import annotations

import datetime as dt
import re

from .. import store
from ..sources import docs, nse
from . import llm

HON = r"(?:Mr|Ms|Mrs|Dr|Shri|Smt)(?:\s?\.)?"  # filings write "Mr.", "Mr" and "Mr ."
NAME = r"((?:[A-Z]\.\s?){0,3}[A-Z][a-zA-Z'’\-]+(?:\s+(?:[A-Z]\.\s?){0,2}[A-Z][a-zA-Z'’\-]+){0,3})"
PERSON_RX = re.compile(HON + r"\s+" + NAME)

ACTIONS = [
    ("completes term", r"completion of (the |his |her )?(tenure|term)|upon completion"),
    ("re-appointed", r"\bre\s*-?\s*appoint"),
    ("resigned", r"resign|tendered his|tendered her"),
    ("retired", r"retire|superannuat"),
    ("ceased", r"cease|cessation"),
    ("assumed office", r"assumed (the )?office|joined"),
    ("appointed", r"appoint|designated|elevat|named as"),
]

ROLE_RX = re.compile(
    r"\b(Chief [A-Z][a-z]+(?: [A-Z][a-z]+){0,3} Officer|Chairman Emeritus|Chairman|Chairperson|(?:Deputy |Joint )?Managing Director(?: & CEO| and CEO)?|"
    r"Whole[- ]?time Director|Executive Director|Non-? ?Executive (?:Non-? ?)?(?:Independent )?Director|Independent Director|"
    r"Additional Director|Group Head[^,.;()]{0,60}|Company Secretary|President[^,.;()]{0,40}|CEO|CFO|COO|CISO|CTO|Head\s?[–-]\s?[A-Z][^,.;()]{0,50})"
)


def _gazetteer() -> dict:
    return store.load_json("orgs.json")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def extract_people(text: str) -> list[dict]:
    """Rule-based pass: every person, their action and role, plus their profile paragraph."""
    t = _norm(text)
    tail = t[-700:]
    found: dict[str, dict] = {}
    for m in PERSON_RX.finditer(t):
        name = _clean_name(m.group(1))
        if not name or len(name) < 4 or not name.split() or len(name.replace(".", "").split()[-1]) < 2:
            continue
        key = _key(name)
        if key in found:
            continue
        window = t[max(0, m.start() - 260) : m.end() + 320]
        # signatories (company secretary signing the letter) are not news
        if name.split()[-1] in tail and re.search(r"company secretary|compliance officer|yours (faithfully|truly|sincerely)", tail, re.I) and t.count(name.split()[-1]) <= 2:
            continue
        # "Appointment of Mr X" / "Retirement of Mr X" — the noun right before the name wins
        before = t[max(0, m.start() - 90) : m.start()]
        after = t[m.end() : m.end() + 220]
        action = next((a for a, rx in ACTIONS if re.search(r"(?:" + rx + r")\w*\s+(?:of\s+)?(?:the\s+)?$", before, re.I)), None)
        action = action or next((a for a, rx in ACTIONS if re.search(rx, after, re.I)), None)
        action = action or next((a for a, rx in ACTIONS if re.search(rx, window, re.I)), None)
        role_m = ROLE_RX.search(t[m.end() : m.end() + 260]) or ROLE_RX.search(window)
        found[key] = {"name": name, "action": action, "role": _norm(role_m.group(1)) if role_m else None}
    # names without an honorific: "re-appointment of Prashant Verma as Chief Information Security Officer"
    for m in re.finditer(r"\b(appointment|re\s*-?\s*appointment|resignation|retirement|cessation) of\s+([A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+)\s+(?:as|from|\()", t):
        name = _clean_name(m.group(2))
        if not name or _key(name) in found or any(name.split()[-1] == f["name"].split()[-1] for f in found.values()):
            continue
        verb = m.group(1).lower().replace(" ", "").replace("-", "")
        action = {"appointment": "appointed", "reappointment": "re-appointed", "resignation": "resigned", "retirement": "retired", "cessation": "ceased"}.get(verb)
        role_m = ROLE_RX.search(t[m.end() - 3 : m.end() + 200])
        found[_key(name)] = {"name": name, "action": action, "role": _norm(role_m.group(1)) if role_m else None}
    # "Mr. Rangan" is the same person as "Mr. V. Srinivasa Rangan"
    names = list(found)
    for k in names:
        n = found[k]["name"]
        for k2 in names:
            if k2 not in found or k not in found:
                continue
            n2 = found[k2]["name"]
            same = n2.split()[-1] == n.split()[-1] or (len(n.split()) == 1 and n2.split()[0] == n)
            if k2 != k and k in found and len(n2) > len(n) and same:
                found[k2]["action"] = found[k2]["action"] or found[k]["action"]
                found[k2]["role"] = found[k2]["role"] or found[k]["role"]
                del found[k]
                break
    for p in found.values():
        p["bio"] = profile_paragraph(t, p["name"])
    # people with neither an action nor a bio are usually incidental mentions
    return [p for p in found.values() if p["action"] or p["bio"]]


STOP = {"Chairman", "Chairperson", "Director", "Directors", "Limited", "Ltd", "Company", "Chief", "Managing", "Executive",
        "Independent", "Non", "Additional", "Group", "Head", "President", "Bank", "The", "Board", "Emeritus", "Officer",
        "Whole", "Senior", "Secretary", "Vice", "As", "Is", "Has", "Who", "And", "Was", "Will", "DIN", "Of", "With", "From",
        "Mr", "Ms", "Mrs", "Dr", "Shri", "Smt", "Deputy"}


def _clean_name(n: str) -> str:
    n = re.sub(r"\s+", " ", n).strip(" .,'’")
    out = []
    for tok in n.split(" "):
        if tok.strip(".,") in STOP or tok.lower() in {"as", "is", "has", "who", "and", "the", "was", "will", "of"}:
            break
        out.append(tok)
    # drop a trailing company word ("Noel N. Tata Trent")
    while len(out) > 2 and out[-1] in ORG_TAILS:
        out.pop()
    return " ".join(out)


ORG_TAILS = {"Trent", "Titan", "HDFC", "ICICI", "Dixon"}


def _key(n: str) -> str:
    return re.sub(r"[^a-z]", "", n.lower())


def profile_paragraph(t: str, name: str) -> str:
    surname = name.split()[-1]
    start = None
    bio_verbs = (
        r"\s+(?:is (?:an?|the|currently|one|presently|a seasoned)\b|has (?:over|around|approximately|about|more than|nearly|\d|a |an |been|served|held|worked)|"
        r"was (?:previously|earlier|the|an?)\b|holds|brings|began|joined|served|started)"
    )
    for rx in (
        # "Annexure A  Profile - Mr. Leo Puri  Name: …  Qualification: …" — the richest form, quals included
        r"\bProfile\s*[-–:]\s*(?:" + HON + r"\s+)?" + re.escape(name) + r"\b",
        r"(?:" + HON + r"\s+)?" + re.escape(name) + bio_verbs,
        r"(?:Profiles?|Brief [Pp]rofile)[^.]{0,80}?" + HON + r"\s+" + re.escape(name) + r"\s+" + HON + r"\s+" + re.escape(name),
    ):
        m = re.search(rx, t)
        if m:
            start = m.start()
            break
    if start is None:
        return ""
    chunk = t[start : start + 1800]
    # stop at the next person's profile or the next numbered annexure item
    stop = re.search(r"\s(?:\d\.\s+[A-Z][a-z]+ (?:of|on) |" + HON + r"\s+(?!" + re.escape(surname) + r")[A-Z][a-z]+ [A-Z][a-z]+ " + HON + r")", chunk[60:])
    if stop:
        chunk = chunk[: 60 + stop.start()]
    stop2 = re.search(r"\s\d\.\s(?:Disclosure|Information as required)", chunk)
    if stop2:
        chunk = chunk[: stop2.start()]
    # drop annexure headers: "Brief profile of Mrs. X", "Profile - Mr. X Name: X Age: 65 years DIN: …"
    nm = re.escape(name)
    chunk = re.sub(r"^(?:Annexure[^A-Za-z]*[A-Z]?\s*[-–:]?\s*)?(?:Brief )?[Pp]rofiles?(?: of)?\s*[-–:]?\s*(?:" + HON + r"\s+)?" + nm + r"\s*", "", chunk.strip())
    chunk = re.sub(r"^Name\s*:\s*" + nm + r"\s*", "", chunk)
    chunk = re.sub(r"^(?:Age\s*:\s*\d+\s*years?\s*)?(?:DIN\s*:\s*\d+\s*)?", "", chunk)
    return chunk.strip(" ,")


# ------------------------------------------------------------------ career graph

def career(bio: str, name: str) -> dict:
    """{orgs:[{org, ticker, role?}], schools:[...], years_exp, source}"""
    if not bio:
        return {"orgs": [], "schools": [], "years_exp": None, "source": None}
    res = llm.json_call(
        "extract",
        "Extract a person's career from an Indian exchange filing's 'brief profile'. Use only facts in the text. "
        "Organisations: normalise to the common company name (e.g. 'ICICI Bank', 'Tata Motors', 'AZB & Partners').",
        f"Person: {name}\nProfile: {bio}",
        {
            "type": "object",
            "properties": {
                "orgs": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"org": {"type": "string"}, "role": {"type": "string"}},
                        "required": ["org", "role"],
                        "additionalProperties": False,
                    },
                },
                "schools": {"type": "array", "items": {"type": "string"}},
                "years_exp": {"type": ["integer", "null"]},
            },
            "required": ["orgs", "schools", "years_exp"],
            "additionalProperties": False,
        },
    )
    if res:
        res["source"] = "model"
    else:
        res = _career_rules(bio)
        res["source"] = "rules"
    gz = _gazetteer()
    for o in res["orgs"]:
        o["key"] = resolve_org(o["org"], gz)
    res["orgs"] = [o for o in res["orgs"] if o["org"]]
    return res


def resolve_org(name: str, gz: dict | None = None) -> str | None:
    gz = gz or _gazetteer()
    n = name.lower()
    for key, o in gz["orgs"].items():
        for a in [o["name"], *o.get("aliases", [])]:
            if re.search(r"\b" + re.escape(a.lower()) + r"\b", n):
                return key
    return None


def _career_rules(bio: str) -> dict:
    gz = _gazetteer()
    orgs, seen = [], set()
    for key, o in gz["orgs"].items():
        for a in sorted([o["name"], *o.get("aliases", [])], key=len, reverse=True):
            m = re.search(r"\b" + re.escape(a) + r"\b", bio, re.I)
            if m and key not in seen:
                seen.add(key)
                # the role nearest *before* the org name ("…Chief Executive Officer of UTI AMC")
                before = bio[max(0, m.start() - 90) : m.start()]
                before = re.split(r"[;.]\s", before)[-1]
                roles = list(ROLE_RX.finditer(before))
                role = roles[-1].group(1) if roles else ""
                orgs.append({"org": o["name"], "role": role, "pos": m.start()})
                break
    # unknown organisations: "at XYZ Limited", "with ABC & Co."
    for m in re.finditer(r"\b(?:at|with|of)\s+((?:[A-Z][\w&.+’'-]*\s?){1,5}(?:Limited|Ltd|Bank|& Co\.?|Partners|Group|Capital|Securities))", bio):
        nm = m.group(1).strip()
        if resolve_org(nm, gz) is None and nm.lower() not in {x["org"].lower() for x in orgs}:
            orgs.append({"org": nm, "role": "", "pos": m.start()})
    orgs.sort(key=lambda x: x.pop("pos"))
    schools = []
    for s in gz["schools"]:
        for a in [s["name"], *s.get("aliases", [])]:
            if re.search(r"\b" + re.escape(a) + r"\b", bio):
                schools.append(s["name"])
                break
    for m in re.finditer(r"((?:[A-Z][a-z]+\s){1,4}(?:University|College(?: of [A-Z][a-z]+)*|Institute of [A-Z][a-z]+(?: [A-Z][a-z]+)*))", bio):
        nm = m.group(1).strip()
        if nm not in schools and not any(nm in s or s in nm for s in schools):
            schools.append(nm)
    if re.search(r"chartered accountant", bio, re.I):
        schools.append("ICAI (Chartered Accountant)")
    words = {"two": 2, "three": 3, "four": 4, "five": 5}
    ym = re.search(r"(?<!Age: )(?<!Age )(\d{1,2})\+?\s+years(?:'|’)?\s+(?:of\s+)?(?:\w+\s+){0,3}experience", bio)
    if not ym:
        wd = re.search(r"\b(two|three|four|five) decades", bio, re.I)
        if wd:
            return {"orgs": orgs, "schools": schools, "years_exp": words[wd.group(1).lower()] * 10}
    return {"orgs": orgs, "schools": schools, "years_exp": int(ym.group(1)) if ym else None}


# ------------------------------------------------------------------ filing → people cards

@store.cached("people_in_filing", ttl=None)
def people_in_filing(pdf_url: str, symbol: str, filed: str) -> list[dict]:
    pages = docs.pdf_pages(pdf_url, max_pages=12) or []
    text = " ".join(pages)
    people = extract_people(text)
    for p in people:
        if not p["bio"] and p["action"] in ("appointed", "assumed office", None):
            p["bio"], p["bio_from"] = lookback_profile(symbol, p["name"], filed)
        else:
            p["bio_from"] = pdf_url if p["bio"] else None
        p["career"] = career(p["bio"], p["name"])
    # incoming people first; then exits
    order = {"appointed": 0, "assumed office": 1, "resigned": 2, "retired": 3, "ceased": 4, "completes term": 5, "re-appointed": 6, None: 7}
    people.sort(key=lambda p: order.get(p["action"], 9))
    return people


def lookback_profile(symbol: str, name: str, filed: str) -> tuple[str, str | None]:
    """Find this person's brief profile in the company's earlier filings (up to ~6 months back)."""
    end = dt.date.fromisoformat(filed[:10])
    start = end - dt.timedelta(days=180)
    surname = name.split()[-1]
    tried = 0
    for a in sorted(nse.announcements(symbol, start.isoformat(), end.isoformat()), key=lambda x: x["ts"], reverse=True):
        if not re.search(r"(?i)appoint|director|management|board meeting", a["nse_desc"]) or a["ts"][:10] >= filed[:10]:
            continue
        tried += 1
        if tried > 15:
            break
        pages = docs.pdf_pages(a["pdf"], max_pages=12) or []
        t = _norm(" ".join(pages))
        if surname not in t:
            continue
        bio = profile_paragraph(t, name)
        if bio:
            return bio, a["pdf"]
    return "", None
