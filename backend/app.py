"""FINN API + static frontend. Run:  uvicorn backend.app:app --port 8000"""
from __future__ import annotations

import os
import pathlib
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import store
from .engine import ask, digest, llm, research
from .network import graph
from .sources import prices

ROOT = pathlib.Path(__file__).resolve().parents[1]
WEB = ROOT / "web"

app = FastAPI(title="FINN", version="0.1")
app.add_middleware(GZipMiddleware, minimum_size=1024)

# --- access control -----------------------------------------------------------------
# The network is personal data. In a deployment every /api call carries the investor's token;
# the demo uses a single shared token from env (unset = open, for local runs).
TOKEN = os.getenv("FINN_TOKEN")


@app.middleware("http")
async def auth(request: Request, call_next):
    if TOKEN and request.url.path.startswith("/api/") and request.url.path != "/api/meta":
        if request.headers.get("x-finn-token") != TOKEN and request.query_params.get("t") != TOKEN:
            return JSONResponse({"error": "unauthorised"}, status_code=401)
    t = time.time()
    resp = await call_next(request)
    resp.headers["x-finn-ms"] = str(int((time.time() - t) * 1000))
    return resp


@app.get("/api/meta")
def meta():
    return {"llm": llm.available(), "models": llm.MODELS, "usage": llm.USAGE, "auth": bool(TOKEN)}


@app.get("/api/portfolio")
def portfolio():
    pf = digest.portfolio()
    out = []
    for h in pf["holdings"]:
        p = prices.eod(h["symbol"])
        st = prices.stats(p["bars"]) if p else {}
        out.append({**h, "name": graph.org_name(h["symbol"]), "sector": graph.orgs().get(h["symbol"], {}).get("sector"), **st})
    return {"holdings": out, "research": pf["research"]}


@app.get("/api/digest")
def get_digest(hours: int = 24):
    hours = max(6, min(hours, 24 * 45))
    d = digest.build(hours)
    d["warm"] = graph.warm_reasons(d["headline"] + d["brief"])[:4]
    return d


@app.get("/api/alerts")
def get_alerts(hours: int = 168, price: float | None = None, volume: float | None = None):
    return digest.alerts(hours, price_move_pct=price, volume_multiple=volume)


class Prefs(BaseModel):
    price_move_pct: float | None = None
    volume_multiple: float | None = None
    alert_categories: list[str] | None = None
    window_hours: int | None = None


@app.post("/api/prefs")
def set_prefs(p: Prefs):
    cur = digest.prefs()
    for k, v in p.model_dump(exclude_none=True).items():
        cur[k] = v
    store.save_json("prefs.json", cur)
    return cur


@app.get("/api/prefs")
def get_prefs():
    return digest.prefs()


@app.get("/api/research/{symbol}")
def get_research(symbol: str):
    r = research.build(symbol)
    if not r:
        raise HTTPException(404, f"No fundamentals found for {symbol}")
    return r


@app.get("/api/network/company/{symbol}")
def net_company(symbol: str):
    return {"symbol": symbol, "name": graph.org_name(symbol), "calls": graph.who_knows_org(symbol.upper(), limit=6)}


@app.get("/api/network/person")
def net_person(name: str):
    r = ask.answer(f"who knows {name}")
    return r


@app.get("/api/network/expertise")
def net_expertise():
    return {"themes": graph.expertise_map()}


@app.get("/api/network/warm")
def net_warm(hours: int = 24 * 14):
    d = digest.build(hours)
    return {"warm": graph.warm_reasons(d["headline"] + d["brief"])}


@app.get("/api/network/graph")
def net_graph():
    return graph.graph()


@app.get("/api/network/contacts")
def net_contacts():
    return {
        "me": graph.me(),
        "contacts": [
            graph._brief(c)
            | {
                "expertise": c.get("expertise", []),
                "employers": [{**e, "org_name": graph.org_name(e["org"]) if e["org"] in graph.orgs() else e["org"]} for e in c["employers"]],
                "notes": c.get("notes", ""),
                "knows": c.get("knows", []),
            }
            for c in graph.contacts()
        ],
    }


class Ask(BaseModel):
    q: str


@app.post("/api/ask")
def post_ask(a: Ask):
    return ask.answer(a.q[:400])


# --- static frontend ------------------------------------------------------------------
app.mount("/static", StaticFiles(directory=WEB), name="static")


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


@app.get("/{path:path}")
def spa(path: str):
    f = WEB / path
    if f.is_file() and WEB in f.resolve().parents:
        return FileResponse(f)
    return FileResponse(WEB / "index.html")
