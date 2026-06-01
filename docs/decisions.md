# Architectural Decisions

A running log. Append as decisions are made; never rewrite past entries.

---

## D1 — Two source papers, explicit attribution

**Decision:** The architecture inherits the **CoDaS pattern** (Kim et al., Google Research + DeepMind, April 2026) and targets the problem identified by **FinSheet-Bench** (Ravnik et al., Qubera AG + UZH, March 2026). Both papers are cited prominently in README and decisions docs.

**Why:** Honest framing. The "deterministic-runner + LLM-interpreter" pattern is CoDaS's contribution, not ours. We transfer it to a different task (closed-form numerical QA over spreadsheets) and add an architectural feature (MCP-served deterministic tool surface) — but the orchestration topology is theirs. Pretending otherwise would be detected in any sophisticated technical review.

**Alternative considered:** Frame as a novel architecture without CoDaS attribution. Rejected — easily falsifiable, weaker pitch.

---

## D2 — Build synthetic bench independently rather than wait for Qubera

**Decision:** Construct a representative synthetic bench from FinSheet-Bench's published Section 3.1 methodology. Email Qubera as a parallel track but don't block on it.

**Why:** No public release of FinSheet-Bench. Contact is a generic info address (commercial signal). The methodology is fully documented, so independent reproduction is possible and is itself a contribution. This also gives us control over the difficulty distribution.

**Trade-off:** Numbers won't be directly comparable to the published 82.4% / 48.6%. We mitigate by mirroring the structural complexity (synthetic4 = 152 companies × 8 funds) so the difficulty signal is preserved.

---

## D3 — Stack: Gemini 3.1 Pro + Gemini Flash, ADK orchestration, MCP tool surface

**Decision:** Gemini 3.1 Pro Preview for reasoning agents (Orchestrator, Query Decomposition, Computation, Verification); Gemini Flash for lightweight agents (Schema, Synthesis). ADK Python 2.0 for orchestration. MCP server for spreadsheet + sandboxed Python.

**Why:**
- 3.1 Pro is FinSheet-Bench's best-published model (82.4%) and CoDaS's reasoning tier — matching makes baselines comparable.
- Flash tier matches CoDaS's pattern and saves cost on agents that don't need Pro-level reasoning.
- ADK is Google's official agent framework, hits the FDE JD signal directly.
- MCP is the architectural differentiation from CoDaS — CoDaS uses embedded subprocesses; we make the deterministic surface portable.

**Alternative considered:** LangGraph instead of ADK. Held as Week-1 fallback if ADK maturity gaps surface at M1.3.

---

## D4 — Global endpoint for Gemini 3.1 Pro

**Decision:** Set `GCP_REGION=global` in `.env`. Do not use `us-central1` or `europe-west4`.

**Why:** As of May 2026, Gemini 3.1 Pro Preview only runs on Vertex AI's global endpoint. Using a regional endpoint produces a confusing 404 "model not found" that wastes ~hour of debugging.

---

## D5 — Sandboxed code execution for the Computation Agent

**Original decision (Week 1 draft):** restricted-subprocess with `resource.setrlimit()` caps and a read-only mount.

**Superseded by D12** (Docker-per-execution). The principle is the same — the Computation Agent runs LLM-generated code in an isolated, read-only, resource-capped environment with no network — but the implementation is Docker containers rather than POSIX subprocess restrictions. Docker is cross-platform (matters now that Windows is a supported dev host) and matches what an FS customer would actually deploy.

See D12 for the current security model.

---

## D6 — Fact Sheet pattern borrowed wholesale from CoDaS

**Decision:** Every numerical answer that will appear in the final response is computed by the Computation Agent, stored in a flat key-value Fact Sheet, and copied verbatim by the Synthesizer. The Synthesizer never infers numbers from narrative context.

**Why:** CoDaS introduced this as a hallucination-prevention mechanism for scientific reporting. It transfers cleanly: spreadsheet QA has the same vulnerability (LLM writing a final answer that subtly disagrees with the deterministic computation). Worth borrowing exactly.

---

## D7 — Verifier returns positive verdicts at high confidence only; defers otherwise

