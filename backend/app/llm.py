"""Large language model access (Google Gemini via the google-genai SDK).

The only module that imports google-genai, so switching providers means changing this file.
Callers get plain results and our own exception types, never SDK objects.
"""

import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import httpx2
from google import genai
from google.genai import errors, types

from app.config import get_settings

logger = logging.getLogger(__name__)


class LLMError(Exception):
    """The model call failed (upstream error, timeout, bad response)."""


class LLMNotConfiguredError(LLMError):
    """No API key is set."""


class LLMRateLimitError(LLMError):
    """The provider's quota is used up (HTTP 429). Retrying immediately won't help."""


class LLMRequestError(LLMError):
    """The provider rejected this request (4xx other than 429, e.g. 400 or a safety block).
    Sending the same request again will fail the same way."""


@dataclass(frozen=True)
class LLMResult:
    text: str
    input_tokens: int
    output_tokens: int
    thinking_tokens: int


class LLM:
    def __init__(self, api_key: str, model: str) -> None:
        self._model = model
        self._client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                timeout=30_000,  # milliseconds (this SDK's unit), i.e. 30 s
                # Retry only server-side hiccups. Not 429: a used-up daily quota won't
                # recover in seconds, so retrying would just make the user wait.
                retry_options=types.HttpRetryOptions(
                    attempts=3, http_status_codes=[500, 502, 503, 504]
                ),
            ),
        )

    async def generate(
        self,
        *,
        system: str,
        prompt: str,
        max_output_tokens: int = 1024,
        json_schema: dict[str, Any] | None = None,
        purpose: str = "other",  # what the call is for, for logs and metrics
    ) -> LLMResult:
        """Generate text. With `json_schema`, the reply is JSON constrained to that schema
        ("structured output"). Callers must still validate it: constrained isn't guaranteed."""
        try:
            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    temperature=0.2,  # low: faithful and consistent, not creative
                    max_output_tokens=max_output_tokens,
                    # Answering from given sources needs little reasoning; thinking tokens
                    # count against the quota and add latency.
                    thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.MINIMAL),
                    response_mime_type="application/json" if json_schema else None,
                    response_json_schema=json_schema,
                ),
            )
        except errors.ClientError as exc:
            if exc.code == 429:
                raise LLMRateLimitError("The LLM provider's rate limit was reached") from exc
            raise LLMRequestError(f"LLM request rejected: {exc.code} {exc.status}") from exc
        except errors.APIError as exc:  # 5xx, after the SDK's retries
            raise LLMError(f"LLM provider error: {exc.code} {exc.status}") from exc
        except httpx2.HTTPError as exc:  # timeouts and network failures
            raise LLMError(f"Could not reach the LLM provider: {type(exc).__name__}") from exc

        usage = response.usage_metadata
        result = LLMResult(
            text=response.text or "",
            input_tokens=(usage.prompt_token_count or 0) if usage else 0,
            output_tokens=(usage.candidates_token_count or 0) if usage else 0,
            thinking_tokens=(usage.thoughts_token_count or 0) if usage else 0,
        )
        logger.info(
            "LLM %s (%s): %d input, %d output, %d thinking tokens",
            self._model, purpose, result.input_tokens, result.output_tokens, result.thinking_tokens,
        )
        return result


@lru_cache
def get_llm() -> LLM:
    """FastAPI dependency: one client per process. Raises if no API key is configured."""
    settings = get_settings()
    key = settings.gemini_api_key.get_secret_value() if settings.gemini_api_key else ""
    if not key:
        raise LLMNotConfiguredError("No LLM configured: set GEMINI_API_KEY in .env")
    return LLM(key, settings.llm_model)
