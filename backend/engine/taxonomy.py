"""Filing classification + materiality.

NSE attaches its own subject line ("desc") to every filing, but it is unreliable — e.g. a
company files its quarterly results under "Change in Management". So we classify on the
*content* (NSE's attachment text, NSE's own summary, and — for anything that might be
material — the first pages of the PDF), using NSE's desc only as a prior.

Rules run first because they are free, instant and auditable. The cheap model is only asked
when the rules land on a generic bucket ("General Updates"/"Updates") — the long tail.
"""
from __future__ import annotations

import re

from . import llm

CATEGORIES = [
    "results", "M&A", "fund_raise", "management_change", "litigation", "insider_trading",
    "earnings_call", "investor_meetings", "credit_rating", "dividend", "bonus_split", "capex",
    "press_release", "postal_ballot", "agm_egm", "pledging", "unclassified",
]

LABELS = {
    "results": "Results", "M&A": "M&A", "fund_raise": "Fund raise", "management_change": "Management",
    "litigation": "Litigation", "insider_trading": "Insider trade", "earnings_call": "Earnings call",
    "investor_meetings": "Investor meet", "credit_rating": "Credit rating", "dividend": "Dividend",
    "bonus_split": "Bonus/split", "capex": "Capex", "press_release": "Press release",
    "postal_ballot": "Postal ballot", "agm_egm": "AGM/EGM", "pledging": "Pledge", "unclassified": "Routine",
}

# (category, regex on NSE desc) — the prior
DESC_RULES = [
    ("results", r"financial result|integrated filing- ?financial"),
    ("investor_meetings", r"analysts/institutional investor meet|investor meet"),
    ("earnings_call", r"con\. call|conference call"),
    ("results", r"investor presentation"),
    ("credit_rating", r"credit rating"),
    ("dividend", r"dividend"),
    ("bonus_split", r"bonus|split|sub-division"),
    ("management_change", r"change in management|change in director|appointment|resignation|retirement|cessation|key managerial|kmp"),
    ("M&A", r"acquisition|amalgamation|merger|scheme of arrangement|sale or disposal|restructuring|joint venture|demerger"),
    ("fund_raise", r"issue of securities|allotment of securities|buy ?back|commercial paper|qip|rights issue|preferential|private placement|non-convertible"),
    ("litigation", r"action\(s\) taken|orders passed|litigation|insolvency|penalt|show cause|dispute|fraud|default"),
    ("insider_trading", r"insider trading|sast|regulation 29|regulation 31|acquisition of shares"),
    ("pledging", r"pledge|encumbrance|invocation"),
    ("capex", r"capacity addition|commercial production|capex|expansion|new plant|commissioning"),
    ("press_release", r"press release|bagging|receiving of orders|news verification|rumour|clarification"),
    ("postal_ballot", r"postal ballot"),
    ("agm_egm", r"shareholders meeting|annual general|extra-?ordinary general|agm|egm"),
    ("unclassified", r"esop|esos|esps|newspaper|certificate under|monitoring agency|statement of deviation|"
                     r"auditor|trading window|committee meeting|share certificate|compliance|book closure|"
                     r"loss of share|duplicate|record date|interest payment|redemption|structural digital"),
]

# Routine paperwork that hides under material-sounding subjects. Checked on NSE's own short
# text/summary (not the PDF, whose boilerplate mentions everything).
ROUTINE_RULES = [
    ("postal_ballot", r"postal ballot"),
    ("agm_egm", r"annual general meeting|extra-?ordinary general meeting|\bagm\b|\begm\b"),
    ("results_deck", r"(results|investor|earnings) presentation|press release on (the )?(financial )?results|"
                     r"audio recording|transcript of"),
    ("unclassified", r"dematerial|physical (form|shares)|reminder to shareholders|unclaimed|transfer to iepf|"
                     r"redeem|redemption|interest (payment|paid)|loss of share|duplicate share|"
                     r"newspaper (publication|advertisement)|trading window|non[- ]convertible|\bncds?\b|debentures?|"
                     r"half[- ]yearly report|commercial papers?|security cover|asset cover|registrar and (share )?transfer|\brta\b|"
                     r"iepf|investor education and protection|newspaper|clippings?|guarantees? or indemnity|change in (corporate|registered) office address"),
    ("investor_meetings", r"analyst\(?s?\)?\s*/\s*investor\(?s?\)?\s*meet|schedule (of|for) (the )?(analyst|investor|institutional)|investor (meet|meeting)s? schedule|"
                          r"will (participate|be participating) in .{0,60}(conference|summit|meet)"),
    ("management_change", r"\bre\s*-?\s*appointment"),
]

