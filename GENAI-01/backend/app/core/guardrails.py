"""Pre-LLM prompt-injection screening and privacy-preserving input normalization."""

import re
from dataclasses import dataclass


INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions?", re.I),
    re.compile(r"(reveal|print|show|dump|expose)\s+(the\s+)?(system prompt|developer message|secrets?|api keys?)", re.I),
    re.compile(r"act\s+as\s+(the\s+)?system", re.I),
    re.compile(r"bypass\s+(the\s+)?(safety|security|access control|rbac)", re.I),
    re.compile(r"<\s*\/?\s*system\s*>", re.I),
    re.compile(r"BEGIN\s+PROMPT\s+INJECTION", re.I),
)
PII_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("email", re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)),
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("phone", re.compile(r"(?<!\w)(?:\+?\d{1,3}[ .-]?)?(?:\(?\d{3,5}\)?[ .-]?)\d{3,4}[ .-]?\d{3,4}(?!\w)")),
)


@dataclass(frozen=True, slots=True)
class GuardrailResult:
    safe_text: str
    pii_types_redacted: tuple[str, ...]


class PromptInjectionDetected(ValueError):
    """Raised when the request attempts to override system instructions."""


class InputGuardrails:
    def __init__(self, max_chars: int = 2000) -> None:
        self.max_chars = max_chars

    def inspect(self, text: str) -> GuardrailResult:
        normalized = " ".join(text.split())
        if not normalized:
            raise ValueError("Question must not be empty")
        if len(normalized) > self.max_chars:
            raise ValueError(f"Question exceeds the {self.max_chars}-character limit")
        for pattern in INJECTION_PATTERNS:
            if pattern.search(normalized):
                raise PromptInjectionDetected("The request contains a prompt-injection pattern")
        redacted: list[str] = []
        safe_text = normalized
        for pii_type, pattern in PII_PATTERNS:
            if pattern.search(safe_text):
                redacted.append(pii_type)
                safe_text = pattern.sub(f"[{pii_type.upper()}_REDACTED]", safe_text)
        return GuardrailResult(safe_text=safe_text, pii_types_redacted=tuple(redacted))
