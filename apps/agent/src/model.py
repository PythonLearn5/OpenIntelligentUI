"""Chat model factory for the agent.

Endpoint resolution (highest priority first)
=============================================

1. Provider-specific base URL override (per provider):
     OPENAI_BASE_URL        -> OpenAI  / gpt-*  / chat-latest targets
     ANTHROPIC_BASE_URL     -> Claude requests target
     TYPESAFE_BASE_URL      -> Jev visualization routing target

2. Shared Vercel-compatible AI Gateway endpoint (global):
     AI_GATEWAY_URL         -> Any of the above providers when their
                               dedicated *_BASE_URL is not set.

3. Provider official endpoints (final default fallbacks).

Notes
-----
* BYOK visitor requests reuse the same precedence but always construct
  isolated SDK clients (trust_env=False, stripped custom headers,
  empty organization/project scopes). See ``request_model()`` below.
* AI_GATEWAY_URL supports the documented Vercel AI Gateway routing
  behavior: send official provider payloads to the gateway host plus
  the provider auth headers; the gateway dispatches upstream. This
  code path does **not** rename or wrap the configured ``*_API_KEY``
  values; deployments that need provider-swapping gateway auth should
  configure the gateway to accept one shared key and route per-model.
"""

import os
from contextlib import contextmanager

from langchain.agents.middleware import AgentMiddleware
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.messages import AIMessageChunk
from pydantic import Field
from langchain_anthropic import ChatAnthropic
from langchain_core.language_models.chat_models import BaseChatModel

DEFAULT_MODEL = "chat-latest"

# langchain-anthropic defaults max_tokens to 4096, which truncates
# generateSandboxedUi tool args mid-stream (jsFunctions/jsExpressions never
# arrive and the widget is stuck in its preview sandbox). Widget generation
# routinely needs tens of thousands of output tokens.
MAX_TOKENS = 64000

# Provider official endpoints. Used as the final fallback when neither the
# provider-specific *_BASE_URL nor AI_GATEWAY_URL are configured.
_OFFICIAL_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    # ChatAnthropic SDK supplies its own default when base_url is None; we
    # keep a sentinel so callers can distinguish "official default" from
    # "shared gateway supplied".
    "anthropic": None,
    # Jev / TypeSafe visualization routing has its own canonical URL.
    "typesafe": "https://api.typesafe.ai/v1/systemone",
}

# Accepted aliases for provider_base_url() caller convenience.
_ALIASES = {
    "gpt": "openai",
    "claude": "anthropic",
    "jev": "typesafe",
}


def _read_env(name):
    """Read and sanitize an env var. None / whitespace only -> None."""
    value = os.environ.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def provider_base_url(provider):
    """Resolve the effective HTTP target for ``provider``.

    Parameters
    ----------
    provider : str
        One of ``"openai"`` | ``"anthropic"`` | ``"typesafe"``.
        Aliases ``"gpt"``, ``"claude"``, ``"jev"`` are also accepted.

    Returns
    -------
    str or None
        A fully qualified base URL, or ``None`` for Anthropic when the SDK
        built-in default should be used (i.e. no override and no gateway).
    """
    canonical = _ALIASES.get(provider.lower(), provider.lower())
    if canonical not in _OFFICIAL_BASE_URLS:
        raise ValueError(
            f"Unknown provider {provider!r}. Expected one of "
            f"{sorted(set(_OFFICIAL_BASE_URLS) | set(_ALIASES))}."
        )

    specific_name = {
        "openai": "OPENAI_BASE_URL",
        "anthropic": "ANTHROPIC_BASE_URL",
        "typesafe": "TYPESAFE_BASE_URL",
    }[canonical]
    specific = _read_env(specific_name)
    if specific:
        return specific

    shared_gateway = _read_env("AI_GATEWAY_URL")
    if shared_gateway:
        return shared_gateway

    return _OFFICIAL_BASE_URLS[canonical]


def build_model(*, allow_unconfigured: bool = False) -> BaseChatModel:
    """Instantiate the server-scoped shared chat model.

    This shared instance is the fallback for requests that do not carry
    visitor credentials. Per-request BYOK overrides use ``request_model``
    below instead.
    """
    model_name = os.environ.get("LLM_MODEL", DEFAULT_MODEL).strip()
    is_openai = model_name == "chat-latest" or model_name.startswith("gpt-")
    if not is_openai and not model_name.startswith("claude-"):
        raise ValueError(
            "LLM_MODEL must name a supported provider: claude-* (Anthropic) "
            "or gpt-*/chat-latest (OpenAI). Unset LLM_MODEL to use the default."
        )
    key_name = "OPENAI_API_KEY" if is_openai else "ANTHROPIC_API_KEY"
    if not os.environ.get(key_name, "").strip():
        if allow_unconfigured:
            return UnconfiguredModel()
        raise ValueError(
            f"{key_name} is required for the selected LLM_MODEL provider. "
            "Set it in the agent environment and restart the agent."
        )
    if is_openai:
        # chat-latest follows ChatGPT Instant; explicit gpt-* pins also work.
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model=model_name, base_url=provider_base_url("openai"))
    base_url = provider_base_url("anthropic")
    kwargs = dict(model=model_name, max_tokens=MAX_TOKENS)
    # Only pass base_url when explicitly configured (gateway or override).
    # langchain-anthropic raises if base_url is explicitly set to the exact
    # SDK default in some release trains; omitting None keeps the SDK path.
    if base_url is not None:
        kwargs["base_url"] = base_url
    return ChatAnthropic(**kwargs)