**Decision:** Tier 1 of the verifier returns `correct=True` at confidence ≥ 0.95, OR `correct=False` only when the extraction is clean and clearly out of range. Borderline cases defer to Tier 2 (5% tolerance). Tier 3 is LLM adjudication via Gemini Flash, wired in at M1.3.

**Why:** Initial implementation returned `correct=False` at Tier 1 for any numeric mismatch outside 2.5% tolerance, which short-circuited Tier 2's broader matching. Fixed by adopting the paper's intended cascade: each tier resolves what it can with confidence, defers the rest.

---

## D8 — Bench is deterministic via seed=42

**Decision:** Every random choice in the bench generator flows through `random.Random(seed + hash(spec.file_id))`. Same seed → identical bench. Default seed = 42.

**Why:** Reproducibility for reviewers who want to regenerate the bench. Also: the canonical DataFrame must be deterministic for ground truth to be valid.

---

## D9 — Out of scope, declared early

- Formula evaluation in spreadsheets (we extract values, ignore formulas)
- Cross-spreadsheet joins (single-workbook scope)
- Vision-modality input (rendered spreadsheet images) — future work
- Real customer financial data — synthetic only
- The full CoDaS pipeline (Hypothesis, Mechanism, Novelty, Strategy, Report agents) — not relevant for closed-form QA
- Real-time / streaming spreadsheets — static files only

Scope discipline is a senior signal. We will not add any of these mid-project to "make the demo better."

---

## D10 — uv for all dependency management

**Decision:** uv (Astral) is the package manager. `pyproject.toml` declares deps in `[project.dependencies]` and four optional groups: `agents`, `observability`, `ui`, `dev`. `uv.lock` is committed for reproducibility. All run commands use `uv run`.

**Why:**
- Native lockfile (`uv.lock`) → reproducible builds across machines, including CI
- `uv sync --extra agents` lets us defer Week-2 deps (ADK, MCP, Vertex) until they're needed, keeping Week-1 install <30s
- `uv run python -m bench.build` works without activating the venv — cleaner for shell scripts and CI
- Modern Python tooling consensus is converging on uv
- Pins Python 3.12 automatically via `.python-version`

**Alternative considered:** Poetry. Rejected — slower, more configuration, and the wider Python ecosystem has clearly shifted toward uv in 2025–2026.

---

## D11 — Bench extended to 8 base templates / 24 files / 528 questions

**Decision:** Match FinSheet-Bench's full scale: 8 base templates × 3 structural variants = 24 files, ~22 questions per file = 528 total questions.

**New templates added beyond the original 4:**
- synthetic5: 58 companies, 4 funds, **descriptive fund names** (Growth, Income, Stability, Diversify) — tests non-numeric fund recognition
- synthetic6: 46 companies, 5 funds, **letter fund naming** (Fund A–E) — alternate naming + structural shift
- synthetic7: 34 companies, 3 funds — smallest, tests compact-portfolio behaviour
- synthetic8: 108 companies, 9 funds — most funds, tests fund-boundary detection at scale

**Why:**
- The original 4-template bench was a "starter" deliberately kept small for time budget; with the generator code already in place, scaling up to 8 is a 10-minute edit
- Matching FinSheet-Bench's scale (~500 questions) means our baseline numbers are directly comparable to the paper's published figures — important for the "I reproduced the paper" claim in the demo
- Adding non-Roman fund-naming schemes (descriptive, letter) creates genuinely harder cases for LLMs that might otherwise rely on ordinal-string heuristics for "latest fund" questions

**Trade-off:** Baseline eval cost goes up roughly 2× (528 vs 264 questions × Gemini 3.1 Pro at ~$0.40/question). Estimate at most ~$25 to run a single full baseline pass — within the eval budget cap.

---

## D12 — Code execution via Docker-per-call, not RestrictedPython or Vertex code interpreter

**Decision:** The Computation Agent's `execute_python` MCP tool runs each LLM-generated code snippet in a short-lived Docker container. The container image is `python:3.12-slim` plus pandas + numpy plus a thin runner script. Each call: spin container → pipe code via stdin → capture JSON result via stdout → container destroyed.

