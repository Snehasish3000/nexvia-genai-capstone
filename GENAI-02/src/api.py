"""FastAPI orchestration API for starting, inspecting, and approving agent runs."""
from contextlib import asynccontextmanager
import hmac
import logging
import time
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from langgraph.types import Command

from src.agents.coder import CoderAgent
from src.agents.planner import PlannerAgent
from src.agents.researcher import ResearcherAgent
from src.agents.reviewer import ReviewerAgent
from src.config import get_settings
from src.graph.workflow import build_workflow, initial_state
from src.llm import LLMClient
from src.logging_config import configure_logging
from src.mcp_servers.gateway import MCPToolGateway
from src.persistence.checkpointer import checkpointer_context
from src.persistence.run_store import RunStore
from src.sandbox.runner import SandboxRunner
from src.schemas import ApprovalAction, ApprovalDecision, HealthResponse, RunStatus, WorkflowRequest, WorkflowResponse
from src.security.guardrails import GuardrailViolation, inspect_user_input
from src.telemetry.otel import configure_telemetry

logger = logging.getLogger(__name__)
settings = get_settings()
configure_logging(settings.log_level)
bearer_scheme = HTTPBearer(auto_error=False)


async def require_api_access(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> None:
    """Require a constant-time checked bearer token whenever one is configured."""
    if settings.api_access_token is None:
        if settings.app_env == "production":
            raise HTTPException(status_code=503, detail="API access token is not configured")
        return
    provided = credentials.credentials if credentials else ""
    expected = settings.api_access_token.get_secret_value()
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A valid bearer token is required",
            headers={"WWW-Authenticate": "Bearer"},
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_telemetry(settings)
    store = RunStore(settings)
    store_connected = await store.connect()
    mcp_gateway = MCPToolGateway(settings)
    try:
        await mcp_gateway.start()
        async with checkpointer_context(settings) as checkpointer:
            llm = LLMClient(settings)
            sandbox = SandboxRunner(settings)
            planner = PlannerAgent(llm, settings)
            researcher = ResearcherAgent(settings, mcp_gateway, mcp_gateway)
            coder = CoderAgent(llm, sandbox, settings)
            reviewer = ReviewerAgent(llm)
            graph = build_workflow(
                settings=settings,
                planner=planner,
                researcher=researcher,
                coder=coder,
                reviewer=reviewer,
                file_service=mcp_gateway,
                run_store=store,
                checkpointer=checkpointer,
            )
            app.state.settings = settings
            app.state.run_store = store
            app.state.store_connected = store_connected
            app.state.graph = graph
            app.state.llm = llm
            app.state.sandbox = sandbox
            app.state.mcp_gateway = mcp_gateway
            logger.info("GENAI-02 service started", extra={"tool": "startup"})
            yield
    finally:
        await mcp_gateway.close()
        await store.close()


app = FastAPI(
    title="Nexvia GENAI-02 — Multi-Agent Autonomous Workflow",
    version="1.0.0",
    description="Planner → Researcher → Coder → Reviewer with durable approval checkpoints, E2B isolation, Postgres state, and OpenTelemetry.",
    lifespan=lifespan,
    dependencies=[Depends(require_api_access)],
    openapi_url="/api/openapi.json",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8501", "http://localhost:5173"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
)

try:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app)
except ImportError:
    logger.info("FastAPI OpenTelemetry instrumentation package is not installed")


def _snapshot_state(snapshot: Any) -> dict[str, Any]:
    values = getattr(snapshot, "values", {}) or {}
    return values if isinstance(values, dict) else {}


def _has_interrupt(snapshot: Any) -> bool:
    for task in getattr(snapshot, "tasks", ()) or ():
        if getattr(task, "interrupts", None):
            return True
    return False


def _interrupt_payload(snapshot: Any) -> dict[str, Any] | None:
    for task in getattr(snapshot, "tasks", ()) or ():
        for item in getattr(task, "interrupts", ()) or ():
            value = getattr(item, "value", None)
            if isinstance(value, dict):
                return value
    return None


def _approval_payload(snapshot: Any, state: dict[str, Any]) -> dict[str, Any] | None:
    next_nodes = list(getattr(snapshot, "next", ()) or ())
    if "finalize" in next_nodes:
        return {
            "type": "final_report_write",
            "path": state.get("report_path", "reports/executive_report.md"),
            "summary": "Persist the reviewed report and sandbox artifacts to the isolated workspace.",
            "risk": "medium",
        }
    return _interrupt_payload(snapshot) or state.get("approval_payload") or None


