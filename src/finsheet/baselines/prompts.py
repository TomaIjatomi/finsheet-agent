"""
Prompt templates for the baseline runner.

Format hints per answer_type are critical: they make responses parseable
by the deterministic verifier, reducing reliance on Tier 3 LLM
adjudication and keeping the verdict cascade fast and cheap.
"""

from __future__ import annotations

SYSTEM_PROMPT = """You are an expert financial analyst answering questions about a private equity portfolio. You will be shown a spreadsheet and asked a question.

Rules:
- Answer using only the data shown in the spreadsheet.
- Do not show your reasoning or working — give just the answer.
- If a fund row divider, average row, or section header is not a portfolio company, do not count it as a company.
- When the question asks for a numerical aggregation, compute it across all relevant rows.
"""

# Format hints per answer_type. These map directly to the verifier's
# expectations, so keep them in sync with bench/verifier.py.
FORMAT_HINTS: dict[str, str] = {
    "numeric": "Answer with just the number, no units, no commentary. Example: 47.3",
    "string": "Answer with just the name, no quotes, no commentary. Example: Fund III",
    "list": "Answer as a comma-separated list, no numbering, no commentary. "
    "Example: Apex Holdings, Bloom Group, Cipher Networks",
    "dict": "Answer as 'Key: value' pairs, one per line, no commentary. "
    "Example:\nFund I: 47.3\nFund II: 89.1",
    "bool": "Answer 'Realized' or 'Unrealized' only.",
    "date": "Answer in YYYY-MM-DD format only. Example: 2024-03-15",
}


def build_user_prompt(spreadsheet_text: str, question: str, answer_type: str) -> str:
    """Construct the full user-side prompt for one question."""
    hint = FORMAT_HINTS.get(answer_type, "")
    return f"""Spreadsheet:

{spreadsheet_text}

Question: {question}

{hint}

Answer:"""
