from __future__ import annotations

from typing import Optional

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


class OpenAIClient:
    """Thin wrapper around OpenAI Responses + Images with retries."""

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

    @retry(
        retry=retry_if_exception_type(Exception),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=8),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def text_echo(self, text: str = "ping") -> Optional[str]:
        """
        Tiny Responses API ping; returns echoed text or None if disabled.
        Uses the model from settings. Returns 'text' exactly on success.
        """
        if not self.enabled:
            return None
        self._require_enabled()

        resp = self._client.responses.create(
            model=settings.omega_llm_model,
            input=f"Return exactly this text: {text}",
        )

        # SDK convenience:
        if hasattr(resp, "output_text") and isinstance(resp.output_text, str):
            return resp.output_text.strip()

        # Fallback: manually read message content
        try:
            chunks = []
            for item in getattr(resp, "output", []) or []:
                if getattr(item, "type", None) == "message":
                    for c in getattr(item, "content", []) or []:
                        t = getattr(c, "text", None)
                        if isinstance(t, str):
                            chunks.append(t)
            out = "".join(chunks).strip()
            return out or None
        except Exception:
            return None

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