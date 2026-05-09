"""
GeminiLLMAdapter — locked LLM provider for Waxwing Voice MVP.

Stack decision: Gemini-2.0 Flash (docs/03-tooling-and-guardrails.md).
Model ID validated against Settings.validate_gemini_model() — must be in the
Gemini Flash family.

Implementation strategy
-----------------------
We call the Gemini REST API directly via httpx rather than the google-generativeai
SDK. This keeps the dependency footprint minimal — the SDK adds ~30 transitive
packages. The REST API is stable and well-documented:
  POST https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent

Streaming: the API streams server-sent events (SSE) where each line prefixed
with "data: " contains a JSON object with the next token chunk. We parse these
incrementally and yield the text from each chunk.

Role mapping:
  LLMAdapter 'user'      → Gemini 'user'
  LLMAdapter 'assistant' → Gemini 'model'

System prompt: sent as the `system_instruction` field in the request body.

Token limit: configured via Settings.max_response_tokens. Gemini may produce
more tokens than this — it is a soft cap passed in generationConfig.

Safety settings: we set all Gemini harm categories to BLOCK_NONE because the
Waxwing system prompt and EscalationDetector handle safety guardrails. Gemini's
built-in filters can reject legitimate real estate questions (e.g., neighbourhood
crime data). We own the safety layer; don't double-filter.

Error mapping
-------------
- 400 Bad Request      → LLMProviderError(retryable=False)
- 401 Unauthorized     → LLMProviderError(retryable=False)
- 403 Forbidden        → LLMProviderError(retryable=False)
- 429 Rate Limited     → LLMProviderError(retryable=True)
- 5xx Server Error     → LLMProviderError(retryable=True)
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator

import httpx

from voice_agent.providers.llm.protocol import LLMAdapter, LLMProviderError, Message

log = logging.getLogger("voice_agent.providers.llm.gemini")

# Gemini streaming endpoint template.
_GEMINI_STREAM_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "{model}:streamGenerateContent?key={api_key}&alt=sse"
)

# Default model — can be overridden via Settings.gemini_model.
_DEFAULT_MODEL = "gemini-2.0-flash"

# Default max output tokens — keeps responses phone-length.
_DEFAULT_MAX_TOKENS = 200

# HTTP timeout for the streaming request.
_REQUEST_TIMEOUT = 15.0

# Gemini harm categories — set to BLOCK_NONE so the agent's own safety layer handles this.
_SAFETY_SETTINGS = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
]


def _build_request_body(
    system_prompt: str,
    conversation_history: list[Message],
    max_tokens: int,
) -> dict:
    """
    Build the Gemini generateContent request body.

    Args:
        system_prompt:         Full system instruction.
        conversation_history:  Ordered list of prior turns.
        max_tokens:            Output token soft cap.

    Returns:
        Dict suitable for JSON serialisation and POST to Gemini.
    """
    # Map conversation history to Gemini content format
    contents = []
    for msg in conversation_history:
        # Map 'assistant' → 'model' (Gemini's role name for the AI)
        role = "model" if msg["role"] == "assistant" else "user"
        contents.append({
            "role": role,
            "parts": [{"text": msg["content"]}],
        })

    return {
        "system_instruction": {
            "parts": [{"text": system_prompt}],
        },
        "contents": contents,
        "generationConfig": {
            "maxOutputTokens": max_tokens,
            "temperature": 0.3,  # Low temp: more predictable, factual responses
            "topP": 0.8,
        },
        "safetySettings": _SAFETY_SETTINGS,
    }


class GeminiLLMAdapter:
    """
    Streaming LLM adapter backed by Google Gemini Flash.

    Implements the LLMAdapter protocol. Calls the Gemini streaming REST endpoint
    directly via httpx. Yields string token chunks as they arrive in SSE frames.

    Construction raises ValueError immediately if api_key is None or empty.

    Thread-safety: not thread-safe. One instance per VoiceSession (one call).
    cancel() is safe to call from any async context while respond_streaming() is
    active.
    """

    def __init__(
        self,
        api_key: str,
        model: str = _DEFAULT_MODEL,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
        request_timeout: float = _REQUEST_TIMEOUT,
    ) -> None:
        """
        Args:
            api_key:         Google Gemini API key. Must be non-empty.
            model:           Gemini model ID. Must be in the Flash family.
            max_tokens:      Output token soft cap. 200 keeps responses phone-length.
            request_timeout: HTTP timeout in seconds.

        Raises:
            ValueError: If api_key is None or empty.
        """
        if not api_key:
            raise ValueError(
                "GeminiLLMAdapter requires a non-empty api_key. "
                "Set GEMINI_API_KEY in the environment. "
                "See services/voice-agent/.env.example."
            )
        # Never log the api_key — it is a secret.
        self._api_key = api_key
        self._model = model
        self._max_tokens = max_tokens
        self._request_timeout = request_timeout

        # Cancellation support: set this event to abort an in-progress stream.
        self._cancel_event: asyncio.Event = asyncio.Event()

        log.info(
            "GeminiLLMAdapter initialized",
            extra={"model": model, "max_tokens": max_tokens},
        )

    async def respond_streaming(
        self,
        system_prompt: str,
        conversation_history: list[Message],
    ) -> AsyncIterator[str]:
        """
        Generate a streaming response from Gemini Flash.

        Makes a POST to the Gemini streamGenerateContent endpoint and parses
        SSE frames. Yields text token chunks as they arrive.

        On cancel() called while streaming, stops yielding and returns.
        The HTTP connection is closed cleanly.

        Args:
            system_prompt:         Full system instruction for the leasing agent.
            conversation_history:  Ordered list of prior turns (oldest first).

        Yields:
            str: Response token chunk. May be partial words; caller accumulates.

        Raises:
            LLMProviderError: On HTTP 4xx/5xx from Gemini, or network failure.
        """
        self._cancel_event.clear()

        url = _GEMINI_STREAM_URL.format(model=self._model, api_key=self._api_key)
        body = _build_request_body(system_prompt, conversation_history, self._max_tokens)

        headers = {
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=self._request_timeout) as client:
                async with client.stream("POST", url, headers=headers, json=body) as response:
                    # Map error status codes before reading the body
                    if response.status_code == 400:
                        raise LLMProviderError(
                            "Gemini rejected the request — check prompt format.",
                            retryable=False,
                            status_code=400,
                        )
                    if response.status_code == 401:
                        raise LLMProviderError(
                            "Gemini authentication failed — check GEMINI_API_KEY.",
                            retryable=False,
                            status_code=401,
                        )
                    if response.status_code == 403:
                        raise LLMProviderError(
                            "Gemini access forbidden — check API key permissions.",
                            retryable=False,
                            status_code=403,
                        )
                    if response.status_code == 429:
                        raise LLMProviderError(
                            "Gemini rate limit exceeded.",
                            retryable=True,
                            status_code=429,
                        )
                    if response.status_code >= 500:
                        raise LLMProviderError(
                            f"Gemini server error ({response.status_code}).",
                            retryable=True,
                            status_code=response.status_code,
                        )
                    if response.status_code != 200:
                        raise LLMProviderError(
                            f"Gemini unexpected status {response.status_code}.",
                            retryable=False,
                            status_code=response.status_code,
                        )

                    # Parse SSE stream
                    async for line in response.aiter_lines():
                        if self._cancel_event.is_set():
                            log.debug(
                                "GeminiLLMAdapter: stream cancelled (barge-in)",
                                extra={"model": self._model},
                            )
                            return

                        # SSE lines with content start with "data: "
                        if not line.startswith("data: "):
                            continue

                        data_str = line[len("data: "):]
                        if data_str == "[DONE]":
                            return

                        try:
                            data = json.loads(data_str)
                        except json.JSONDecodeError:
                            # Malformed SSE frame — skip rather than crashing
                            log.warning(
                                "GeminiLLMAdapter: failed to parse SSE frame",
                                extra={"line_preview": data_str[:100]},
                            )
                            continue

                        # Extract text from the Gemini response structure:
                        # data.candidates[0].content.parts[0].text
                        try:
                            candidates = data.get("candidates", [])
                            if not candidates:
                                continue
                            parts = candidates[0].get("content", {}).get("parts", [])
                            for part in parts:
                                text = part.get("text", "")
                                if text:
                                    yield text
                        except (KeyError, IndexError, TypeError):
                            # Unexpected structure — skip this frame
                            continue

        except httpx.TimeoutException as exc:
            raise LLMProviderError(
                "Gemini request timed out.",
                retryable=True,
            ) from exc
        except httpx.RequestError as exc:
            raise LLMProviderError(
                f"Gemini network error: {exc}",
                retryable=True,
            ) from exc

    async def cancel(self) -> None:
        """
        Signal the current generation stream to stop.

        Sets the cancel event; the SSE loop checks it before each token chunk
        and exits cleanly. Idempotent — safe to call when no stream is active.
        """
        self._cancel_event.set()
        log.debug("GeminiLLMAdapter.cancel called", extra={"model": self._model})
