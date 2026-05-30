# FinSheet Agent

A multi-agent system for **financial spreadsheet question answering**, applying the **CoDaS architectural pattern** (Kim et al., Google Research + DeepMind, April 2026, [arXiv:2604.14615](https://arxiv.org/abs/2604.14615)) to the problem identified by **FinSheet-Bench** (Ravnik et al., Qubera AG + UZH, March 2026, [arXiv:2603.07316](https://arxiv.org/abs/2603.07316)).

> FinSheet-Bench showed that frontier LLMs (Gemini 3.1 Pro: 82.4% overall, but only 48.6% on the hardest multi-fund spreadsheets) cannot be used unsupervised for financial spreadsheet QA, and explicitly called for *"agentic and pipeline-based approaches that decompose document understanding from numerical computation."* CoDaS demonstrated the design pattern that does this — *"paired deterministic code runners and language model interpreters"* — for biomedical biomarker discovery. This project applies that pattern to FinSheet's problem space, with a **portable MCP-served deterministic-tool surface** as the architectural differentiation from CoDaS itself.

## Architecture (target)

```
User question + spreadsheet
       ↓
Orchestrator (Gemini 3.1 Pro)
       ↓
Schema Agent (Gemini Flash) ←→ Spreadsheet MCP Server
       ↓
Query Decomposition (Gemini 3.1 Pro)
       ↓
Computation Agent (Gemini 3.1 Pro) ←→ Sandboxed Python via MCP
       ↓
Fact Sheet (deterministic key-value of every reportable number)
       ↓
Verification Agent (Gemini 3.1 Pro) — independent cell-level cross-check
       ↓
Synthesis (Gemini Flash) — copies Fact Sheet numbers verbatim
       ↓
Answer + cell citations
```

## Status

| Milestone | Status |
|---|---|
| M1.1 Stack alive | scaffold ready; see `docs/SETUP.md` |
| M1.2 Synthetic bench | ✓ Built — **24 files, 528 questions** (matches FinSheet-Bench's full scale) |
| M1.3 Baseline #1: full-context Gemini 3.1 Pro | pending |
| M1.4 Baseline #2: naive RAG | pending |
| M1.5 Eval report v1 | pending |
| M2.* Multi-agent architecture | pending |
| M3.* Eval + dashboard + ship | pending |

## Quickstart

```bash
# One-time: install uv (replaces pip/venv/poetry)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Sync deps (creates .venv, installs from uv.lock, ~30s first time)
uv sync --extra dev

# Configure GCP (one-time) — see docs/SETUP.md for the full walkthrough
cp .env.example .env
# fill in .env

# Smoke-test Gemini access
uv run python scripts/smoke_test.py

# Build the synthetic bench (deterministic, seed=42)
uv run python -m bench.build

# Run tests
uv run pytest -q
```

## Repository layout

```
finsheet-agent/
├── bench/                  # synthetic test bench (this Week 1)
│   ├── templates.py        # 4 base file specs
│   ├── generator.py        # canonical DataFrame → xlsx
│   ├── variants.py         # B/C structural variants
│   ├── questions.py        # 16 question templates
│   ├── ground_truth.py     # deterministic GT computation
│   ├── verifier.py         # 3-tier cascading verification
│   ├── build.py            # end-to-end build script
│   └── data/               # generated xlsx + ground_truth.jsonl
├── src/finsheet/           # agent code (Week 2)
│   ├── agents/             # Schema, Decomposition, Computation, Verification
│   └── mcp/                # Spreadsheet MCP server
├── scripts/
│   └── smoke_test.py       # Gemini access check
├── docs/
│   ├── SETUP.md            # M1.1 step-by-step
│   ├── decisions.md        # architectural choices + rationale
│   └── synthetic-bench.md  # bench construction methodology
├── tests/
└── .github/workflows/ci.yml
```

## Citations

If you use the synthetic bench or the architectural pattern, please cite both source papers:

```bibtex
@article{ravnik2026finsheet,
  title={FinSheet-Bench: From Simple Lookups to Complex Reasoning, Where LLMs Break on Financial Spreadsheets},
  author={Ravnik, Jan and Li{\v{c}}en, Matja{\v{z}} and B{\"u}hrmann, Felix and Yuan, Bithiah and Stinson, Felix and Singh, Tanvi},
  journal={arXiv preprint arXiv:2603.07316},
  year={2026}
}

@article{kim2026codas,
  title={CoDaS: AI Co-Data-Scientist for Biomarker Discovery via Wearable Sensors},
  author={Kim, Yubin and others},
  journal={arXiv preprint arXiv:2604.14615},
  year={2026}
}
```

## License

MIT for code. The synthetic bench data is also MIT-licensed and reproducible from `python -m bench.build` with seed=42.
