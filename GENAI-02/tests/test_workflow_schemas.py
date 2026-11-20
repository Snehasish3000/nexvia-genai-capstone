from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.schemas import ApprovalAction, ApprovalDecision, ExecutionPlan, PlanTask


def test_edit_approval_requires_non_empty_edited_report() -> None:
    with pytest.raises(ValidationError):
        ApprovalDecision(action=ApprovalAction.EDIT, note="edit it")
    decision = ApprovalDecision(action=ApprovalAction.EDIT, edited_report="# Reviewed report")
    assert decision.edited_report == "# Reviewed report"


def test_execution_plan_rejects_unknown_dependencies() -> None:
    with pytest.raises(ValidationError):
        ExecutionPlan(
            objective="Produce an auditable research report",
            tasks=[
                PlanTask(id="task-1", title="Collect sources", description="Collect official sources", expected_output="Source register", depends_on=["missing"]),
                PlanTask(id="task-2", title="Review facts", description="Review each retrieved fact", expected_output="Fact review"),
            ],
            success_criteria=["Source provenance is preserved"],
        )


def test_execution_plan_accepts_valid_dependency_dag() -> None:
    plan = ExecutionPlan(
        objective="Produce an auditable research report",
        tasks=[
            PlanTask(id="task-1", title="Collect sources", description="Collect official sources", expected_output="Source register"),
            PlanTask(id="task-2", title="Review facts", description="Review each retrieved fact", expected_output="Fact review", depends_on=["task-1"]),
        ],
        success_criteria=["Source provenance is preserved"],
    )
    assert plan.tasks[1].depends_on == ["task-1"]
