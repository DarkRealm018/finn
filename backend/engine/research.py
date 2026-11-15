"""PULL — a research view of any NSE ticker, grounded only in primary data.

Sources: screener.in's standardised statements (lifted from the company's filings) and the
company's own documents (annual reports, concall transcripts). No news, no broker notes, and
the model is never asked what it "knows" about the company — every number it sees is one we
computed, every quote one we extracted, each with a page reference.
"""
from __future__ import annotations

import math
import re

from .. import store
from ..network import graph
from ..sources import prices, screener
from . import llm, promises


def _row(tbl: dict | None, *names) -> list | None:
    if not tbl:
        return None
    for n in names:
        for k, v in tbl["rows"].items():
            if k.lower().startswith(n.lower()):
                return v
    return None


def _last(xs, n=1):
    xs = [x for x in (xs or []) if x is not None]
    return xs[-n] if len(xs) >= n else None


def _cagr(series: list, years: int) -> float | None:
    s = [x for x in (series or [])]
    if len(s) <= years:
        return None
    a, b = s[-years - 1], s[-1]
    if a is None or b is None or a <= 0 or b <= 0:
        return None
    return round(((b / a) ** (1 / years) - 1) * 100, 1)


def _annual(tbl):
    """Annual columns only (drop TTM), aligned lists."""
    if not tbl:
        return [], {}
    idx = [i for i, c in enumerate(tbl["cols"]) if re.match(r"(Mar|Dec|Jun|Sep) \d{4}", c)]
    cols = [tbl["cols"][i] for i in idx]
    rows = {k: [v[i] if i < len(v) else None for i in idx] for k, v in tbl["rows"].items()}
    return cols, rows


def build(symbol: str) -> dict | None:
    symbol = symbol.upper().strip()
    co = screener.company(symbol)
    if not co:
        return None
    px = prices.eod(symbol)
    st = prices.stats(px["bars"]) if px else {}
    out = {
        "symbol": symbol,
        "name": co["name"],
        "basis": co["basis"],
        "price": st,
        "sources": _sources(co),
    }
    out["snapshot"] = snapshot(co)
    out["financials"] = financials(co)
    out["trajectory"] = trajectory(co)
    out["balance"] = balance(co)
    out["cash"] = cash(co)
    out["promises"] = promises.build(symbol, co)
    out["bull_bear"] = bull_bear(out)
    out["network"] = network_for(symbol, co)
    out["model"] = "rules+model" if llm.available() else "rules"
    return out


def _sources(co):
    d = co["docs"]
    return {
        "screener": f"https://www.screener.in/company/{co['symbol']}/{'consolidated/' if co['basis'] == 'Consolidated' else ''}",
        "annual_reports": d["annual_reports"][:3],
        "concalls": [c for c in d["concalls"] if c.get("transcript")][:4],
    }


# ------------------------------------------------------------------ sections

def snapshot(co: dict) -> dict:
    mix = []
    for line in co.get("key_points", "").split("\n"):
        m = re.match(r"^\s*[-•]?\s*([A-Za-z&/,()' .\-]{3,60}):\s*~?([\d.]+)\s*%", line)
        if m:
            mix.append({"segment": m.group(1).strip(), "pct": float(m.group(2))})
    if mix and not (80 <= sum(x["pct"] for x in mix) <= 120):
        mix = mix[:8]
    heading = None
    m = re.search(r"(Revenue|Segment|Business)[^:\n]{0,40}(Breakup|Mix|Split)[^:\n]*", co.get("key_points", ""))
    if m:
        heading = m.group(0).strip()
    about = co.get("about", "")
    about = re.sub(r"\[\d+\]", "", about).strip()
    return {
        "about": about,
        "mix": mix[:10],
        "mix_label": heading,
        "industry": co.get("industry", []),
        "ratios": {k: v for k, v in co.get("ratios", {}).items() if k in ("Market Cap", "Current Price", "Stock P/E", "ROCE", "ROE", "Dividend Yield", "Book Value", "High / Low")},
        "key_points": [l.strip("- •") for l in co.get("key_points", "").split("\n") if len(l.strip()) > 3][:12],
    }


