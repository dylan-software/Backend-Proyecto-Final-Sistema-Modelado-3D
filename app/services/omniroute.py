import os

import httpx

DEFAULT_BASE_URL = "http://localhost:20128/v1"

class OmniRouteError(Exception):
    """Raised when OmniRoute is not configured or returns an unusable answer."""


async def chat_completion(messages, temperature=0.4, timeout=60.0):
    """Send an OpenAI-compatible chat completion request to OmniRoute and return the reply text."""
    base_url = os.getenv("OMNIROUTE_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    api_key = os.getenv("OMNIROUTE_API_KEY")
    model = os.getenv("OMNIROUTE_MODEL")
    if not api_key:
        raise OmniRouteError("OMNIROUTE_API_KEY is not set")
    if not model:
        raise OmniRouteError("OMNIROUTE_MODEL is not set")

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": model, "messages": messages, "temperature": temperature},
            )
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise OmniRouteError(f"request failed: {exc}") from exc

    try:
        return response.json()["choices"][0]["message"]["content"] or ""
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise OmniRouteError("unexpected response shape") from exc

