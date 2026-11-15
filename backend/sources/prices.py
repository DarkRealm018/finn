"""End-of-day price & volume from Yahoo Finance's public chart endpoint (no pandas needed)."""
from __future__ import annotations

import datetime as dt

from .. import store
from . import http

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
CHART ="https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range={rng}&interval=1d"


@store.cached("prices", ttl=6 * store.HOUR)
def eod(symbol: str, rng: str = "1y") -> dict | None:
    """Return {symbol, currency, bars:[{d,o,h,l,c,v}]} for an NSE symbol (falls back to BSE)."""
    for suffix in (".NS", ".BO"):
        r = http.get(CHART.format(sym=symbol + suffix, rng=rng))
        if not r.ok:
            continue
        res = (r.json().get("chart") or {}).get("result") or []
        if not res:
            continue
        res = res[0]
        ts = res.get("timestamp") or []
        q = (res.get("indicators") or {}).get("quote", [{}])[0]
        bars = []
        for i, t in enumerate(ts):
            c = q.get("close", [None])[i]
            if c is None:
                continue
            bars.append(
                {
                    "d": dt.datetime.fromtimestamp(t, IST).strftime("%Y-%m-%d"),
                    "o": q["open"][i],
                    "h": q["high"][i],
                    "l": q["low"][i],
                    "c": c,
                    "v": q["volume"][i] or 0,
                }
            )
        if bars:
            meta = res.get("meta", {})
            return {"symbol": symbol, "yahoo": symbol + suffix, "currency": meta.get("currency"), "bars": bars}
    return None


def stats(bars: list[dict], lookback: int = 20) -> dict:
    """Derived EOD stats used by alerts and cards."""
    if len(bars) < 2:
        return {}
    last, prev = bars[-1], bars[-2]
    window = bars[-lookback - 1 : -1] or bars[:-1]
    avg_vol = sum(b["v"] for b in window) / max(1, len(window))
    closes = [b["c"] for b in bars[-252:]]
    hi, lo = max(closes), min(closes)

    def ret(n):
        if len(bars) > n:
            return (last["c"] / bars[-n - 1]["c"] - 1) * 100
        return None

    return {
        "date": last["d"],
        "close": round(last["c"], 2),
        "chg_pct": round((last["c"] / prev["c"] - 1) * 100, 2),
        "vol": last["v"],
        "avg_vol": round(avg_vol),
        "vol_x": round(last["v"] / avg_vol, 2) if avg_vol else None,
        "r5": _r(ret(5)),
        "r21": _r(ret(21)),
        "r252": _r(ret(252)),
        "hi52": round(hi, 2),
        "lo52": round(lo, 2),
        "off_hi_pct": round((last["c"] / hi - 1) * 100, 1),
        "spark": [round(b["c"], 2) for b in bars[-60:]],
    }


def _r(x):
    return None if x is None else round(x, 1)


def day_moves(bars: list[dict], lookback: int = 20) -> list[dict]:
    """Per-day change and volume multiple for every bar (used to scan history for alerts)."""
    out = []
    for i in range(1, len(bars)):
        w = bars[max(0, i - lookback) : i]
        av = sum(b["v"] for b in w) / max(1, len(w))
        out.append(
            {
                "d": bars[i]["d"],
                "c": bars[i]["c"],
                "chg_pct": round((bars[i]["c"] / bars[i - 1]["c"] - 1) * 100, 2),
                "vol_x": round(bars[i]["v"] / av, 2) if av else None,
            }
        )
    return out
