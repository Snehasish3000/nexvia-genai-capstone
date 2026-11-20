from __future__ import annotations
import asyncio

import pytest

from src.config import Settings
from src.sandbox.runner import SandboxRunner
from src.security.guardrails import BudgetLedger, GuardrailViolation, ensure_safe_tool_action


def test_budget_ledger_stops_at_step_ceiling() -> None:
    ledger = BudgetLedger(max_steps=2, max_tokens=100, max_cost_usd=0.01)
    ledger.record_step("planner")
    ledger.record_step("researcher")
    with pytest.raises(GuardrailViolation, match="Maximum workflow steps"):
        ledger.record_step("coder")


def test_budget_ledger_detects_repeated_node_cycle() -> None:
    ledger = BudgetLedger(max_steps=10, max_tokens=100, max_cost_usd=0.01)
    for _ in range(4):
        ledger.record_step("reviewer")
    with pytest.raises(GuardrailViolation, match="Cycle detected"):
        ledger.record_step("reviewer")


def test_budget_ledger_enforces_cumulative_token_and_cost_caps() -> None:
    token_ledger = BudgetLedger(max_steps=10, max_tokens=100, max_cost_usd=1.0)
    with pytest.raises(GuardrailViolation, match="Token budget exceeded"):
        token_ledger.record_usage(tokens=101, cost_usd=0.001)
    cost_ledger = BudgetLedger(max_steps=10, max_tokens=1000, max_cost_usd=0.01)
    with pytest.raises(GuardrailViolation, match="Cost budget exceeded"):
        cost_ledger.record_usage(tokens=10, cost_usd=0.02)


def test_risky_action_requires_human_approval() -> None:
    with pytest.raises(GuardrailViolation, match="requires explicit human approval"):
        ensure_safe_tool_action("send_email", approved=False)
    ensure_safe_tool_action("send_email", approved=True)


def test_mock_sandbox_is_non_executing_and_returns_fixture_output() -> None:
    settings = Settings(sandbox_mode="mock", persistence_enabled=False, otel_enabled=False)
    result = asyncio.run(SandboxRunner(settings).execute("print('hello from mock')"))
    assert result.success is True
    assert result.mode == "mock"
    assert "Evidence inventory completed" in result.stdout


def test_mock_sandbox_rejects_host_os_import_without_execution() -> None:
    settings = Settings(sandbox_mode="mock", persistence_enabled=False, otel_enabled=False)
    result = asyncio.run(SandboxRunner(settings).execute("import os\nprint(os.getcwd())"))
    assert result.success is False
    assert result.mode == "mock"
    assert "not on the sandbox allowlist" in str(result.error)
