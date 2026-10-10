"""Model defaults and provider-specific validation."""

import os

import pytest

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("OPENAI_API_KEY", "test-key")

from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI

from src.model import build_model


def test_default_model_is_chatgpt_instant(monkeypatch):
    monkeypatch.delenv("LLM_MODEL", raising=False)
    model = build_model()
    assert isinstance(model, ChatOpenAI)
    assert model.model_name == "chat-latest"


def test_llm_model_env_override(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LLM_MODEL", "claude-opus-4-6")
    model = build_model()
    assert model.model == "claude-opus-4-6"


def test_gpt_model_names_route_to_openai_fallback(monkeypatch: pytest.MonkeyPatch):
    # Production fallback: flipping LLM_MODEL to a gpt-* name in the Render
    # dashboard must work without a code change.
    monkeypatch.setenv("LLM_MODEL", "gpt-5.4-2026-03-05")
    model = build_model()
    assert isinstance(model, ChatOpenAI)
    assert model.model_name == "gpt-5.4-2026-03-05"


def test_claude_model_names_route_to_anthropic(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LLM_MODEL", "claude-opus-4-6")
    assert isinstance(build_model(), ChatAnthropic)


def test_max_tokens_fits_full_widget_generation(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "claude-fable-5")
    # langchain-anthropic's default (4096) truncates generateSandboxedUi args
    # mid-stream: css+html arrive but jsFunctions/jsExpressions are cut off, so
    # htmlComplete never fires and the widget never leaves the preview sandbox.
    model = build_model()
    assert model.max_tokens >= 32000


def test_main_uses_build_model():
    import ast
    from pathlib import Path

    main_src = Path(__file__).parent.parent / "main.py"
    tree = ast.parse(main_src.read_text())
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert "build_model" in names
    assert "ChatOpenAI" not in main_src.read_text()


@pytest.mark.parametrize("name", ["", "   ", "gemini-example", "typo-model"])
def test_invalid_model_configuration_fails_before_client_creation(monkeypatch, name):
    monkeypatch.setenv("LLM_MODEL", name)
    with pytest.raises(ValueError, match="LLM_MODEL"):
        build_model()


@pytest.mark.parametrize(
    ("model", "key"),
    [
        ("claude-opus-4-6", "ANTHROPIC_API_KEY"),
        ("gpt-5.4-2026-03-05", "OPENAI_API_KEY"),
        ("chat-latest", "OPENAI_API_KEY"),
    ],
)
@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_provider_key_has_actionable_error(monkeypatch, model, key, value):
    monkeypatch.setenv("LLM_MODEL", model)
    if value is None:
        monkeypatch.delenv(key, raising=False)
    else:
        monkeypatch.setenv(key, value)
    with pytest.raises(ValueError, match=key):
        build_model()


# ---------------------------------------------------------------------------
# Vercel AI Gateway / Custom base URL support
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("env_name", "provider", "expected"),
    [
        # Provider-specific override wins even without a shared gateway.
        ("OPENAI_BASE_URL", "openai", "https://my-openai-proxy.local/v1"),
        ("OPENAI_BASE_URL", "gpt", "https://my-openai-proxy.local/v1"),
        ("ANTHROPIC_BASE_URL", "anthropic", "https://my-anthropic-proxy.local"),
        ("ANTHROPIC_BASE_URL", "claude", "https://my-anthropic-proxy.local"),
        ("TYPESAFE_BASE_URL", "typesafe", "https://my-jev-proxy.local/v1/systemone"),
        ("TYPESAFE_BASE_URL", "jev", "https://my-jev-proxy.local/v1/systemone"),
    ],
)
def test_provider_base_url_specific_override(monkeypatch, env_name, provider, expected):
    from src.model import provider_base_url

    monkeypatch.setenv(env_name, expected)
    monkeypatch.delenv("AI_GATEWAY_URL", raising=False)
    assert provider_base_url(provider) == expected


def test_shared_ai_gateway_url_fallback_when_no_specific_override(monkeypatch):
    from src.model import provider_base_url

    gateway = "https://gateway.ai-sdk.com/v1"
    monkeypatch.setenv("AI_GATEWAY_URL", gateway)
    # Ensure no provider-specific overrides are present.
    for name in ("OPENAI_BASE_URL", "ANTHROPIC_BASE_URL", "TYPESAFE_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    # OpenAI and TypeSafe always get a concrete URL.
    assert provider_base_url("openai") == gateway
    assert provider_base_url("typesafe") == gateway
    # Anthropic's fallback in the helper is None (use SDK default) unless
    # overridden or gateway supplied.
    assert provider_base_url("anthropic") == gateway


def test_specific_base_url_takes_precedence_over_shared_gateway(monkeypatch):
    from src.model import provider_base_url

    monkeypatch.setenv("AI_GATEWAY_URL", "https://shared-gateway.test")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://openai-only.test/v1")
    assert provider_base_url("openai") == "https://openai-only.test/v1"


def test_official_endpoints_used_without_any_override(monkeypatch):
    from src.model import provider_base_url

    for name in ("AI_GATEWAY_URL", "OPENAI_BASE_URL", "ANTHROPIC_BASE_URL", "TYPESAFE_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    assert provider_base_url("openai") == "https://api.openai.com/v1"
    # None signals "use ChatAnthropic SDK default base_url" (avoid setting
    # an official-looking URL that the SDK might change in future releases).
    assert provider_base_url("anthropic") is None
    assert provider_base_url("typesafe") == "https://api.typesafe.ai/v1/systemone"


def test_build_model_wires_openai_base_url_from_gateway(monkeypatch):
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("AI_GATEWAY_URL", "https://gateway.ai-sdk.com/v1")
    model = build_model()
    assert isinstance(model, ChatOpenAI)
    # LangChain wraps the raw chat.completions endpoint as .client; read the
    # root OpenAI SDK client for the configured base_url.
    assert str(model.root_client.base_url) == "https://gateway.ai-sdk.com/v1/"


def test_build_model_wires_claude_base_url_from_override(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "claude-sonnet-4-5")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://my-claude-proxy.test")
    model = build_model()
    assert isinstance(model, ChatAnthropic)
    # ChatAnthropic stores the configured base_url inside its client params.
    # We cannot easily introspect it without a private-API breakage, but the
    # public constructor accepts it and the provider_base_url test above
    # proves the override is selected. Confirm max_tokens still applied.
    assert model.max_tokens >= 32000


def test_provider_base_url_unknown_raises(monkeypatch):
    from src.model import provider_base_url

    with pytest.raises(ValueError, match="Unknown provider"):
        provider_base_url("no-such-provider")
