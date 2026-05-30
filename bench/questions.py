"""
The 16 question templates from FinSheet-Bench Table 2.

Each template knows:
  - its category (Simple Lookup / List Extraction / Filtering / Counting /
    Aggregation / Sorting / Complex Aggregation)
  - its complexity (Low / Medium / High / Very High)
  - its answer type (numeric / string / list / dict / boolean / date)
  - whether it's parameterized (Q9, Q10, Q15 vary by entity)
  - how to render its prompt
  - how to compute the ground truth from the canonical DataFrame
"""
from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass
class QuestionTemplate:
    qid: int
    template: str              # e.g. "How many funds are there?"
    category: str
    complexity: str            # Low | Medium | High | Very High
    answer_type: str           # numeric | string | list | dict | bool | date
    parameterized: bool        # True if {entity} or {fund} placeholder is used
    parameter_kind: str | None # "company" | "fund" | None
    compute_fn: Callable[[pd.DataFrame, dict], Any]
    """compute_fn signature: (canonical_df, params_dict) -> ground_truth_value"""


# --- Ground truth compute functions ---------------------------------------

def _q1(df, _params):  # How many funds?
    return int(df["Fund"].nunique())

def _q2(df, _params):  # How many companies per fund?
    return df.groupby("Fund", sort=False).size().to_dict()

def _q3(df, _params):  # Which fund is the latest?
    # Latest = the one with the most recent earliest entry date
    return df.groupby("Fund", sort=False)["Entry Date"].min().idxmax()

def _q4(df, _params):  # List all companies in the newest fund
    latest = _q3(df, _params)
    return sorted(df[df["Fund"] == latest]["Company"].tolist())

def _q5(df, _params):  # List all companies sorted by entry EBITDA
    sorted_df = df.sort_values("Entry EBITDA", ascending=False)
    return sorted_df["Company"].tolist()

def _q6(df, _params):  # Highest entry EBITDA company per fund
    out = {}
    for fund, group in df.groupby("Fund", sort=False):
        if group["Entry EBITDA"].dropna().empty:
            continue
        top = group.loc[group["Entry EBITDA"].idxmax()]
        out[fund] = top["Company"]
    return out

def _q7(df, _params):  # Which funds have unrealized investments?
    return sorted(df[df["Status"] == "Unrealized"]["Fund"].unique().tolist())

def _q8(df, _params):  # How many unrealized per fund
    counts = df[df["Status"] == "Unrealized"].groupby("Fund", sort=False).size().to_dict()
    # Include funds with zero
    for fund in df["Fund"].unique():
        counts.setdefault(fund, 0)
    return counts

def _q9(df, params):  # Is {company} realized or unrealized?
    company = params["company"]
    matches = df[df["Company"] == company]
    if matches.empty:
        return None
    return matches.iloc[0]["Status"]

def _q10(df, params):  # Entry EV for {company}
    company = params["company"]
    matches = df[df["Company"] == company]
    if matches.empty:
        return None
    return float(matches.iloc[0]["Entry EV"])

def _q11(df, _params):  # Total unrealized capital per fund (sum of Entry EV)
    sub = df[df["Status"] == "Unrealized"]
    sums = sub.groupby("Fund", sort=False)["Entry EV"].sum().to_dict()
    for fund in df["Fund"].unique():
        sums.setdefault(fund, 0.0)
    return {k: round(float(v), 1) for k, v in sums.items()}

def _q12(df, _params):  # Average entry EV per fund
    avgs = df.groupby("Fund", sort=False)["Entry EV"].mean().to_dict()
    return {k: round(float(v), 1) for k, v in avgs.items()}

def _q13(df, _params):  # Most recent exit date
    exits = df["Exit Date"].dropna()
    if exits.empty:
        return None
    return max(exits)

