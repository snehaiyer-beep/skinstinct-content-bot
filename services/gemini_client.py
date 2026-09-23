"""
Thin wrapper around the Gemini API. Isolated behind GeminiClient so
services/pipeline.py never imports google.genai directly — swapping providers
later (the movie-matchmaker README notes the same need) means editing only
this file.
"""
from __future__ import annotations

import json
import logging
import re

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from config import settings

logger = logging.getLogger(__name__)


class GeminiEmptyResponseError(RuntimeError):
    pass


class GeminiJsonParseError(RuntimeError):
    def __init__(self, raw_text: str):
        super().__init__("Gemini response was not valid JSON")
        self.raw_text = raw_text


class GeminiTransientError(RuntimeError):
    """Rate limit, timeout, 5xx — safe to retry."""


def _extract_json_object(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(json)?|```$", "", text, flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        raise GeminiJsonParseError(text)
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise GeminiJsonParseError(text) from exc


class GeminiClient:
    def __init__(self):
        self._client = None

    def _get_client(self):
        if self._client is None:
            settings.require_gemini()
            from google import genai  # imported lazily so tests don't need the package installed

            self._client = genai.Client(api_key=settings.gemini_api_key)
        return self._client

    @retry(
        retry=retry_if_exception_type(GeminiTransientError),
        stop=stop_after_attempt(4),
        wait=wait_exponential_jitter(initial=1, max=20),
        reraise=True,
    )
    def _generate_raw(self, *, system_instruction: str, contents: str) -> str:
        from google.genai import types  # lazy import, mirrors _get_client

        client = self._get_client()
        try:
            response = client.models.generate_content(
                model=settings.gemini_model,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=settings.gemini_temperature,
                    response_mime_type="application/json",
                ),
            )
        except Exception as exc:  # noqa: BLE001 — narrow SDK exceptions vary by version
            status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
            if status in (429, 500, 502, 503, 504) or "rate limit" in str(exc).lower():
                logger.warning("Gemini transient error (status=%s), will retry: %s", status, exc)
                raise GeminiTransientError(str(exc)) from exc
            logger.error("Gemini non-retryable error: %s", exc)
            raise

        text = getattr(response, "text", None)
        if not text:
            raise GeminiEmptyResponseError("Gemini returned an empty response")
        return text

    def generate_json(self, *, system_instruction: str, contents: str) -> dict:
        raw = self._generate_raw(system_instruction=system_instruction, contents=contents)
        return _extract_json_object(raw)
