# M2.2 — Schema Agent + Query Decomposition Agent

The upstream agents. They produce the context the Computation Agent (M2.3) needs to generate pandas code.

## What's in this milestone

```
src/finsheet/agents/
├── __init__.py             # public API
├── types.py                # SchemaCard, QueryPlan, Subgoal, AgentResult (Pydantic)
├── schema.py               # SchemaAgent — deterministic, no LLM
└── decomposition.py        # DecompositionAgent — Gemini 2.5 Pro + controlled gen

scripts/
└── run_agent_plan.py       # demo: file + question → schema + plan

tests/test_agents.py        # 14 tests, mocked Gemini client
```

## The two agents

### Schema Agent — deterministic

Wraps the MCP `get_sheet_schema` tool, converts the result into a typed `SchemaCard`, and derives plain-English `structural_notes` for downstream agents. **No LLM call.** Tests run without any GCP access.

What the `SchemaCard` carries:
- `data_range`: Excel range string (e.g. `"A4:N179"`) — pass this verbatim to the Computation Agent's `execute_python` call
- `columns`: canonical names (multi-line headers collapsed) + dtypes + column letters
- `fund_layout`: `"column"` | `"row_separator"` | `"unknown"`
- `funds`: per-fund row spans + company counts
- `average_rows`: row indices to exclude from aggregations
- `structural_notes`: plain-English warnings about quirks (multi-line headers, row-separator layout, average rows present)

### Decomposition Agent — Gemini 2.5 Pro, controlled generation

Takes a question + a SchemaCard and outputs a `QueryPlan`: an ordered list of subgoals + expected answer type + expected output shape. **Uses `response_schema=QueryPlan`** so JSON output is reliable — no regex fallback, no "respond in JSON format" prompt instruction.

The plan contains:
- `interpretation`: one-sentence restatement of the question
- `needed_columns`: subset of the schema's columns this query touches
- `expected_answer_type`: one of `numeric|string|list|dict|bool|date` (matches the verifier's type system)
- `subgoals`: ordered pandas operations (filter, groupby, aggregate, sort, count, lookup, compute, transform, other)
- `notes`: assumptions + caveats for downstream agents

## Demo it

Single command, takes a file and a question, prints schema + plan:

```powershell
uv run python scripts/run_agent_plan.py `
    bench/data/files/synthetic4_A.xlsx `
    "What is the total unrealized capital per fund?"
```

Expected output (excerpt):

```
=== Schema Agent (deterministic) on bench/data/files/synthetic4_A.xlsx ===
  sheet: Portfolio  (179 rows × 13 cols)
  data_range: A4:M179
  fund_layout: row_separator
  columns (13):
    A: Company (string)
    B: Sector (string)
    ...
    G: Entry Enterprise Value (number)
    ...
  funds (8):
    Fund I: rows 6-22 (15 cos)
    Fund II: rows 24-48 (23 cos)
    ...
  structural_notes:
    - Column names may be collapsed multi-line headers (e.g. 'Entry Enterprise Value', ...). Use them EXACTLY as listed; do not abbreviate.
    - Fund layout is 'row_separator': ... use the fund_boundaries to slice.
    - There are 8 average/summary rows ... Exclude them by filtering ...

=== Decomposition Agent (gemini-2.5-pro) ===
  Question: What is the total unrealized capital per fund?
  Interpretation: Sum the Entry Enterprise Value of every Unrealized company, grouped by fund.
  Expected answer type: dict
  Expected output shape: One numeric entry per fund (8 funds in this workbook).
  Needed columns: ['Status', 'Entry Enterprise Value']
  Subgoals:
    1. [filter] Filter to Unrealized companies
       pandas hint: df[df['Status']=='Unrealized']
    2. [groupby] Group rows by fund using fund_boundaries (no Fund column)
    3. [aggregate] Sum Entry Enterprise Value per fund

  Tokens in:  ~1200
  Tokens out: ~200
  Cost:       ~$0.0035
```

That plan is what M2.3's Computation Agent will consume to generate the actual pandas code, call `execute_python`, and capture the result in the Fact Sheet.

## Why these design choices

**Schema Agent is deterministic.** The MCP `get_sheet_schema` tool already returns structurally rich, machine-readable output (column dtypes, fund boundaries, average row indices). Wrapping it in an LLM call adds a hallucination surface and a token bill for zero new information. The Schema Agent contributes value by computing the `data_range` string and writing plain-English structural notes that the Decomposition Agent's prompt can include — both deterministic transformations.

This makes the Schema Agent's tests run in milliseconds with no network and no GCP. Important for CI and for fast iteration.

**Decomposition Agent uses controlled generation.** Earlier in M1.3 we saw 36 empty responses out of 528 — Gemini occasionally returns nothing on long structured prompts. With `response_schema=QueryPlan`, the model is forced to emit JSON matching our Pydantic model or fail loudly. Parse errors become rare and we can surface them through the AgentResult.error field instead of silently returning a broken plan.

**Pydantic models double as wire types AND response schemas.** Single source of truth — when we change a field in `types.py`, the response schema updates automatically, the JSON validation updates automatically, and the test mocks stay aligned.

## Tests

```powershell
uv run python -m pytest tests/test_agents.py -q
```

Covers:
- SchemaAgent against all 3 layout variants (column, row_separator, mixed)
- SchemaCard JSON round-trip
- Structural notes for each layout
- DecompositionAgent happy path with mocked Gemini
- Bad JSON from the LLM → AgentResult.error populated
- Partial QueryPlan from the LLM → Pydantic rejects, caller sees error
- Client exceptions propagate cleanly (no crash, populated error)
- All 7 question categories from M1.2 produce valid plans

14 tests, ~14 seconds, no GCP required.

## What's next — M2.3 (Computation Agent + Fact Sheet)

The Computation Agent takes a QueryPlan + SchemaCard and:
1. For each subgoal, generates pandas code (Gemini 2.5 Pro)
2. Calls `execute_python` via MCP with `named_ranges={"df": {"sheet": ..., "range": card.data_range}}`
3. Captures the returned value into a Fact Sheet (key-value dict of computed values + their cell-citation provenance)
4. **Never emits numbers that didn't come from a tool call** — the architectural commitment from CoDaS

After M2.3 we'll have the core CoDaS-pattern loop working end-to-end on real questions.