class UnconfiguredModel(BaseChatModel):
    """Compile a BYOK-only graph without constructing a provider with fake credentials."""

    @property
    def _llm_type(self):
        return "unconfigured"

    def _generate(self, *args, **kwargs):
        raise ValueError("Provider credentials are required. Add OpenAI and Jev keys.")

    def bind_tools(self, tools, **kwargs):
        return self


def request_model(fallback):
    """Build a credential-scoped OpenAI chat model for the active BYOK visitor.

    The returned client never inherits host env variables: organization,
    project, proxy, custom headers, and the global HTTPS proxy settings are
    all stripped.

    URL resolution for BYOK visitor traffic (intentionally narrower than the
    general purpose ``provider_base_url`` helper used by server-scoped models):

      * ``AI_GATEWAY_URL`` (shared deployment-wide gateway origin) is honored.
      * Provider-specific ``OPENAI_BASE_URL`` is **intentionally ignored** so
        that host deployments cannot silently redirect visitor BYOK keys (the
        same hardening policy applied to organization/project/proxy env vars).
        If the deployment wants all visitor traffic to transit the same
        gateway, configure ``AI_GATEWAY_URL`` instead.

    This separation lets server-scoped models keep using ``OPENAI_BASE_URL``
    for Azure OpenAI / OpenAI-compatible proxy endpoint routing while
    guaranteeing the BYOK path is never re-routed by host environment.
    """
    from src.credentials import current_credentials
    credentials = current_credentials.get()
    if credentials is None:
        return fallback
    from langchain_openai import ChatOpenAI
    from openai import AsyncOpenAI, OpenAI, DefaultAsyncHttpxClient, DefaultHttpxClient

    # For BYOK traffic only the shared, operator-configured AI_GATEWAY_URL is
    # consulted. Never apply OPENAI_BASE_URL on the behalf of a BYOK visitor:
    # that variable can be set by host env and could misdirect a user-supplied
    # bearer key to an untrusted endpoint.
    shared_gateway = _read_env("AI_GATEWAY_URL")
    resolved_base_url = shared_gateway or _OFFICIAL_BASE_URLS["openai"]
    # SDK None defaults inherit server organization/project settings. Explicit
    # clients with empty scopes and isolated transports prevent that inheritance,
    # including server HTTP(S)_PROXY and OPENAI_PROXY settings.
    options = {
        "api_key": credentials.openai,
        "base_url": resolved_base_url,
        "organization": "",
        "project": "",
    }
    sync_client = OpenAI(**options, http_client=DefaultHttpxClient(trust_env=False))
    async_client = AsyncOpenAI(
        **options, http_client=DefaultAsyncHttpxClient(trust_env=False)
    )
    # After construction, None omits these headers without rereading env vars.
    for client in (sync_client, async_client):
        client.organization = None
        client.project = None
        client.admin_api_key = None
        client.webhook_secret = None
        # The SDK merges OPENAI_CUSTOM_HEADERS even with default_headers={}.
        # Clear that merge before any request so host Authorization cannot win.
        client._custom_headers = {}
    model = ChatOpenAI(
        model=DEFAULT_MODEL,
        api_key=credentials.openai,
        base_url=resolved_base_url,
        openai_proxy="",
        root_client=sync_client,
        client=sync_client.chat.completions,
        root_async_client=async_client,
        async_client=async_client.chat.completions,
    )
    # LangChain's metadata field separately inherits organization environment
    # values even when prebuilt SDK clients are supplied. Clear the inert field.
    model.openai_organization = None
    return model


@contextmanager
def provider_error_boundary():
    from src.credentials import current_credentials
    try:
        yield
    except Exception:
        if current_credentials.get() is None:
            raise
        raise ValueError("OpenAI request failed. Check your key and model access, or retry.") from None


class RequestModelMiddleware(AgentMiddleware):
    def wrap_model_call(self, request, handler):
        with provider_error_boundary():
            return handler(request.override(model=request_model(request.model)))

    async def awrap_model_call(self, request, handler):
        with provider_error_boundary():
            return await handler(request.override(model=request_model(request.model)))


class CredentialScopedModel(BaseChatModel):
    """Route DeepAgents' internal summary/subagent model calls through BYOK too."""

    fallback: BaseChatModel = Field(exclude=True)

    @property
    def _llm_type(self):
        return "credential-scoped"

    def bind_tools(self, tools, **kwargs):
        return self.bind(_credential_tools=tools, _credential_tool_options=kwargs)

    def _target(self, kwargs):
        model = request_model(self.fallback)
        options = dict(kwargs)
        tools = options.pop("_credential_tools", None)
        tool_options = options.pop("_credential_tool_options", {})
        if tools is not None:
            model = model.bind_tools(tools, **tool_options)
        return model, options

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        with provider_error_boundary():
            model, options = self._target(kwargs)
            message = model.invoke(messages, stop=stop, **options)
            return ChatResult(generations=[ChatGeneration(message=message)])

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        with provider_error_boundary():
            model, options = self._target(kwargs)
            message = await model.ainvoke(messages, stop=stop, **options)
            return ChatResult(generations=[ChatGeneration(message=message)])

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        with provider_error_boundary():
            model, options = self._target(kwargs)
            for chunk in model.stream(messages, stop=stop, **options):
                yield ChatGenerationChunk(message=as_chunk(chunk))

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        with provider_error_boundary():
            model, options = self._target(kwargs)
            async for chunk in model.astream(messages, stop=stop, **options):
                yield ChatGenerationChunk(message=as_chunk(chunk))


def as_chunk(message):
    if isinstance(message, AIMessageChunk):
        return message
    return AIMessageChunk(**message.model_dump(exclude={"type"}))
