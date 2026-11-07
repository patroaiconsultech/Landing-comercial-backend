"""Conservative daily AI spend reservation for R3-A (USD micro-units).

This is a per-request liability ceiling, not proof of the provider invoice.
A reservation is never refunded: timeouts and provider failures may be billable.
Only the explicitly priced model and text-only Responses contract are allowed.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_CEILING
import json
import os

MODEL_PRICE_USD_PER_M = {
    # Published standard API price at 2026-11-07, revalidation required before GO.
    "gpt-4.1-mini": (Decimal("0.40"), Decimal("1.60")),
    "gpt-4.1-mini-2025-04-14": (Decimal("0.40"), Decimal("1.60")),
}
MODEL_DEFAULT = "gpt-4.1-mini"
MAX_OUTPUT_TOKENS = 350
MAX_INPUT_UPPER_BOUND_TOKENS = 24000
INPUT_FRAME_OVERHEAD_TOKENS = 8192
MAX_DAILY_BUDGET_MICRO_USD = 50_000_000  # $50 hard safety ceiling

class BudgetUnavailable(ValueError):
    pass

def budget_settings(environ=None) -> tuple[str, int]:
    env = os.environ if environ is None else environ
    model = env.get("OPENAI_MODEL", MODEL_DEFAULT)
    if model not in MODEL_PRICE_USD_PER_M:
        raise BudgetUnavailable("Model is not on the reviewed allowlist")
    raw = env.get("PATROAI_DIAGNOSIS_DAILY_BUDGET_MICRO_USD", "")
    if not raw or not raw.isascii() or not raw.isdecimal() or len(raw) > 8:
        raise BudgetUnavailable("Daily budget must be set explicitly in USD micro-units")
    limit = int(raw)
    if not 1_000 <= limit <= MAX_DAILY_BUDGET_MICRO_USD:
        raise BudgetUnavailable("Daily budget is outside approved safety bounds")
    return model, limit

def reserve_amount_micro_usd(message: str, history, language: str, model: str) -> int:
    """Conservative cost upper bound of text-only Responses request.

    Uses UTF-8 byte count as an upper bound for text tokens, plus an 8192-token
    margin for protocol framing. No tool calls, image/audio, or token discounts.
    Fail closed if the input bound exceeds the approved maximum.
    """
    if model not in MODEL_PRICE_USD_PER_M:
        raise BudgetUnavailable("Unpriced model")
    if language not in ("pt", "en", "es"):
        raise BudgetUnavailable("Unsupported language")
    from app import DIAGNOSIS_PROMPT
    content = {
        "model": model,
        "instructions": DIAGNOSIS_PROMPT.format(language=language),
        "input": [{"role": h.role, "content": h.content} for h in history] +
                 [{"role": "user", "content": message}],
        "max_output_tokens": MAX_OUTPUT_TOKENS,
        "store": False,
    }
    input_upper = len(json.dumps(content, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) + INPUT_FRAME_OVERHEAD_TOKENS
    if input_upper > MAX_INPUT_UPPER_BOUND_TOKENS:
        raise BudgetUnavailable("Estimated input exceeds safe maximum")
    p_in, p_out = MODEL_PRICE_USD_PER_M[model]
    # USD/million tokens -> microUSD/token; round up, never reserve too little.
    estimated = Decimal(input_upper) * p_in + Decimal(MAX_OUTPUT_TOKENS) * p_out
    return int(estimated.to_integral_value(rounding=ROUND_CEILING))
