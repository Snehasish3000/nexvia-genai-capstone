"""Execute the 20-task benchmark against a running GENAI-02 API instance."""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sys
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from benchmark import BenchmarkObservation, evaluate_observation, load_tasks, render_markdown, summarize

HERE = Path(__file__).resolve().parent
DEFAULT_DATASET = HERE / "tasks.json"
DEFAULT_OUTPUT = HERE / "results"


async def _request_json(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    payload: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    response = await client.request(method, url, json=payload)
    try:
        value = response.json()
    except ValueError:
        value = {"detail": response.text[:1200]}
    return response.status_code, value if isinstance(value, dict) else {"data": value}


async def run_one_task(
    client: httpx.AsyncClient,
    base_url: str,
    task: Any,
    *,
    resume_approvals: bool,
    approval_note: str,
) -> BenchmarkObservation:
    thread_id = f"bench-{task.id.lower()}-{uuid4().hex[:10]}"
    payload: dict[str, Any] = {
        "goal": task.goal,
        "task_domain": "market_research_financial_analysis",
        "company": task.company,
        "thread_id": thread_id,
        "simulate_risky_actions": task.simulate_risky_actions,
    }
    if task.max_steps is not None:
        payload["max_steps"] = task.max_steps
    if task.max_cost_usd is not None:
        payload["max_cost_usd"] = task.max_cost_usd

    started = time.perf_counter()
    status_code, body = await _request_json(client, "POST", f"{base_url}/api/v1/runs", payload=payload)
    elapsed_ms = (time.perf_counter() - started) * 1000
    if status_code not in {200, 202} or not body.get("approval_required") or not resume_approvals:
        return evaluate_observation(task, http_status=status_code, body=body, latency_ms=elapsed_ms)

    # Approval is explicit and opt-in. The graph's side-effect branch is simulation-only;
    # the runner never invokes email dispatch, payments, or other external mutations.
    resumed = False
    latest = body
    for _ in range(4):
        if not latest.get("approval_required"):
            break
        action_payload = {
            "action": "approve",
            "note": approval_note,
        }
        approve_status, approve_body = await _request_json(
            client,
            "POST",
            f"{base_url}/api/v1/threads/{thread_id}/approve",
            payload=action_payload,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000
        resumed = True
        latest = approve_body
        if approve_status >= 400:
            break
    return evaluate_observation(
        task,
        http_status=status_code,
        body=body,
        latency_ms=elapsed_ms,
        resumed=resumed,
        final_body=latest if resumed else None,
    )


async def execute(args: argparse.Namespace) -> int:
    tasks = load_tasks(args.dataset)
    if args.limit is not None:
        tasks = tasks[: args.limit]
    if args.validate_only:
        print(f"Validated {len(tasks)} benchmark tasks from {args.dataset}.")
        print("No API calls were made; no runtime benchmark metrics were generated.")
        return 0

    token = args.token or os.getenv("API_ACCESS_TOKEN", "")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    timeout = httpx.Timeout(args.timeout, connect=min(args.timeout, 10.0))
    observations: list[BenchmarkObservation] = []
    base_url = args.base_url.rstrip("/")
    async with httpx.AsyncClient(headers=headers, timeout=timeout) as client:
        health_status, health = await _request_json(client, "GET", f"{base_url}/api/v1/health")
        if health_status != 200:
            raise RuntimeError(f"API health check failed ({health_status}): {health}")
        print(f"Connected to {health.get('service', 'GENAI-02')} — sandbox={health.get('sandbox_mode', 'unknown')}")
        for index, task in enumerate(tasks, start=1):
            print(f"[{index:02d}/{len(tasks):02d}] {task.id} {task.title}", flush=True)
            try:
                observation = await run_one_task(
                    client,
                    base_url,
                    task,
                    resume_approvals=args.resume_approvals,
                    approval_note=args.approval_note,
                )
            except (httpx.HTTPError, RuntimeError) as exc:
                observation = BenchmarkObservation(
                    task_id=task.id,
                    category=task.category,
                    expected_checkpoint=task.expected_checkpoint,
                    observed_checkpoint="transport_error",
                    http_status=0,
                    task_success=False,
                    guardrail_triggered=False,
                    approval_triggered=False,
                    final_status="unavailable",
                    error=f"{type(exc).__name__}: {str(exc)[:900]}",
                )
            observations.append(observation)
            print(
                f"       {'PASS' if observation.task_success else 'FAIL'} | "
                f"observed={observation.observed_checkpoint} | {observation.latency_ms:.0f} ms",
                flush=True,
            )
            if args.pause_seconds > 0:
                await asyncio.sleep(args.pause_seconds)

    summary = summarize(observations)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    json_path = output_dir / f"benchmark-{stamp}.json"
    csv_path = output_dir / f"benchmark-{stamp}.csv"
    md_path = output_dir / f"benchmark-{stamp}.md"
    run_mode = "live-api-with-auto-resume" if args.resume_approvals else "live-api-checkpoint-observation"

    payload = {
        "run_mode": run_mode,
        "base_url": base_url,
        "started_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "summary": summary,
        "observations": [item.model_dump(mode="json") for item in observations],
    }
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(BenchmarkObservation.model_fields))
        writer.writeheader()
        for item in observations:
            writer.writerow(item.model_dump(mode="json"))
    md_path.write_text(render_markdown(summary, observations, run_mode=run_mode), encoding="utf-8")
    print("\nAggregate metrics")
    print(json.dumps(summary, indent=2))
    print(f"\nWrote:\n- {json_path}\n- {csv_path}\n- {md_path}")
    return 0 if summary["task_success_rate"] == 1.0 else 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.getenv("GENAI02_API_URL", "http://localhost:8010"))
    parser.add_argument("--token", default=None, help="Bearer token; otherwise API_ACCESS_TOKEN is read from the environment")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--limit", type=int, default=None, help="Run the first N tasks for a smoke test")
    parser.add_argument("--pause-seconds", type=float, default=0.5)
    parser.add_argument("--resume-approvals", action="store_true", help="Automatically approve persisted checkpoints; external actions remain simulated")
    parser.add_argument("--approval-note", default="Approved for benchmark validation; any risky external action remains simulated.")
    parser.add_argument("--validate-only", action="store_true", help="Validate the dataset without invoking the API")
    return parser.parse_args()


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(execute(parse_args())))
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Benchmark failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
