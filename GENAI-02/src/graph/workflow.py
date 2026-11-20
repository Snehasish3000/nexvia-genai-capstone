"""LangGraph multi-agent workflow with bounded loops and durable HITL barriers."""
import base64
import logging
import time
from typing import Any, Literal
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from src.agents.coder import CoderAgent
from src.agents.planner import PlannerAgent
from src.agents.researcher import ResearcherAgent
from src.agents.reviewer import ReviewerAgent
from src.config import Settings
from src.graph.state import AgentState
from src.mcp_servers.filesystem_service import WorkspaceFileService
from src.persistence.run_store import RunStore
from src.schemas import WorkflowRequest
from src.security.guardrails import GuardrailViolation, ensure_safe_tool_action, inspect_user_input
from src.telemetry.otel import record_node, span

logger = logging.getLogger(__name__)


def _usage(state: AgentState, tokens: int = 0, cost_usd: float = 0.0) -> dict[str, Any]:
    totals = {
        "token_estimate": int(state.get("token_estimate", 0)) + max(tokens, 0),
        "cost_usd_estimate": round(float(state.get("cost_usd_estimate", 0.0)) + max(cost_usd, 0.0), 6),
    }
    metrics = dict(state.get("metrics", {}))
    metrics.update(totals)
    return {**totals, "metrics": metrics}


def _advance(state: AgentState, node: str) -> dict[str, Any]:
    step_count = int(state.get("step_count", 0)) + 1
    max_steps = int(state.get("max_steps", 12))
    metrics = dict(state.get("metrics", {}))
    visits = dict(metrics.get("node_visits", {}))
    visits[node] = int(visits.get(node, 0)) + 1
    if step_count > max_steps:
        raise GuardrailViolation(f"Maximum step count exceeded ({max_steps})")
    if visits[node] > 4:
        raise GuardrailViolation(f"Cycle detection triggered for node '{node}'")
    if int(state.get("token_estimate", 0)) > int(metrics.get("max_tokens", 18_000)):
        raise GuardrailViolation("Cumulative token budget exceeded")
    if float(state.get("cost_usd_estimate", 0.0)) > float(metrics.get("max_cost_usd", 0.75)):
        raise GuardrailViolation("Cumulative USD budget exceeded")
    visits_list = list(metrics.get("visited_nodes", []))
    visits_list.append(node)
    metrics.update({"node_visits": visits, "visited_nodes": visits_list, "step_count": step_count})
    return {"step_count": step_count, "metrics": metrics}


def _elapsed(metrics: dict[str, Any], key: str, started: float) -> dict[str, Any]:
    latencies = dict(metrics.get("latency_ms", {}))
    latencies[key] = round((time.perf_counter() - started) * 1000, 2)
    metrics["latency_ms"] = latencies
    metrics["total_latency_ms"] = round(sum(float(value) for value in latencies.values()), 2)
    return metrics


