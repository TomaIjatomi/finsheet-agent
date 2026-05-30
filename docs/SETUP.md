# M1.1 — Stack Setup

Step-by-step to get the FinSheet Agent dev environment alive. Allow ~3 hours end-to-end. Run the smoke test at the end before moving on to M1.2.

## Prerequisites

Cross-platform: Windows, Mac, Linux, or WSL2 all work. Docker handles the Week-2 execution sandbox so the host OS is transparent.

- **Week 1**: Git, uv (installed in step 1), gcloud CLI (step 2), code editor (VS Code or Cursor)
- **Week 2**: Docker Desktop (Windows/Mac) or Docker Engine (Linux) — not needed until M2.1
- A Google Cloud account with billing enabled (Vertex AI is not free; budget ~$10–$30 for Week 1 baselines)

Python is **not** a prerequisite — `uv` manages Python versions for the project. You do NOT need to install Python 3.12 yourself.

## 1. Install uv (one-time)

**Mac / Linux / WSL:**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Windows (PowerShell):**
```powershell
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Or via winget: `winget install --id=astral-sh.uv`

Verify:
```bash
uv --version
```

uv replaces pip + venv + pyenv + poetry. We use it for everything.

## 2. Install the gcloud CLI

- **Mac**: `brew install --cask google-cloud-sdk`
- **Windows**: download installer from https://cloud.google.com/sdk/docs/install
- **Linux/WSL**: see https://cloud.google.com/sdk/docs/install

Verify: `gcloud --version`

## 3. Create the GCP project

```bash
# Login and set the project (same on all platforms)
gcloud auth login
gcloud projects create finsheet-agent-dev --name="FinSheet Agent Dev"
gcloud config set project finsheet-agent-dev

# Link a billing account (replace BILLING_ACCOUNT_ID with yours from console.cloud.google.com/billing)
gcloud billing accounts list
gcloud billing projects link finsheet-agent-dev --billing-account=BILLING_ACCOUNT_ID
```

## 4. Enable required APIs

```bash
gcloud services enable \
  aiplatform.googleapis.com \
  generativelanguage.googleapis.com \
  storage.googleapis.com \
  cloudtrace.googleapis.com \
  run.googleapis.com \
  artifactregistry.googleapis.com
```

On Windows cmd, replace the backslash line continuations with `^`. On PowerShell, use backticks (`` ` ``). Or just put it all on one line.

This takes ~2 minutes.

## 5. Set up Application Default Credentials

```bash
gcloud auth application-default login
gcloud auth application-default set-quota-project finsheet-agent-dev
```

This generates a credentials file the SDKs will pick up automatically — no service-account JSON needed for local dev.

## 6. Request access to Gemini 3.1 Pro Preview

As of May 2026, Gemini 3.1 Pro is in preview on Vertex AI and only runs on the **global endpoint** (not `us-central1`, not `europe-west4`). This is the single most common day-one configuration error.

- Go to https://console.cloud.google.com/vertex-ai/model-garden
- Search "gemini-3.1-pro"
- If you see "Request access" or "Enable", click through. Approval is usually instant.
- If you see the model card, you're good.

Quota for preview models is lower than GA. Default is usually fine for Week 1 (a few hundred queries/day) but if you hit 429s, request a quota increase at console.cloud.google.com/iam-admin/quotas (takes 2–5 business days, so do it early if you anticipate needing it).

## 7. Install project dependencies

From the repo root:

```bash
# Installs Python 3.12 if not present, creates a venv, installs everything in uv.lock
uv sync --extra dev
```

This is the only install command you need for Week 1. It:
- Pins Python 3.12 for the project (via `.python-version`)
- Creates `.venv/` in the repo
- Installs core deps + the `dev` extra (pytest, ruff, etc.) from the locked versions in `uv.lock`
- Takes ~30 seconds the first time, ~2 seconds on subsequent syncs

When you're ready for Week 2 agent work, you'll add the agent + observability extras:

```bash
uv sync --extra dev --extra agents --extra observability
```

You won't need `--extra ui` until the Streamlit dashboard in Week 3.

## 8. Configure environment variables

**Mac / Linux / WSL:**
```bash
cp .env.example .env
```

**Windows (cmd):**
```cmd
copy .env.example .env
```

**Windows (PowerShell):**
```powershell
Copy-Item .env.example .env
```

Then edit `.env` and set:
```
GCP_PROJECT_ID=finsheet-agent-dev
GCP_REGION=global
GEMINI_PRO_MODEL=gemini-3.1-pro-preview
GEMINI_FLASH_MODEL=gemini-flash-latest
```

`.env` is gitignored. Never commit it.

## 9. Smoke-test Gemini access

```bash
uv run python scripts/smoke_test.py
```

