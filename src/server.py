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
def read_company_file(filename: str) -> dict[str, Any]:
    """Read a supported company TXT, MD, JSON, CSV, XLSX, PDF, or DOCX file as text."""
    relative_path, path = file_handler.resolve_path(filename)
    content = file_handler.read_file(filename)
    return {
        "filename": relative_path.as_posix(),
        "format": relative_path.suffix.lower().lstrip("."),
        "size_bytes": path.stat().st_size,
        "content": content,
    }


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


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