def _render_report(state: AgentState) -> str:
    evidence = state.get("research_summary", "No research evidence was successfully retrieved.")
    sources = state.get("sources", [])
    execution = state.get("code_execution", {})
    output_lines = [
        "# Executive Research & Financial Analysis Report",
        "",
        f"**Objective:** {state.get('goal', '')}",
        f"**Company / subject:** {state.get('company') or 'Not separately specified'}",
        f"**Generated (UTC):** {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())}",
        "",
        "## Executive summary",
        "This run assembled a source register, inspected a bounded set of approved source pages, and executed a Python analysis step in the configured sandbox. Treat statements below as evidence inventory and analytical workflow results; do not interpret an unverified search result as an audited financial fact.",
        "",
        "## Research evidence",
        evidence[:12_000],
        "",
        "## Structured SEC XBRL facts",
        f"The researcher supplied {len(state.get('financial_facts', []))} selected annual US-GAAP facts to the sandbox analysis step.",
        "| Metric | Fiscal year | Value | Unit | Filing date |",
        "|---|---:|---:|---|---|",
    ]
    for fact in state.get("financial_facts", [])[:30]:
        output_lines.append(
            f"| {fact.get('metric', 'unknown')} | {fact.get('fiscal_year', '')} | {fact.get('value', '')} | {fact.get('unit', '')} | {fact.get('filed', '')} |"
        )
    output_lines.extend([
        "",
        "## Computational analysis",
        f"- Sandbox execution success: **{bool(execution.get('success', False))}**",
        f"- Execution mode: **{execution.get('mode', 'unknown')}**",
        f"- Execution duration: **{execution.get('execution_ms', 0)} ms**",
        "- Captured standard output:",
        "```text",
        str(execution.get("stdout", "No stdout captured"))[:6000],
        "```",
        "",
        "## Source register",
    ])
    if sources:
        for index, source in enumerate(sources[:10], start=1):
            title = str(source.get("title", "Source"))
            url = str(source.get("url", ""))
            kind = str(source.get("source_type", "web"))
            output_lines.append(f"{index}. [{title}]({url}) — {kind}")
            excerpt = str(source.get("excerpt", "")).strip()
            if excerpt:
                output_lines.append(f"   - Search excerpt: {excerpt[:500]}")
    else:
        output_lines.append("No external source was retrieved successfully. This is a process demonstration, not a completed factual research deliverable.")
    output_lines.extend([
        "",
        "## Limitations & verification requirements",
        "- Search result snippets are discovery aids and are not equivalent to reading or validating an official filing.",
        "- SEC EDGAR search links must be opened and the filing period, units, and reported line items verified before financial conclusions are published.",
        "- Missing company identifiers, restatements, fiscal-period mismatches, or incomplete source coverage can materially change calculations.",
        "- Mock sandbox mode does not execute generated code. Enable E2B for isolated live execution; no generated code is run on the host OS.",
        "- This report is an engineering demonstration and is not investment advice.",
        "",
        "## Execution plan",
    ])
    for task in state.get("plan", {}).get("tasks", []):
        output_lines.append(f"- **{task.get('title', task.get('id', 'Task'))}:** {task.get('expected_output', '')}")
    return "\n".join(output_lines)[:30_000]