def _response(
    state: dict[str, Any],
    thread_id: str,
    *,
    pending: bool = False,
    current_step: str | None = None,
    approval_payload: dict[str, Any] | None = None,
) -> WorkflowResponse:
    status_value = state.get("status", "running")
    if pending:
        status_value = RunStatus.AWAITING_APPROVAL.value
    if status_value not in {item.value for item in RunStatus}:
        status_value = RunStatus.RUNNING.value
    return WorkflowResponse(
        run_id=str(state.get("run_id", "")),
        thread_id=thread_id,
        status=RunStatus(status_value),
        current_step=current_step,
        approval_required=pending,
        approval_payload=(approval_payload or state.get("approval_payload") or None) if pending else None,
        report=state.get("report_text") or None,
        artifacts=state.get("artifacts", []),
        metrics=state.get("metrics", {}),
        errors=state.get("errors", []),
    )


@app.get("/api/v1/health", response_model=HealthResponse, tags=["operations"])
async def health(request: Request) -> HealthResponse:
    return HealthResponse(
        status="ok",
        service=settings.app_name,
        database="connected" if getattr(request.app.state, "store_connected", False) else "degraded_or_in_memory",
        sandbox_mode=settings.sandbox_mode,
    )


@app.post("/api/v1/runs", response_model=WorkflowResponse, status_code=status.HTTP_202_ACCEPTED, tags=["workflow"])
async def start_run(payload: WorkflowRequest, request: Request) -> WorkflowResponse:
    graph = request.app.state.graph
    store: RunStore = request.app.state.run_store
    try:
        sanitized_goal = inspect_user_input(payload.goal, settings)
        sanitized_company = inspect_user_input(payload.company, settings) if payload.company else None
        safe_payload = payload.model_copy(update={"goal": sanitized_goal, "company": sanitized_company})
    except GuardrailViolation as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    config = {"configurable": {"thread_id": payload.thread_id}, "recursion_limit": settings.max_steps + 5}
    existing = await graph.aget_state(config)
    if getattr(existing, "values", None):
        raise HTTPException(status_code=409, detail="thread_id already exists; use a new thread_id for a new workflow")
    initial = initial_state(safe_payload, settings)
    await store.create_run(initial["run_id"], payload.thread_id, safe_payload.goal, {"task_domain": payload.task_domain})
    started = time.perf_counter()
    try:
        await graph.ainvoke(initial, config=config)
        snapshot = await graph.aget_state(config)
        state = _snapshot_state(snapshot)
        next_nodes = list(getattr(snapshot, "next", ()) or ())
        pending = "finalize" in next_nodes or _has_interrupt(snapshot)
        if pending:
            approval_payload = _interrupt_payload(snapshot) or state.get("approval_payload") or {
                "type": "final_report_write",
                "path": state.get("report_path"),
                "summary": "Persist reviewed report to the managed workspace.",
            }
            await graph.aupdate_state(config, {"status": "awaiting_approval", "approval_payload": approval_payload})
            snapshot = await graph.aget_state(config)
            state = _snapshot_state(snapshot)
        elapsed = round((time.perf_counter() - started) * 1000, 2)
        state_metrics = dict(state.get("metrics", {}))
        state_metrics["api_elapsed_ms"] = elapsed
        state["metrics"] = state_metrics
        run_status = "awaiting_approval" if pending else str(state.get("status", "failed"))
        await store.update_run(
            str(state.get("run_id", initial["run_id"])),
            status=run_status,
            total_steps=int(state.get("step_count", 0)),
            tokens=int(state.get("token_estimate", 0)),
            cost_usd=float(state.get("cost_usd_estimate", 0.0)),
            metadata={"metrics": state_metrics},
        )
        return _response(
            state,
            payload.thread_id,
            pending=pending,
            current_step=next_nodes[0] if next_nodes else None,
            approval_payload=_approval_payload(snapshot, state),
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Workflow execution failed")
        await store.update_run(initial["run_id"], status="failed", metadata={"error": type(exc).__name__})
        raise HTTPException(status_code=500, detail=f"Workflow execution failed: {type(exc).__name__}") from exc


@app.get("/api/v1/runs", tags=["workflow"])
async def list_runs(request: Request, limit: int = 50) -> list[dict[str, Any]]:
    store: RunStore = request.app.state.run_store
    records = await store.list_runs(limit)
    return [{key: value.isoformat() if hasattr(value, "isoformat") else value for key, value in row.items()} for row in records]


@app.get("/api/v1/runs/{run_id}", tags=["workflow"])
async def get_run(run_id: str, request: Request) -> dict[str, Any]:
    store: RunStore = request.app.state.run_store
    record = await store.get_run(run_id)
    if not record:
        raise HTTPException(status_code=404, detail="Run not found in operational history")
    return {key: value.isoformat() if hasattr(value, "isoformat") else value for key, value in record.items()}


@app.get("/api/v1/runs/{run_id}/events", tags=["workflow"])
async def list_run_events(run_id: str, request: Request, limit: int = 100) -> list[dict[str, Any]]:
    """Expose bounded operational events for the authenticated approval dashboard."""
    store: RunStore = request.app.state.run_store
    events = await store.list_events(run_id, limit)
    return [
        {
            key: value.isoformat() if hasattr(value, "isoformat") else value
            for key, value in event.items()
        }
        for event in events
    ]


@app.get("/api/v1/threads/{thread_id}", response_model=WorkflowResponse, tags=["workflow"])
async def get_thread(thread_id: str, request: Request) -> WorkflowResponse:
    graph = request.app.state.graph
    config = {"configurable": {"thread_id": thread_id}}
    snapshot = await graph.aget_state(config)
    state = _snapshot_state(snapshot)
    if not state:
        raise HTTPException(status_code=404, detail="Workflow thread not found")
    pending = "finalize" in list(getattr(snapshot, "next", ()) or ()) or _has_interrupt(snapshot)
    return _response(
        state,
        thread_id,
        pending=pending,
        current_step=(list(snapshot.next)[0] if getattr(snapshot, "next", ()) else None),
        approval_payload=_approval_payload(snapshot, state),
    )


@app.post("/api/v1/threads/{thread_id}/approve", response_model=WorkflowResponse, tags=["human-in-the-loop"])
async def approve_thread(thread_id: str, decision: ApprovalDecision, request: Request) -> WorkflowResponse:
    graph = request.app.state.graph
    store: RunStore = request.app.state.run_store
    config = {"configurable": {"thread_id": thread_id}}
    snapshot = await graph.aget_state(config)
    state = _snapshot_state(snapshot)
    if not state:
        raise HTTPException(status_code=404, detail="Workflow thread not found")
    next_nodes = list(getattr(snapshot, "next", ()) or ())
    if not next_nodes and not _has_interrupt(snapshot):
        raise HTTPException(status_code=409, detail="Workflow has no pending approval checkpoint")
    try:
        if _has_interrupt(snapshot):
            resume_payload = decision.model_dump(mode="json")
            await graph.ainvoke(Command(resume=resume_payload), config=config)
        elif "finalize" in next_nodes:
            if decision.action == ApprovalAction.REJECT:
                update = {"approval_status": "rejected", "status": "rejected", "approval_note": decision.note}
            elif decision.action == ApprovalAction.EDIT:
                update = {"approval_status": "approved", "approval_note": decision.note, "report_text": decision.edited_report}
            else:
                update = {"approval_status": "approved", "approval_note": decision.note}
            await graph.aupdate_state(config, update)
            await graph.ainvoke(None, config=config)
        else:
            raise HTTPException(status_code=409, detail=f"No supported approval checkpoint is pending: {next_nodes}")
        updated = await graph.aget_state(config)
        updated_state = _snapshot_state(updated)
        updated_next = list(getattr(updated, "next", ()) or ())
        pending = "finalize" in updated_next or _has_interrupt(updated)
        if pending:
            payload = _approval_payload(updated, updated_state) or {
                "type": "final_report_write",
                "path": updated_state.get("report_path", "reports/executive_report.md"),
                "summary": "Persist reviewed report to the managed workspace.",
                "risk": "medium",
            }
            await graph.aupdate_state(config, {"status": "awaiting_approval", "approval_payload": payload})
            updated = await graph.aget_state(config)
            updated_state = _snapshot_state(updated)
        final_status = "awaiting_approval" if pending else str(updated_state.get("status", "failed"))
        await store.update_run(
            str(updated_state.get("run_id", "")),
            status=final_status,
            total_steps=int(updated_state.get("step_count", 0)),
            tokens=int(updated_state.get("token_estimate", 0)),
            cost_usd=float(updated_state.get("cost_usd_estimate", 0.0)),
            metadata={"approval_action": decision.action.value, "approval_note": decision.note[:500]},
        )
        await store.append_event(str(updated_state.get("run_id", "")), "human_approval", {"action": decision.action.value, "pending_again": pending})
        return _response(
            updated_state,
            thread_id,
            pending=pending,
            current_step=updated_next[0] if updated_next else None,
            approval_payload=_approval_payload(updated, updated_state),
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Approval/resume failed")
        raise HTTPException(status_code=500, detail=f"Unable to resume workflow: {type(exc).__name__}") from exc