def _q14(df, _params):  # Highest entry debt/EBITDA ratio (which company?)
    ratios = df["Net Debt at Entry"] / df["Entry EBITDA"]
    if ratios.dropna().empty:
        return None
    idx = ratios.idxmax()
    return df.loc[idx]["Company"]

def _q15(df, params):  # Average net debt at acquisition for {fund}
    fund = params["fund"]
    sub = df[df["Fund"] == fund]
    if sub.empty:
        return None
    avg = sub["Net Debt at Entry"].mean()
    return round(float(avg), 1)

def _q16(df, _params):  # Median net debt/EBITDA for all funds
    ratios = (df["Net Debt at Entry"] / df["Entry EBITDA"]).dropna()
    if ratios.empty:
        return None
    return round(float(ratios.median()), 2)


TEMPLATES: list[QuestionTemplate] = [
    QuestionTemplate(1, "How many funds are there?",
                     "Simple Lookup", "Low", "numeric", False, None, _q1),
    QuestionTemplate(2, "How many companies are in each fund?",
                     "Counting", "Medium", "dict", False, None, _q2),
    QuestionTemplate(3, "Which fund is the latest (most recently initiated)?",
                     "Simple Lookup", "Low", "string", False, None, _q3),
    QuestionTemplate(4, "List all companies in the newest fund.",
                     "List Extraction", "Medium", "list", False, None, _q4),
    QuestionTemplate(5, "List all companies sorted by entry EBITDA from highest to lowest.",
                     "Sorting", "High", "list", False, None, _q5),
    QuestionTemplate(6, "For each fund, which company has the highest entry EBITDA?",
                     "Aggregation", "High", "dict", False, None, _q6),
    QuestionTemplate(7, "Which funds have at least one unrealized investment?",
                     "Filtering", "Medium", "list", False, None, _q7),
    QuestionTemplate(8, "How many unrealized investments does each fund have?",
                     "Counting", "Medium", "dict", False, None, _q8),
    QuestionTemplate(9, "Is {company} a realized or unrealized investment?",
                     "Simple Lookup", "Low", "string", True, "company", _q9),
    QuestionTemplate(10, "What is the entry enterprise value of {company}?",
                     "Simple Lookup", "Low", "numeric", True, "company", _q10),
    QuestionTemplate(11, "What is the total unrealized capital (sum of entry EV) per fund?",
                     "Aggregation", "High", "dict", False, None, _q11),
    QuestionTemplate(12, "What is the average entry enterprise value per fund?",
                     "Aggregation", "High", "dict", False, None, _q12),
    QuestionTemplate(13, "What is the most recent exit date across all investments?",
                     "Aggregation", "Medium", "date", False, None, _q13),
    QuestionTemplate(14, "Which company has the highest entry debt/EBITDA ratio?",
                     "Aggregation", "High", "string", False, None, _q14),
    QuestionTemplate(15, "What is the average net debt at acquisition for {fund}?",
                     "Aggregation", "High", "numeric", True, "fund", _q15),
    QuestionTemplate(16, "What is the median net debt/EBITDA ratio across all investments?",
                     "Complex Aggregation", "Very High", "numeric", False, None, _q16),
]


def sample_parameters(template: QuestionTemplate, df: pd.DataFrame,
                      sample_size: int, seed: int = 42) -> list[dict]:
    """For parameterized templates, return sample_size random parameter sets."""
    if not template.parameterized:
        return [{}]
    rng = random.Random(seed + template.qid)
    if template.parameter_kind == "company":
        companies = df["Company"].dropna().unique().tolist()
        sampled = rng.sample(companies, min(sample_size, len(companies)))
        return [{"company": c} for c in sampled]
    if template.parameter_kind == "fund":
        funds = df["Fund"].dropna().unique().tolist()
        sampled = rng.sample(funds, min(sample_size, len(funds)))
        return [{"fund": f} for f in sampled]
    return [{}]


def render_prompt(template: QuestionTemplate, params: dict) -> str:
    return template.template.format(**params)
