# Synthetic Bench — Methodology

This document describes how the synthetic FinSheet bench was constructed, mirroring **FinSheet-Bench (Ravnik et al. 2026, arXiv:2603.07316) Section 3.1** so the bench is independently reproducible and the project doesn't depend on access to Qubera AG's private dataset.

## Why a synthetic bench

FinSheet-Bench is not openly distributed (corresponding author `info@qubera.ch`). Their dataset is itself synthetic — generated from real PE portfolio templates with all cell values regenerated. We reconstruct an equivalent bench from their fully-documented methodology.

## Spec overview

| File | Companies | Funds | Fund naming | Multi-line headers | Average rows | Fund placement | Notes |
|---|---|---|---|---|---|---|---|
| synthetic1 | 45 | 4 | roman | no | no | column | Small, clean baseline |
| synthetic2 | 89 | 5 | roman | no | yes | column | Medium with summary rows |
| synthetic3 | 114 | 6 | roman | yes | no | row separator | Multi-line headers; fund as row |
| **synthetic4** | **152** | **8** | **roman** | **yes** | **yes** | **row separator** | **Hardest — mirrors FinSheet-Bench synthetic4_A (48.6% avg accuracy)** |
| synthetic5 | 58 | 4 | descriptive | no | no | column | "Growth", "Income", "Stability", "Diversify" — non-numeric fund recognition |
| synthetic6 | 46 | 5 | letter | no | yes | row separator | "Fund A" through "Fund E" — alternate naming + structural shift |
| synthetic7 | 34 | 3 | roman | no | no | column | Smallest — tests behaviour on compact portfolios |
| synthetic8 | 108 | 9 | roman | yes | no | row separator | Most funds — tests fund-boundary detection at scale |

Each base template gets two structural variants (B, C) by row removal (~1/3 dropped) and structural transforms (separator changes, fund placement inversion, average-row toggles). Total: **8 templates × 3 versions = 24 files, ~22 questions per file = 528 total questions** — matching FinSheet-Bench's full scale.

## Generation pipeline

```
TemplateSpec (deterministic spec)
        │  build_canonical_df(seed=42)
        ▼
Canonical DataFrame ──────────────────────────┐
        │                                      │
        │  write_xlsx(applies layout choices)  │  ground_truth.compute_fn
        ▼                                      ▼
   xlsx file ←──── the LLM sees this        Ground truth value
   (messy, formatted)                       (deterministic from canonical)
```

The canonical DataFrame is the **source of truth**. It captures the underlying portfolio data as a clean pandas table. Ground-truth answers are computed from this table using deterministic Python functions — never from the xlsx file, never via an LLM. This matches FinSheet-Bench Section 3.1.4.

The xlsx file is the **stimulus**. It's the messy, structurally complex artifact the LLM agent sees. Layout choices (multi-line headers, fund dividers, average rows, list separators) are applied per-template in `generator.py:write_xlsx`.

## Data generation (mirrors FinSheet-Bench §3.1.2)

| Field | Method |
|---|---|
| Company name | Random combination of 40 prefixes × 26 suffixes; unique within file |
| Sector | Random choice from 10 GICS categories |
| Headquarters | Random choice from 16 financial-centre cities |
| Status (Realized/Unrealized) | Vintage-weighted: older funds more realized (older = more time to exit) |
| Entry date | Within vintage year + 0–3 years |
| Exit date | Entry + 3–8 years (Realized only); capped at 2025 |
| Entry EV | Uniform $50M–$2000M |
| Entry EBITDA | Entry EV × uniform 8–28% margin |
| Net Debt at Entry | Entry EBITDA × uniform 0.5–4.5× (debt multiple) |
| Ownership % | Uniform 15–100% |
| Exit EV | Entry EV × uniform 0.7–3.5× (Realized only) |
| Exit EBITDA | Entry EBITDA × uniform 0.8–2.5× (Realized only) |
| Board Members | 2–5 random "FirstName LastName" combinations |

