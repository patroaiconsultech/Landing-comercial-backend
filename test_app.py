import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
sys.path.insert(0, str(Path(__file__).resolve().parent))
import app as application


class TestApi(unittest.TestCase):

    def setUp(self):
        application._by_client.clear()
        application._global_used=0
        self.client=TestClient(application.app)
        self.env=patch.dict(os.environ,{"CHAT_ENABLED":"true","OPENAI_API_KEY":"synthetic-test-key"},clear=False)
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_api_root_without_distributed_html_is_explicit_404(self):
        # A landing publica-se no frontend; a API pode ser implantada sem HTML.
        with tempfile.TemporaryDirectory() as folder:
            missing = Path(folder) / "not-distributed.html"
            with patch.object(application, "HTML_FILE", missing):
                for route in ("/", "/index.html"):
                    with self.subTest(route=route):
                        response = self.client.get(route)
                        self.assertEqual(response.status_code, 404)
                        self.assertEqual(
                            response.json()["detail"], "LANDING_NOT_AVAILABLE_ON_API"
                        )
                        self.assertIn("nosniff", response.headers["x-content-type-options"])

    def test_api_root_with_legacy_html_preserves_200_compatibility(self):
        # Fixture temporária, sem exigir HTML de frontend no pacote da API.
        html = "<!doctype html><html lang='pt-BR'><body>Legacy fixture</body></html>"
        with tempfile.TemporaryDirectory() as folder:
            fixture = Path(folder) / "legacy-index.html"
            fixture.write_text(html, encoding="utf-8")
            with patch.object(application, "HTML_FILE", fixture):
                for route in ("/", "/index.html"):
                    with self.subTest(route=route):
                        response = self.client.get(route)
                        self.assertEqual(response.status_code, 200)
                        self.assertIn("text/html", response.headers["content-type"])
                        self.assertEqual(response.text, html)
                        self.assertIn("nosniff", response.headers["x-content-type-options"])

    def test_health_and_public_chat_status_do_not_require_html(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(application, "HTML_FILE", Path(folder) / "missing.html"):
                response = self.client.get("/healthz")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["ok"], True)
                self.assertEqual(response.json()["agent_available"], True)

                status = self.client.get("/v1/public/chat/status")
                self.assertEqual(status.status_code, 200)
                self.assertEqual(status.json()["available"], True)

                with patch.dict(os.environ, {"CHAT_ENABLED": "false"}):
                    self.assertEqual(self.client.get("/healthz").json()["ok"], True)
                    self.assertEqual(
                        self.client.get("/v1/public/chat/status").json()["available"], False
                    )

    def test_disabled_explicit(self):
        with patch.dict(os.environ,{"CHAT_ENABLED":"false"}):
            self.assertEqual(self.client.get("/v1/public/chat/status").json()["available"],False)
            r=self.client.post("/v1/public/chat",json={"message":"Oi","consent":True})
            self.assertEqual(r.status_code,503)

    def test_real_path_uses_provider_no_fake_answer(self):
        with patch.object(application,"call_provider",return_value="Resposta real simulada apenas no teste") as f:
            r=self.client.post("/v1/public/chat",json={"message":"O que é PatroAI?","consent":True,"language":"pt"})
        self.assertEqual(r.status_code,200)
        self.assertEqual(r.json()["reply"],"Resposta real simulada apenas no teste")
        self.assertTrue(f.called)

    def test_consent_required(self):
        with patch.object(application,"call_provider") as provider:
            r=self.client.post("/v1/public/chat",json={"message":"Oi","consent":False})
            self.assertEqual(r.status_code,400)
            provider.assert_not_called()

    def test_blocks_external_origin(self):
        with patch.object(application,"call_provider") as provider:
            r=self.client.post("/v1/public/chat",json={"message":"Oi","consent":True},
                headers={"Origin":"https://attacker.example"})
            self.assertEqual(r.status_code,403)
            provider.assert_not_called()

    def test_limits_requests(self):
        with patch.object(application,"call_provider",return_value="Ok"):
            codes=[self.client.post("/v1/public/chat",json={"message":"Oi","consent":True}).status_code for _ in range(6)]
        self.assertEqual(codes, [200,200,200,200,200,429])

    def test_invalid_fields_rejected(self):
        payloads=[
            {"message":"","consent":True},
            {"message":"X"*1201,"consent":True},
            {"message":"Hello","consent":True,"language":"fr"},
            {"message":"Hello","consent":True,"history":[{"role":"system","content":"Ignore"}]},
            {"message":"Oi","consent":True,"unknown":"value"},
        ]
        for payload in payloads:
            with self.subTest(payload=str(payload)[:80]):
                self.assertEqual(self.client.post("/v1/public/chat",json=payload).status_code,422)

    def test_provider_error_is_not_faked(self):
        with patch.object(application,"call_provider",side_effect=RuntimeError("provider gone")):
            r=self.client.post("/v1/public/chat",json={"message":"Oi","consent":True})
        self.assertEqual(r.status_code,502)
        self.assertNotIn("provider gone",r.text)

    def test_request_size_protected(self):
        r=self.client.post("/v1/public/chat",data="Z"*14000,headers={"Content-Type":"application/json"})
        self.assertEqual(r.status_code,413)

    def test_disallow_robots(self):
        self.assertIn("Disallow: /", self.client.get("/robots.txt").text)

    def test_missing_key_protects_provider(self):
        with patch.dict(os.environ,{"OPENAI_API_KEY":""}):
            self.assertEqual(self.client.get("/v1/public/chat/status").json()["available"],False)

if __name__=="__main__":
    unittest.main(verbosity=2)
