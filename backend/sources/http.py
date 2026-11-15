"""Shared HTTP sessions. NSE needs a browser-like session; everything else is plain."""
from __future__ import annotations

import threading
import time

import requests

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)

_lock = threading.Lock()
_nse: requests.Session | None = None
_nse_born = 0.0


def nse() -> requests.Session:
    """A session that looks like the NSE website calling its own API. Rotated every 10 min."""
    global _nse, _nse_born
    with _lock:
        if _nse is None or time.time() - _nse_born > 600:
            s = requests.Session()
            s.headers.update(
                {
                    "User-Agent": UA,
                    "Accept": "application/json, text/plain, */*",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Referer": "https://www.nseindia.com/",
                }
            )
            try:  # cookie warm-up; NSE often 403s the homepage but still sets cookies
                s.get("https://www.nseindia.com/", timeout=10)
            except Exception:
                pass
            _nse, _nse_born = s, time.time()
        return _nse


def get(url: str, *, session: requests.Session | None = None, timeout: int = 25, **kw) -> requests.Response:
    s = session or requests
    headers = kw.pop("headers", {}) or {}
    headers.setdefault("User-Agent", UA)
    last: Exception | None = None
    for attempt in range(3):
        try:
            r = s.get(url, headers=headers, timeout=timeout, **kw)
            if r.status_code in (429, 503):
                time.sleep(1.5 * (attempt + 1))
                continue
            return r
        except requests.RequestException as e:  # network blip — back off and retry
            last = e
            time.sleep(1.0 * (attempt + 1))
    if last:
        raise last
    return r  # type: ignore[possibly-undefined]
