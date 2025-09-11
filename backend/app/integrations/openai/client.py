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


def _coerce_part_to_input_text(part: Any) -> Dict[str, Any]:
    """
    Normalize any content 'part' into Responses format:
      {"type": "input_text", "text": "<...>"}

    Accepts:
      - plain strings
      - {"type": "text", "text": "..."}  (old style)  -> coerced
      - {"type": "input_text", "text": "..."}         -> passed through
      - {"text": "..."} with no 'type'                -> coerced
      - anything else -> stringified
    """
    if isinstance(part, str):
        return {"type": "input_text", "text": part}

    if isinstance(part, dict):
        p = dict(part)
        # prefer explicit text if present
        txt = p.get("text")
        # normalize the type
        ptype = p.get("type")
        if ptype == "input_text" and isinstance(txt, str):
            return {"type": "input_text", "text": txt}
        if ptype == "text" and isinstance(txt, str):
            return {"type": "input_text", "text": txt}
        if ptype is None and isinstance(txt, str):
            return {"type": "input_text", "text": txt}
        # last-ditch: stringify whole dict
        return {"type": "input_text", "text": str(part)}

    # unknown type -> stringify
    return {"type": "input_text", "text": str(part)}


def _messages_to_responses_input(messages: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Convert chat-style messages:
        [{"role": "system|user|assistant", "content": str|list|dict}, ...]
    into a Responses API 'input' payload.

    Responses expects:
      {
        "input": [
          {
            "role": "...",
            "content": [{"type": "input_text", "text": "..."}]
          },
          ...
        ]
      }
    """
    formatted: List[Dict[str, Any]] = []
    for m in messages:
        role = str(m.get("role", "")).strip() or "user"
        content = m.get("content", "")

        # common cases fast-path
        if isinstance(content, str):
            formatted.append({"role": role, "content": [{"type": "input_text", "text": content}]})
            continue

        # already list of parts? normalize each
        parts: List[Dict[str, Any]] = []
        if isinstance(content, list):
            for part in content:
                parts.append(_coerce_part_to_input_text(part))
        elif isinstance(content, dict):
            # single part dict
            parts.append(_coerce_part_to_input_text(content))
        else:
            # anything else -> stringify
            parts.append({"type": "input_text", "text": str(content)})

        formatted.append({"role": role, "content": parts})

    return {"input": formatted}


def _extract_responses_text(resp: Any) -> str:
    """
    Best-effort extraction of text from a Responses API result.
    Supports:
      - resp.output_text (SDK convenience)
      - resp.output[*].content[*].text or .text.value
      - resp.output[*].content[*]["text"] when plain dicts are returned
    """
    # Preferred convenience attr (many SDK versions add this)
    if hasattr(resp, "output_text") and isinstance(resp.output_text, str):
        return resp.output_text.strip()

    chunks: List[str] = []

    # Typical Responses object: resp.output is a list of items
    try:
        output = getattr(resp, "output", None) or []
        for item in output:
            itype = getattr(item, "type", None) if not isinstance(item, dict) else item.get("type")
            if itype != "message":
                continue

            content = getattr(item, "content", None)
            if isinstance(item, dict):
                content = item.get("content", content)
            content = content or []

            for c in content:
                # object-like access
                text_attr = None
                if not isinstance(c, dict):
                    # attempt attribute forms
                    if hasattr(c, "text"):
                        t = getattr(c, "text")
                        if isinstance(t, str):
                            text_attr = t
                        else:
                            text_attr = getattr(t, "value", None)
                else:
                    # dict form
                    text_attr = c.get("text")

                if isinstance(text_attr, str):
                    chunks.append(text_attr)
    except Exception:
        # if structure is different, try a very defensive parse
        pass

    return "".join(chunks).strip()


class OpenAIClient:
    """Thin wrapper around OpenAI Responses + Images with retries (O3 & GPT-5 compatible)."""

    def __init__(self) -> None:
        self.enabled = bool(settings.openai_api_key and OpenAI is not None)
        if self.enabled:
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
        # Kept for signature compatibility; Responses often rejects temperature:
        temperature: Optional[float] = None,  # noqa: ARG002
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
        model_name = (model or settings.omega_llm_model or "").strip()
        req: Dict[str, Any] = {
            "model": model_name or settings.omega_llm_model,
            **payload,
        }
        if max_output_tokens is not None:
            req["max_output_tokens"] = max_output_tokens

        # Optional, harmless hint for GPT-5 family
        if model_name.lower().startswith("gpt-5"):
            req["reasoning"] = {"effort": "medium"}

        resp = self._client.responses.create(**req)
        return _extract_responses_text(resp)

    @retry(
        retry=retry_if_exception_type(Exception),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=8),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def text_echo(self, text: str = "ping") -> Optional[str]:
        """
        Tiny Responses API ping; returns echoed text or None if disabled.
        """
        if not self.enabled:
            return None
        self._require_enabled()

        out = self.respond(
            model=settings.omega_llm_model,
            messages=[{"role": "user", "content": f"Return exactly this text: {text}"}],
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