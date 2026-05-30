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
