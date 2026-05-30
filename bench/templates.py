"""
Base template specifications for the synthetic FinSheet bench.

Each template defines the shape of a private-equity portfolio monitoring
spreadsheet at varying levels of complexity. The generator turns each
template into one xlsx file (version A); variants.py derives B and C
structural variants from each base.

Methodology follows FinSheet-Bench (Ravnik et al. 2026, arXiv:2603.07316)
Section 3.1.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class TemplateSpec:
    """Spec for one synthetic spreadsheet."""
    file_id: str                   # e.g. "synthetic1"
    n_companies: int               # total rows of investment data
    n_funds: int                   # number of distinct funds
    fund_naming: str               # "roman" | "descriptive" | "letter"
    has_average_rows: bool         # average rows below each fund
    multiline_headers: bool        # column headers split across lines
    fund_as_column: bool           # True: Fund is a column. False: row separator.
    list_separator: str            # separator for list-valued cells (board members, co-investors)
    blank_rows_between_funds: bool
    notes: str = ""

# Eight base templates spanning the FinSheet-Bench difficulty range.
TEMPLATES: list[TemplateSpec] = [
    TemplateSpec(
        file_id="synthetic1",
        n_companies=45,
        n_funds=4,
        fund_naming="roman",
        has_average_rows=False,
        multiline_headers=False,
        fund_as_column=True,
        list_separator="; ",
        blank_rows_between_funds=False,
        notes="Small, clean structure. Baseline difficulty.",
    ),
    TemplateSpec(
        file_id="synthetic2",
        n_companies=89,
        n_funds=5,
        fund_naming="roman",
        has_average_rows=True,
        multiline_headers=False,
        fund_as_column=True,
        list_separator="; ",
        blank_rows_between_funds=True,
        notes="Medium size with summary rows.",
    ),
    TemplateSpec(
        file_id="synthetic3",
        n_companies=114,
        n_funds=6,
        fund_naming="roman",
        has_average_rows=False,
        multiline_headers=True,
        fund_as_column=False,
        list_separator=", ",
        blank_rows_between_funds=True,
        notes="Multi-line headers; fund as row separator.",
    ),
    TemplateSpec(
        file_id="synthetic4",
        n_companies=152,
        n_funds=8,
        fund_naming="roman",
        has_average_rows=True,
        multiline_headers=True,
        fund_as_column=False,
        list_separator=", ",
        blank_rows_between_funds=True,
        notes="Largest, hardest. Mirrors FinSheet-Bench synthetic4_A (152 companies, 8 funds, 48.6% average accuracy in the paper).",
    ),
    TemplateSpec(
        file_id="synthetic5",
        n_companies=58,
        n_funds=4,
        fund_naming="descriptive",
        has_average_rows=False,
        multiline_headers=False,
        fund_as_column=True,
        list_separator="; ",
        blank_rows_between_funds=False,
        notes="Descriptive fund names (Growth, Income, Stability, Diversify). Tests non-numeric fund recognition; the 'latest' fund cannot be inferred from name alone.",
    ),
    TemplateSpec(
        file_id="synthetic6",
        n_companies=46,
        n_funds=5,
        fund_naming="letter",
        has_average_rows=True,
        multiline_headers=False,
        fund_as_column=False,
        list_separator=", ",
        blank_rows_between_funds=True,
        notes="Letter fund naming (Fund A, B, C, D, E). Fund as row separator; tests alternate naming + structural shift together.",
    ),
    TemplateSpec(
        file_id="synthetic7",
        n_companies=34,
        n_funds=3,
        fund_naming="roman",
        has_average_rows=False,
        multiline_headers=False,
        fund_as_column=True,
        list_separator="; ",
        blank_rows_between_funds=False,
        notes="Smallest file (34 companies, 3 funds). Tests behavior on compact portfolios where context cost is minimal.",
    ),
    TemplateSpec(
        file_id="synthetic8",
        n_companies=108,
        n_funds=9,
        fund_naming="roman",
        has_average_rows=False,
        multiline_headers=True,
        fund_as_column=False,
        list_separator=", ",
        blank_rows_between_funds=True,
        notes="Most funds (9). Tests fund-boundary detection at scale; small avg fund size makes Q6/Q8 harder.",
    ),
]

# Column schema. Every template uses this superset; some are stripped in variants.
COLUMNS = [
    ("Company", "str"),
    ("Sector", "str"),
    ("Headquarters", "str"),
    ("Status", "str"),          # "Realized" | "Unrealized"
    ("Entry Date", "date"),
    ("Exit Date", "date"),      # blank if Unrealized
    ("Entry EV", "money"),       # $M
    ("Exit EV", "money"),        # $M, blank if Unrealized
    ("Entry EBITDA", "money"),   # $M
    ("Exit EBITDA", "money"),    # $M, blank if Unrealized
    ("Net Debt at Entry", "money"),
    ("Ownership %", "pct"),
    ("Board Members", "list"),
    ("Fund", "str"),
]

SECTORS = [
    "Technology",
    "Healthcare",
    "Industrials",
    "Consumer Discretionary",
    "Consumer Staples",
    "Financials",
    "Energy",
    "Materials",
    "Communication Services",
    "Real Estate",
]

CITIES = [
    "New York, NY", "San Francisco, CA", "Boston, MA", "Chicago, IL",
    "Austin, TX", "Seattle, WA", "Denver, CO", "Atlanta, GA",
    "London, UK", "Munich, DE", "Paris, FR", "Zurich, CH",
    "Singapore, SG", "Tokyo, JP", "Sydney, AU", "Toronto, CA",
]

# Realistic fictitious PE-style portfolio company name parts.
COMPANY_PREFIXES = [
    "Apex", "Bloom", "Cipher", "Delta", "Echo", "Forge", "Gravity",
    "Helix", "Iris", "Junction", "Kepler", "Lumen", "Meridian", "Nimbus",
    "Orion", "Polaris", "Quanta", "Radial", "Stratus", "Tessera",
    "Umbra", "Vertex", "Wavelength", "Xenith", "Yields", "Zenith",
    "Atlas", "Beacon", "Cobalt", "Drift", "Ember", "Fathom", "Glacier",
    "Halo", "Indigo", "Junction", "Krypton", "Lattice", "Mosaic", "Nova",
]

COMPANY_SUFFIXES = [
    "Holdings", "Group", "Partners", "Capital", "Industries", "Systems",
    "Networks", "Labs", "Solutions", "Dynamics", "Logic", "Tech",
    "Health", "Bio", "Pharma", "Media", "Logistics", "Foods", "Energy",
    "Materials", "Inc", "Corp", "Ltd", "AG", "GmbH", "SA",
]

BOARD_FIRST_NAMES = [
    "Sarah", "Michael", "Emma", "David", "Sophie", "James", "Olivia",
    "Daniel", "Hannah", "Christopher", "Rachel", "Andrew", "Laura",
    "Matthew", "Jennifer", "Robert", "Amanda", "Thomas", "Jessica",
    "Mark", "Elena", "Ravi", "Yuki", "Ahmed", "Priya", "Wei",
]

BOARD_LAST_NAMES = [
    "Anderson", "Brown", "Chen", "Davis", "Evans", "Fischer", "Gupta",
    "Hassan", "Ivanov", "Johnson", "Kim", "Lopez", "Mueller", "Nakamura",
    "O'Brien", "Patel", "Qureshi", "Rossi", "Schmidt", "Tanaka",
    "Ueno", "Vega", "Williams", "Xu", "Yamamoto", "Zhang",
]
