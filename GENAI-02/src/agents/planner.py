"""Planner agent: converts a business goal into a validated execution DAG."""
from typing import Any

from src.config import Settings
from src.llm import LLMClient
from src.schemas import ExecutionPlan, PlanTask
from src.telemetry.otel import span


class PlannerAgent:
    def __init__(self, llm: LLMClient, settings: Settings) -> None:
        self.llm = llm
        self.settings = settings

    async def run(self, goal: str, company: str | None = None) -> dict[str, Any]:
        with span("agent.planner", {"agent.name": "planner"}):
            company_context = f"Target company: {company}." if company else ""
            generated = await self.llm.complete_json(
                "You are an enterprise workflow planner. Return only JSON with objective, tasks, success_criteria. "
                "Create a dependency-aware DAG with 3-6 atomic tasks. IDs must be unique. Do not propose unapproved external side effects.",
                f"Goal: {goal}\n{company_context}\nSchema: {{'objective':str,'tasks':[{{'id':str,'title':str,'description':str,'depends_on':[str],'expected_output':str}}],'success_criteria':[str]}}",
            )
            if generated:
                try:
                    return ExecutionPlan.model_validate(generated).model_dump(mode="json")
                except Exception as exc:
                    generated = None
            tasks = [
                PlanTask(id="research", title="Gather primary and secondary evidence", description="Search official filings, company sources, and reputable reference material; record source URLs and dates.", depends_on=[], expected_output="Source register and evidence-backed findings"),
                PlanTask(id="analysis", title="Analyze evidence and compute metrics", description="Normalize available facts, run reproducible calculations, and distinguish reported values from estimates.", depends_on=["research"], expected_output="Analysis outputs and reproducible Python results"),
                PlanTask(id="review", title="Review claims, calculations, and limitations", description="Cross-check all numerical claims against sources, flag missing data, and state uncertainty.", depends_on=["analysis"], expected_output="Reviewed executive report"),
            ]
            return ExecutionPlan(
                objective=goal[:1000],
                tasks=tasks,
                success_criteria=[
                    "Every material factual claim has a retrievable source URL.",
                    "All numeric values include units, period, and calculation provenance where available.",
                    "Limitations and unavailable data are disclosed explicitly.",
                ],
            ).model_dump(mode="json")
