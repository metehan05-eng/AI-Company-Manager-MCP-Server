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
