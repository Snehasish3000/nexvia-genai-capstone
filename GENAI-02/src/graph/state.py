"""Typed graph state persisted by LangGraph checkpoints."""
from typing import Any, TypedDict


class AgentState(TypedDict, total=False):
    run_id: str
    thread_id: str
    goal: str
    task_domain: str
    company: str | None
    plan: dict[str, Any]
    sources: list[dict[str, Any]]
    page_evidence: list[dict[str, Any]]
    research_summary: str
    financial_facts: list[dict[str, Any]]
    code: str
    code_execution: dict[str, Any]
    report_text: str
    review: dict[str, Any]
    review_feedback: str
    revision: int
    max_revisions: int
    step_count: int
    max_steps: int
    token_estimate: int
    cost_usd_estimate: float
    metrics: dict[str, Any]
    errors: list[str]
    status: str
    approval_status: str
    approval_note: str
    approval_payload: dict[str, Any]
    requested_actions: list[str]
    action_results: list[dict[str, Any]]
    simulate_risky_actions: bool
    report_path: str
    artifacts: list[str]
