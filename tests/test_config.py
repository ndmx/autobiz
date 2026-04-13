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

    def test_custom_providers_are_limited_and_added_to_model_options(self):
        providers = config.clean_provider_configs([
            {"name": "Local Ollama", "kind": "local", "base_url": "http://127.0.0.1:11434/v1", "models": "llama3.1, mistral"},
            {"name": "Hosted API", "kind": "hosted", "base_url": "https://api.example.com/v1", "env_key": "HOSTED_API_KEY", "models": "model-a"},
            {"name": "Another Host", "kind": "hosted", "base_url": "https://open.example.com/v1", "models": "model-b"},
            {"name": "Lab Box", "kind": "local", "base_url": "http://127.0.0.1:8000/v1", "models": "model-c"},
            {"name": "Too Many", "kind": "hosted", "base_url": "https://extra.example.com/v1", "models": "model-d"},
        ])

        self.assertEqual(len(providers), config.MAX_EXTRA_PROVIDERS)
        merged = config.provider_models_for_config({"providers": providers})
        self.assertIn("custom-local-ollama", merged)
        self.assertEqual(merged["custom-local-ollama"][0], "llama3.1")


if __name__ == "__main__":
    unittest.main()
