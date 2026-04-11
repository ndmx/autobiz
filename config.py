"""
config.py — Unified configuration loader for autobiz.

Priority order (highest → lowest):
  1. config.json (saved via settings page)
  2. Environment variables (env/.env or shell)
  3. Built-in defaults

Usage:
    from config import load_config, llm_score_call, get_research_client
"""

import json
import os
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).parent
CONFIG_PATH = PROJECT_DIR / "config.json"
ENV_PATHS = [
    PROJECT_DIR / ".env",
    PROJECT_DIR / "env" / ".env",
]

DEFAULTS = {
    "scoring": {
        "provider": "anthropic",
        "model": "claude-opus-4-6",
        "api_key": "",
    },
    "research": {
        "provider": "xai",
        "model": "grok-4.20-multi-agent-latest",
        "api_key": "",
    },
    "defaults": {
        "location": "Pennsylvania",
        "budget_min": 75000,
        "budget_max": 250000,
    },
}

# Maps provider name → env var name for API key fallback
ENV_KEY_MAP = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai":    "OPENAI_API_KEY",
    "xai":       "XAI_API_KEY",
    "gemini":    "GEMINI_API_KEY",
}

# Suggested models per provider (used by the settings UI)
PROVIDER_MODELS = {
    "anthropic": [
        "claude-opus-4-6",
        "claude-sonnet-4-6",
        "claude-haiku-4-5-20251001",
    ],
    "openai": [
        "gpt-4o",
        "gpt-4o-mini",
        "gpt-4-turbo",
        "o1-preview",
    ],
    "xai": [
        "grok-4.20-multi-agent-latest",
        "grok-4.20-multi-agent-0309",
        "grok-4",
        "grok-3",
    ],
    "gemini": [
        "gemini-2.0-flash",
        "gemini-1.5-pro",
        "gemini-1.5-flash",
    ],
}

MAX_RETRIES = 3
RETRY_DELAY = 2


# ---------------------------------------------------------------------------
# .env loading
# ---------------------------------------------------------------------------

def _strip_env_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def load_env_files(paths: list[Path] = None) -> None:
    """Load project .env files without overriding already-exported variables."""
    for path in paths or ENV_PATHS:
        if not path.exists():
            continue
        try:
            lines = path.read_text().splitlines()
        except Exception:
            continue
        for line in lines:
            raw = line.strip()
            if not raw or raw.startswith("#") or "=" not in raw:
                continue
            key, value = raw.split("=", 1)
            key = key.strip()
            if key.startswith("export "):
                key = key[len("export "):].strip()
            if not key or key in os.environ:
                continue
            os.environ[key] = _strip_env_quotes(value)


# Make env/.env available to CLI scripts, Flask routes, and client factories.
load_env_files()


# ---------------------------------------------------------------------------
# Config load / save
# ---------------------------------------------------------------------------

def load_config() -> dict:
    """Return merged config. Never raises."""
    import copy
    cfg = copy.deepcopy(DEFAULTS)

    if CONFIG_PATH.exists():
        try:
            saved = json.loads(CONFIG_PATH.read_text())
            for section in ("scoring", "research", "defaults"):
                if section in saved and isinstance(saved[section], dict):
                    cfg[section].update(saved[section])
        except Exception:
            pass

    return cfg


def save_config(data: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(data, indent=2))


# ---------------------------------------------------------------------------
# API key resolution
# ---------------------------------------------------------------------------

def resolve_api_key(provider: str, stored_key: str) -> str:
    """
    Returns stored_key if non-empty, else falls back to the env var.
    Raises ValueError with a clear message if neither is set.
    """
    key = stored_key.strip() if stored_key else ""
    if not key:
        env_var = ENV_KEY_MAP.get(provider, "")
        key = os.environ.get(env_var, "").strip()
    if not key:
        env_var = ENV_KEY_MAP.get(provider, f"{provider.upper()}_API_KEY")
        raise ValueError(
            f"No API key found for provider '{provider}'. "
            f"Set it in the settings page or via the {env_var} environment variable."
        )
    return key


