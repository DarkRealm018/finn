"""NSE corporate announcements + financial-results XBRL (the primary filing itself)."""
from __future__ import annotations

import datetime as dt
import re

from .. import store
from . import http

BASE = "https://www.nseindia.com/api"


def _d(s: str) -> str:
    return dt.datetime.strptime(s, "%Y-%m-%d").strftime("%d-%m-%Y")


@store.cached("nse_ann", ttl=20 * 60)
def announcements(symbol: str, start: str, end: str) -> list[dict]:
    """All filings for one symbol between ISO dates start..end (inclusive)."""
    url = f"{BASE}/corporate-announcements?index=equities&symbol={symbol}&from_date={_d(start)}&to_date={_d(end)}"
    r = http.get(url, session=http.nse())
    if not r.ok:
        raise RuntimeError(f"NSE {r.status_code}")  # → store serves a stale copy if it has one
    out = []
    for a in r.json() or []:
        ts = a.get("sort_date") or ""
        out.append(
            {
                "id": a.get("seq_id") or a.get("attchmntFile"),
                "symbol": a.get("symbol") or symbol,
                "company": a.get("sm_name"),
                "ts": ts,
                "nse_desc": (a.get("desc") or "").strip(),
                "text": (a.get("attchmntText") or "").strip(),
                "summary": (a.get("voiceSummary") or "").strip(),
                "pdf": a.get("attchmntFile"),
                "industry": a.get("smIndustry"),
            }
        )
    return out


# ---------------------------------------------------------------- results (XBRL)

@store.cached("nse_results_list", ttl=6 * store.HOUR)
def results_filings(symbol: str) -> list[dict]:
    """Every quarterly result with its XBRL. Since 2025 results are 'Integrated Filings';
    older ones live on the legacy endpoint — we merge both."""
    rows = []
    r = http.get(f"{BASE}/integrated-filing-results?index=equities&symbol={symbol}&period=Quarterly", session=http.nse())
    if r.ok:
        for x in (r.json() or {}).get("data", []):
            if not x.get("consolidated") or not x.get("xbrl") or "GOVERNANCE" in x["xbrl"]:
                continue
            try:
                to = dt.datetime.strptime(x["qe_Date"], "%d-%b-%Y").date()
            except Exception:
                continue
            rows.append(
                {
                    "from": None,
                    "to": to.isoformat(),
                    "consolidated": x["consolidated"].lower().startswith("consol"),
                    "bank": "BANKING" in x["xbrl"],
                    "audited": x.get("audited"),
                    "filed": x.get("creation_Date"),
                    "xbrl": x["xbrl"],
                }
            )
    r = http.get(f"{BASE}/corporates-financial-results?index=equities&symbol={symbol}&period=Quarterly", session=http.nse())
    have = {(x["to"], x["consolidated"]) for x in rows}
    for x in (r.json() or []) if r.ok else []:
        try:
            to = dt.datetime.strptime(x["toDate"], "%d-%b-%Y").date().isoformat()
            frm = dt.datetime.strptime(x["fromDate"], "%d-%b-%Y").date().isoformat()
        except Exception:
            continue
        if (to, (x.get("consolidated") or "").lower().startswith("consol")) in have:
            continue
        rows.append(
            {
                "from": frm,
                "to": to,
                "consolidated": (x.get("consolidated") or "").lower().startswith("consol"),
                "bank": x.get("bank") == "Y",
                "audited": x.get("audited"),
                "filed": x.get("filingDate"),
                "xbrl": x.get("xbrl"),
            }
        )
    rows.sort(key=lambda r: (r["to"], r["consolidated"]), reverse=True)
    return rows


# Candidate XBRL element names per metric (Ind-AS corporate + banking taxonomies).
TAGS = {
    "revenue": ["RevenueFromOperations", "TotalIncomeFromOperations", "InterestEarned", "Income"],
    "other_income": ["OtherIncome"],
    "finance_cost": ["FinanceCosts", "InterestExpended"],
    "dep": ["DepreciationDepletionAndAmortisationExpense", "DepreciationAndAmortisationExpense"],
    "pbt": ["ProfitBeforeTax", "ProfitLossBeforeTax", "ProfitLossFromOrdinaryActivitiesBeforeTax"],
    "pat": ["ProfitLossForPeriod", "ProfitLossAfterTaxesMinorityInterestAndShareOfProfitLossOfAssociates", "ProfitLossForThePeriod", "NetProfitLossForThePeriod"],
    "ppop": ["OperatingProfitBeforeProvisionAndContingencies", "OperatingProfitBeforeProvisionsAndContingencies"],
    "impairment": ["ImpairmentOnFinancialInstruments"],
    "int_earned": ["InterestEarned"],
    "int_expended": ["InterestExpended"],
}


