from __future__ import annotations

from evaluation.benchmark import BenchmarkObservation, BenchmarkTask, evaluate_observation, identify_checkpoint, summarize


def task(expected: str = "final_report_write") -> BenchmarkTask:
    return BenchmarkTask(
        id="MR-90",
        category="test-category",
        title="Scoring fixture",
        goal="Run the benchmark scoring fixture against a valid API response.",
        expected_checkpoint=expected,
        simulate_risky_actions=expected == "external_action_approval",
        tags=["prompt-injection"] if expected == "guardrail_rejection" else [],
    )


def test_identify_final_report_checkpoint() -> None:
    response = {
        "status": "awaiting_approval",
        "approval_required": True,
        "approval_payload": {"type": "final_report_write"},
    }
    assert identify_checkpoint(202, response) == "final_report_write"


def test_identify_guardrail_failure_from_graph_errors() -> None:
    response = {"status": "failed", "errors": ["guardrail: Maximum workflow steps exceeded (3)"]}
    assert identify_checkpoint(202, response) == "guardrail_failure"


def test_prompt_injection_is_scored_as_guardrail_rejection() -> None:
    result = evaluate_observation(
        task("guardrail_rejection"),
        http_status=400,
        body={"detail": "Potential prompt injection detected; request rejected"},
        latency_ms=9.4,
    )
    assert result.task_success is True
    assert result.guardrail_triggered is True
    assert result.observed_checkpoint == "guardrail_rejection"


def test_external_action_approval_is_detected() -> None:
    result = evaluate_observation(
        task("external_action_approval"),
        http_status=202,
        body={
            "status": "awaiting_approval",
            "approval_required": True,
            "approval_payload": {"type": "external_action_approval", "risk": "high"},
            "metrics": {"step_count": 5, "token_estimate": 1250, "cost_usd_estimate": 0.0025},
        },
        latency_ms=1225.0,
    )
    assert result.task_success is True
    assert result.approval_triggered is True
    assert result.steps == 5
    assert result.tokens == 1250
    assert result.cost_usd == 0.0025


def test_summary_uses_observed_values_and_zero_safe_denominators() -> None:
    items = [
        BenchmarkObservation(
            task_id="MR-01", category="research", expected_checkpoint="final_report_write",
            observed_checkpoint="final_report_write", http_status=202, task_success=True,
            guardrail_triggered=False, approval_triggered=True, steps=8, latency_ms=1000, cost_usd=0.01,
        ),
        BenchmarkObservation(
            task_id="MR-02", category="security", expected_checkpoint="guardrail_rejection",
            observed_checkpoint="guardrail_rejection", http_status=400, task_success=True,
            guardrail_triggered=True, approval_triggered=False, steps=0, latency_ms=10, cost_usd=0,
        ),
    ]
    result = summarize(items)
    assert result["tasks_total"] == 2
    assert result["task_success_rate"] == 1.0
    assert result["average_steps"] == 4.0
    assert result["average_latency_ms"] == 505.0
    assert result["guardrail_trigger_rate"] == 0.5
    assert result["approval_trigger_rate"] == 0.5
    assert summarize([])["tasks_total"] == 0
