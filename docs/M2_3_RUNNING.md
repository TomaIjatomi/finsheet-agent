# M2.3 — Computation Agent + Fact Sheet

The core architectural loop. Takes a QueryPlan + SchemaCard from M2.2 and produces a typed answer via sandboxed code execution. **No number is emitted that didn't come from a tool call.**

## What's in this milestone

```
src/finsheet/agents/
├── computation.py          # ComputationAgent — codegen + execution + retry loop
├── prelude.py              # deterministic Python preamble (Fund column + filter)
└── types.py                # +FactSheet, FactSheetEntry, ComputationResult

scripts/
└── run_question_end_to_end.py  # full pipeline demo on real Gemini + Docker

tests/test_computation.py   # 20 tests; uses LocalSandbox so no Docker required for CI
```

## The architectural commitment

**Every value in the final answer comes from a sandbox call.** The LLM never invents numbers — it generates pandas code; the sandbox executes the code; the result lands in a `FactSheetEntry` alongside the exact code that produced it. The Fact Sheet is the audit trail.

This is the CoDaS-pattern transfer to spreadsheet QA. It's also why the architecture should beat the M1.3 baseline on Q5 sort (42% → ~95%) and Q16 median (67% → ~95%): deterministic computation eliminates the LLM-arithmetic failure mode.

## The deterministic prelude — a small but consequential design choice

Every sandbox call gets a deterministic Python preamble *before* the LLM-generated subgoal code runs. The prelude is built by the orchestrator from the SchemaCard and handles two universal concerns:

1. **For `fund_layout == "row_separator"`**: injects a `Fund` column into df using the SchemaCard's `fund_boundaries`. After this, the LLM can always call `df.groupby('Fund')` regardless of the file's layout.
2. **Removes non-company rows**: fund dividers (rows where only the Company column has a fund name) and average rows ("Fund X Average"). Uses a threshold on populated-column count, which works across all 8 templates.

After the prelude, the LLM-generated code can rely on these invariants:
- `df['Fund']` is always populated for every row
- `df` contains only portfolio companies
- `df.index` is `0..N-1` (reset)

This reduces the LLM's job from "understand the structural variant + generate code" to just "generate code for a clean df". Significantly lower surface area for failure.

## The execution loop

For each subgoal in the plan:

