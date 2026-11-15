"""Model router.

Two tiers, chosen per step (not per app):
  • "extract" — Claude Haiku 5.5. Bulk, schema-bound work: structuring a filing's person
    profile, pulling guidance statements out of a 30-page transcript, classifying a filing
    the rules couldn't. ~$0.10/M input tokens; called once per document, cached forever.
  • "judge"   — Claude Opus 5.5 at low/medium effort. Only where reasoning matters: matching
    promises to delivered numbers, writing the bull/bear case. A handful of calls per company.

Every call is content-addressed in the disk cache, so the same transcript is never paid for
twice. With no ANTHROPIC_API_KEY, FINN runs fully on its deterministic pipeline and every
caller has a non-LLM fallback — `available()` lets the UI say which path produced an answer.
"""
from __future__ import annotations

import json
import os

from .. import store

MODELS = {
    "extract": os.getenv("FINN_EXTRACT_MODEL", "claude-haiku-5-5"),
    "judge": os.getenv("FINN_JUDGE_MODEL", "claude-opus-5-5"),
}

_client = None
USAGE = {"calls": 0, "cache_hits": 0, "input_tokens": 0, "output_tokens": 0}


def available() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY")) and os.getenv("FINN_OFFLINE") != "1"


def _get_client():
    global _client
    if _client is None:
        import anthropic

        _client = anthropic.Anthropic()
    return _client


def json_call(tier: str, system: str, user: str, schema: dict, *, effort: str = "low", max_tokens: int = 4000) -> dict | None:
    """Schema-constrained call. Returns parsed JSON or None (caller falls back)."""
    if not available():
        return None
    model = MODELS[tier]
    key = json.dumps([model, system, user, schema, effort], sort_keys=True)
    hit = store.get("llm", key, ttl=None)
    if hit is not None:
        USAGE["cache_hits"] += 1
        return hit
    try:
        import anthropic

        kwargs = dict(
            model=model,
            max_tokens=max_tokens,
            # The system prompt is the stable prefix → cacheable across companies.
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        )
        resp = _get_client().messages.create(**kwargs)
        if resp.stop_reason == "refusal":
            return None
        USAGE["calls"] += 1
        USAGE["input_tokens"] += resp.usage.input_tokens
        USAGE["output_tokens"] += resp.usage.output_tokens
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        out = json.loads(text)
        store.put("llm", key, out)
        return out
    except (anthropic.APIStatusError, anthropic.APIConnectionError, json.JSONDecodeError) as e:
        print(f"[llm] {tier} call failed, falling back: {type(e).__name__}: {e}")
        return None
