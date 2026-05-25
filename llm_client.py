"""
LiteLLM HTTP client for the MCP server.

Configuration via environment variables:
    LITELLM_BASE_URL   – base URL of LiteLLM proxy  (default: http://localhost:4000)
    LITELLM_API_KEY    – API key sent as x-litellm-api-key header (default: sk-litellm)
    LITELLM_MODEL      – model name                 (default: my-agent)
    LITELLM_TIMEOUT    – request timeout in seconds (default: 60)
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import requests

# ── Defaults (overridden by env vars) ─────────────────────────────────────────
LITELLM_BASE_URL: str = os.environ.get("LITELLM_BASE_URL", "http://localhost:4000")
LITELLM_API_KEY: str  = os.environ.get("LITELLM_API_KEY",  "sk-litellm")
DEFAULT_MODEL: str     = os.environ.get("LITELLM_MODEL",    "my-agent")
DEFAULT_TIMEOUT: int   = int(os.environ.get("LITELLM_TIMEOUT", "60"))


def ask_llm(
    prompt: str,
    system_prompt: Optional[str] = None,
    model: str = DEFAULT_MODEL,
    base_url: str = LITELLM_BASE_URL,
    api_key: str = LITELLM_API_KEY,
    timeout: int = DEFAULT_TIMEOUT,
    extra_body: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Send a chat completion request to LiteLLM and return the assistant reply.

    Args:
        prompt:        User message content.
        system_prompt: Optional system message prepended before the user message.
        model:         LiteLLM model alias (default from LITELLM_MODEL env var).
        base_url:      LiteLLM proxy base URL (default from LITELLM_BASE_URL).
        api_key:       API key (default from LITELLM_API_KEY).
        timeout:       HTTP request timeout in seconds.
        extra_body:    Any additional fields merged into the request body.

    Returns:
        The assistant's reply as a plain string.

    Raises:
        requests.HTTPError: on non-2xx HTTP responses.
        KeyError:           if the response JSON is not in the expected format.
    """
    messages: List[Dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    url = f"{base_url.rstrip('/')}/v1/chat/completions"
    headers: Dict[str, str] = {
        "accept": "application/json",
        "x-litellm-api-key": api_key,
        "Content-Type": "application/json",
    }
    payload: Dict[str, Any] = {"model": model, "messages": messages}
    if extra_body:
        payload.update(extra_body)

    resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
    resp.raise_for_status()
    data: Dict[str, Any] = resp.json()
    return data["choices"][0]["message"]["content"]


def get_available_models(
    base_url: str = LITELLM_BASE_URL,
    api_key: str = LITELLM_API_KEY,
    timeout: int = 10,
) -> List[str]:
    """Return a list of model IDs exposed by the LiteLLM proxy."""
    url = f"{base_url.rstrip('/')}/v1/models"
    headers = {"accept": "application/json", "x-litellm-api-key": api_key}
    resp = requests.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    return [m["id"] for m in data.get("data", [])]
