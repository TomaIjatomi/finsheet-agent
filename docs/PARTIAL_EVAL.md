# Partial Eval — running the agent stack against the bench

The agent stack (M2.2 + M2.3) is now wired into the same baseline runner that produced the M1.3 (94.3%) and M1.4 (69.1%) reports. Same bench, same verifier, same JSONL/markdown report format — you can compare any-to-any.

## Sequence

```powershell
# Make sure the Docker sandbox image is built
uv run python scripts/build_sandbox_image.py

# Save your existing baseline reports if you haven't (so they don't get overwritten)
mkdir docs\history -Force
copy docs\eval-report.md docs\history\eval-report-baseline-fullcontext-2.5pro-final.md -ErrorAction SilentlyContinue
```

## Step 1 — Smoke test (1 question, ~$0.02, ~10s)

```powershell
uv run python scripts/run_baseline.py `
    --strategy agent `
    --files synthetic1_A `
    --limit 1
```

If the printed accuracy is 100% (1/1) and no errors, the wiring is good. If it errors out on `Docker not found`, build the sandbox image first (see SETUP).

## Step 2 — Hard tier (22 questions, ~$0.50, ~10-15 min)

The official FinSheet-Bench hard-tier file. Most architecturally informative — exercises row_separator layout, the prelude's Fund injection, the per-fund aggregation that M1.3 scored 18% on (Q11), and the questions M1.3 scored 67% on (Q16) and 42% on (Q5).

```powershell
uv run python scripts/run_baseline.py `
    --strategy agent `
    --files synthetic4_A `
    --concurrency 3
```

**Lower the concurrency to 3** — each question fans out to ~3-4 Gemini calls (decomp + per-subgoal codegen), so at concurrency=5 you have 15-20 in-flight calls. Concurrency=3 keeps you well below quota and avoids 429s.

Expected outcome: ~85-95% on this single file (vs M1.3's 81.8% on the same file). The full answer category breakdown in the auto-generated `docs/eval-report.md` will show where the architecture wins (Q5 sort, Q16 median, Q11 per-fund aggregation should all jump from M1.3's numbers).

## Step 3 — Stratified small eval (66 questions, ~$1.50, ~30-40 min)

Three representative files: easy (synthetic1_A), hard (synthetic4_A), most-funds (synthetic8_A).

```powershell
uv run python scripts/run_baseline.py `
    --strategy agent `
    --files synthetic1_A,synthetic4_A,synthetic8_A `
    --concurrency 3
```

This is the **right partial eval before committing to the full run**. It covers:
- The easiest file (where M1.3 scored 100% — we should match)
- The hardest file (M1.3: 81.8% — we should improve)
- The most-funds file (M1.3: 36% on synthetic8_A — biggest potential lift)

If overall is ≥90% across all three, the full 528-question run is justified.

## Step 4 — Full eval (528 questions, ~$8-15, ~3-5 hours)

Only do this once Step 3 looks strong.

```powershell
uv run python scripts/run_baseline.py `
    --strategy agent `
    --concurrency 3
```

Resumable: same as the baselines. Ctrl-C is fine; re-running picks up where it left off.

## Comparing reports

After each run, save the report so you have the cumulative comparison:

```powershell
copy docs\eval-report.md docs\history\eval-report-agent-2.5pro-partial.md
copy docs\eval-report.json docs\history\eval-report-agent-2.5pro-partial.json
```

To compare overall accuracy at a glance:

```powershell
Get-Content docs\history\eval-report-baseline-fullcontext-2.5pro-final.json | jq .overall
Get-Content docs\history\eval-report-baseline-naive-rag-2.5pro-final.json | jq .overall
Get-Content docs\history\eval-report-agent-2.5pro-partial.json | jq .overall
```

Per-template comparison (where the architecture should genuinely win):

```powershell
Get-Content docs\history\eval-report-baseline-fullcontext-2.5pro-final.json | jq '.by_template | to_entries | map({q: .key, acc: .value.accuracy})'
Get-Content docs\history\eval-report-agent-2.5pro-partial.json | jq '.by_template | to_entries | map({q: .key, acc: .value.accuracy})'
```

Look at Q5, Q11, Q16 specifically — these are the three M1.3 weak spots the architecture was designed to fix.

## What "good" looks like

| Bucket | M1.3 baseline | Naive RAG | Agent target |
|---|---|---|---|
| Overall | 94.3% | 69.1% | ≥95% |
| synthetic4_A (hard tier) | 81.8% | 45.5% | ≥90% |
| Q5 (sort) | 42% | 46% | ≥90% |
| Q11 (per-fund agg) | 38% | 38% | ≥90% |
| Q16 (median) | 67% | 46% | ≥90% |

If the agent stack hits these targets on the partial eval, the architecture pitch is **substantively true** and the demo writes itself. If it doesn't, the per-template breakdown tells you exactly which subgoals are misbehaving and the Fact Sheet entries (in the results JSONL) give you the failing code for diagnosis.

## Troubleshooting

- **429 errors mid-run**: lower `--concurrency` to 2. Each question fans out into multiple Gemini calls.
- **Docker not found**: ensure Docker Desktop is running before starting the eval.
- **Resume after Ctrl-C**: just rerun the same command. Already-completed questions are skipped.
- **Tier-0 errors (solver error)**: open the results JSONL, grep for `"error"`, look at the failure pattern. Usually a single failing question, not a systemic issue. The retry loop (max_retries=2) handles transient sandbox errors; persistent ones get surfaced.

## What comes after

If the partial eval comes back strong (≥90% overall, clear wins on Q5/Q11/Q16), the next milestone is **M2.4 — Verification Agent** to push accuracy further on remaining failure modes, then the full run for the official agent stack number.

If it underperforms, the per-template breakdown will tell us which subgoal patterns the Decomposition Agent is mishandling — that's a prompt-tuning conversation, not a re-architecture.