def build_workflow(
    *,
    settings: Settings,
    planner: PlannerAgent,
    researcher: ResearcherAgent,
    coder: CoderAgent,
    reviewer: ReviewerAgent,
    file_service: WorkspaceFileService,
    run_store: RunStore | None = None,
    checkpointer: Any = None,
) -> Any:
    async def guardrail_node(state: AgentState) -> dict[str, Any]:
        with span("graph.guardrail", {"run.id": state.get("run_id", "")}):
            advance = _advance(state, "guardrail")
            try:
                sanitized = inspect_user_input(str(state.get("goal", "")), settings)
                requested = list(state.get("requested_actions", []))
                goal_lower = sanitized.lower()
                if state.get("simulate_risky_actions"):
                    if "send email" in goal_lower or "dispatch email" in goal_lower:
                        requested.append("send_email")
                    if "spend" in goal_lower or "purchase" in goal_lower:
                        requested.append("spend_money")
                return {**advance, "goal": sanitized, "requested_actions": sorted(set(requested)), "status": "running"}
            except GuardrailViolation as exc:
                return {**advance, "status": "failed", "errors": [str(exc)]}

    async def planner_node(state: AgentState) -> dict[str, Any]:
        started = time.perf_counter()
        advance = _advance(state, "planner")
        try:
            result = await planner.run(state["goal"], state.get("company"))
            usage = _usage(state, int(planner.llm.last_usage.get("tokens", 0)), float(planner.llm.last_usage.get("cost_usd", 0.0)))
            metrics = _elapsed(dict(advance["metrics"]), "planner", started)
            metrics.update({"token_estimate": usage["token_estimate"], "cost_usd_estimate": usage["cost_usd_estimate"]})
            if run_store:
                await run_store.append_event(state["run_id"], "planner_completed", {"task_count": len(result.get("tasks", []))})
            return {**advance, **usage, "plan": result, "metrics": metrics}
        except Exception as exc:
            logger.exception("Planner node failed")
            return {**advance, "status": "failed", "errors": list(state.get("errors", [])) + [f"planner: {type(exc).__name__}: {str(exc)[:500]}"]}

    async def researcher_node(state: AgentState) -> dict[str, Any]:
        started = time.perf_counter()
        advance = _advance(state, "researcher")
        try:
            result = await researcher.run(state["goal"], state.get("company"))
            metrics = _elapsed(dict(advance["metrics"]), "researcher", started)
            if run_store:
                await run_store.append_event(state["run_id"], "research_completed", {"sources": len(result.get("sources", []))})
            return {**advance, **result, "financial_facts": result.get("financial_facts", []), "metrics": metrics}
        except Exception as exc:
            logger.exception("Researcher node failed")
            return {**advance, "sources": [], "financial_facts": [], "research_summary": f"Research retrieval failed: {type(exc).__name__}", "status": "running", "errors": list(state.get("errors", [])) + [f"researcher: {type(exc).__name__}: {str(exc)[:500]}"]}

    async def coder_node(state: AgentState) -> dict[str, Any]:
        started = time.perf_counter()
        advance = _advance(state, "coder")
        try:
            result = await coder.run(
                state["goal"],
                state.get("research_summary", ""),
                state.get("review_feedback", ""),
                state.get("financial_facts", []),
            )
            usage = coder.llm.last_usage
            usage_update = _usage(state, int(usage.get("tokens", 0)), float(usage.get("cost_usd", 0.0)))
            metrics = _elapsed(dict(advance["metrics"]), "coder", started)
            metrics.update({"token_estimate": usage_update["token_estimate"], "cost_usd_estimate": usage_update["cost_usd_estimate"]})
            return {**advance, **usage_update, **result, "metrics": metrics}
        except Exception as exc:
            logger.exception("Coder node failed")
            return {**advance, "status": "failed", "errors": list(state.get("errors", [])) + [f"coder: {type(exc).__name__}: {str(exc)[:500]}"]}

    async def reviewer_node(state: AgentState) -> dict[str, Any]:
        started = time.perf_counter()
        advance = _advance(state, "reviewer")
        report = _render_report(state)
        try:
            result = await reviewer.run(
                state["goal"], report, state.get("sources", []), state.get("code_execution", {}), int(state.get("revision", 0))
            )
            usage = reviewer.llm.last_usage
            usage_update = _usage(state, int(usage.get("tokens", 0)), float(usage.get("cost_usd", 0.0)))
            revision = int(state.get("revision", 0))
            if not result.get("approved", False) and revision < int(state.get("max_revisions", settings.max_revisions)):
                revision += 1
            metrics = _elapsed(dict(advance["metrics"]), "reviewer", started)
            metrics.update({"token_estimate": usage_update["token_estimate"], "cost_usd_estimate": usage_update["cost_usd_estimate"]})
            if run_store:
                await run_store.append_event(state["run_id"], "review_completed", {"approved": result.get("approved"), "score": result.get("score"), "revision": revision})
            return {**advance, **usage_update, "report_text": report, "review": result, "review_feedback": result.get("critique", ""), "revision": revision, "metrics": metrics}
        except Exception as exc:
            logger.exception("Reviewer node failed")
            return {**advance, "report_text": report, "review": {"approved": False, "score": 0.0, "issues": [str(exc)], "critique": "Review service failed; manual approval required."}, "errors": list(state.get("errors", [])) + [f"reviewer: {type(exc).__name__}: {str(exc)[:500]}"]}

    async def risk_gate_node(state: AgentState) -> dict[str, Any]:
        actions = list(state.get("requested_actions", []))
        if not actions:
            return {**_advance(state, "risk_gate"), "approval_status": "not_required"}
        request_payload = {
            "type": "external_action_approval",
            "actions": actions,
            "summary": "The workflow requests simulated external mutations. No action will execute before explicit approval.",
            "risk": "high",
        }
        decision = interrupt(request_payload)
        action = str(decision.get("action", "reject")) if isinstance(decision, dict) else str(decision)
        note = str(decision.get("note", "")) if isinstance(decision, dict) else ""
        approved = action in {"approve", "edit"}
        if approved:
            for requested in actions:
                ensure_safe_tool_action(requested, approved=True)
        return {
            **_advance(state, "risk_gate"),
            "approval_status": "approved" if approved else "rejected",
            "approval_note": note,
            "approval_payload": request_payload,
            "metrics": {**state.get("metrics", {}), "risky_action_decision": action},
        }

    async def simulate_actions_node(state: AgentState) -> dict[str, Any]:
        """Record approved action simulations; no real email or spend operation is ever dispatched."""
        advance = _advance(state, "simulate_actions")
        if state.get("approval_status") != "approved":
            return {**advance, "action_results": [{"action": item, "status": "not_executed", "reason": "Human approval was not granted"} for item in state.get("requested_actions", [])]}
        results: list[dict[str, Any]] = []
        for action in state.get("requested_actions", []):
            ensure_safe_tool_action(action, approved=True)
            results.append({"action": action, "status": "simulated", "message": "Approval recorded; no external side effect was dispatched."})
        metrics = dict(advance["metrics"])
        metrics["simulated_risky_actions"] = len(results)
        if run_store:
            await run_store.append_event(state["run_id"], "risky_actions_simulated", {"results": results})
        return {**advance, "action_results": results, "metrics": metrics}

    async def finalize_node(state: AgentState) -> dict[str, Any]:
        advance = _advance(state, "finalize")
        approval_status = state.get("approval_status", "pending")
        if approval_status != "approved":
            status = "rejected" if approval_status == "rejected" else "awaiting_approval"
            return {**advance, "status": status, "approval_payload": {"type": "final_report_write", "path": state.get("report_path", "reports/executive_report.md"), "summary": "Persist reviewed report to the isolated workspace."}}
        report = str(state.get("report_text", _render_report(state)))
        if len(report) > settings.max_report_chars:
            return {**advance, "status": "failed", "errors": list(state.get("errors", [])) + ["Report exceeds configured maximum length"]}
        try:
            file_info = await file_service.write_text(str(state.get("report_path", "reports/executive_report.md")), report)
            artifact_names = list(state.get("artifacts", []))
            artifact_names.append(str(file_info["path"]))
            artifact_payloads = state.get("code_execution", {}).get("artifact_data", {})
            if isinstance(artifact_payloads, dict):
                for artifact_name, artifact_value in artifact_payloads.items():
                    safe_name = str(artifact_name).replace("\\", "/").split("/")[-1]
                    extension = safe_name.rsplit(".", 1)[-1].lower() if "." in safe_name else ""
                    if extension not in {"png", "svg"} or not safe_name:
                        continue
                    try:
                        if extension == "png":
                            artifact_bytes = base64.b64decode(str(artifact_value), validate=True)
                        else:
                            artifact_bytes = str(artifact_value).encode("utf-8")
                        saved = await file_service.write_bytes(f"artifacts/{state['run_id']}_{safe_name}", artifact_bytes)
                        artifact_names.append(str(saved["path"]))
                    except (ValueError, TypeError) as exc:
                        logger.warning("Skipping malformed sandbox artifact %s: %s", safe_name, type(exc).__name__)
            metrics = dict(advance["metrics"])
            metrics["report_bytes"] = int(file_info["bytes_written"])
            metrics["step_count"] = int(advance["step_count"])
            if run_store:
                await run_store.append_event(state["run_id"], "report_persisted", file_info)
            return {**advance, "status": "completed", "report_text": report, "artifacts": artifact_names, "metrics": metrics, "approval_payload": {}}
        except Exception as exc:
            logger.exception("Final report persistence failed")
            return {**advance, "status": "failed", "errors": list(state.get("errors", [])) + [f"finalize: {type(exc).__name__}: {str(exc)[:500]}"]}

    async def finish_node(state: AgentState) -> dict[str, Any]:
        return {**_advance(state, "finish"), "status": state.get("status", "failed")}

    def route_after_guardrail(state: AgentState) -> Literal["planner", "finish"]:
        return "planner" if state.get("status") != "failed" else "finish"

    def route_after_planner(state: AgentState) -> Literal["researcher", "finish"]:
        return "researcher" if state.get("status") != "failed" else "finish"

    def route_after_researcher(state: AgentState) -> Literal["coder", "finish"]:
        return "coder" if state.get("status") != "failed" else "finish"

    def route_after_coder(state: AgentState) -> Literal["reviewer", "finish"]:
        return "reviewer" if state.get("status") != "failed" else "finish"

    def route_after_reviewer(state: AgentState) -> Literal["coder", "risk_gate", "finalize", "finish"]:
        if state.get("status") == "failed":
            return "finish"
        review = state.get("review", {})
        if not review.get("approved", False) and int(state.get("revision", 0)) < int(state.get("max_revisions", settings.max_revisions)):
            return "coder"
        if state.get("requested_actions"):
            return "risk_gate"
        return "finalize"

    def instrument_node(name: str, fn: Any) -> Any:
        async def wrapped(state: AgentState) -> dict[str, Any]:
            started = time.perf_counter()
            succeeded = True
            with span(f"graph.node.{name}", {"graph.node": name, "run.id": state.get("run_id", "")}):
                try:
                    return await fn(state)
                except GuardrailViolation as exc:
                    succeeded = False
                    errors = list(state.get("errors", []))
                    errors.append(f"guardrail: {str(exc)[:500]}")
                    return {"status": "failed", "errors": errors}
                except Exception:
                    succeeded = False
                    raise
                finally:
                    record_node(name, (time.perf_counter() - started) * 1000, succeeded)
        return wrapped

    builder = StateGraph(AgentState)
    builder.add_node("guardrail", instrument_node("guardrail", guardrail_node))
    builder.add_node("planner", instrument_node("planner", planner_node))
    builder.add_node("researcher", instrument_node("researcher", researcher_node))
    builder.add_node("coder", instrument_node("coder", coder_node))
    builder.add_node("reviewer", instrument_node("reviewer", reviewer_node))
    builder.add_node("risk_gate", instrument_node("risk_gate", risk_gate_node))
    builder.add_node("simulate_actions", instrument_node("simulate_actions", simulate_actions_node))
    builder.add_node("finalize", instrument_node("finalize", finalize_node))
    builder.add_node("finish", instrument_node("finish", finish_node))
    builder.add_edge(START, "guardrail")
    builder.add_conditional_edges("guardrail", route_after_guardrail)
    builder.add_conditional_edges("planner", route_after_planner)
    builder.add_conditional_edges("researcher", route_after_researcher)
    builder.add_conditional_edges("coder", route_after_coder)
    builder.add_conditional_edges("reviewer", route_after_reviewer)
    builder.add_edge("risk_gate", "simulate_actions")
    builder.add_edge("simulate_actions", "finalize")
    builder.add_edge("finalize", END)
    builder.add_edge("finish", END)
    # Persist a checkpoint immediately before committing the report to the workspace.
    return builder.compile(checkpointer=checkpointer, interrupt_before=["finalize"])


