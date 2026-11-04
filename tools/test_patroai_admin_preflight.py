import importlib.util
import pathlib
import unittest

P = pathlib.Path(__file__).with_name("patroai_admin_preflight.py")
spec = importlib.util.spec_from_file_location("patroai_admin_preflight", P)
module = importlib.util.module_from_spec(spec)
import sys
sys.modules[spec.name] = module
spec.loader.exec_module(module)

class TestSettings(unittest.TestCase):
    def test_empty_env_fails_closed(self):
        s = module.get_settings({})
        self.assertFalse(s.flag or s.origin or s.keys or s.database_url)

    def test_valid_env(self):
        e = {
            "PATROAI_DASHBOARD_ENABLED": "true",
            "PATROAI_ADMIN_ORIGIN": "https://patroai.com.br",
            "DATABASE_URL": "postgres://user:pass@localhost:5432/db",
            **{k: (bytes([i])*32).hex() for i,k in enumerate(module.REQUIRED_KEYS,1)}
        }
        s = module.get_settings(e)
        self.assertTrue(all((s.flag,s.origin,s.keys,s.database_url)))
        self.assertTrue(s.db_url.startswith("postgresql+psycopg://"))

    def test_repeated_key_is_rejected(self):
        e = {k: "aa"*32 for k in module.REQUIRED_KEYS}
        self.assertFalse(module.get_settings(e).keys)

    def test_flag_and_origin_are_strict(self):
        e = {"PATROAI_DASHBOARD_ENABLED": "True",
             "PATROAI_ADMIN_ORIGIN": "https://patroai.com.br/"}
        s = module.get_settings(e)
        self.assertFalse(s.flag)
        self.assertFalse(s.origin)

if __name__ == "__main__":
    unittest.main()