def financials(co: dict) -> dict:
    q = co["quarters"]
    top = _row(q, "Sales", "Revenue")
    op = _row(q, "Operating Profit", "Financing Profit")
    opm = _row(q, "OPM", "Financing Margin")
    pat = _row(q, "Net Profit")
    pbt = _row(q, "Profit before tax")
    cols = q["cols"] if q else []
    quarters = []
    for i, c in enumerate(cols):
        quarters.append(
            {
                "q": c,
                "revenue": top[i] if top and i < len(top) else None,
                "op": op[i] if op and i < len(op) else None,
                "opm": opm[i] if opm and i < len(opm) else None,
                "pbt": pbt[i] if pbt and i < len(pbt) else None,
                "pat": pat[i] if pat and i < len(pat) else None,
            }
        )
    for i, x in enumerate(quarters):
        y = quarters[i - 4] if i >= 4 else None
        p = quarters[i - 1] if i >= 1 else None
        x["rev_yoy"] = _g(x["revenue"], y and y["revenue"])
        x["pat_yoy"] = _g(x["pat"], y and y["pat"])
        x["rev_qoq"] = _g(x["revenue"], p and p["revenue"])
    pnl = co["pnl"]
    ttm = {}
    if pnl and pnl["cols"] and pnl["cols"][-1].upper() == "TTM":
        for k, names in {"revenue": ("Sales", "Revenue"), "op": ("Operating Profit", "Financing Profit"), "pat": ("Net Profit",), "opm": ("OPM", "Financing Margin")}.items():
            r = _row(pnl, *names)
            ttm[k] = r[-1] if r else None
    return {
        "unit": "₹ cr",
        "quarters": quarters[-8:],
        "ttm": ttm,
        "op_label": "Financing profit" if _row(q, "Financing Profit") else "Operating profit (EBITDA)",
        "is_financial": bool(_row(q, "Financing Profit")),
    }


def _g(a, b):
    if a is None or b in (None, 0):
        return None
    return round((a - b) / abs(b) * 100, 1)


def trajectory(co: dict) -> dict:
    cols, rows = _annual(co["pnl"])
    sales = _row({"rows": rows}, "Sales", "Revenue") if rows else None
    op = _row({"rows": rows}, "Operating Profit", "Financing Profit") if rows else None
    opm = _row({"rows": rows}, "OPM", "Financing Margin") if rows else None
    pat = _row({"rows": rows}, "Net Profit") if rows else None
    eps = _row({"rows": rows}, "EPS") if rows else None
    if not sales:
        return {}
    metrics = []
    for label, s in (("Revenue", sales), ("Operating profit", op), ("Net profit", pat), ("EPS", eps)):
        if not s:
            continue
        c3, c5, c10 = _cagr(s, 3), _cagr(s, 5), _cagr(s, 10)
        trend = None
        if c3 is not None and c5 is not None:
            trend = "accelerating" if c3 > c5 + 3 else "decelerating" if c3 < c5 - 3 else "steady"
        metrics.append({"label": label, "cagr3": c3, "cagr5": c5, "cagr10": c10, "trend": trend, "series": s[-10:]})
    opm_s = [x for x in (opm or []) if x is not None]
    margin = None
    if len(opm_s) >= 5:
        recent, older = sum(opm_s[-3:]) / 3, sum(opm_s[-6:-3]) / max(1, len(opm_s[-6:-3]))
        delta = recent - older
        margin = {
            "latest": opm_s[-1],
            "avg3": round(recent, 1),
            "avg_prior3": round(older, 1),
            "delta_pp": round(delta, 1),
            "verdict": "expanding" if delta > 1.5 else "compressing" if delta < -1.5 else "stable",
            "series": opm_s[-10:],
        }
    growing = [m["label"] for m in metrics if (m["cagr3"] or 0) >= 12]
    lagging = [m["label"] for m in metrics if m["cagr3"] is not None and m["cagr3"] < 5]
    return {"years": cols[-10:], "metrics": metrics, "margin": margin, "growing": growing, "lagging": lagging}


