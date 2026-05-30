# M1.3 — Full-context Gemini 3.1 Pro Baseline

Reproduces FinSheet-Bench's published baseline (Gemini 3.1 Pro, whole spreadsheet in context). Run this once before moving to M1.4 (naive RAG) and M2 (multi-agent). The output — `docs/eval-report.md` — becomes the comparison anchor for everything that follows.

## Prerequisites

- M1.1 done: smoke test passing
- M1.2 done: `bench/data/files/` and `bench/data/ground_truth.jsonl` exist
- Billing alert set up on the GCP project — full run costs ~$15–$25 in Gemini Pro tokens

## What this does

For each of 528 questions across 24 xlsx files:
1. Serializes the xlsx to pipe-separated text (whole file, no chunking)
2. Builds a prompt with the system instruction + the spreadsheet + the question + a format hint per answer type
3. Calls Gemini 3.1 Pro Preview via Vertex AI
4. Parses the response through the 3-tier cascading verifier (`bench/verifier.py`)
5. Writes one JSONL record per question to `bench/data/results/baseline_fullcontext.jsonl` (incremental — resumable on crash)
6. Aggregates and writes `docs/eval-report.md` + a sibling `eval-report.json`

## Dry run first (recommended)

Before the full run, do a 10-question sanity check to confirm everything wires up:

```bash
uv run python scripts/run_baseline.py --limit 10
```

Expected: ~30 seconds, ~$0.10–$0.20, prints summary at the end.

If this works, do the full run. If it errors, see "Common errors" below.

## Full run

```bash
uv run python scripts/run_baseline.py
```

Expected output during run:
```
Running baseline: fullcontext_gemini_3.1_pro
  Bench: bench/data
  Results: bench/data/results/baseline_fullcontext.jsonl
  Concurrency: 10

Total questions: 528
Already complete: 0
To process: 528

baseline:  47%|███▎    | 248/528 [11:22<13:34, ...]
```

Wall-clock: typically 25–45 minutes depending on Gemini 3.1 Pro Preview latency that day (it varies). Concurrency=10 is conservative for preview quota; if you've upgraded quota you can push to 20 with `--concurrency 20`.

If the run is interrupted (Ctrl-C, network drop, GCP 429s), just re-run the same command. The runner reads existing results and skips qids that already have a response — no work is lost.

## What "good" looks like

Default model is now **Gemini 2.5 Pro (GA)**. Target numbers shift accordingly:

| Bucket | 2.5 Pro target | Acceptable range | (3.1 Pro paper target for reference) |
|---|---|---|---|
| Overall accuracy | ~75% | 70–80% | 82.4% |
| Hard tier (synthetic4_A) | ~40% | 35–50% | ~48.6% (avg across all models) |
| Easy (synthetic1_A) | ~85% | 80–92% | ~86%+ |

If you want direct paper comparison, switch to `GEMINI_PRO_MODEL=gemini-3.1-pro-preview` in `.env` — *but* you'll need to request quota increase from GCP first (preview models default to tight per-project limits; a 528-question run at concurrency≥5 will hit 429 RESOURCE_EXHAUSTED otherwise).

**If you land outside these ranges:**
- *Way above* (e.g., 95% overall) → suspicious. Check: is the verifier accepting too liberally? Is the synthetic bench too easy? Sample 10 random failures from the report and verify them manually.
- *Way below* (e.g., 50% overall) → also suspicious. Check: is the prompt malformed? Are answer-type format hints clear? Are responses being parsed wrong by the verifier? Look at the failure samples in the report.

These deltas are the substance of the project — explaining *why* the numbers landed where they did is the demo content.

## Inspecting the result

```bash
# Read the markdown report (renders in any editor / GitHub)
cat docs/eval-report.md

# Or the structured JSON for tooling
cat docs/eval-report.json | jq .overall
```

Sections of the report:
- **Headline** — overall + hard-tier accuracy, with delta vs paper
- **Cost & latency** — actual GCP cost for this run
- **Accuracy by complexity** — Low / Medium / High / Very High split (matches paper)
- **Accuracy by category** — the 7 question categories
- **Accuracy by file** — per-file accuracy with `synthetic4_A` flagged as the hardest
- **Accuracy by question template** — Q1–Q16 individually (reveals which question types are weakest)
- **Verifier tier distribution** — how many answers resolved at Tier 1 (exact), Tier 2 (fuzzy), or Tier 3 (LLM-judge / unresolved). High Tier-3 counts mean the response format is messier than expected.
- **Failure samples** — 10 random wrong answers with the actual response and verdict reason. This is the analytical substance — read these carefully.

## Regenerate the report without re-running

If you change the verifier or want to re-aggregate without burning more API budget:

```bash
uv run python scripts/run_baseline.py --report-only
```

This reads `bench/data/results/baseline_fullcontext.jsonl` and re-renders the report. Free, instant.

## Common errors

| Error | Fix |
|---|---|
| `403 Permission denied` on Vertex | Re-run `gcloud services enable aiplatform.googleapis.com` |
| `404 Model not found` | `.env` has wrong `GEMINI_PRO_MODEL` or `GCP_REGION` not `global` |
| `429 Resource exhausted` (occasional) | Auto-retried with exponential backoff. Usually transient. |
| `429 Resource exhausted` (every call) | Hit preview quota. Request increase, or reduce `--concurrency` to 3–5. |
| `RESOURCE_EXHAUSTED` for tokens | Spreadsheet too big for context window. Shouldn't happen with our largest file (synthetic4_A is ~10K tokens, well under Gemini 3.1 Pro's window). If it does, file an issue. |
| Run hangs without progress | Network issue. Ctrl-C and re-run — resumable. |
| Cost climbs faster than expected | Check `--limit` wasn't accidentally removed. Each Pro call is ~$0.02–$0.04. |

## Tips

- **Run it once in the morning** so you can react to any quota issues during business hours
- **Save the eval-report.json** — it's the historical record. Each baseline run should produce a dated snapshot in `docs/history/eval-report-YYYY-MM-DD.json` for the project journal
- **Read the failure samples**. Pattern-match across 10 failures: are they all in one category? One file? One question template? That tells you where the architecture needs to earn its keep

## What this enables

Once M1.3 is done you have:
- A reproducible baseline number on your own machine
- An honest comparison anchor for everything you build next
- A reusable runner (`run_baseline.py`) that M1.4 (RAG) and M2 (agents) plug into via the `Solver` protocol — no rewrite needed

Move on to **M1.4 (naive RAG baseline)** next: same runner, swap in a `NaiveRagSolver` that chunks the spreadsheet, retrieves the top-K rows, and prompts Gemini Pro with just that subset. Goal: establish a second baseline that's the production pattern an FDE customer would otherwise reach for. Expected to land lower than full-context (45–60% overall) because spreadsheet structure doesn't chunk cleanly.