The FinSheet-Bench paper uses scaling factors A, B in [0.5, 2.0] and a perturbation k in [0.95, 1.05] applied to anonymize values from real source spreadsheets. Our generator skips that anonymization step (we have no real spreadsheets) and goes directly to the ranges above. Order-of-magnitude realism matches PE portfolio monitoring.

## Variant transforms (mirrors FinSheet-Bench §3.1.3)

**B variants:** ~33% of rows dropped at random (min 3 per fund preserved); list separator swapped (`;` ↔ `,`); average rows removed if present; blank rows between funds toggled.

**C variants:** ~25% of rows dropped at random; average rows always added; fund placement inverted (column ↔ row separator); list separator swapped.

These choices mirror the modifications enumerated in FinSheet-Bench Appendix A (column splits, separator changes, summary row additions, fund-column removals).

## Question templates (16 templates, FinSheet-Bench §4.2)

| Q | Question | Category | Complexity |
|---|---|---|---|
| 1 | How many funds are there? | Simple Lookup | Low |
| 2 | How many companies are in each fund? | Counting | Medium |
| 3 | Which fund is the latest? | Simple Lookup | Low |
| 4 | List all companies in the newest fund | List Extraction | Medium |
| 5 | List all companies sorted by entry EBITDA | Sorting | High |
| 6 | Highest entry EBITDA company per fund | Aggregation | High |
| 7 | Which funds have unrealized investments? | Filtering | Medium |
| 8 | How many unrealized investments per fund? | Counting | Medium |
| 9 | Is {company} realized or unrealized? | Simple Lookup | Low |
| 10 | Entry EV for {company} | Simple Lookup | Low |
| 11 | Total unrealized capital per fund | Aggregation | High |
| 12 | Average entry EV per fund | Aggregation | High |
| 13 | Most recent exit date | Aggregation | Medium |
| 14 | Highest entry debt/EBITDA ratio | Aggregation | High |
| 15 | Average net debt at acquisition for {fund} | Aggregation | High |
| 16 | Median net debt/EBITDA across all investments | Complex Aggregation | Very High |

Q9, Q10, Q15 are parameterized: 3 random samples each per file (`sample_size=3, seed=42`), yielding ~22 questions per file.

## Verification (mirrors FinSheet-Bench §4.3.1)

A 3-tier cascading verifier in `bench/verifier.py`:

- **Tier 1 (exact, confidence ≥ 0.95):** strict regex numeric extraction with 2.5% relative tolerance; case-insensitive string match; Jaccard 0.95 for lists; 1-day date tolerance; keyword boolean.
- **Tier 2 (fuzzy, confidence ≥ 0.70):** broader numeric regex with 5% relative tolerance; SequenceMatcher 0.95 for strings; Jaccard 0.75 for lists.
- **Tier 3 (LLM adjudication):** placeholder; will be wired to Gemini 3 Flash judge in M1.3.

Tier 1 returns positive verdicts at high confidence, OR explicit negative verdicts only when the extraction is clean and clearly out of range. Borderline cases defer to Tier 2.

## Reproducibility

```bash
python -m bench.build --seed 42
```

This is fully deterministic: same seed → identical xlsx files, identical canonical DataFrames, identical ground truth. The seed flows through both data generation (random company names, financial values, status assignments) and parameterized-question sampling.

## Differences from FinSheet-Bench

We document what's different so reviewers can compare apples to apples:

| Aspect | FinSheet-Bench | This synthetic bench |
|---|---|---|
| Source | Real PE portfolios anonymized | Fully synthetic; no real source |
| Base files | 8 | **8 (matched)** |
| Variants per base | 2 (B + C) | 2 (B + C) |
| Total files | 24 | **24 (matched)** |
| Questions per file | ~22 | ~22 |
| Total questions | ~500 per model | **528** |
| Anonymization step | Scaling A,B + perturbation k | Direct generation in range |
| Layout variations | Documented per-file in Appendix A | Spec-driven; consistent |
| Fund naming schemes | Roman + descriptive (some files) | Roman + descriptive + letter |
| Vision modality | Future work | Future work |

Reproducibility is fully deterministic via `seed=42`. To add more files, append another `TemplateSpec` to `bench/templates.py` and rerun `uv run python -m bench.build`.