1. **Codegen call** (Gemini 2.5 Pro, no controlled-output since we want raw Python). The system prompt explains the invariants from the prelude; the user prompt includes the schema, the full plan, the Fact Sheet so far, and the specific subgoal to execute.
2. **Strip markdown fences** if the model wrapped its code in ` ```python ... ``` ` despite our prompt.
3. **Prepend the prelude**, send to `execute_python` via MCP with `named_ranges={"df": {"sheet": ..., "range": card.data_range}}`.
4. **On error**: retry with the failing code + error message fed back via the fix prompt. Default `max_retries=2`. The `n_retries` count surfaces in the result for cost/observability.
5. **On persistent failure**: stop early (subsequent subgoals can't continue without earlier results) and surface `error="one or more subgoals failed"`.

After all subgoals run, `_format_final_answer()` casts the last entry's value into the shape expected by the verifier — float for `numeric`, list for `list`, dict for `dict`, etc. Handles common LLM quirks (single-key dict when a number was asked for, dict values flattened to list, etc.).

## Demo it

The end-to-end script runs the full pipeline (schema → plan → computation) on a real Gemini call and the Docker sandbox you verified in M2.1:

```powershell
# Build sandbox image once (M2.1 step — skip if already done)
uv run python scripts/build_sandbox_image.py

# Full pipeline, real question
uv run python scripts/run_question_end_to_end.py `
    bench/data/files/synthetic4_A.xlsx `
    "What is the total unrealized capital per fund?"
```

Expected output structure:

```
=== [1/3] Schema Agent ===
  fund_layout: row_separator  (8 funds, 152 companies)

=== [2/3] Decomposition Agent (gemini-2.5-pro) ===
  expected_answer_type: dict
  subgoals (2):
    1. [filter] Filter to Unrealized companies
    2. [aggregate] Group by Fund, sum Entry Enterprise Value

=== [3/3] Computation Agent (gemini-2.5-pro, sandbox=docker) ===
  subgoals attempted: 2, succeeded: 2, retries: 0
  Fact Sheet:
    ✓ Step 1: Filter to Unrealized companies
        value: [{'Company': 'Apex Holdings', ...}, ...]  (52 rows kept)
    ✓ Step 2: Group by Fund, sum Entry Enterprise Value
        value: {'Fund I': 4231.5, 'Fund II': 3870.2, ...}

  computation cost: $0.012
  computation latency: 8230 ms

=== FINAL ANSWER (dict) ===
  {'Fund I': 4231.5, 'Fund II': 3870.2, 'Fund III': ...}

Total cost this question: $0.018
```

Per-question economics: ~$0.015–0.025 (decomp + 2–4 codegen calls). Latency ~6–12s. Compare to full-context baseline ($0.008/q, ~2s) — agentic is slower and more expensive per question but should land 5–15pp higher accuracy on the hard categories.

## Fast iteration mode (no Docker)

For prompt tuning, skip Docker and use LocalSandbox:

```powershell
uv run python scripts/run_question_end_to_end.py `
    bench/data/files/synthetic4_A.xlsx `
    "What is the median net debt/EBITDA ratio across all investments?" `
    --sandbox=local_unsafe
```

LocalSandbox runs the code in your Python process — fast (no container start), unsafe (no isolation), suitable only for development against your own prompts. Never enable this for the final eval.

## Try the four M1.3 weak spots

These are the questions M1.3's full-context baseline struggled with:

```powershell
# Q11-style: per-fund aggregation (M1.3: 37%)
uv run python scripts/run_question_end_to_end.py `
    bench/data/files/synthetic4_A.xlsx `
    "What is the total unrealized capital per fund?"

# Q5-style: sort all companies (M1.3: 42%)
uv run python scripts/run_question_end_to_end.py `
    bench/data/files/synthetic4_A.xlsx `
    "List all companies sorted by entry EBITDA from highest to lowest."

# Q16-style: median across all investments (M1.3: 67%)
uv run python scripts/run_question_end_to_end.py `
    bench/data/files/synthetic4_A.xlsx `
    "What is the median net debt/EBITDA across all investments?"

# Q10-style: simple lookup (sanity check — M1.3: 97%, should still be ~100%)
uv run python scripts/run_question_end_to_end.py `
    bench/data/files/synthetic1_A.xlsx `
    "What is the entry enterprise value of Apex Holdings?"
```

If all four return correct answers, the architecture is genuinely working. The next step is M2.4 (Verification Agent) and then M3 — running the full 528-question eval to produce the architecture's official accuracy number.

## Tests

```powershell
uv run python -m pytest tests/test_computation.py -q
```

20 tests, ~70 seconds. Covers:
- Prelude correctness for both `column` and `row_separator` layouts
- Prelude executes cleanly against real bench files (the integration test that caught the divider-row bug)
- Final-answer formatting across all 6 answer types
- End-to-end agent loop with LocalSandbox + mocked Gemini
- Retry-on-failure behaviour (1 bad code → 1 good code = success with n_retries=1)
- Give-up after max retries (3 bad codes = surfaces error)
- Markdown fence stripping
- Codegen exception propagation (simulated GCP outage)
- Fact Sheet carries intermediate values into subsequent prompts

Tests don't require GCP — Gemini is mocked. They DO use LocalSandbox for real pandas execution, so the prelude integration is genuinely verified end-to-end on the real bench files.

## What's next — M2.4 (Verification Agent)

The Verification Agent will:
1. Independently read source cells via `get_range`
2. Sanity-check the Computation Agent's outputs (arithmetic drift, sign errors, scale mismatches)
3. Flag suspicious results for revision

In practice, M2.4 is most useful when the Computation Agent's confidence is low or when the expected output is a single critical number. After M2.4, we run the full eval and the architecture has produced its first real accuracy number on the same 528-question bench.
