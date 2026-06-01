# M1.1 — Stack Setup

Step-by-step to get the FinSheet Agent dev environment alive. Allow ~3 hours end-to-end. Run the smoke test at the end before moving on to M1.2.

## Prerequisites

Cross-platform: Windows, Mac, Linux, or WSL2 all work. Docker handles the M2 execution sandbox so the host OS is transparent.

- **For M1.x (bench + baselines)**: Git, uv (installed in step 1), gcloud CLI (step 2), code editor (VS Code or Cursor)
- **For M2.x (agent stack + sandbox)**: Docker Desktop (Windows/Mac) or Docker Engine (Linux) — not needed until M2.1
- A Google Cloud account with billing enabled (Vertex AI is not free; budget ~$10–$30 for M1 baselines)

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

## 6. Confirm Gemini 2.5 Pro access (default) — and optionally 3.1 Pro Preview

The project defaults to **Gemini 2.5 Pro (GA)** for all agent calls — see `docs/decisions.md` D13 for why. 2.5 Pro is available globally on Vertex AI with no special approval needed; if step 5 succeeded, you have access.

The global endpoint (`GCP_REGION=global` in `.env`) is required for both 2.5 Pro and 3.1 Pro on Vertex AI — regional endpoints produce a confusing `404 model not found` error.

**Optional — only if you want direct comparison to the FinSheet-Bench paper's published numbers:** request access to Gemini 3.1 Pro Preview at https://console.cloud.google.com/vertex-ai/model-garden, search "gemini-3.1-pro". Preview quota is tight, so request a quota bump at console.cloud.google.com/iam-admin/quotas if you plan to run a full 528-question pass (2-5 business days for approval). Then switch the model in `.env`:

```
GEMINI_PRO_MODEL=gemini-3.1-pro-preview
```

## 7. Install project dependencies

From the repo root:

```bash
# Installs Python 3.12 if not present, creates a venv, installs everything in uv.lock
uv sync --extra dev
```

This is the only install command you need for M1. It:
- Pins Python 3.12 for the project (via `.python-version`)
- Creates `.venv/` in the repo
- Installs core deps + the `dev` extra (pytest, ruff, etc.) from the locked versions in `uv.lock`
- Takes ~30 seconds the first time, ~2 seconds on subsequent syncs

When you're ready for M2 agent work, you'll add the agent + observability extras:

```bash
uv sync --extra dev --extra agents --extra observability
```

You won't need `--extra ui` until the Streamlit dashboard milestone (M3).

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

Then edit `.env` and confirm:
```
GCP_PROJECT_ID=finsheet-agent-dev
GCP_REGION=global
GEMINI_PRO_MODEL=gemini-2.5-pro
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
✓ Gemini 2.5 Pro responded: "Hello! I'm ready to help with financial spreadsheet analysis."
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

## 11. Set up git, pre-commit, and CI

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

- [ ] `uv run python scripts/smoke_test.py` succeeds — Gemini 2.5 Pro and Flash both respond
- [ ] `uv run python -m bench.build` produces 24 xlsx files and 528 ground-truth records
- [ ] `uv run pytest -q` passes
- [ ] Repo committed to GitHub

Estimated time: ~2 hours if GCP setup is fresh; ~30 min if you already have Vertex AI on another project. Move to M1.3 next.

## M2 prerequisite (do before M2.1)

Install Docker:
- **Windows / Mac**: Docker Desktop from https://www.docker.com/products/docker-desktop/ (Mac users may prefer OrbStack — faster, lighter, free)
- **Linux**: native Docker Engine; see https://docs.docker.com/engine/install/

Verify: `docker run --rm hello-world`

The Computation Agent's `execute_python` MCP tool runs LLM-generated pandas code in short-lived containers with `--read-only`, `--memory=512m`, `--cpus=1`, `--network=none`, `--user=nobody`. The container image is a thin `python:3.12-slim` with pandas + numpy preloaded. See `docs/decisions.md` D12 for the full security model.