`uv run` executes a command inside the project's venv without needing to activate it manually. Works identically on all platforms. (If you prefer the classic flow: `source .venv/bin/activate` on Mac/Linux, `.venv\Scripts\activate` on Windows cmd, `.\.venv\Scripts\Activate.ps1` on PowerShell.)

Expected output:
```
✓ Vertex AI client initialized for project finsheet-agent-dev, region global
✓ Gemini 3.1 Pro responded: "Hello! I'm ready to help with financial spreadsheet analysis."
✓ Gemini Flash responded: "OK."
✓ Last call used ~12 tokens (negligible cost)
```

If it errors:
- `403 Permission denied` → API not enabled. Re-run step 4.
- `404 Model not found` → Wrong model ID, or the region isn't `global`. Check `.env`.
- `429 Resource exhausted` → Preview quota hit. Wait or request a quota increase.
- `401 Authentication` → Re-run `gcloud auth application-default login`.

## 10. Build the synthetic bench

```bash
uv run python -m bench.build
```

Expected output:
```
  ✓ synthetic1_A: 45 rows, 22 questions
  ✓ synthetic1_B: 28 rows, 22 questions
  ...
  ✓ synthetic8_C: 78 rows, 22 questions
Generated 24 xlsx files across 8 base templates × 3 versions.
Total questions: 528
```

Files land in `bench/data/files/` and `bench/data/ground_truth.jsonl`.

The bench mirrors FinSheet-Bench's full scale: 8 base templates × 3 versions = 24 files, ~22 questions per file = 528 total questions. `seed=42` makes this fully deterministic — anyone who runs `uv run python -m bench.build` gets identical output.

## 11. Email Qubera (parallel track — do this once and forget about it)

In parallel with this setup, send a short note to `info@qubera.ch` requesting dataset access. Template:

> Subject: FinSheet-Bench dataset access — academic / portfolio project
>
> Hi Qubera team,
>
> I'm building a multi-agent system for spreadsheet QA that implements the architectural approach your FinSheet-Bench paper recommends (separating document understanding from deterministic computation, per the CoDaS pattern from Google Research). Would it be possible to access the FinSheet-Bench dataset for benchmark reproduction? I'm building a representative synthetic bench following your Section 3.1 methodology in parallel, but reproducing your published numbers on the actual bench would be valuable.
>
> Happy to share my implementation and results when shipped.
>
> Thanks,
> Toma Ijatomi
> [LinkedIn / GitHub link]

If they respond, great — swap their bench in. If they don't, your synthetic bench is the deliverable.

## 12. Set up git, pre-commit, and CI

```bash
git init
git add -A
git commit -m "Initial scaffold + synthetic bench"

# Pre-commit (formatters + linters before each commit)
uv run pre-commit install

# Sanity check everything
uv run ruff check .
uv run pytest -q
```

The CI workflow in `.github/workflows/ci.yml` installs uv, runs `uv sync --frozen`, then runs ruff + pytest on push to GitHub.

## Common uv commands you'll use

| What you want | Command |
|---|---|
| Install deps from lockfile | `uv sync --extra dev` |
| Run a script in the venv | `uv run python scripts/foo.py` |
| Run a module in the venv | `uv run python -m bench.build` |
| Run pytest | `uv run pytest -q` |
| Add a new package | `uv add openpyxl` (or `uv add --optional agents google-adk`) |
| Update lockfile | `uv lock` |
| Show what's installed | `uv tree` |
| Drop into a shell with venv activated | `source .venv/bin/activate` (POSIX) / `.\.venv\Scripts\Activate.ps1` (PowerShell) |

## Done criteria for M1.1

- [ ] `uv run python scripts/smoke_test.py` succeeds — Gemini 3.1 Pro and Flash both respond
- [ ] `uv run python -m bench.build` produces 24 xlsx files and 528 ground-truth records
- [ ] `uv run pytest -q` passes 8 tests
- [ ] Repo committed to GitHub (private at this stage)
- [ ] Qubera email sent (or noted not-sent with reasoning)
- [ ] One screenshot of synthetic4_A.xlsx in `docs/screenshots/` for the project journal

Estimated time: ~3 hours if GCP setup is fresh; ~1 hour if you already have Vertex AI on another project. Move to M1.3 next.

## Week 2 prerequisite (do before M2.1)

Install Docker:
- **Windows / Mac**: Docker Desktop from https://www.docker.com/products/docker-desktop/ (Mac users may prefer OrbStack — faster, lighter, free)
- **Linux**: native Docker Engine; see https://docs.docker.com/engine/install/

Verify: `docker run --rm hello-world`

The Computation Agent's `execute_python` MCP tool runs LLM-generated pandas code in short-lived containers with `--read-only`, `--memory=512m`, `--cpus=1`, `--network=none`, `--user=nobody`. The container image is a thin `python:3.12-slim` with pandas + numpy preloaded. See `docs/decisions.md` D12 for the full security model.
