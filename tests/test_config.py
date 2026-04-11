import os
import tempfile
import unittest
from pathlib import Path

import config


class ConfigTests(unittest.TestCase):
    def test_load_env_files_sets_missing_values(self):
        key = "AUTOBIZ_TEST_ENV_LOAD"
        os.environ.pop(key, None)
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text(f"{key}='loaded value'\n")
            config.load_env_files([env_path])

        self.assertEqual(os.environ[key], "loaded value")
        os.environ.pop(key, None)

    def test_load_env_files_does_not_override_existing_values(self):
        key = "AUTOBIZ_TEST_ENV_KEEP"
        os.environ[key] = "already exported"
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text(f"{key}=from file\n")
            config.load_env_files([env_path])

        self.assertEqual(os.environ[key], "already exported")
        os.environ.pop(key, None)

    def test_resolve_api_key_uses_provider_env_fallback(self):
        os.environ["OPENAI_API_KEY"] = "env-openai-key"
        self.assertEqual(config.resolve_api_key("openai", ""), "env-openai-key")
        os.environ.pop("OPENAI_API_KEY", None)


if __name__ == "__main__":
    unittest.main()
