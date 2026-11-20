"""Pydantic v2 request and response contracts shared by API and graph."""
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator


class ApprovalAction(StrEnum):
    APPROVE = "approve"
    EDIT = "edit"
    REJECT = "reject"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    REJECTED = "rejected"
    FAILED = "failed"


class WorkflowRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    goal: str = Field(min_length=12, max_length=8000)
    task_domain: str = Field(default="market_research_financial_analysis", min_length=3, max_length=80)
    company: str | None = Field(default=None, max_length=160)
    thread_id: str = Field(default_factory=lambda: str(uuid4()), min_length=8, max_length=80)
    simulate_risky_actions: bool = False
    max_steps: int | None = Field(default=None, ge=3, le=50)
    max_cost_usd: float | None = Field(default=None, gt=0, le=100)


class ApprovalDecision(BaseModel):
    action: ApprovalAction
    note: str = Field(default="", max_length=2000)
    edited_report: str | None = Field(default=None, max_length=30_000)

    @model_validator(mode="after")
    def validate_edit(self) -> "ApprovalDecision":
        if self.action == ApprovalAction.EDIT and not (self.edited_report and self.edited_report.strip()):
            raise ValueError("edited_report is required when action='edit'")
        return self


class PlanTask(BaseModel):
    id: str = Field(min_length=2, max_length=40)
    title: str = Field(min_length=3, max_length=180)
    description: str = Field(min_length=5, max_length=1000)
    depends_on: list[str] = Field(default_factory=list)
    expected_output: str = Field(min_length=3, max_length=240)


class ExecutionPlan(BaseModel):
    objective: str = Field(min_length=8, max_length=1000)
    tasks: list[PlanTask] = Field(min_length=2, max_length=12)
    success_criteria: list[str] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def validate_dependencies(self) -> "ExecutionPlan":
        identifiers = {task.id for task in self.tasks}
        if len(identifiers) != len(self.tasks):
            raise ValueError("Plan task identifiers must be unique")
        for task in self.tasks:
            unknown = set(task.depends_on) - identifiers
            if unknown:
                raise ValueError(f"Unknown dependency IDs for {task.id}: {sorted(unknown)}")
            if task.id in task.depends_on:
                raise ValueError(f"Task {task.id} cannot depend on itself")
        return self


class ResearchSource(BaseModel):
    title: str
    url: str
    source_type: str = "web"
    excerpt: str = ""
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ResearchFinding(BaseModel):
    claim: str
    value: str | None = None
    unit: str | None = None
    source_url: str
    confidence: float = Field(default=0.65, ge=0, le=1)


class ReviewResult(BaseModel):
    approved: bool
    score: float = Field(ge=0, le=1)
    issues: list[str] = Field(default_factory=list)
    critique: str = ""
    verified_checks: list[str] = Field(default_factory=list)


class ToolCallRecord(BaseModel):
    tool_name: str
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    duration_ms: float = 0
    success: bool = True
    error_type: str | None = None


class WorkflowResponse(BaseModel):
    run_id: str
    thread_id: str
    status: RunStatus
    current_step: str | None = None
    approval_required: bool = False
    approval_payload: dict[str, Any] | None = None
    report: str | None = None
    artifacts: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: str
    service: str
    database: str
    sandbox_mode: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RunRecord(BaseModel):
    run_id: UUID
    thread_id: str
    status: RunStatus
    goal: str
    started_at: datetime
    completed_at: datetime | None = None
    total_steps: int = 0
    token_estimate: int = 0
    cost_usd_estimate: float = 0
    metadata: dict[str, Any] = Field(default_factory=dict)
