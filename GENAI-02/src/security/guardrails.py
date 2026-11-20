"""Input safety, PII minimization, cycle detection, and run-budget enforcement."""
import re
from dataclasses import dataclass, field
from typing import Any

from src.config import Settings


class GuardrailViolation(ValueError):
    """Raised when a request or workflow action violates a hard safety limit."""


INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"ignore\s+(all\s+)?(previous|prior)\s+instructions", re.I),
    re.compile(r"reveal\s+(the\s+)?(system|developer)\s+prompt", re.I),
    re.compile(r"act\s+as\s+(an?\s+)?(unrestricted|jailbroken)", re.I),
    re.compile(r"disable\s+(all\s+)?safety", re.I),
    re.compile(r"exfiltrat(e|ion)\s+(secrets|credentials|keys)", re.I),
)
PII_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I), "[REDACTED_EMAIL]"),
    (re.compile(r"\b(?:\+?\d{1,3}[ -]?)?(?:\d[ -]?){9,12}\b"), "[REDACTED_PHONE]"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[REDACTED_SSN]"),
    (re.compile(r"\b\d{12}\b"), "[REDACTED_ID_NUMBER]"),
)


@dataclass
class BudgetLedger:
    max_steps: int
    max_tokens: int
    max_cost_usd: float
    steps: int = 0
    tokens: int = 0
    cost_usd: float = 0.0
    visited_nodes: list[str] = field(default_factory=list)
    node_visits: dict[str, int] = field(default_factory=dict)

    def record_step(self, node: str) -> None:
        self.steps += 1
        self.node_visits[node] = self.node_visits.get(node, 0) + 1
        self.visited_nodes.append(node)
        if self.steps > self.max_steps:
            raise GuardrailViolation(f"Maximum workflow steps exceeded ({self.max_steps})")
        if self.node_visits[node] > 4:
            raise GuardrailViolation(f"Cycle detected: node '{node}' visited more than four times")

    def record_usage(self, tokens: int, cost_usd: float) -> None:
        if tokens < 0 or cost_usd < 0:
            raise GuardrailViolation("Token and cost usage cannot be negative")
        self.tokens += tokens
        self.cost_usd += cost_usd
        if self.tokens > self.max_tokens:
            raise GuardrailViolation(f"Token budget exceeded ({self.max_tokens})")
        if self.cost_usd > self.max_cost_usd:
            raise GuardrailViolation(f"Cost budget exceeded (${self.max_cost_usd:.2f})")

    def as_dict(self) -> dict[str, Any]:
        return {
            "steps": self.steps,
            "tokens": self.tokens,
            "cost_usd": round(self.cost_usd, 6),
            "max_steps": self.max_steps,
            "max_tokens": self.max_tokens,
            "max_cost_usd": self.max_cost_usd,
            "visited_nodes": list(self.visited_nodes),
        }


def inspect_user_input(text: str, settings: Settings) -> str:
    normalized = text.strip()
    if not normalized:
        raise GuardrailViolation("The goal cannot be empty")
    if len(normalized) > settings.max_input_chars:
        raise GuardrailViolation(f"The goal exceeds {settings.max_input_chars} characters")
    matched = [pattern.pattern for pattern in INJECTION_PATTERNS if pattern.search(normalized)]
    if matched:
        raise GuardrailViolation("Potential prompt injection detected; request rejected")
    scrubbed = normalized
    for pattern, replacement in PII_PATTERNS:
        scrubbed = pattern.sub(replacement, scrubbed)
    return scrubbed


def ensure_safe_tool_action(action: str, approved: bool) -> None:
    risky = {"send_email", "write_final_file", "spend_money", "external_mutation"}
    if action in risky and not approved:
        raise GuardrailViolation(f"Action '{action}' requires explicit human approval")
