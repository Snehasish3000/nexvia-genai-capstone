#!/usr/bin/env python3
"""Compare dense, hybrid RRF and hybrid+rerank retrieval with RAGAS + LangSmith.

Live judge mode requires OPENAI_API_KEY and a populated Qdrant collection. Use --offline
for a deterministic, key-free harness that reports coverage/abstention checks (not RAGAS scores).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from app.api import _build_runtime  # noqa: E402
from app.core.config import Settings  # noqa: E402
from app.tracing import traceable  # noqa: E402

DATASET_PATH = Path(__file__).resolve().parent / "questions.json"
STRATEGIES = ("dense", "hybrid", "hybrid_rerank")


def load_questions(limit: int | None) -> list[dict[str, Any]]:
    rows = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    return rows[:limit] if limit else rows


def lexical_overlap(question: str, answer: str) -> float:
    q_terms = {word.casefold().strip(".,?!:;") for word in question.split() if len(word) > 3}
    a_terms = {word.casefold().strip(".,?!:;") for word in answer.split() if len(word) > 3}
    return len(q_terms & a_terms) / max(1, len(q_terms))


@traceable(name="nexvia_ragas_strategy_evaluation", run_type="chain")
async def run_strategy(
    strategy: str,
    questions: list[dict[str, Any]],
    service: Any,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for question in questions:
        started = time.perf_counter()
        query_text = str(question["question"])
        query_vector = await service.embeddings.embed_query(query_text)
        hits = await service.retriever.retrieve(
            query=query_text,
            query_vector=query_vector,
            role=str(question["role"]),
            mode=strategy,
            final_top_n=service.settings.final_top_n,
        )
        if strategy == "hybrid_rerank":
            hits = await service.reranker.rerank(query_text, hits, service.settings.final_top_n)
        response = await service.answer(
            question=query_text,
            user_id="evaluation-harness",
            role=str(question["role"]),
            retrieval_mode=strategy,
            request_id=f"eval-{strategy}-{question['id']}",
            query_vector_override=query_vector,
            precomputed_hits=hits,
        )
        contexts = [hit.chunk.text for hit in hits]
        records.append(
            {
                "id": question["id"],
                "user_input": question["question"],
                "response": response.answer,
                "retrieved_contexts": contexts,
                "reference": question["reference"],
                "answerable": question["answerable"],
                "abstained": response.abstained,
                "confidence": response.confidence,
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                "estimated_cost_usd": response.usage.estimated_cost_usd,
                "question_type": question["question_type"],
                "role": question["role"],
                "expected_source": question["expected_source"],
            }
        )
    return records


def calculate_offline_metrics(records: list[dict[str, Any]]) -> dict[str, float | int]:
    total = len(records)
    answerable = [row for row in records if row["answerable"]]
    unanswerable = [row for row in records if not row["answerable"]]
    correct_abstentions = sum(bool(row["abstained"]) for row in unanswerable)
    false_abstentions = sum(bool(row["abstained"]) for row in answerable)
    source_coverage = sum(bool(row["retrieved_contexts"]) for row in answerable) / max(1, len(answerable))
    return {
        "tasks": total,
        "answerable_context_coverage": round(source_coverage, 4),
        "unanswerable_abstention_accuracy": round(correct_abstentions / max(1, len(unanswerable)), 4),
        "answerable_false_abstention_rate": round(false_abstentions / max(1, len(answerable)), 4),
        "avg_latency_ms": round(sum(float(row["latency_ms"]) for row in records) / max(1, total), 2),
        "avg_estimated_cost_usd": round(sum(float(row["estimated_cost_usd"]) for row in records) / max(1, total), 8),
    }


async def run_ragas_metrics(records_by_strategy: dict[str, list[dict[str, Any]]], api_key: str) -> dict[str, Any]:
    try:
        from openai import AsyncOpenAI
        from ragas.embeddings import embedding_factory
        from ragas.llms import llm_factory
        from ragas.metrics.collections import AnswerRelevancy, ContextPrecision, ContextRecall, Faithfulness
    except ImportError as exc:
        raise RuntimeError(
            "Install evaluation extras from backend/requirements.txt (ragas, openai and datasets)."
        ) from exc

    client = AsyncOpenAI(api_key=api_key)
    judge = llm_factory("gpt-4o-mini", provider="openai", client=client)
    embeddings = embedding_factory("openai", model="text-embedding-3-small", client=client)
    metrics = {
        "faithfulness": Faithfulness(llm=judge),
        "answer_relevancy": AnswerRelevancy(llm=judge, embeddings=embeddings),
        "context_precision": ContextPrecision(llm=judge),
        "context_recall": ContextRecall(llm=judge),
    }
    semaphore = asyncio.Semaphore(4)

    async def score_one(metric_name: str, metric: Any, row: dict[str, Any]) -> float:
        if not row["retrieved_contexts"] and metric_name in {"faithfulness", "context_precision", "context_recall"}:
            return 0.0
        async with semaphore:
            result = await metric.ascore(
                user_input=row["user_input"],
                response=row["response"],
                retrieved_contexts=row["retrieved_contexts"],
                reference=row["reference"],
            )
            value = getattr(result, "value", result)
            return float(value)

    output: dict[str, Any] = {}
    for strategy, records in records_by_strategy.items():
        answerable_records = [row for row in records if row["answerable"]]
        strategy_scores: dict[str, float] = {}
        for metric_name, metric in metrics.items():
            scores = await asyncio.gather(*(score_one(metric_name, metric, row) for row in answerable_records))
            strategy_scores[metric_name] = round(sum(scores) / max(1, len(scores)), 4)
        strategy_scores["ragas_samples_scored"] = len(answerable_records)
        strategy_scores["unanswerable_tasks"] = sum(not row["answerable"] for row in records)
        strategy_scores["abstention_accuracy"] = calculate_offline_metrics(records)["unanswerable_abstention_accuracy"]
        strategy_scores["false_abstention_rate"] = calculate_offline_metrics(records)["answerable_false_abstention_rate"]
        strategy_scores["avg_latency_ms"] = calculate_offline_metrics(records)["avg_latency_ms"]
        strategy_scores["avg_estimated_cost_usd"] = calculate_offline_metrics(records)["avg_estimated_cost_usd"]
        output[strategy] = strategy_scores
    await client.close()
    return output


async def async_main(args: argparse.Namespace) -> int:
    questions = load_questions(args.limit)
    settings = Settings()
    store, database, service, _ = _build_runtime(settings)
    try:
        ensure = getattr(store, "ensure_collection", None)
        if ensure is not None:
            await ensure()
        results: dict[str, Any] = {}
        records_by_strategy: dict[str, list[dict[str, Any]]] = {}
        for strategy in STRATEGIES:
            print(f"Running {strategy} over {len(questions)} questions ...", flush=True)
            records = await run_strategy(strategy, questions, service)
            records_by_strategy[strategy] = records
            results[strategy] = calculate_offline_metrics(records)
        if args.offline:
            report = {
                "evaluation_kind": "offline_retrieval_and_abstention_smoke_test",
                "note": "These are deterministic operational metrics, not RAGAS quality scores.",
                "question_count": len(questions),
                "strategies": results,
                "records": records_by_strategy,
            }
        else:
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                raise RuntimeError("OPENAI_API_KEY is required for the RAGAS judge; use --offline for key-free checks")
            ragas_scores = await run_ragas_metrics(records_by_strategy, api_key)
            report = {
                "evaluation_kind": "ragas_llm_judge",
                "judge_model": "gpt-4o-mini",
                "question_count": len(questions),
                "ragas_metrics": ragas_scores,
                "operational_metrics": results,
                "records": records_by_strategy,
            }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(json.dumps(report.get("ragas_metrics", report["strategies"]), indent=2))
        print(f"Report written to {args.output.resolve()}")
        return 0
    finally:
        await store.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None, help="Run only the first N questions")
    parser.add_argument("--offline", action="store_true", help="Skip paid RAGAS judge and report smoke metrics only")
    parser.add_argument("--output", type=Path, default=Path("./results/ragas_comparison.json"))
    args = parser.parse_args()
    return asyncio.run(async_main(args))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError) as error:
        print(f"Evaluation failed: {error}", file=sys.stderr)
        raise SystemExit(2) from error
