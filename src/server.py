from __future__ import annotations

import functools
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import Field, ValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.company_wizard import CompanyWizard
from src.file_handler import FileHandler

mcp = FastMCP("AI Company Manager")
file_handler = FileHandler()
company_wizard = CompanyWizard(file_handler)

NonNegativeAmount = Annotated[float, Field(ge=0, allow_inf_nan=False)]


def _format_validation_error(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in item['loc']) or 'input'}: {item['msg']}"
        f" (received: {item.get('input')!r})"
        for item in error.errors()
    )


def readable_errors(
    func: Callable[..., Any],
) -> Callable[..., Any]:
    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except ValidationError as exc:
            raise ValueError(_format_validation_error(exc)) from exc

    return wrapper


@mcp.tool()
@readable_errors
def list_company_files() -> dict[str, Any]:
    """List all files in company_data with type, byte size, and modification time."""
    files = file_handler.list_files()
    return {
        "data_directory": str(file_handler.data_dir),
        "count": len(files),
        "files": files,
    }


@mcp.tool()
@readable_errors
def get_company_overview() -> dict[str, Any]:
    """Return a current company profile and financial summary from local files."""
    return company_wizard.get_company_overview()


@mcp.tool()
@readable_errors
def get_financial_report(
    period: Annotated[str | None, Field(min_length=1, max_length=100)] = None,
) -> dict[str, Any]:
    """Report ledger totals, income and expense splits by category, and cash flow periods."""
    return company_wizard.get_financial_report(period)


@mcp.tool()
@readable_errors
def read_company_file(
    filename: str,
    start_char: Annotated[int, Field(ge=0)] = 0,
    max_chars: Annotated[int | None, Field(ge=0)] = None,
    pages: Annotated[str | None, Field(min_length=1, max_length=100)] = None,
) -> dict[str, Any]:
    """Read a supported company TXT, MD, JSON, CSV, XLSX, PDF, or DOCX file as text.

    Output is capped so a large file cannot flood the context. Read `total_chars` and
    `next_start_char` from the response and pass `next_start_char` back as `start_char`
    to continue. `pages` narrows a PDF to a range such as "1-5" or "2,7-9".
    """
    return file_handler.read_report(
        filename,
        start_char=start_char,
        max_chars=max_chars,
        pages=pages,
    )


@mcp.tool()
@readable_errors
def create_new_company(
    company_name: Annotated[str, Field(min_length=1, max_length=200)],
    sector: Annotated[str, Field(min_length=1, max_length=200)],
    initial_budget: NonNegativeAmount,
) -> dict[str, Any]:
    """Create a new company profile, financial ledger, and founder employee record."""
    return company_wizard.init_company(company_name, sector, initial_budget)


@mcp.tool()
@readable_errors
def add_financial_record(
    type: Literal["income", "expense"],
    category: Annotated[str, Field(min_length=1, max_length=200)],
    amount: Annotated[float, Field(gt=0, allow_inf_nan=False)],
    description: Annotated[str, Field(min_length=1, max_length=2_000)],
) -> dict[str, Any]:
    """Append an income or expense transaction to financials.json."""
    return company_wizard.add_financial_record(
        type,
        category,
        amount,
        description,
    )


@mcp.tool()
@readable_errors
def add_employee(
    name: Annotated[str, Field(min_length=1, max_length=200)],
    role: Annotated[str, Field(min_length=1, max_length=200)],
    department: Annotated[str, Field(min_length=1, max_length=200)],
    salary: NonNegativeAmount,
) -> dict[str, Any]:
    """Append a validated employee record to employees.csv."""
    return company_wizard.add_employee(name, role, department, salary)


@mcp.tool()
@readable_errors
def inspect_company_data() -> dict[str, Any]:
    """Check whether the files in company_data match the schema the tools expect."""
    return company_wizard.inspect_data_schema()


@mcp.tool()
@readable_errors
def migrate_company_data(apply: bool = False) -> dict[str, Any]:
    """Convert an existing employees.csv to the current column schema.

    Runs as a dry run by default. With apply=True it renames the known columns,
    fills in start_date/status defaults, keeps unknown columns such as
    performance_score, and writes a timestamped backup first.
    """
    return company_wizard.migrate_company_data(apply=apply)


@mcp.tool()
@readable_errors
def plan_company_data_migration() -> dict[str, Any]:
    """Propose how to map an existing company_profile.json and financials.json.

    Read-only: nothing is written. Reports which source fields are already valid,
    which ones can be mapped and how, which information would be dropped, and the
    questions that must be answered before the data can be migrated.
    """
    return company_wizard.plan_company_data_migration()


@mcp.tool()
@readable_errors
def apply_company_data_migration(
    answers: dict[str, Any] | None = None,
    apply: bool = False,
) -> dict[str, Any]:
    """Write a confirmed company_profile.json and financials.json mapping.

    Pass one answer per open item reported by plan_company_data_migration, keyed by
    its key (for example "company_profile.json:mission"). An answer of true accepts
    the proposed value; any other answer is used as the final value. Runs as a dry
    run unless apply=True, and backs up each changed file first.
    """
    return company_wizard.apply_company_data_migration(answers=answers, apply=apply)