def balance(co: dict) -> dict:
    cols, rows = _annual(co["balance"])
    if not rows:
        return {}
    eq = _row({"rows": rows}, "Equity Capital") or []
    res = _row({"rows": rows}, "Reserves") or []
    debt = _row({"rows": rows}, "Borrowings") or []
    dep = _row({"rows": rows}, "Deposits")
    nw = [(a or 0) + (b or 0) for a, b in zip(eq, res)]
    de = [round(d / n, 2) if d is not None and n else None for d, n in zip(debt, nw)]
    _, rr = _annual(co["ratios_hist"])
    wc = {}
    for k in ("Debtor Days", "Inventory Days", "Days Payable", "Cash Conversion Cycle", "Working Capital Days", "ROCE %", "ROE %"):
        r = _row({"rows": rr}, k) if rr else None
        if r:
            wc[k] = r[-6:]
    ccc = wc.get("Cash Conversion Cycle") or wc.get("Working Capital Days")
    ccc_trend = None
    if ccc and len([x for x in ccc if x is not None]) >= 4:
        a = [x for x in ccc if x is not None]
        ccc_trend = "lengthening" if a[-1] > a[-4] + 10 else "shortening" if a[-1] < a[-4] - 10 else "stable"
    _, pr = _annual(co["pnl"])
    op = (_row({"rows": pr}, "Operating Profit") or [None])[-1] if pr else None
    net_debt_ebitda = round(debt[-1] / op, 2) if debt and op and op > 0 and debt[-1] is not None and not dep else None
    return {
        "years": cols[-6:],
        "debt": debt[-6:],
        "net_worth": nw[-6:],
        "de": de[-6:],
        "debt_to_ebitda": net_debt_ebitda,
        "deposits": dep[-6:] if dep else None,
        "wc": wc,
        "ccc_trend": ccc_trend,
        "is_lender": bool(dep) or "Financing Profit" in str(co.get("quarters", {}).get("rows", {}).keys()),
    }


def cash(co: dict) -> dict:
    cols, rows = _annual(co["cashflow"])
    if not rows:
        return {}
    cfo = _row({"rows": rows}, "Cash from Operating") or []
    cfi = _row({"rows": rows}, "Cash from Investing") or []
    _, pr = _annual(co["pnl"])
    pat = (_row({"rows": pr}, "Net Profit") or []) if pr else []
    n = min(len(cfo), len(pat))
    cfo_a, pat_a, cols_a = cfo[-n:], pat[-n:], cols[-n:]
    conv = [round(c / p * 100) if c is not None and p and p > 0 else None for c, p in zip(cfo_a, pat_a)]
    last5 = [(c, p) for c, p in zip(cfo_a[-5:], pat_a[-5:]) if c is not None and p is not None]
    cum = round(sum(c for c, _ in last5) / sum(p for _, p in last5) * 100) if last5 and sum(p for _, p in last5) > 0 else None
    fcf = [round(a + b) if a is not None and b is not None else None for a, b in zip(cfo[-6:], cfi[-6:])]
    verdict = None
    if cum is not None:
        verdict = "strong" if cum >= 90 else "adequate" if cum >= 70 else "weak"
    return {
        "years": cols_a[-6:],
        "cfo": cfo_a[-6:],
        "pat": pat_a[-6:],
        "conversion": conv[-6:],
        "cum5_pct": cum,
        "post_invest": fcf,
        "verdict": verdict,
        "note": "Post-investment cash = CFO + CFI (screener doesn't split capex from treasury moves); treat as a proxy for FCF.",
    }


# ------------------------------------------------------------------ bull / bear

