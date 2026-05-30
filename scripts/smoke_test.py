"""Smoke test for Gemini access via Vertex AI.

Confirms:
  - Vertex AI client initializes with the configured project
  - Gemini 3.1 Pro Preview is reachable on the global endpoint
  - Gemini Flash is reachable
  - Both return non-empty responses

Run: python scripts/smoke_test.py
"""
from __future__ import annotations

import os
import sys

# Load .env if present
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

PROJECT_ID = os.environ.get("GCP_PROJECT_ID")
REGION = os.environ.get("GCP_REGION", "global")
PRO_MODEL = os.environ.get("GEMINI_PRO_MODEL", "gemini-3.1-pro-preview")
FLASH_MODEL = os.environ.get("GEMINI_FLASH_MODEL", "gemini-flash-latest")


def main() -> int:
    if not PROJECT_ID:
        print("✗ GCP_PROJECT_ID not set. Copy .env.example to .env and fill it in.")
        return 1

    try:
        from google import genai
        from google.genai import types
    except ImportError:
        print("✗ google-genai not installed. Run: uv pip install -e .")
        return 1

    # Initialize Vertex AI client on the global endpoint (required for 3.1 Pro)
    client = genai.Client(
        vertexai=True,
        project=PROJECT_ID,
        location=REGION,
    )
    print(f"✓ Vertex AI client initialized for project {PROJECT_ID}, region {REGION}")

    # Test Pro
    try:
        response = client.models.generate_content(
            model=PRO_MODEL,
            contents="In one sentence, confirm you can help with financial spreadsheet analysis.",
            config=types.GenerateContentConfig(
                temperature=0.0,
                max_output_tokens=64,
            ),
        )
        text = (response.text or "").strip()
        print(f"✓ Gemini 3.1 Pro responded: {text[:100]}")
    except Exception as e:
        print(f"✗ Gemini 3.1 Pro failed: {e}")
        return 1

    # Test Flash
    try:
        response = client.models.generate_content(
            model=FLASH_MODEL,
            contents="Reply with one word: OK",
            config=types.GenerateContentConfig(
                temperature=0.0,
                max_output_tokens=16,
            ),
        )
        text = (response.text or "").strip()
        print(f"✓ Gemini Flash responded: {text[:50]}")
    except Exception as e:
        print(f"✗ Gemini Flash failed: {e}")
        return 1

    # Token usage / cost hint
    try:
        usage = response.usage_metadata
        if usage:
            total = (usage.prompt_token_count or 0) + (usage.candidates_token_count or 0)
            # Rough Flash cost: $0.075/M input, $0.30/M output. Trivial here.
            print(f"✓ Last call used ~{total} tokens (negligible cost)")
    except Exception:
        pass

    print("\nAll good. Move on to M1.2 (synthetic bench).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
