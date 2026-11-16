from pathlib import Path


def test_agent_commercial_scope_and_safety():
    text = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
    for term in [
        "viabilidade econômico-financeira",
        "consultores e auditores associados",
        "participação societária",
        "empresas estabelecidas",
        "Tecnologia",
        "provedor externo",
        "não é automaticamente sócia",
        "NÃO uma funcionalidade já demonstrada em produção",
        "Não invente",
        "DADOS",
    ]:
        assert term.casefold() in text.casefold(), term
    assert 'store=False' in text or '"store": False' in text or "'store': False" in text or '"store": false' in text, "verify provider storage policy"
