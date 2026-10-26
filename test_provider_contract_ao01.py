"""Offline tests for provider transport contract. NO external network and NO real keys."""
import json
import os
from unittest import TestCase
from unittest.mock import patch

import app

class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self, size=-1):
        return self.payload[:size] if size >= 0 else self.payload

class ProviderContractTests(TestCase):
    def test_request_contract_and_output_parsing_without_network(self):
        captured = []
        def fake_urlopen(req, timeout):
            captured.append((req, timeout))
            return FakeResponse({"output": [
                {"type": "reasoning", "content": []},
                {"type": "message", "content": [{"type": "output_text", "text": "Resposta de ensaio, não real."}]}
            ]})
        with patch.dict(os.environ, {"OPENAI_API_KEY": "ONLY_SYNTHETIC_TEST_SECRET",
                                     "OPENAI_MODEL": "gpt-4.1-mini"}, clear=False):
            with patch.object(app, "urlopen", fake_urlopen):
                answer = app.call_provider("Pergunta de ensaio", [], "pt")
        self.assertEqual(answer, "Resposta de ensaio, não real.")
        self.assertEqual(len(captured), 1)
        req, timeout = captured[0]
        self.assertEqual(req.full_url, "https://api.openai.com/v1/responses")
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(timeout, 18)
        self.assertEqual(req.get_header("Authorization"), "Bearer ONLY_SYNTHETIC_TEST_SECRET")
        body = json.loads(req.data.decode("utf-8"))
        self.assertIs(body["store"], False)
        self.assertEqual(body["max_output_tokens"], 350)
        self.assertEqual(body["model"], "gpt-4.1-mini")
        self.assertEqual(body["input"][-1], {"role": "user", "content": "Pergunta de ensaio"})
        self.assertIn("português do Brasil", body["instructions"])

    def test_missing_key_fails_closed(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=False):
            with patch.object(app, "urlopen") as outbound:
                with self.assertRaisesRegex(RuntimeError, "provider unavailable"):
                    app.call_provider("Olá", [], "pt")
                outbound.assert_not_called()

    def test_network_error_is_sanitized(self):
        from urllib.error import URLError
        with patch.dict(os.environ, {"OPENAI_API_KEY": "ONLY_SYNTHETIC_TEST_SECRET"}, clear=False):
            with patch.object(app, "urlopen", side_effect=URLError("synthetic sensitive failure")):
                with self.assertRaisesRegex(RuntimeError, "^provider error$") as cm:
                    app.call_provider("Pergunta", [], "pt")
                self.assertNotIn("synthetic sensitive failure", str(cm.exception))

    def test_empty_provider_output_is_not_faked(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "ONLY_SYNTHETIC_TEST_SECRET"}, clear=False):
            with patch.object(app, "urlopen", return_value=FakeResponse({"output": []})):
                with self.assertRaisesRegex(RuntimeError, "empty provider reply"):
                    app.call_provider("Pergunta", [], "pt")
