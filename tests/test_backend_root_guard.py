from fastapi.testclient import TestClient
import app as module

def test_root_and_index_expose_explicit_not_found_when_html_is_not_shipped(monkeypatch, tmp_path):
    monkeypatch.setattr(module, "HTML_FILE", tmp_path / "missing.html")
    client = TestClient(module.app)
    for path in ("/", "/index.html"):
        response = client.get(path)
        assert response.status_code == 404
        assert response.json()["detail"] == "LANDING_NOT_AVAILABLE_ON_API"
