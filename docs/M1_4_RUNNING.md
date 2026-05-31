# M1.4 — Naive RAG Baseline

The second baseline. Establishes that the obvious "chunk + retrieve" pattern an FDE customer might reach for *underperforms* full-context substantially on spreadsheet QA. Together with M1.3, this gives you two pragmatic comparison points for the M2 agentic architecture.

## Prerequisites

- M1.3 done: `bench/data/results/baseline_fullcontext.jsonl` exists, you have a final eval-report
- M1.1 stack still wired (GCP, Vertex AI, .env populated)

## What this does

For each of 528 questions:
1. **First time a file is seen**: serialize → split into 10-row chunks (header included in every chunk) → embed all chunks via `text-embedding-005` in one batch call → cache embeddings keyed by file path
2. **Per question**: embed the question text → retrieve top-K=5 chunks by cosine similarity → prompt Gemini 2.5 Pro with the retrieved subset (NOT full file)
3. Score the response through the same 3-tier cascading verifier as M1.3
4. Write one JSONL record per question to `bench/data/results/baseline_naive_rag.jsonl`
5. Aggregate and write `docs/eval-report.md` (overwrites M1.3's; save the M1.3 version first if you haven't)

The naive part: no spreadsheet-aware chunking, no query rewriting, no reranking, no metadata filtering. Just the "first thing you'd build" RAG.

## Save your M1.3 report first

Before running M1.4, snapshot the M1.3 report so it isn't overwritten:

```powershell
# If not already saved during M1.3
mkdir docs\history -Force
copy docs\eval-report.md   docs\history\eval-report-baseline-fullcontext-2.5pro-final.md
copy docs\eval-report.json docs\history\eval-report-baseline-fullcontext-2.5pro-final.json
```

## Dry run first

```powershell
uv run python scripts/run_baseline.py --strategy naive_rag --limit 10
```

Expected: ~45 seconds, ~$0.05–0.10, prints summary at the end. Most cost is the LLM, not embeddings.

## Full run

```powershell
uv run python scripts/run_baseline.py --strategy naive_rag
```

Expected output during run:
```
Running baseline: naive_rag_gemini_2.5_pro  [strategy=naive_rag]
  Bench: bench/data
  Results: bench/data/results/baseline_naive_rag.jsonl
  Concurrency: 5

Total questions: 528
Already complete: 0
To process: 528

baseline:  ...
```

Wall-clock: 10-15 minutes. Faster than M1.3 because retrieved context is much smaller than full-context, so each LLM call is quicker.

Cost: ~$1.50–2.50 total ($0.01–0.05 embeddings, ~$1.50–2 chat).

Resumable: same as M1.3 — Ctrl-C and re-run; completed qids are skipped.

## What "good" looks like

The defining property of this baseline is that it should be *worse* than M1.3.

| Bucket | Naive RAG target | M1.3 reference |
|---|---|---|
| Overall | 40–55% | 94.3% |
| Hard tier (synthetic4_A) | 25–40% | ~82% |
| Easy (synthetic1_A) | 55–75% | 100% |

By category, expect:
- **Counting / Aggregation / Sorting**: near-zero (top-K can't supply all rows)
- **Simple Lookup** (Q9, Q10): moderate (embedding similarity finds the right chunk most of the time)
- **List Extraction** (Q4): low (the full company list rarely fits in 5 chunks)
- **Complex Aggregation** (Q16): near-zero

If numbers land *above* this range (e.g., overall > 65%) something is suspicious — either the chunking is accidentally including too much, or top-K is large enough to cover most files. Look at chunks per file via the manifest.

If numbers land *below* this range (overall < 30%) — also worth investigating. Check that the headers are actually showing up in retrieved chunks (test by looking at a few raw_response failures).

## Inspecting the result

```powershell
# Markdown report
cat docs\eval-report.md

# Compare to M1.3 side-by-side
cat docs\history\eval-report-baseline-fullcontext-2.5pro-final.json | jq .overall
cat docs\eval-report.json | jq .overall
```

The two reports use the same structure, so per-category and per-template comparisons are direct.

## After M1.4 — save the report

Like M1.3:

```powershell
copy docs\eval-report.md   docs\history\eval-report-baseline-naive-rag-2.5pro-final.md
copy docs\eval-report.json docs\history\eval-report-baseline-naive-rag-2.5pro-final.json
git add -A
git commit -m "M1.4 final: naive RAG baseline (Gemini 2.5 Pro + text-embedding-005)"
```

## What this enables for M2

You now have two baseline numbers and a clear failure-mode story:

| Strategy | Overall | Hard tier | Q5 (sort) | Q16 (median) |
|---|---|---|---|---|
| Full-context Gemini 2.5 Pro | 94% | 82% | 42% | 67% |
| Naive RAG Gemini 2.5 Pro | ~45% | ~30% | near-zero | near-zero |
| **Target — multi-agent (M2)** | **>95%** | **>92%** | **>90%** | **>95%** |

The two-baseline story makes the M2 pitch concrete: *"the obvious budget-conscious alternative (RAG) collapses, the obvious accuracy-maximizing alternative (full-context) leaves real headroom on hard tier and on enumeration/aggregation. The agentic architecture is the principled answer to both."*

## Common issues

| Issue | Fix |
|---|---|
| `ImportError: text-embedding-005` not found | Check Vertex AI model garden access; some regions require enabling embedding models separately |
| Very slow first-file (>30s for embedding) | Network latency to Vertex; subsequent files reuse the connection |
| Embedding cost shows $0.00 | Token estimate is `len/4`; tiny content rounds to zero. Real billing will show actual cost on GCP |
| Run cost lower than M1.3 by a lot | Expected — retrieved context is ~10-15% the size of full-context |

## Move to M2.1 next

You now have everything you need to start the architecture work — both baselines committed, both reports saved, the runner and verifier proven. M2.1 (Spreadsheet MCP server with Docker-per-execution) is the next coding block.