def initial_state(request: WorkflowRequest, settings: Settings) -> AgentState:
    max_steps = min(request.max_steps or settings.max_steps, settings.max_steps)
    max_cost = min(request.max_cost_usd or settings.max_cost_usd_per_run, settings.max_cost_usd_per_run)
    run_id = str(uuid4())
    return {
        "run_id": run_id,
        "thread_id": request.thread_id,
        "goal": request.goal,
        "task_domain": request.task_domain,
        "company": request.company,
        "plan": {},
        "sources": [],
        "page_evidence": [],
        "research_summary": "",
        "financial_facts": [],
        "code": "",
        "code_execution": {},
        "report_text": "",
        "review": {},
        "review_feedback": "",
        "revision": 0,
        "max_revisions": settings.max_revisions,
        "step_count": 0,
        "max_steps": max_steps,
        "token_estimate": 0,
        "cost_usd_estimate": 0.0,
        "metrics": {"max_tokens": settings.max_tokens_per_run, "max_cost_usd": max_cost, "latency_ms": {}, "node_visits": {}, "visited_nodes": []},
        "errors": [],
        "status": "queued",
        "approval_status": "pending",
        "approval_note": "",
        "approval_payload": {},
        "requested_actions": [],
        "action_results": [],
        "simulate_risky_actions": request.simulate_risky_actions,
        "report_path": f"reports/{run_id}_executive_report.md",
        "artifacts": [],
    }