@mcp.tool()
@readable_errors
def update_company_notes(
    note_title: Annotated[str, Field(min_length=1, max_length=200)],
    content: Annotated[str, Field(min_length=1, max_length=100_000)],
) -> dict[str, Any]:
    """Create or update a company policy, meeting, strategy, or vision note."""
    return company_wizard.update_company_notes(note_title, content)


@mcp.tool()
@readable_errors
def list_audit_entries(
    action: Annotated[
        str | None,
        Field(
            min_length=1,
            max_length=100,
            description="Only entries with this action, for example 'financial_record_added'.",
        ),
    ] = None,
    limit: Annotated[int, Field(ge=1, le=500)] = 50,
    offset: Annotated[int, Field(ge=0)] = 0,
) -> dict[str, Any]:
    """Read the append-only change journal: who changed what, newest first.

    Every mutating tool records one entry here before its data is written, and a failed
    write removes the entry again. Page with limit/offset; entries carry a gapless `seq`
    number, so a missing sequence means an entry was deleted.
    """
    return company_wizard.list_audit_entries(action=action, limit=limit, offset=offset)


@mcp.tool()
@readable_errors
def get_variance_report(
    month: Annotated[
        str | None,
        Field(
            max_length=7,
            description="Narrow the monthly table to one month, formatted as YYYY-MM.",
        ),
    ] = None,
) -> dict[str, Any]:
    """Compare spending against the budget and each month against the previous month.

    Reports budget usage, monthly income/expense/net totals and how much each month
    moved against the one before it. Read-only; nothing is written.
    """
    return company_wizard.get_variance_report(month=month)


@mcp.tool()
@readable_errors
def run_scenario(
    horizon_months: Annotated[int, Field(ge=1, le=120)] = 12,
    income_change_percent: Annotated[float, Field(ge=-100, le=10_000)] = 0.0,
    expense_change_percent: Annotated[float, Field(ge=-100, le=10_000)] = 0.0,
    category_changes: Annotated[
        dict[str, float] | None,
        Field(
            description=(
                "Extra monthly amount per expense category, for example "
                '{"payroll": 50000} to add 50,000 to payroll every month.'
            )
        ),
    ] = None,
    one_time_expense: Annotated[
        NonNegativeAmount, Field(description="Charged once, in month 1.")
    ] = 0.0,
) -> dict[str, Any]:
    """Project the balance forward under stated assumptions. A simulation only: nothing is saved.

    The baseline is the historical monthly average. Output includes every projected month,
    the closing balance and `runway_months` - the month the balance first turns negative,
    or null when it never does inside the horizon.
    """
    return company_wizard.run_scenario(
        horizon_months=horizon_months,
        income_change_percent=income_change_percent,
        expense_change_percent=expense_change_percent,
        category_changes=category_changes,
        one_time_expense=one_time_expense,
    )


@mcp.tool()
@readable_errors
def list_risks(
    status: Annotated[
        str | None,
        Field(description="Only risks in this state: 'open', 'mitigating' or 'closed'."),
    ] = None,
    category: Annotated[str | None, Field(max_length=100)] = None,
    sort: Annotated[
        str,
        Field(description="Order by 'score' (default, highest first), 'created' or 'title'."),
    ] = "score",
) -> dict[str, Any]:
    """List registered risks with their severity score and level. Read-only."""
    return company_wizard.list_risks(status=status, category=category, sort=sort)


@mcp.tool()
@readable_errors
def add_risk(
    title: Annotated[str, Field(min_length=1, max_length=200)],
    likelihood: Annotated[str, Field(description="How likely the risk is: low, medium or high.")],
    impact: Annotated[str, Field(description="How damaging it would be: low, medium or high.")],
    description: Annotated[str, Field(max_length=4_000)] = "",
    category: Annotated[str, Field(min_length=1, max_length=100)] = "general",
    owner: Annotated[str, Field(max_length=200)] = "",
    mitigation: Annotated[str, Field(max_length=4_000)] = "",
) -> dict[str, Any]:
    """Register a risk. Likelihood times impact gives a 1-9 score and a severity level."""
    return company_wizard.add_risk(
        title,
        likelihood,
        impact,
        description=description,
        category=category,
        owner=owner,
        mitigation=mitigation,
    )


@mcp.tool()
@readable_errors
def update_risk(
    risk_id: Annotated[str, Field(min_length=1, max_length=100)],
    changes: Annotated[
        dict[str, str],
        Field(
            description=(
                "Fields to change: title, description, category, owner, mitigation, "
                "likelihood, impact or status."
            )
        ),
    ],
) -> dict[str, Any]:
    """Change parts of an existing risk. Omitted fields keep their current value."""
    return company_wizard.update_risk(risk_id, changes)


@mcp.tool()
@readable_errors
def delete_risk(risk_id: Annotated[str, Field(min_length=1, max_length=100)]) -> dict[str, Any]:
    """Remove a risk from the register. The deleted risk stays in the audit journal."""
    return company_wizard.delete_risk(risk_id)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
