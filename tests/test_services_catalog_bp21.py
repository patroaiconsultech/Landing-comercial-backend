"""Ensure the public sales agent presents CEOaaS conditionally, without powers."""
from pathlib import Path


def test_agent_prompts_reflect_business_plan_v21_without_promises():
    text = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
    system = text.split('SYSTEM_PROMPT = """', 1)[1].split('"""', 1)[0]
    diagnosis = text.split('DIAGNOSIS_PROMPT = """', 1)[1].split('"""', 1)[0]
    for mode in ("fracionada", "dedicada", "interina", "consultiva", "sob demanda"):
        assert mode in system, mode
    for term in ("CEOaaS", "CTOaaS", "direção comercial compartilhada",
                 "gestão financeira", "operações", "PMO", "aprovação humana"):
        assert term.casefold() in system.casefold(), term
    for caution in ("NÃO afirme", "não confere cargo de administrador",
                    "representação legal", "formalidades jurídicas",
                    "Não invente"):
        assert caution.casefold() in system.casefold(), caution
    assert "CEOaaS" in diagnosis and "CTOaaS" in diagnosis
    assert "não presuma disponibilidade" in diagnosis.lower()
    assert "provedor externo" in system.lower()