# ---------------------------------------------------------------------------
# Client factories
# ---------------------------------------------------------------------------

def get_research_client(cfg: dict = None):
    """Return an XaiClient configured for the research model."""
    from xai_sdk import Client as XaiClient
    if cfg is None:
        cfg = load_config()
    key = resolve_api_key(cfg["research"]["provider"], cfg["research"].get("api_key", ""))
    return XaiClient(api_key=key)


def get_scoring_client(cfg: dict = None):
    """
    Return (provider_str, client_object) for the scoring model.
    Supports: anthropic, openai, xai, gemini.
    """
    if cfg is None:
        cfg = load_config()
    provider = cfg["scoring"]["provider"]
    key = resolve_api_key(provider, cfg["scoring"].get("api_key", ""))

    if provider == "anthropic":
        import anthropic
        return provider, anthropic.Anthropic(api_key=key)

    elif provider == "openai":
        from openai import OpenAI
        return provider, OpenAI(api_key=key)

    elif provider == "xai":
        from openai import OpenAI
        return provider, OpenAI(api_key=key, base_url="https://api.x.ai/v1")

    elif provider == "gemini":
        try:
            import google.generativeai as genai
        except ImportError:
            raise ImportError("Install google-generativeai: uv add google-generativeai")
        genai.configure(api_key=key)
        return provider, genai

    else:
        raise ValueError(f"Unknown scoring provider: '{provider}'")


# ---------------------------------------------------------------------------
# Provider-agnostic scoring call
# ---------------------------------------------------------------------------

def llm_score_call(prompt: str, cfg: dict = None, max_tokens: int = 1800) -> str:
    """
    Provider-agnostic LLM call for scoring. Returns raw text.
    Retries on rate-limit errors.
    """
    if cfg is None:
        cfg = load_config()

    provider = cfg["scoring"]["provider"]
    model = cfg["scoring"]["model"]
    _, client = get_scoring_client(cfg)

    for attempt in range(MAX_RETRIES):
        try:
            if provider == "anthropic":
                import anthropic
                resp = client.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    messages=[{"role": "user", "content": prompt}],
                )
                return resp.content[0].text.strip()

            elif provider in ("openai", "xai"):
                resp = client.chat.completions.create(
                    model=model,
                    max_tokens=max_tokens,
                    messages=[{"role": "user", "content": prompt}],
                )
                return resp.choices[0].message.content.strip()

            elif provider == "gemini":
                m = client.GenerativeModel(model)
                resp = m.generate_content(prompt)
                return resp.text.strip()

            else:
                raise ValueError(f"Unknown provider: {provider}")

        except Exception as e:
            err = str(e).lower()
            is_rate_limit = any(x in err for x in ("rate", "429", "limit", "quota"))
            if is_rate_limit and attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY * (attempt + 1))
                continue
            if attempt == MAX_RETRIES - 1:
                raise
            time.sleep(RETRY_DELAY)

    raise RuntimeError("llm_score_call: exhausted retries")


# ---------------------------------------------------------------------------
# Quick connection test (used by /test-key endpoint in app.py)
# ---------------------------------------------------------------------------

def test_connection(provider: str, model: str, api_key: str) -> dict:
    """
    Makes a minimal API call. Returns {"ok": True, "latency_ms": N} or {"ok": False, "error": "..."}.
    """
    import time as _time
    mini_cfg = {
        "scoring": {"provider": provider, "model": model, "api_key": api_key},
        "research": {"provider": "xai", "model": "", "api_key": ""},
        "defaults": {},
    }
    start = _time.monotonic()
    try:
        result = llm_score_call("Reply with only the word OK.", cfg=mini_cfg, max_tokens=5)
        ms = int((_time.monotonic() - start) * 1000)
        return {"ok": True, "latency_ms": ms, "response": result[:20]}
    except Exception as e:
        return {"ok": False, "error": str(e)}