# Content overrides — strong signals in the text beat NSE's label.
CONTENT_RULES = [
    ("results", r"(un-?audited|audited) (standalone and consolidated |consolidated |standalone )?financial results for the quarter"),
    ("results", r"financial results .{0,40}(quarter|half year) ended"),
    ("results", r"(un-?audited|audited)[^.]{0,60}financial results[^.]{0,80}(quarter|half[- ]year)"),
    ("earnings_call", r"transcript of (the )?(earnings|conference) call|audio recording of (the )?(earnings|conference) call|earnings call"),
    ("management_change", r"appointment of (mr|ms|mrs|dr|shri|smt)\.? [a-z.\s]{3,40}(\(din[^)]*\)\s*)?,?\s*as (an? |the )?"
                          r"(additional |independent |whole[- ]time |executive |non[- ]executive |managing )*"
                          r"(director|chief|chairman|chairperson|ceo|cfo|coo|md|company secretary|president)"),
    ("M&A", r"\b(acquire|acquisition of|amalgamation|merger|demerger|share purchase agreement|joint venture)\b"),
    ("capex", r"(capital expenditure|capex) of|new (plant|facility|manufacturing)|capacity expansion|greenfield|brownfield"),
    ("litigation", r"show cause notice|penalty of|order (passed|received) .{0,40}(tax|gst|sebi|court|tribunal)|demand (order|notice)|writ petition"),
    ("credit_rating", r"(upgrade|downgrade|reaffirm|assigned) .{0,40}rating|rating .{0,30}(upgrade|downgrade|reaffirm)"),
    ("fund_raise", r"qualified institution(al|s) placement|\bqip\b|rights issue|preferential (issue|allotment)|buy-?back of"),
    ("press_release", r"(order|contract)s? (worth|valued|aggregating)|letter of award|bagged|secured (an? )?order"),
]

# NSE subjects precise enough that the PDF's boilerplate must not override them
LOCKED = {"credit_rating", "dividend", "postal_ballot", "agm_egm", "bonus_split", "pledging", "insider_trading"}

BASE = {
    "results": 85, "M&A": 70, "management_change": 70, "fund_raise": 60, "litigation": 70,
    "credit_rating": 45, "capex": 65, "insider_trading": 55, "pledging": 60, "dividend": 35,
    "bonus_split": 55, "press_release": 40, "earnings_call": 25, "investor_meetings": 12,
    "agm_egm": 18, "postal_ballot": 18, "unclassified": 4,
}

SENIOR = r"\b(chief executive|ceo|managing director|\bmd\b|chief financial|cfo|chairman|chairperson|whole[- ]time director|executive director|president|chief operating|coo|chief business|joint managing)\b"