def bull_bear(r: dict) -> dict:
    """Deterministic case from computed signals; the judge model rewrites it tighter if available."""
    bull, bear = [], []
    t, b, c, p = r.get("trajectory") or {}, r.get("balance") or {}, r.get("cash") or {}, r.get("promises") or {}
    for m in t.get("metrics", []):
        if m["label"] == "Revenue" and m["cagr3"] is not None:
            (bull if m["cagr3"] >= 12 else bear if m["cagr3"] < 6 else []).append(
                f"Revenue {'compounding' if m['cagr3'] >= 12 else 'growing only'} {m['cagr3']}% a year over 3 years ({m['trend'] or '—'} vs {m['cagr5']}% 5-yr)."
            )
        if m["label"] == "Net profit" and m["cagr3"] is not None and m["cagr3"] >= 15:
            bull.append(f"Net profit up {m['cagr3']}% CAGR over 3 years.")
    mg = t.get("margin")
    if mg:
        if mg["verdict"] == "expanding":
            bull.append(f"Margins expanding: {mg['avg3']}% avg over last 3 yrs vs {mg['avg_prior3']}% before.")
        elif mg["verdict"] == "compressing":
            bear.append(f"Margins compressing: {mg['avg3']}% avg over last 3 yrs vs {mg['avg_prior3']}% before.")
    if c.get("verdict") == "strong":
        bull.append(f"Profits convert to cash: 5-yr CFO/PAT {c['cum5_pct']}%.")
    elif c.get("verdict") == "weak" and not b.get("is_lender"):
        bear.append(f"Cash lags profit: 5-yr CFO/PAT only {c['cum5_pct']}%.")
    if not b.get("is_lender"):
        de = _last(b.get("de"))
        if de is not None:
            (bull if de < 0.3 else bear if de > 1.0 else []).append(f"{'Lightly' if de < 0.3 else 'Heavily'} geared: debt/equity {de}×.")
        if b.get("ccc_trend") == "lengthening":
            bear.append("Working-capital cycle lengthening — cash getting tied up.")
    roce = (b.get("wc") or {}).get("ROCE %")
    if roce and _last(roce) is not None:
        v = _last(roce)
        (bull if v >= 20 else bear if v < 12 else []).append(f"ROCE {v}% last year.")
    sc = p.get("scorecard") or {}
    if sc.get("checked"):
        rate = sc["delivered"] / sc["checked"]
        (bull if rate >= 0.6 else bear).append(
            f"Management delivered {sc['delivered']} of {sc['checked']} checkable guidance points over the last {p.get('n_calls', 4)} calls."
        )
    out = {"bull": bull[:5], "bear": bear[:5], "source": "rules"}
    if llm.available() and (bull or bear):
        facts = {k: r.get(k) for k in ("trajectory", "balance", "cash")}
        facts["promises"] = {"scorecard": sc, "items": (p.get("items") or [])[:10]}
        res = llm.json_call(
            "judge",
            "You are a buy-side analyst. Using ONLY the computed facts provided (no outside knowledge of the company), "
            "write a tight bull case and bear case: 3 bullets each, each under 22 words, each citing a number from the facts.",
            f"Company: {r['name']}\nFacts: {str(facts)[:12000]}",
            {
                "type": "object",
                "properties": {"bull": {"type": "array", "items": {"type": "string"}}, "bear": {"type": "array", "items": {"type": "string"}}},
                "required": ["bull", "bear"],
                "additionalProperties": False,
            },
            effort="medium",
        )
        if res:
            out = {**res, "source": "model", "rules": {"bull": bull, "bear": bear}}
    return out


# ------------------------------------------------------------------ network

def network_for(symbol: str, co: dict) -> dict:
    if symbol in graph.orgs():
        calls = graph.who_knows_org(symbol, limit=5)
    else:
        # unknown company: route by theme, using screener's industry chain
        themes = []
        for ind in co.get("industry", []):
            themes += graph.find_theme(ind)
        seen, calls = set(), []
        for t in themes:
            for p in t["people"]:
                if p["id"] not in seen and not p.get("implied"):
                    seen.add(p["id"])
                    calls.append({**p, "kind": "expert", "why": f"Your go-to on {t['theme']}", "score": p["strength"], "ask": f"Sector context for {co['name']}."})
        calls = calls[:5]
    mtg = [m for m in graph.meetings() if m["org"] == symbol]
    return {"calls": calls, "meetings": mtg}