@store.cached("xbrl", ttl=None)  # a filed XBRL never changes
def parse_xbrl(url: str) -> dict | None:
    r = http.get(url, session=http.nse(), timeout=40)
    if not r.ok:
        return None
    t = r.text

    def val(names):
        for n in names:
            m = re.search(r"<(?:[\w-]+:)?" + n + r"\b[^>]*contextRef=\"OneD\"[^>]*>\s*([-\d.]+)\s*<", t)
            if m:
                return float(m.group(1)) / 1e7  # rupees -> ₹ crore
        return None

    out = {k: val(v) for k, v in TAGS.items()}
    if out["pbt"] is not None and out["finance_cost"] is not None and out["dep"] is not None:
        out["ebitda"] = out["pbt"] + out["finance_cost"] + out["dep"] - (out["other_income"] or 0)
    else:
        out["ebitda"] = None
    if out["int_earned"] is not None and out["int_expended"] is not None:
        out["nii"] = out["int_earned"] - out["int_expended"]
    # NBFCs file Ind-AS but are lenders: EBITDA is meaningless, pre-provision profit isn't.
    out["nbfc"] = bool(out["impairment"] is not None and out["revenue"] and (out["finance_cost"] or 0) / out["revenue"] > 0.2)
    if out["nbfc"] and out["pbt"] is not None:
        out["ppop"] = out["pbt"] + out["impairment"]
    return out


def quarter_label(to_iso: str) -> str:
    d = dt.date.fromisoformat(to_iso)
    fy = d.year + 1 if d.month > 3 else d.year
    q = {6: 1, 9: 2, 12: 3, 3: 4}.get(d.month, 0)
    return f"Q{q} FY{str(fy)[-2:]}"


def headline_results(symbol: str) -> dict | None:
    """Latest quarter vs previous quarter (QoQ) and vs same quarter last year (YoY), from XBRL."""
    rows = results_filings(symbol)
    if not rows:
        return None
    prefer_consol = any(r["consolidated"] for r in rows[:4])
    by_to: dict[str, dict] = {}
    for r in rows:
        if r["consolidated"] != prefer_consol or not r["xbrl"]:
            continue
        by_to.setdefault(r["to"], r)
    if not by_to:
        return None
    ends = sorted(by_to, reverse=True)
    cur = ends[0]
    cur_d = dt.date.fromisoformat(cur)

    def find(months_back):
        target = (cur_d.replace(day=1) - dt.timedelta(days=months_back * 30.4)).strftime("%Y-%m")
        return next((e for e in ends if e.startswith(target)), None)

    prev, yago = find(3), find(12)
    nums = {k: parse_xbrl(by_to[k]["xbrl"]) for k in (cur, prev, yago) if k}
    c = nums.get(cur) or {}
    metrics = []
    bank = by_to[cur]["bank"]
    # Banks: "revenue" and "EBITDA" are meaningless — net interest income and pre-provision profit are the analogues.
    nbfc = bool(c.get("nbfc"))
    spec = (
        [("NII", "nii"), ("PPOP", "ppop"), ("PBT", "pbt"), ("PAT", "pat")]
        if bank
        else [("Revenue", "revenue"), ("PPOP", "ppop"), ("PBT", "pbt"), ("PAT", "pat")]
        if nbfc
        else [("Revenue", "revenue"), ("EBITDA", "ebitda"), ("PBT", "pbt"), ("PAT", "pat")]
    )
    bank = bank or nbfc  # margins on a lender's "revenue" mislead
    for label, key in spec:
        v = c.get(key)
        p = (nums.get(prev) or {}).get(key) if prev else None
        y = (nums.get(yago) or {}).get(key) if yago else None
        metrics.append(
            {
                "label": label,
                "value": _round(v),
                "prev": _round(p),
                "yago": _round(y),
                "qoq": _pct(v, p),
                "yoy": _pct(v, y),
                "margin": round(v / c["revenue"] * 100, 1) if not bank and v is not None and c.get("revenue") and key != "revenue" else None,
                "margin_yago": round(y / nums[yago]["revenue"] * 100, 1) if not bank and y is not None and yago and (nums.get(yago) or {}).get("revenue") and key != "revenue" else None,
            }
        )
    return {
        "symbol": symbol,
        "quarter": quarter_label(cur),
        "period_end": cur,
        "basis": "Consolidated" if prefer_consol else "Standalone",
        "bank": bank,
        "filed": by_to[cur]["filed"],
        "source": by_to[cur]["xbrl"],
        "metrics": metrics,
        "unit": "₹ cr",
    }


def _round(x):
    return None if x is None else round(x, 1)


def _pct(a, b):
    if a is None or b in (None, 0):
        return None
    return round((a / abs(b) - 1) * 100 if b > 0 else (a - b) / abs(b) * 100, 1)
