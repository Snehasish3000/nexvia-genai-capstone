# GENAI-01 Evaluation

`questions.json` is a deterministic 60-question benchmark: 40 factual questions, 8 multi-hop questions and 12 SQuAD-style unanswerable questions. Each row contains the expected answer, user role, question category and expected source. Regenerate it with:

```bash
python generate_dataset.py
```

Run the no-key smoke benchmark against a populated local Qdrant collection (or `VECTOR_STORE=memory` for a process-local experiment):

```bash
cd ../backend
pip install -r requirements.txt
uvicorn app.main:app --reload
# in a second terminal from GENAI-01/evaluation:
python run_ragas.py --offline
```

For actual RAGAS scoring, set `OPENAI_API_KEY`, `LLM_PROVIDER=openai`, and (for dense embeddings) `EMBEDDING_PROVIDER=openai`, then run `python run_ragas.py`. The runner evaluates Dense-only, Hybrid (BM25 + RRF), and Hybrid + Cohere Rerank separately. It uses the current collections metric interface for `faithfulness`, `answer_relevancy`, `context_precision`, and `context_recall`; LangSmith tracing is enabled by `LANGSMITH_TRACING=true` and `LANGSMITH_API_KEY`.

Unanswerable questions remain in the benchmark and contribute to operational abstention-accuracy reporting. LLM-judge RAGAS averages are computed on answerable tasks with retrieved evidence because evidence-free abstention samples cannot provide a meaningful context-precision/recall comparison. The output is JSON with per-strategy RAGAS metrics, operational metrics and individual records. No benchmark score is pre-populated or represented as measured before execution.