def classify(nse_desc: str, text: str, pdf_head: str = "") -> dict:
    d = (nse_desc or "").lower()
    body = f"{text}\n{pdf_head}".lower()
    cat = None
    for c, rx in DESC_RULES:
        if re.search(rx, d):
            cat = c
            break
    via = "nse_desc" if cat else None
    short = (text or "").lower()
    for c, rx in ROUTINE_RULES:
        if re.search(rx, short) and not (c == "management_change" and cat == "results"):
            if c == "results_deck":
                if re.search(r"transcript|audio recording", short):
                    return {"category": "earnings_call", "via": "content", **materiality("earnings_call", short)}
                return {"category": "results", "via": "content", "score": 30, "tier": "brief", "flags": ["deck"]}
            return {"category": c, "via": "content", **materiality(c, short + " " + body[:1500])}
    if cat in LOCKED or re.search(r"bagging|receiving of orders", d):
        return {"category": cat, "via": via, **materiality(cat, short + " " + body[:3000])}
    # Content beats label — but only from the text NSE/PDF gives us, and only for these overrides.
    for c, rx in CONTENT_RULES:
        if re.search(rx, body):
            if cat in (None, "press_release", "management_change", "unclassified", "investor_meetings") or (
                cat == "M&A" and c == "results"
            ):
                if c != cat:
                    cat, via = c, "content"
            break
    if cat is None and re.search(r"^(general )?updates?$", d) and llm.available():
        res = llm.json_call(
            "extract",
            "Classify an Indian listed-company exchange filing into exactly one category. Reply JSON.",
            f"NSE subject: {nse_desc}\nText: {text[:1200]}\nPDF start: {pdf_head[:2500]}",
            {
                "type": "object",
                "properties": {"category": {"type": "string", "enum": CATEGORIES}},
                "required": ["category"],
                "additionalProperties": False,
            },
        )
        if res:
            cat, via = res["category"], "model"
    if cat is None:
        cat, via = ("press_release" if re.search(r"update", d) else "unclassified"), "fallback"
    return {"category": cat, "via": via, **materiality(cat, body)}


BOILER = re.compile(
    r"reason for change viz[^.:]{0,160}|appointment\s*/\s*re-?\s*-?\s*appointment\s*/\s*cessation|"
    r"appointment,?\s*re-?\s*-?\s*appointment,?\s*resignation,?\s*removal,?\s*death or otherwise|"
    r"in case of (appointment|resignation)[^)]{0,40}\)?",
    re.I,
)


def materiality(cat: str, body: str) -> dict:
    body = BOILER.sub(" ", re.sub(r"\s+", " ", body))  # SEBI annexure templates mention every action — ignore them
    score = BASE.get(cat, 10)
    flags = []
    if cat == "management_change":
        if re.search(SENIOR, body):
            score += 15
            flags.append("senior")
        reapp = re.search(r"\b(approved|considered|approval (of|for)|for)\s+(the\s+)?re\s*-?\s*appointment of|\bre\s*-?\s*appointed as\b", body)
        fresh = re.search(r"\b(approved|considered)\s+(the\s+)?appointment of|(?<![\w\-\s]-)(?<![\w-])appointed as\b|resign|retire|superannuat", body)
        if reapp and not fresh:
            score -= 38
            flags.append("re-appointment")
        if re.search(r"independent director", body) and "senior" not in flags:
            score -= 20
        if re.search(r"resign", body):
            score += 5
            flags.append("exit")
    elif cat == "M&A":
        if re.search(r"incorporat\w+ .{0,90}subsidiary|incorporation of (a )?(wholly[- ]owned )?(step[- ]down )?subsidiary", body[:2500]) and not re.search(r"acquire[sd]? .{0,40}(stake|equity|shares)|share purchase", body[:2500]):
            score = 22
            flags.append("new subsidiary")
    elif cat == "credit_rating":
        for rx, s, f in ((r"downgrad|outlook .{0,20}negative|rating watch", 80, "downgrade"),
                         (r"upgrad", 68, "upgrade"), (r"reaffirm", 28, "reaffirmed")):
            if re.search(rx, body):
                score = s
                flags.append(f)
                break
    elif cat == "fund_raise":
        if re.search(r"esop|employee stock|esos", body):
            score = 6
            flags.append("ESOP")
    elif cat == "press_release":
        if re.search(r"order|contract|award", body):
            score += 15
            flags.append("order")
    elif cat == "litigation":
        if re.search(r"insolvency", body) and re.search(r"(corporate debtor|creditor|borrower)", body):
            score = 30  # a bank/lender reporting a borrower's CIRP — usually routine for large lenders
            flags.append("borrower CIRP")
    elif cat == "earnings_call" and re.search(r"transcript", body):
        score += 8
        flags.append("transcript")
    if re.search(r"₹|rs\.?\s?[\d,]+|inr\s?[\d,]+|crore", body) and cat in ("M&A", "capex", "press_release", "fund_raise", "litigation"):
        score += 5
    score = max(0, min(100, score))
    tier = "headline" if score >= 62 else "brief" if score >= 32 else "routine"
    return {"score": score, "tier": tier, "flags": flags}
