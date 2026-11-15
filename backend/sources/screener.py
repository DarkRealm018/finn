"""screener.in: standardised multi-year financials + links to annual reports and concall transcripts.

We use screener for (a) numbers already lifted from the company's own filings and (b) the
document index. We deliberately do NOT use screener's "pros/cons", "AI summary" or peer
commentary — the pull side stays on primary data.
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from .. import store
from . import http

URL = "https://www.screener.in/company/{sym}/{view}"


def _num(s: str):
    s = s.replace(",", "").replace("%", "").replace("₹", "").strip()
    if s in ("", "-", "--"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _table(sec) -> dict | None:
    if sec is None:
        return None
    t = sec.select_one("table")
    if t is None:
        return None
    heads = [th.get_text(strip=True) for th in t.select("thead th")][1:]
    rows = {}
    for tr in t.select("tbody tr"):
        tds = tr.select("td")
        if not tds:
            continue
        label = re.sub(r"\s*\+\s*$", "", tds[0].get_text(" ", strip=True)).strip()
        if not label or label == "Raw PDF":
            continue
        rows[label] = [_num(td.get_text(" ", strip=True)) for td in tds[1:]]
    return {"cols": heads, "rows": rows}


@store.cached("screener", ttl=12 * store.HOUR)
def company(symbol: str) -> dict | None:
    html = None
    for view in ("consolidated/", ""):
        r = http.get(URL.format(sym=symbol, view=view))
        if r.ok and "section" in r.text and 'id="quarters"' in r.text:
            soup = BeautifulSoup(r.text, "html.parser")
            # screener serves an empty consolidated view for standalone-only companies
            q = _table(soup.select_one("section#quarters"))
            if q and q["rows"]:
                html, basis = r.text, ("Consolidated" if view else "Standalone")
                break
    if html is None:
        return None
    soup = BeautifulSoup(html, "html.parser")

    name = (soup.select_one("h1") or soup.select_one("title")).get_text(strip=True)
    about = soup.select_one(".company-profile .about")
    key_points = soup.select_one(".company-profile .commentary")
    ratios = {}
    for li in soup.select("#top-ratios li"):
        n = li.select_one(".name")
        v = li.select_one(".value")
        if n and v:
            ratios[n.get_text(strip=True)] = " ".join(v.get_text(" ", strip=True).split())

    docs = {"annual_reports": [], "concalls": []}
    for a in soup.select("section#documents .annual-reports a"):
        title = a.get_text(" ", strip=True)
        m = re.search(r"(20\d\d)", title)
        docs["annual_reports"].append({"title": title, "year": int(m.group(1)) if m else None, "url": a.get("href")})
    for li in soup.select("section#documents .concalls li"):
        date = li.select_one("div")
        item = {"date": date.get_text(strip=True) if date else None}
        for a in li.select("a"):
            kind = a.get_text(strip=True).lower()
            if kind in ("transcript", "ppt", "rec", "notes"):
                item[kind] = a.get("href")
        docs["concalls"].append(item)

    industry = [a.get_text(strip=True) for a in soup.select("section#peers a[href*='/market/']")]

    return {
        "symbol": symbol,
        "name": name,
        "basis": basis,
        "about": about.get_text(" ", strip=True) if about else "",
        "key_points": key_points.get_text("\n", strip=True) if key_points else "",
        "ratios": ratios,
        "industry": industry[:4],
        "quarters": _table(soup.select_one("section#quarters")),
        "pnl": _table(soup.select_one("section#profit-loss")),
        "balance": _table(soup.select_one("section#balance-sheet")),
        "cashflow": _table(soup.select_one("section#cash-flow")),
        "ratios_hist": _table(soup.select_one("section#ratios")),
        "shareholding": _table(soup.select_one("section#shareholding")),
        "docs": docs,
        "is_bank": _table(soup.select_one("section#quarters"))["rows"].get("Revenue") is not None
        and "Financing Profit" in str(soup.select_one("section#quarters")),
    }
