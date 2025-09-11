# backend/app/integrations/openai/client.py
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from backend.app.core.config import settings

# The official SDK (openai>=1.40.0)
try:
    from openai import OpenAI  # type: ignore
except Exception:  # pragma: no cover
    OpenAI = None  # soft fail if not installed


class OpenAIUnavailable(RuntimeError):
    """Raised when OpenAI client is not available/enabled."""
    pass


def _messages_to_responses_input(messages: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Convert a list of chat-style messages:
        [{"role": "system|user|assistant", "content": str|list}, ...]
    into a Responses API input payload.

    Responses API expects:
        {
          "input": [
            {
              "role": "...",
              "content": [{"type": "input_text", "text": "..."}]
            }
          ]
        }
    """
    formatted: List[Dict[str, Any]] = []
    for m in messages:
        role = str(m.get("role", "")).strip() or "user"
        content = m.get("content", "")
        if isinstance(content, str):
            formatted.append(
                {
                    "role": role,
                    "content": [{"type": "input_text", "text": content}],
                }
            )
        elif isinstance(content, list):
            # If caller already provided responses-style parts, pass through (sanity fallback).
            # NOTE: ensure any plain "text" types are upgraded to "input_text".
            parts: List[Dict[str, Any]] = []
            for part in content:
                if isinstance(part, dict):
                    p = dict(part)
                    t = p.get("type")
                    if t == "text":  # normalize to input_text
                        p["type"] = "input_text"
                    parts.append(p)
            formatted.append({"role": role, "content": parts})
        else:
            # Coerce unknowns to text
            formatted.append(
                {
                    "role": role,
                    "content": [{"type": "input_text", "text": str(content)}],
                }
            )
    return {"input": formatted}


def _extract_responses_text(resp: Any) -> str:
    """
    Best-effort extraction of text from a Responses API result.
    """
    # Preferred convenience attr (SDK adds this on Responses objects)
    if hasattr(resp, "output_text") and isinstance(resp.output_text, str):
        return resp.output_text.strip()

    # Manual traversal fallback
    chunks: List[str] = []
    try:
        output = getattr(resp, "output", None) or []
        for item in output:
            if getattr(item, "type", None) == "message":
                for c in getattr(item, "content", []) or []:
                    # The Responses API uses parts like {"type": "output_text", "text": "..."}
                    # Some SDK variants expose .text (str) or .text.value (str)
                    tval = None
                    if hasattr(c, "text"):
                        t = getattr(c, "text")
                        if isinstance(t, str):
                            tval = t
                        else:
                            tval = getattr(t, "value", None)
                    if not tval and isinstance(c, dict):  # defensive if SDK returns plain dicts
                        tval = c.get("text")
                    if isinstance(tval, str):
                        chunks.append(tval)
    except Exception:
        pass
    return "".join(chunks).strip()


class OpenAIClient:
    """Thin wrapper around OpenAI Responses + Images with retries (O3 & GPT-5 compatible)."""

    def __init__(self) -> None:
        self.enabled = bool(settings.openai_api_key and OpenAI is not None)
        if self.enabled:
            # Pass key + optional org/project explicitly (works with Teams/Enterprise)
            self._client = OpenAI(
                api_key=settings.openai_api_key,
                organization=(getattr(settings, "openai_org_id", "") or None),
                project=(getattr(settings, "openai_project", "") or None),
            )
        else:
            self._client = None

    def _require_enabled(self) -> None:
        if not self.enabled or self._client is None:
            raise OpenAIUnavailable("OpenAI key missing or SDK not installed")

    # ---------------------------------------------------------------------
    # Generic respond(): one call that works for O3 (planner) and GPT-5 (coder)
    # via the Responses API. Provide chat-style messages and pick a model.
    # ---------------------------------------------------------------------
    @retry(
        retry=retry_if_exception_type(Exception),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=8),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def respond(
        self,
        *,
        model: Optional[str] = None,
        messages: Sequence[Dict[str, Any]],
        # Do NOT pass temperature to Responses (some models reject it)
        temperature: Optional[float] = None,  # kept for signature compatibility; ignored below
        max_output_tokens: Optional[int] = None,
    ) -> str:
        """
        Send a chat-style conversation to the Responses API using the given model (O3, GPT-5, etc.)
        and return the concatenated text output.
        """
        if not self.enabled:
            return ""
        self._require_enabled()

        payload = _messages_to_responses_input(messages)
        req: Dict[str, Any] = {
            "model": model or settings.omega_llm_model,
            **payload,
        }
        # IMPORTANT: do not include 'temperature' with Responses API
        if max_output_tokens is not None:
            req["max_output_tokens"] = max_output_tokens

        resp = self._client.responses.create(**req)
        return _extract_responses_text(resp)

    # ---------------------------------------------------------------------
    # Simple health pings (text/images)
    # ---------------------------------------------------------------------
    @retry(
        retry=retry_if_exception_type(Exception),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=8),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def text_echo(self, text: str = "ping") -> Optional[str]:
        """
        Tiny Responses API ping; returns echoed text or None if disabled.
        (We still avoid passing temperature to Responses.)
        """
        if not self.enabled:
            return None
        self._require_enabled()

        out = self.respond(
            model=settings.omega_llm_model,
            messages=[
                {"role": "user", "content": f"Return exactly this text: {text}"},
            ],
            max_output_tokens=64,
        )
        return out or None

    @retry(
        retry=retry_if_exception_type(Exception),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=8),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def image_probe(self) -> Optional[bool]:
        """
        Tiny image generation ping; returns True if a response arrived.
        Does not save or return the image; just checks reachability.
        """
        if not self.enabled:
            return None
        self._require_enabled()

        _ = self._client.images.generate(
            model=settings.omega_image_model,
            prompt="A simple solid color square for health check.",
            size=settings.omega_image_size,
        )
        return True


# Simple singleton
_client: Optional[OpenAIClient] = None


def get_openai_client() -> OpenAIClient:
    global _client
    if _client is None:
        _client = OpenAIClient()
    return _client