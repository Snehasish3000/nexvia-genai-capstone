"""Streamlit HITL dashboard for human operators of the GENAI-02 workflow."""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

import streamlit as st

from api_client import ApiError, WorkflowApiClient

st.set_page_config(
    page_title="Nexvia GENAI-02 | Agent Control Room",
    page_icon="🧭",
    layout="wide",
    initial_sidebar_state="expanded",
)

DEFAULT_API_URL = os.getenv("API_BASE_URL", "http://localhost:8010")
DEFAULT_JAEGER_URL = os.getenv("JAEGER_UI_URL", "http://localhost:16686")


def _format_time(value: Any) -> str:
    if not value:
        return "—"
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone().strftime("%d %b %Y, %H:%M:%S %Z")
    except ValueError:
        return str(value)


def _money(value: Any) -> str:
    try:
        return f"${float(value):.6f}"
    except (TypeError, ValueError):
        return "$0.000000"


def _safe_json(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, default=str)


def _run_label(item: dict[str, Any]) -> str:
    goal = str(item.get("goal", "Workflow run")).replace("\n", " ")
    if len(goal) > 78:
        goal = goal[:75] + "..."
    return f"{item.get('status', 'unknown').upper()} · {goal} · {str(item.get('run_id', ''))[:8]}"


st.markdown(
    """
    <style>
      .main .block-container { padding-top: 1.7rem; padding-bottom: 3rem; max-width: 1500px; }
      div[data-testid="stMetric"] { border: 1px solid rgba(128,128,128,.22); border-radius: 12px; padding: 12px 15px; }
      .status-pill { display:inline-block; padding:4px 10px; border-radius:999px; font-size:12px; font-weight:700; background:#e8eefc; color:#1d4ed8; }
      .muted { color: #6b7280; font-size: .88rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("🧭 Agent Control Room")
st.caption("Nexvia Technologies · GENAI-02 Multi-Agent Autonomous Workflow System")

with st.sidebar:
    st.subheader("Connection")
    api_url = st.text_input("API base URL", value=DEFAULT_API_URL)
    api_token = st.text_input(
        "API bearer token",
        value=os.getenv("API_ACCESS_TOKEN", ""),
        type="password",
        help="Required only when API_ACCESS_TOKEN is configured on the backend.",
    )
    jaeger_url = st.text_input("Jaeger trace UI", value=DEFAULT_JAEGER_URL)
    refresh = st.checkbox("Refresh run history on rerun", value=True)
    st.divider()
    st.markdown("**Safety boundary**")
    st.caption("This dashboard never sends email or spends money. Risky-action approvals record a simulation only. Final report writes are constrained to the backend-managed workspace.")
    st.link_button("Open Jaeger traces", jaeger_url, use_container_width=True)

try:
    api = WorkflowApiClient(api_url, api_token)
    health = api.health()
    runs = api.list_runs(limit=75)
except (ApiError, ValueError) as exc:
    st.error(f"Backend unavailable: {exc}")
    st.info("Start the GENAI-02 API and verify the API URL and bearer token in the sidebar.")
    st.stop()

status_counts: dict[str, int] = {}
for item in runs:
    key = str(item.get("status", "unknown")).lower()
    status_counts[key] = status_counts.get(key, 0) + 1

m1, m2, m3, m4 = st.columns(4)
m1.metric("API status", str(health.get("status", "unknown")).upper())
m2.metric("Database", str(health.get("database", "unknown")).replace("_", " ").title())
m3.metric("Sandbox mode", str(health.get("sandbox_mode", "unknown")).upper())
m4.metric("Recorded runs", len(runs))

st.divider()
left, right = st.columns([1.1, 1.0], gap="large")
with left:
    st.subheader("Start a workflow")
    with st.form("start_workflow", clear_on_submit=False):
        goal = st.text_area(
            "Research / analysis objective",
            height=150,
            placeholder="Example: Compare the latest annual results and disclosed risks for two public companies...",
        )
        company = st.text_input("Company or subject (optional)")
        task_domain = st.selectbox(
            "Task domain",
            ["market_research_financial_analysis", "repository_bug_fixing", "general_research"],
        )
        c1, c2 = st.columns(2)
        with c1:
            max_steps = st.number_input("Maximum graph steps", min_value=3, max_value=50, value=12, step=1)
        with c2:
            max_cost = st.number_input("Maximum estimated cost (USD)", min_value=0.001, max_value=100.0, value=0.75, step=0.05, format="%.3f")
        simulate_actions = st.checkbox(
            "Enable simulated risky-action branch",
            value=False,
            help="Only activates an approval checkpoint and records a simulation. It never dispatches external side effects.",
        )
        start_submitted = st.form_submit_button("Start agent workflow", type="primary", use_container_width=True)
    if start_submitted:
        if len(goal.strip()) < 12:
            st.warning("Enter a goal containing at least 12 characters.")
        else:
            payload: dict[str, Any] = {
                "goal": goal.strip(),
                "company": company.strip() or None,
                "task_domain": task_domain,
                "simulate_risky_actions": simulate_actions,
                "max_steps": int(max_steps),
                "max_cost_usd": float(max_cost),
            }
            try:
                result = api.start_run(payload)
                st.session_state["selected_thread_id"] = result.get("thread_id", "")
                st.success(f"Workflow accepted · run {result.get('run_id', '')}")
                st.json({"status": result.get("status"), "current_step": result.get("current_step"), "approval_payload": result.get("approval_payload")})
                st.rerun()
            except ApiError as exc:
                st.error(str(exc))

with right:
    st.subheader("Run history")
    if runs:
        options = {str(item.get("thread_id")): item for item in runs if item.get("thread_id")}
        thread_ids = list(options)
        prior = st.session_state.get("selected_thread_id")
        default_index = thread_ids.index(prior) if prior in thread_ids else 0
        chosen_thread = st.selectbox(
            "Select a workflow thread",
            thread_ids,
            index=default_index,
            format_func=lambda value: _run_label(options[value]),
        )
        st.session_state["selected_thread_id"] = chosen_thread
        selected_run = options[chosen_thread]
        st.caption(f"Started: {_format_time(selected_run.get('started_at'))} · Run ID: `{selected_run.get('run_id', '')}`")
    else:
        chosen_thread = st.session_state.get("selected_thread_id", "")
        selected_run = None
        st.info("No operational run records are available yet. Start a workflow to create one.")
    st.markdown(
        " · ".join(f"**{name.title()}:** {count}" for name, count in sorted(status_counts.items())) or "No run statuses",
        unsafe_allow_html=False,
    )

st.divider()
thread_id = st.session_state.get("selected_thread_id", "")
if thread_id:
    try:
        snapshot = api.get_thread(thread_id)
        run_id = str(snapshot.get("run_id", selected_run.get("run_id", "") if selected_run else ""))
        status_value = str(snapshot.get("status", "unknown")).upper()
        st.subheader("Selected workflow")
        st.markdown(f'<span class="status-pill">{status_value}</span>', unsafe_allow_html=True)
        metric_values = snapshot.get("metrics") or {}
        a, b, c, d, e = st.columns(5)
        a.metric("Current step", snapshot.get("current_step") or "—")
        a_steps = metric_values.get("step_count", metric_values.get("steps", "—"))
        b.metric("Steps", a_steps)
        c.metric("Tokens", metric_values.get("token_estimate", "—"))
        d.metric("Estimated cost", _money(metric_values.get("cost_usd_estimate", 0)))
        latency = metric_values.get("total_latency_ms", metric_values.get("api_elapsed_ms", 0))
        e.metric("Latency", f"{float(latency):.0f} ms" if isinstance(latency, (int, float)) else "—")

        if snapshot.get("errors"):
            with st.expander("Workflow errors", expanded=True):
                for error in snapshot["errors"]:
                    st.error(str(error))

        if snapshot.get("approval_required"):
            payload = snapshot.get("approval_payload") or {}
            st.warning("Human approval is required before the workflow can continue.")
            p1, p2, p3 = st.columns([1.0, 1.0, 2.5])
            p1.metric("Approval type", str(payload.get("type", "unknown")))
            p2.metric("Risk", str(payload.get("risk", "unspecified")).upper())
            with p3:
                st.write(payload.get("summary", "Review the requested action."))
            if payload.get("path"):
                st.code(str(payload["path"]), language="text")
            if payload.get("actions"):
                st.write("Requested simulated actions:")
                st.json(payload["actions"])

            st.markdown("#### Human decision")
            action = st.radio("Decision", ["approve", "edit", "reject"], horizontal=True, key=f"decision-{thread_id}")
            note = st.text_input("Decision note", key=f"note-{thread_id}")
            current_report = str(snapshot.get("report") or "")
            edited_report = current_report
            if action == "edit":
                edited_report = st.text_area(
                    "Edited report (required for Edit)",
                    value=current_report,
                    height=260,
                    key=f"edited-{thread_id}",
                )
            confirm_label = {"approve": "Approve and resume", "edit": "Save edit and resume", "reject": "Reject checkpoint"}[action]
            if st.button(confirm_label, type="primary" if action != "reject" else "secondary", key=f"submit-{thread_id}"):
                if action == "edit" and not edited_report.strip():
                    st.error("An edited report is required for the Edit action.")
                else:
                    try:
                        result = api.approve(
                            thread_id,
                            action=action,
                            note=note,
                            edited_report=edited_report if action == "edit" else None,
                        )
                        st.success(f"Decision recorded. Workflow status: {result.get('status', 'unknown')}.")
                        st.rerun()
                    except ApiError as exc:
                        st.error(str(exc))
        else:
            st.info("No approval checkpoint is currently pending for this thread.")

        report = snapshot.get("report")
        if report:
            with st.expander("Generated executive report", expanded=True):
                st.markdown(str(report))
        artifacts = snapshot.get("artifacts") or []
        if artifacts:
            with st.expander(f"Workspace artifacts ({len(artifacts)})"):
                for artifact in artifacts:
                    st.code(str(artifact), language="text")

        st.subheader("Run event timeline")
        if run_id:
            try:
                events = api.run_events(run_id, limit=150)
                if events:
                    for event in events:
                        created = _format_time(event.get("created_at"))
                        with st.expander(f"{created} · {event.get('event_type', 'event')}", expanded=False):
                            st.json(event.get("event_payload", {}))
                else:
                    st.caption("No durable event records are available. The run store may be disconnected or still initializing.")
            except ApiError as exc:
                st.caption(f"Event history unavailable: {exc}")
        with st.expander("Raw workflow snapshot"):
            st.code(_safe_json(snapshot), language="json")
    except ApiError as exc:
        st.error(f"Unable to load thread: {exc}")
else:
    st.info("Select a recorded workflow above or start a new workflow to inspect its state and approval checkpoints.")

api.close()

st.divider()
st.caption("Operator guidance: verify the source register, review the report and generated code results, then approve or reject each checkpoint. The dashboard records decisions; it does not bypass backend policy checks.")