Security flags applied per container:
- `--read-only` — root filesystem is read-only
- `--memory=512m` — OOM protection
- `--cpus=1` — CPU bound
- `--network=none` — no egress, prevents data exfil
- `--user=nobody` — non-root execution
- `-v $BENCH_DATA:/data:ro` — spreadsheet mounted read-only
- `--rm` — container deleted after run

**Why:**
- This is the security posture a regulated FS customer would actually require for production deployment. Demonstrating it in the demo signals production-engineering maturity.
- Cross-platform: Docker Desktop abstracts Windows / Mac / Linux. The host OS no longer matters.
- Cost: $0 — Docker runs locally. Only LLM calls cost money.
- The MCP boundary makes the underlying sandbox a one-file change. Future swap to gVisor or Firecracker for multi-tenant production deployment is straightforward.

**Alternatives considered:**
- **RestrictedPython** — AST-level restriction, no subprocess, ~1ms overhead. Rejected: every Python sandboxing library has CVEs in its history because Python's introspection is hostile to sandboxing. Fine for a demo, not production-credible.
- **Vertex AI built-in code interpreter** — Gemini has native server-side code execution. Lower local infra burden but loses the MCP-served-deterministic-surface differentiation story (the surface becomes Google's, not ours). Reasonable fallback if Week 2 falls behind schedule.
- **gVisor / Firecracker microVMs** — production-grade isolation but way too heavyweight for a 3-week project.

**Trade-off:** ~200–400ms cold-start per container call. Since each query triggers 2–4 computation calls, the overhead is acceptable (within the < 45s P95 latency target). Container image reuse across calls (only the runner code changes per call) keeps warm-start latency low.

**What stays unchanged:** the MCP server interface. Agents call `execute_python(code, named_ranges)` and receive structured JSON. The Docker implementation is hidden behind that boundary — same code, same agent prompts, same observability.

---

## D13 — Switch baseline model from Gemini 3.1 Pro Preview to Gemini 2.5 Pro (GA)

**Decision:** The full-context baseline uses Gemini **2.5 Pro (GA)** as the default model, not Gemini 3.1 Pro Preview. Concurrency lowered to 5; retry backoff lengthened to `5 × 2^attempt + jitter` (max ~80s per call).

**Why:**
- **Quota:** 3.1 Pro Preview has tight per-project rate limits — a 528-question run with concurrency=10 hit 429 RESOURCE_EXHAUSTED on 210/528 calls (40%) during the first attempt. Quota increase requests for preview models take 2-5 business days; doesn't fit the project timeline.
- **Production realism:** No regulated FS customer deploys preview models. The demo's framing is "agentic architecture lifting a production-grade single-model baseline." 2.5 Pro IS what production deployments actually use today; 3.1 Pro is the "look what's coming" model. The former is the more honest baseline for an FDE pitch.
- **Architecture story doesn't change:** the demo's value is "multi-agent lifts accuracy from X% to Y%." Whether the X is 82% (3.1 Pro) or ~75% (2.5 Pro), the architectural gap to clear is similar in shape, and the case for an agentic system is arguably stronger when the baseline is what customers actually have access to.

**Trade-off — disclosed in eval report:** the FinSheet-Bench paper specifically benchmarked 3.1 Pro at 82.4% overall. With 2.5 Pro the expected baseline is ~73-78% on our synthetic bench, which is *not* a direct paper reproduction. Stated in `docs/eval-report.md` alongside the headline number rather than buried — transparency on this is the senior-engineer move.

**What stays unchanged:** the prompt template, serializer, verifier, runner, scoring code, synthetic bench. The Solver protocol means the model swap is a single env-var change.

---

## D14 — Naive RAG baseline: row-window chunking, headers-per-chunk, top-K=5, no spreadsheet-aware reranking

**Decision:** The M1.4 naive RAG baseline uses:
- **Chunking**: 10-row windows over the body of the spreadsheet
- **Header preservation**: title + units + header row included at the top of every chunk so the LLM always sees column names
- **Embedding model**: `text-embedding-005` (Vertex AI), `RETRIEVAL_DOCUMENT` task type for chunks, `RETRIEVAL_QUERY` for the question
- **Index**: in-memory, cosine similarity, no vector DB
- **Retrieval**: top-K = 5, no reranking, no query rewriting, no metadata filtering
- **Cache**: file-level embedding cache — each xlsx is embedded once and reused across its ~22 questions

**Why these specific choices:**
- **Naive on purpose.** The point of M1.4 is to characterize what a generic "first pass at RAG" achieves on spreadsheet QA — the kind of pipeline an FDE customer might stand up before deciding they need something more principled. Tuning chunk size, K, or adding reranking would defeat that purpose.
- **Headers per chunk** is a small concession to fairness — without column names, the LLM literally can't interpret any cell. Including them is what any reasonable engineer would do day one. Not doing it would make the baseline collapse to ~10% and be uninteresting.
- **Embedding cache** is purely a cost optimization. 24 files × ~30-50 chunks each = ~1,000 embeddings cached vs ~12,000 if we re-embedded per query.

**Expected outcome:**
- Overall: 40-55% (vs full-context 94.3%)
- Hard tier (synthetic4): 25-40%
- Aggregation and Sorting questions should collapse to near-zero (top-K can't supply all rows)
- Simple Lookup may hold up reasonably (embedding similarity often picks the right chunk)

**What this baseline establishes for the demo:**
> *"I tested both pragmatic baselines an FDE customer would consider — full-context (94%) and naive RAG (~45%). Full-context works but doesn't scale to enterprise spreadsheets with hundreds of sheets. Naive RAG scales but fails because spreadsheet structure doesn't chunk cleanly. The agentic architecture in M2 is the principled answer that handles both context cost and structural complexity."*

**Cost:** ~$0.01 embeddings + ~$1.50 chat = ~$1.50 total per full run. Wall-clock 10-15 minutes.

---

## D15 — Spreadsheet MCP server: stateless, five tools, sandbox-injected

**Decision:** The MCP server exposes five tools, each stateless (file path passed per call):
- `list_sheets(file_path)`
- `get_sheet_schema(file_path, sheet)` — returns columns, dtypes, fund layout (`column` vs `row_separator`), fund boundaries, average-row indices, sample rows
- `get_range(file_path, sheet, range)` — cell values keyed by coordinate
- `execute_python(file_path, code, named_ranges)` — sandboxed pandas/numpy execution
- `cite_cells(claim, sheet, cells)` — formats `[Sheet!A1,A2,A3]`-style citations

The Docker sandbox is injected into `build_server()` so tests can swap in a `LocalSandbox` without touching Docker. Production callers (`scripts/start_mcp_server.py`) create the Docker sandbox at server boot.

**Why stateless:**
- Same server instance can serve any xlsx. That's the property that makes the surface "portable beyond this project" — the differentiation story vs CoDaS (which uses subprocess-embedded execution).
- The xlsx path naturally lives in the orchestrator's context, not the server's state.

**Why FastMCP rather than the lower-level Server API:**
- Decorator-based tool registration is much cleaner.
- Type hints become tool argument schemas automatically.
- The tradeoff (less control over MCP protocol details) doesn't matter for our tool surface.

**Why ColumnInfo, FundBoundary, SheetSchema as dataclasses + `.to_dict()`:**
- The MCP wire format is JSON. dataclasses with explicit serializers are cleaner than relying on `dataclasses.asdict()` and chasing JSON-incompatible types (e.g. `pd.Timestamp` in sample rows).

**Sandbox injection pattern:**
- `build_server(sandbox=...)` lets tests run the full server surface without Docker.
- Production CLI defaults to `make_sandbox(prefer="docker")` which raises a clear error if Docker isn't installed.
- `LocalSandbox` requires explicit `allow_unsafe=True` to construct — fails noisily if anyone tries to use it with untrusted code by accident.

**Multi-line header handling — important architectural decision:** when the source xlsx has multi-line headers like `"Entry\nEnterprise Value"`, both `get_sheet_schema` and `load_range_as_df` collapse them to single-line canonical form: `"Entry Enterprise Value"`. The agent reads canonical names from the schema and uses them verbatim in pandas code. **Prompts for the Computation Agent in M2.3 must include the schema's column names as the authoritative reference** — referring to columns by their "natural" abbreviation (e.g., "Entry EV") will fail on multi-line-header files.

**Security model (Docker sandbox):**
- `--read-only` — root filesystem
- `--network=none` — no egress
- `--user=sandbox` — non-root
- `--memory=512m --cpus=1` — resource caps
- `--tmpfs /tmp:size=64m,exec` — writable /tmp for Python's import machinery
- `--rm` — container deleted after run
- Data mounted read-only at `/data`
- 30s default timeout per call (configurable)

**Out of scope for M2.1:** writing back to spreadsheets; cross-workbook joins; vision-modal spreadsheet input. Stays consistent with D9.

---

## D16 — Schema Agent deterministic; Decomposition Agent uses controlled generation

**Decision (Schema Agent):** No LLM call. The `SchemaAgent.profile()` method calls the MCP `get_sheet_schema` tool (already deterministic), converts the result into a typed `SchemaCard` (Pydantic), and adds machine-derived plain-English `structural_notes` for downstream agents.

**Why:** The MCP tool already produces rich structured output — columns + dtypes + fund layout + fund boundaries + average rows + sample rows. An LLM call on top would either:
- "Summarize" the structure (adds hallucination surface for no informational gain), or
- "Annotate" the columns with semantic types like 'amount in $M' (genuinely useful BUT can be done deterministically by inspecting column-name patterns + sample values).

For now we defer LLM enrichment — it can be added as an option later without changing the SchemaCard wire format.

**Practical benefit:** SchemaAgent tests run with no GCP access in milliseconds. Important for CI and for fast iteration on prompts in M2.3.

**Decision (Decomposition Agent):** Gemini 2.5 Pro with controlled generation — `response_schema=QueryPlan` passed to `GenerateContentConfig`. The Pydantic `QueryPlan` model in `types.py` is the single source of truth for both wire format and response schema.

**Why:** M1.3 showed 36 empty responses out of 528 on long structured prompts when relying on prompt-side JSON instructions. Controlled generation eliminates that failure mode — the model is forced to emit JSON matching the schema or fail in a way we can detect (parse error → AgentResult.error populated). No regex fallback, no "respond in JSON format" instruction in the prompt.

**Trade-off:** controlled generation can occasionally truncate complex plans if `max_output_tokens` is too low. We default to 4096 (vs 2048 for the baseline solver) since plans include longer per-subgoal descriptions.

**What stays unchanged:** the MCP tool surface, the QueryPlan type, the verifier. The agent stack only USES the MCP tools — it doesn't add to or modify them. M2.3 follows the same pattern.

---

## D17 — Computation Agent: deterministic prelude + per-subgoal codegen + retry loop

**Decision:** The Computation Agent does NOT ask the LLM to handle structural concerns. A deterministic Python preamble (built by the orchestrator from the SchemaCard) handles fund-column injection for `row_separator` layouts and removal of non-company rows. The LLM-generated subgoal code runs *after* this preamble and can rely on a clean `df` with `df['Fund']` always populated.

**Why deterministic prelude rather than asking the LLM to handle layout:**
- M2.2's plans showed the LLM is structurally *aware* (it correctly notes "fund_layout is row_separator, must add a Fund column from boundaries") but asking it to *generate* that injection code on every call wastes tokens and adds a failure mode for no informational gain.
- The fund-boundary mapping is purely deterministic — given the SchemaCard, the preamble is the same every call. No reason for an LLM call here.
- Similarly, "filter out average rows + dividers" is a universal precondition for ~70% of questions. Baking it into the prelude means the LLM never has to remember.

**Threshold-based row filter:** non-company rows (fund dividers, average rows) get excluded by counting populated cells per row. Real companies have ~13 non-null fields; dividers have just 2 (Company + the auto-injected Fund). Threshold of 5 cleanly separates them, works across all 8 templates without per-template tuning. Discovered by an end-to-end test that asserted `n_rows == 152` for synthetic4 and got 160 — the 8 extra were dividers slipping through an earlier prelude version that only filtered on Company endswith 'Average'.

**Retry loop with error feedback:** when LLM code fails in the sandbox, the agent calls Gemini again with the original code + the exact error message in the prompt. Default `max_retries=2` (so 3 attempts total per subgoal). Stops early if a subgoal fails permanently — subsequent subgoals almost certainly can't continue without the earlier result. The `n_retries` count is surfaced in `ComputationResult` for observability and cost tracking.

**Per-subgoal Gemini calls vs single-shot:** one Gemini call per subgoal lets the Fact Sheet from previous steps become context for the next. This matters when subgoal N needs to reference subgoal N-1's value as a literal (e.g., "now filter to companies above the median you just computed"). Costs more tokens but enables the architecture's compositional structure.

**Markdown fence stripping:** Gemini occasionally wraps code in ` ```python ... ``` ` despite our prompt explicitly asking for raw code. We strip fences in `_strip_markdown_fences()` rather than burn retries on it.

**Final-answer formatting in `_format_final_answer()`:** handles common LLM quirks — single-key dict when a number was asked for (`{"total": 42}` → `42.0`), dict values flattened to list when a list was asked for, etc. Maps to the verifier's 6 answer types. The output goes straight into the existing verifier from M1.3 — no new comparison logic needed.

**What stays unchanged for M2.4 onwards:** the MCP tool surface, the FactSheet type, the verifier. M2.4 (Verification Agent) reads cells via `get_range` and checks the Computation Agent's outputs — it consumes FactSheets, it doesn't produce them.

---

## D18 — Single-codegen-call per question (architectural correction to M2.3)

**Decision:** the Computation Agent generates **one** block of Python code per question, not one block per subgoal. The QueryPlan's subgoals appear in the prompt as ordered context, but the LLM produces a single chained pandas expression that performs all operations in sequence.

**Why the change:**

The original M2.3 design (D17) made one Gemini call per subgoal — each subgoal's code was a separate sandbox call, and the Fact Sheet carried intermediate values between them as JSON snippets in the next subgoal's prompt. The intent was CoDaS-style audit granularity.

The partial eval on synthetic4_A (78.3% / 17 of 22) exposed the failure mode. For two-subgoal plans like "filter to Unrealized, then sum EV per fund" (Q11), the LLM's Step 2 code was `df.groupby('Fund')[...].sum()` — operating on the *original unfiltered* `df`, not Step 1's filtered result. The filter was lost between sandbox calls. Same failure on Q4 (newest fund + list cos), Q8 (per-fund unrealized count). Together these accounted for **3 of 5 failures** on the hard tier — recoverable by structural change.

Inspecting the working cases (Q5 sort) showed the LLM was often *implicitly* combining subgoals into one chained expression anyway. So the architecture was paying the cost of multi-call orchestration without getting the granularity benefit.

**What the change does:**

- One Gemini call per question. The user prompt includes the full ordered subgoal list as logical context.
- One sandbox call. The LLM emits one chained pandas expression (or 2-3 statements if a chain isn't natural) that sets `__result__` to the final answer.
- One FactSheetEntry per question. The `code` field still contains the full executed code (prelude + LLM block) for audit; the granular per-step decomposition lives in the QueryPlan in the results JSONL alongside.

**What stays the same:**

- The deterministic prelude (D17) — still injected before every sandbox call.
- The retry loop — still 2 retries by default, with the error fed back to the LLM in the fix prompt.
- The plan structure — Decomposition Agent still emits N subgoals; they guide the LLM's reasoning even though they no longer become N executions.
- Final-answer formatting — unchanged.

**Projected impact on the hard tier:**
- Q11 (per-fund unrealized sum) — likely passes
- Q8 (per-fund unrealized count) — likely passes
- Q4 (newest fund company list) — likely passes
- Hard-tier accuracy: 77.3% → ~91% (target above M1.3's 81.8%)

**What the failure data taught us about prompt design:** explicit chained examples in the system prompt — three of them, covering filter+aggregate, lookup-by-fund-name, and ratio+median — give the LLM a concrete template to follow. The earlier "set `__result__` for this subgoal" framing was too abstract.

**Cost / latency impact:** one Gemini call instead of N reduces per-question cost by roughly (N-1)/N. For a 3-subgoal question, that's ~67% fewer codegen calls. The output codegen call is slightly longer (the LLM emits one chained expression rather than one short statement), but overall cost drops.
