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
    """
    if isinstance(part, str):
        return {"type": "input_text", "text": part}

    if isinstance(part, dict):
        p = dict(part)
        txt = p.get("text")
        ptype = p.get("type")
        if ptype == "input_text" and isinstance(txt, str):
            return {"type": "input_text", "text": txt}
        if ptype == "text" and isinstance(txt, str):
            return {"type": "input_text", "text": txt}
        if ptype is None and isinstance(txt, str):
            return {"type": "input_text", "text": txt}
        return {"type": "input_text", "text": str(part)}

    return {"type": "input_text", "text": str(part)}


def _messages_to_responses_input(messages: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Convert chat-style messages:
        [{"role": "system|user|assistant|tool", "content": str|list|dict}, ...]
    into a Responses API 'input' payload:

      {"input":[{"role":"...","content":[{"type":"input_text","text":"..."}]}]}
    """
    formatted: List[Dict[str, Any]] = []
    for m in messages:
        role = str(m.get("role", "")).strip() or "user"
        content = m.get("content", "")

        if isinstance(content, str):
            formatted.append({"role": role, "content": [{"type": "input_text", "text": content}]})
            continue

        parts: List[Dict[str, Any]] = []
        if isinstance(content, list):
            for part in content:
                parts.append(_coerce_part_to_input_text(part))
        elif isinstance(content, dict):
            parts.append(_coerce_part_to_input_text(content))
        else:
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
                text_attr = None
                if not isinstance(c, dict):
                    if hasattr(c, "text"):
                        t = getattr(c, "text")
                        if isinstance(t, str):
                            text_attr = t
                        else:
                            text_attr = getattr(t, "value", None)
                else:
                    text_attr = c.get("text")

                if isinstance(text_attr, str):
                    chunks.append(text_attr)
    except Exception:
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
        try:
            if model_name and model_name.lower().startswith("gpt-5"):
                req["reasoning"] = {"effort": "medium"}
        except Exception:
            pass

        resp = self._client.responses.create(**req)
        return _extract_responses_text(resp)

    # ---------------------------------------------------------------------
    # Back-compat: emulate Chat Completions via Responses (text-only).
    # This is sufficient for simple "messages -> content" uses (e.g., planner/coder).
    # It does NOT emulate function/tool-calls; those call sites should migrate to Responses.
    # ---------------------------------------------------------------------
    def chat_completions_create_compat(self, **kwargs):
        """
        Emulate openai.chat.completions.create(...) using the Responses API.

        Supported kwargs:
          - model
          - messages
          - max_tokens / max_output_tokens
          - temperature (ignored)

        Returns an object with .choices[0].message.content so existing code works.
        """
        model = kwargs.get("model") or settings.omega_llm_model
        messages = kwargs.get("messages") or []
        max_tokens = kwargs.get("max_tokens")
        max_output_tokens = kwargs.get("max_output_tokens") or max_tokens

        text = self.respond(
            model=model,
            messages=messages,
            max_output_tokens=max_output_tokens,
        ) or ""

        class _Msg:
            def __init__(self, content: str):
                self.content = content

        class _Choice:
            def __init__(self, msg: _Msg):
                self.message = msg

        class _Resp:
            def __init__(self, choices):
                self.choices = choices

        return _Resp([_Choice(_Msg(text))])

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