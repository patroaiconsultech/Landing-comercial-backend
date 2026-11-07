"""R3-A financial cap checks (unit + contract, no PostgreSQL writes)."""
from types import SimpleNamespace
import pytest
from diagnosis.budget import (
    BudgetUnavailable, budget_settings, reserve_amount_micro_usd,
    MAX_OUTPUT_TOKENS, MAX_INPUT_UPPER_BOUND_TOKENS,
)

def test_requires_explicit_daily_micro_usd_budget():
    for bad in ("", "0", "-1", "1.2", "unlimited", "999999999"):
        with pytest.raises(BudgetUnavailable):
            budget_settings({"OPENAI_MODEL": "gpt-4.1-mini",
                             "PATROAI_DIAGNOSIS_DAILY_BUDGET_MICRO_USD": bad})

def test_unknown_model_fails_closed():
    with pytest.raises(BudgetUnavailable):
        budget_settings({"OPENAI_MODEL": "gpt-6-astra",
                         "PATROAI_DIAGNOSIS_DAILY_BUDGET_MICRO_USD": "1000000"})

def test_valid_budget_config():
    assert budget_settings({"PATROAI_DIAGNOSIS_DAILY_BUDGET_MICRO_USD": "1000000"}) == (
        "gpt-4.1-mini", 1_000_000)

def test_cost_reservation_is_positive_monotone_and_no_input_overrun():
    model="gpt-4.1-mini"
    short=reserve_amount_micro_usd("oi", [], "pt", model)
    long=reserve_amount_micro_usd("x"*1200, [], "pt", model)
    hist=[SimpleNamespace(role="user",content="oi"),SimpleNamespace(role="assistant",content="ok")]
    with_history=reserve_amount_micro_usd("oi", hist, "pt", model)
    assert short > 0 and long >= short and with_history >= short
    assert MAX_OUTPUT_TOKENS == 350 and MAX_INPUT_UPPER_BOUND_TOKENS == 24000
    with pytest.raises(BudgetUnavailable):
        reserve_amount_micro_usd("x"*30000, [], "pt", model)

def test_route_reserves_budget_before_provider(monkeypatch):
    from pathlib import Path
    code=(Path(__file__).resolve().parents[1]/"diagnosis/router.py").read_text()
    assert 'take_quota(db,cfg,"ai-budget-microusd"' in code
    assert code.index('take_quota(db,cfg,"ai-budget-microusd"') < code.index('answer=await model_reply(')
    assert 'pg_advisory_xact_lock' in code
    # Never refund a reservation after an ambiguous timeout/provider response.
    assert 'row.count-=liability' not in code

def test_public_status_reports_unavailable_without_financial_config(monkeypatch):
    from fastapi.testclient import TestClient
    from app import app
    for key in ("PATROAI_DIAGNOSIS_DAILY_BUDGET_MICRO_USD","PATROAI_DIAGNOSIS_ENABLED",
                "PATROAI_DIAGNOSIS_PRIVACY_APPROVED"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("PATROAI_DIAGNOSIS_ENABLED","true")
    monkeypatch.setenv("PATROAI_DIAGNOSIS_PRIVACY_APPROVED","true")
    with TestClient(app) as client:
        r=client.get("/v1/public/diagnoses/status")
        assert r.status_code==200
        assert r.json()=={"available":False}
