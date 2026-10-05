from __future__ import annotations

import contextlib
import json
import re
import threading
from collections.abc import Callable
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Literal
from uuid import uuid4

import pandas as pd
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)

from src.file_handler import FileHandler

CompanyStatus = Literal["template", "active"]
FinancialType = Literal["income", "expense"]

DEFAULT_DEPARTMENTS = [
    "Genel Yönetim",
    "Finans",
    "Operasyon",
    "İnsan Kaynakları",
    "Satış ve Pazarlama",
    "Bilgi Teknolojileri",
]
DEFAULT_REVENUE_CATEGORIES = ["sales", "services", "other_income"]
DEFAULT_EXPENSE_CATEGORIES = [
    "payroll",
    "rent",
    "utilities",
    "software",
    "marketing",
    "travel",
    "tax",
    "other_expense",
]
EMPLOYEE_COLUMNS = [
    "employee_id",
    "name",
    "role",
    "department",
    "salary",
    "start_date",
    "status",
]

EMPLOYEE_COLUMN_ALIASES = {
    "id": "employee_id",
    "employeeid": "employee_id",
    "employee_code": "employee_id",
    "code": "employee_id",
    "no": "employee_id",
    "full_name": "name",
    "fullname": "name",
    "ad_soyad": "name",
    "employee_name": "name",
    "personel_adi": "name",
    "title": "role",
    "position": "role",
    "gorev": "role",
    "unite": "department",
    "dept": "department",
    "team": "department",
    "bolum": "department",
    "salary_usd": "salary",
    "monthly_salary": "salary",
    "monthly_salary_usd": "salary",
    "maas": "salary",
    "ucret": "salary",
    "start": "start_date",
    "hire_date": "start_date",
    "hired_at": "start_date",
    "ise_giris": "start_date",
    "employment_status": "status",
    "state": "status",
    "durum": "status",
}

REQUIRED_EMPLOYEE_COLUMNS = ["name", "role", "department", "salary"]
DEFAULTED_EMPLOYEE_COLUMNS = ["employee_id", "start_date", "status"]

# Read-only mapping proposals for real-world profile/financial variants. Every rule
# is a (target_field, transform) pair; a transform returns a note whenever it has to
# assume or drop information, which downgrades the proposal to "needs_confirmation".
PROFILE_FIELD_RULES: dict[str, tuple[str, str]] = {
    "company_name": ("company_name", "copy"),
    "legal_name": ("company_name", "copy"),
    "name": ("company_name", "copy"),
    "sector": ("sector", "copy"),
    "industry": ("sector", "copy"),
    "founded_year": ("established_date", "year_to_date"),
    "establishment_year": ("established_date", "year_to_date"),
    "founded_at": ("established_date", "iso_date"),
    "founded": ("established_date", "iso_date"),
    "established_at": ("established_date", "iso_date"),
    "vision": ("vision", "copy"),
    "mission": ("mission", "copy"),
    "purpose": ("mission", "copy"),
    "goals": ("mission", "copy"),
    "departments": ("departments", "department_names"),
    "teams": ("departments", "department_names"),
    "currency": ("currency", "currency_code"),
    "status": ("status", "lifecycle_status"),
    "created_at": ("created_at", "copy"),
    "updated_at": ("updated_at", "copy"),
}

FINANCIAL_FIELD_RULES: dict[str, tuple[str, str]] = {
    "currency": ("currency", "currency_code"),
    "company_name": ("company_name", "copy"),
    "initial_budget": ("initial_budget", "money"),
    "budget": ("initial_budget", "money"),
    "initial_capital": ("initial_budget", "money"),
    "opening_balance": ("opening_balance", "money"),
    "bank_balance": ("opening_balance", "money"),
    "cash_balance": ("opening_balance", "money"),
    "current_balance": ("opening_balance", "money"),
    "revenue_categories": ("revenue_categories", "category_names"),
    "expense_categories": ("expense_categories", "category_names"),
    "records": ("records", "ledger_records"),
    "transactions": ("records", "ledger_records"),
    "revenue_breakdown_monthly": ("records", "monthly_records"),
    "monthly_breakdown": ("records", "monthly_records"),
    "monthly_financials": ("records", "monthly_records"),
    "status": ("status", "lifecycle_status"),
    "created_at": ("created_at", "copy"),
    "updated_at": ("updated_at", "copy"),
}

# Fields that look like they belong somewhere but must not be converted silently.
UNMAPPED_FIELD_NOTES: dict[str, str] = {
    "headquarters": "The canonical profile has no address field; keep it in a note.",
    "tax_id": "The canonical profile has no tax identifier field; keep it in a note.",
    "bank_accounts": "The canonical ledger stores no bank account numbers; keep them outside.",
    "metrics": "Aggregate metrics have no canonical field; keep them in a company note.",
    "monthly_runway_months": "Runway is a derived metric, not a ledger field.",
    "fiscal_year": "The canonical ledger has no fiscal calendar field.",
    "quarter": "The canonical ledger has no fiscal calendar field.",
    "major_expense_categories": (
        "These are percentage shares, not amounts. They cannot become expense "
        "categories or records without choosing real amounts first."
    ),
    "pending_invoices_receivable": (
        "Receivables are not collected yet, so converting them would overstate "
        "income. Add them as income records only after they are received."
    ),
    "tags": "No canonical field; keep it in a note.",
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


def _clean_required_text(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _normalize_currency(value: str) -> str:
    currency = _clean_required_text(value, "currency").upper()
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise ValueError("currency must be a three-letter ISO 4217 code")
    return currency


def _normalize_departments(value: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for department in value:
        clean_department = _clean_required_text(department, "department")
        key = clean_department.casefold()
        if key not in seen:
            normalized.append(clean_department)
            seen.add(key)
    if not normalized:
        raise ValueError("at least one department is required")
    return normalized


def _normalize_categories(value: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for category in value:
        clean_category = _clean_required_text(category, "category")
        key = clean_category.casefold()
        if key not in seen:
            normalized.append(clean_category)
            seen.add(key)
    if not normalized:
        raise ValueError("at least one category is required")
    return normalized


def _normalize_column_key(column: object) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(column).strip().lower()).strip("_")


def _resolve_employee_columns(columns: list[object]) -> dict[object, str]:
    """Map incoming CSV headers onto the canonical employee schema.

    Unrecognized columns are intentionally left out of the mapping so that
    caller-defined data such as `performance_score` survives a read/write cycle.
    """
    resolved: dict[object, str] = {}
    for column in columns:
        key = _normalize_column_key(column)
        if key in EMPLOYEE_COLUMNS:
            resolved[column] = key
            continue
        alias = EMPLOYEE_COLUMN_ALIASES.get(key)
        if alias is not None and alias not in resolved.values():
            resolved[column] = alias
    return resolved


def _normalize_employee_id(value: object) -> str:
    """Return a usable EMP id for one cell, or an empty string when it has none."""
    text = str(value).strip()
    if not text:
        return ""
    if re.fullmatch(r"EMP-\d{4,}", text):
        return text
    digits = re.findall(r"\d+", text)
    if digits:
        return f"EMP-{int(digits[-1]):04d}"
    return ""


def _assign_employee_ids(values: pd.Series) -> pd.Series:
    """Normalize the id column and give every row without one the next free EMP number.

    Ids are derived from the row order and never from a hash, so migrating the same
    file twice produces the same file.
    """
    normalized = [_normalize_employee_id(value) for value in values]
    used = {value for value in normalized if value}
    filled: list[str] = []
    next_number = 1
    for value in normalized:
        if value:
            filled.append(value)
            continue
        while f"EMP-{next_number:04d}" in used:
            next_number += 1
        assigned = f"EMP-{next_number:04d}"
        used.add(assigned)
        filled.append(assigned)
    return pd.Series(filled, index=values.index, dtype=object)


def _default_employee_start_date(value: object) -> str:
    text = str(value).strip()
    if not text:
        return _utc_today().isoformat()
    for pattern in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, pattern).date().isoformat()
        except ValueError:
            continue
    return _utc_today().isoformat()


def _default_employee_status(value: object) -> str:
    text = str(value).strip().casefold()
    if text in {"inactive", "pasif", "left", "terminated", "false", "0", "no"}:
        return "inactive"
    return "active"


def _normalize_money(value: float | str, field_name: str) -> Decimal:
    try:
        amount = Decimal(str(value).strip())
    except ArithmeticError as exc:
        raise ValueError(f"{field_name} must be a valid number") from exc
    if not amount.is_finite():
        raise ValueError(f"{field_name} must be a finite number")
    exponent = amount.as_tuple().exponent
    if isinstance(exponent, int) and exponent < -2:
        raise ValueError(f"{field_name} must have at most 2 decimal places (received {value})")
    return amount


def _transform_copy(value: Any) -> tuple[Any, str | None]:
    return value, None


def _transform_money(value: Any) -> tuple[Any, str | None]:
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise ValueError(f"expected a number, received {value!r}")
    _normalize_money(str(value), "amount")
    return value, None


def _transform_currency_code(value: Any) -> tuple[Any, str | None]:
    text = str(value).strip().upper()
    if text == str(value).strip():
        return text, None
    return text, f"Currency code was upper-cased from {value!r} to {text!r}."


def _transform_year_to_date(value: Any) -> tuple[Any, str | None]:
    text = str(value).strip()
    if not re.fullmatch(r"(1[89]|20)\d{2}", text):
        raise ValueError(f"expected a four-digit year, received {value!r}")
    return (
        f"{text}-01-01",
        f"The canonical field needs a full date, so {text}-01-01 was assumed. "
        "Confirm the real founding month and day.",
    )


def _transform_iso_date(value: Any) -> tuple[Any, str | None]:
    text = str(value).strip()
    match = re.match(r"^(\d{4}-\d{2}-\d{2})", text)
    if match is None:
        raise ValueError(f"expected an ISO date, received {value!r}")
    if match.group(1) == text:
        return text, None
    return match.group(1), f"The time part of {text!r} was dropped; the field only stores a date."


def _transform_lifecycle_status(value: Any) -> tuple[Any, str | None]:
    text = str(value).strip()
    folded = text.casefold()
    for canonical in ("template", "active"):
        if folded == canonical:
            return canonical, None
    for canonical in ("template", "active"):
        if canonical in folded:
            return canonical, (
                f"The canonical status only accepts 'template' or 'active', so {text!r} "
                f"was read as {canonical!r}. Move the extra wording into a note if needed."
            )
    raise ValueError(f"expected 'template' or 'active', received {value!r}")


def _transform_department_names(value: Any) -> tuple[Any, str | None]:
    if not isinstance(value, list) or not value:
        raise ValueError("expected a non-empty list of departments")
    names: list[str] = []
    dropped: set[str] = set()
    for item in value:
        if isinstance(item, str) and item.strip():
            names.append(item.strip())
        elif isinstance(item, dict) and isinstance(item.get("name"), str):
            names.append(item["name"].strip())
            dropped.update(key for key in item if key != "name")
        else:
            raise ValueError("expected a string or an object with a 'name' field")
    if not names:
        raise ValueError("no department name could be read")
    if not dropped:
        return names, None
    return names, (
        "The canonical departments list holds names only, so these keys are dropped: "
        f"{', '.join(sorted(dropped))}."
    )


def _transform_category_names(value: Any) -> tuple[Any, str | None]:
    if isinstance(value, dict):
        return _transform_department_names(list(value))
    if isinstance(value, list):
        return _transform_department_names(value)
    raise ValueError("expected a list or object of category names")


def _transform_ledger_records(value: Any) -> tuple[Any, str | None]:
    if not isinstance(value, list):
        raise ValueError("expected a list of transactions")
    records: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict) or "amount" not in item:
            raise ValueError("every transaction needs an 'amount' field")
        kind = str(item.get("type", "")).strip().casefold()
        if kind not in {"income", "expense"}:
            raise ValueError("every transaction needs 'type' set to income or expense")
        records.append(
            {
                "type": kind,
                "category": str(item.get("category", "")).strip(),
                "amount": item["amount"],
                "description": str(item.get("description", "")).strip(),
            }
        )
    return records, "Each transaction is re-created with a new id and the current timestamp."


def _transform_monthly_records(value: Any) -> tuple[Any, str | None]:
    """Expand a monthly breakdown into canonical income and expense records."""
    if not isinstance(value, list) or not value:
        raise ValueError("expected a non-empty list of monthly entries")
    records: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("expected an object per month")
        period = str(item.get("month") or item.get("period") or "").strip()
        if not period:
            raise ValueError("every monthly entry needs a 'month' or 'period' label")
        income = _first_present(item, ("mrr", "revenue", "income"))
        expense = _first_present(item, ("expenses", "expense", "costs"))
        if income is None or expense is None:
            raise ValueError(
                f"month {period!r} needs both a revenue and an expense amount to be converted"
            )
        records.append(
            {
                "type": "income",
                "category": "sales",
                "amount": income,
                "description": f"Monthly revenue - {period}",
            }
        )
        records.append(
            {
                "type": "expense",
                "category": "other_expense",
                "amount": expense,
                "description": f"Monthly expenses - {period}",
            }
        )
    return records, (
        f"{len(value)} monthly rows become {len(records)} ledger records using the default "
        "categories 'sales' and 'other_expense'. Choose real categories per month, and "
        "confirm the amounts are already in the file's own currency."
    )


def _first_present(source: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if source.get(key) is not None:
            return source[key]
    return None


def _summarize_value(value: Any) -> Any:
    """Keep reports small: collapse long lists and drop generated identifiers."""
    if isinstance(value, list):
        if len(value) > 3:
            head = [_summarize_value(item) for item in value[:3]]
            return {"items": len(value), "first_items": head}
        return [_summarize_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _summarize_value(item) for key, item in value.items() if key != "id"}
    return value


def _field_accepts_value(model_type: type[BaseModel], field_name: str, value: Any) -> bool:
    field = model_type.model_fields[field_name]
    try:
        TypeAdapter(field.annotation).validate_python(value)
    except ValueError:
        return False
    return True


def _required_model_fields(model_type: type[BaseModel]) -> list[str]:
    return [name for name, field in model_type.model_fields.items() if field.is_required()]


def _describe_open_item(item: dict[str, Any]) -> str:
    if item["kind"] == "missing_required":
        return f"{item['key']} is required and no source field provides it. Answer it with a value."
    source = item.get("source_field")
    prefix = f"{source!r} -> {item['field']!r}: " if source else f"{item['field']!r}: "
    return f"{item['key']} needs confirmation. {prefix}{item['note']}"


def _category_breakdown(
    financials: CompanyFinancials, record_type: FinancialType
) -> list[dict[str, Any]]:
    """Group one kind of record by category, biggest amount first."""
    totals: dict[str, Decimal] = {}
    counts: dict[str, int] = {}
    for record in financials.records:
        if record.type != record_type:
            continue
        totals[record.category] = totals.get(record.category, Decimal("0.00")) + record.amount
        counts[record.category] = counts.get(record.category, 0) + 1

    grand_total = sum(totals.values(), Decimal("0.00"))
    rows: list[dict[str, Any]] = [
        {
            "category": category,
            "amount": amount,
            "record_count": counts[category],
            "share_percent": (
                float((amount / grand_total * 100).quantize(Decimal("0.1")))
                if grand_total
                else None
            ),
        }
        for category, amount in totals.items()
    ]
    rows.sort(key=lambda row: (-row["amount"], row["category"]))
    return rows


def _apply_transform(name: str, value: Any) -> tuple[Any, str | None]:
    transforms: dict[str, Callable[[Any], tuple[Any, str | None]]] = {
        "copy": _transform_copy,
        "money": _transform_money,
        "currency_code": _transform_currency_code,
        "year_to_date": _transform_year_to_date,
        "iso_date": _transform_iso_date,
        "lifecycle_status": _transform_lifecycle_status,
        "department_names": _transform_department_names,
        "category_names": _transform_category_names,
        "ledger_records": _transform_ledger_records,
        "monthly_records": _transform_monthly_records,
    }
    transform = transforms.get(name)
    if transform is None:
        raise ValueError(f"unknown transform {name!r}")
    return transform(value)


class CompanyProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    company_name: str = Field(min_length=1, max_length=200)
    sector: str = Field(min_length=1, max_length=200)
    established_date: date
    departments: list[str] = Field(default_factory=lambda: list(DEFAULT_DEPARTMENTS))
    vision: str = Field(min_length=1, max_length=4_000)
    mission: str = Field(min_length=1, max_length=4_000)
    currency: str = Field(default="TRY", min_length=3, max_length=3)
    status: CompanyStatus = "active"
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)

    @field_validator("company_name", "sector", "vision", "mission", mode="before")
    @classmethod
    def clean_text_fields(cls, value: str) -> str:
        return _clean_required_text(value, "company profile text")

    @field_validator("departments", mode="before")
    @classmethod
    def clean_departments(cls, value: list[str]) -> list[str]:
        return _normalize_departments(value)

    @field_validator("currency", mode="before")
    @classmethod
    def clean_currency(cls, value: str) -> str:
        return _normalize_currency(value)


class FinancialRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    id: str = Field(default_factory=lambda: str(uuid4()))
    type: FinancialType
    category: str = Field(min_length=1, max_length=200)
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    description: str = Field(min_length=1, max_length=2_000)
    recorded_at: datetime = Field(default_factory=_utc_now)

    @field_validator("category", "description", mode="before")
    @classmethod
    def clean_record_text(cls, value: str) -> str:
        return _clean_required_text(value, "financial record text")


class CashFlowTemplateEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    period: str = Field(min_length=1, max_length=100)
    opening_balance: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    net_cash_flow: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    closing_balance: Decimal = Field(ge=0, max_digits=18, decimal_places=2)

    @field_validator("period", mode="before")
    @classmethod
    def clean_period(cls, value: str) -> str:
        return value.strip()


class CompanyFinancials(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    company_name: str = Field(min_length=1, max_length=200)
    currency: str = Field(default="TRY", min_length=3, max_length=3)
    initial_budget: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    opening_balance: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    revenue_categories: list[str] = Field(default_factory=lambda: list(DEFAULT_REVENUE_CATEGORIES))
    expense_categories: list[str] = Field(default_factory=lambda: list(DEFAULT_EXPENSE_CATEGORIES))
    records: list[FinancialRecord] = Field(default_factory=list)
    cash_flow_template: list[CashFlowTemplateEntry] = Field(default_factory=list)
    status: CompanyStatus = "active"
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)

    @field_validator("company_name", mode="before")
    @classmethod
    def clean_company_name(cls, value: str) -> str:
        return _clean_required_text(value, "company_name")

    @field_validator("currency", mode="before")
    @classmethod
    def clean_currency(cls, value: str) -> str:
        return _normalize_currency(value)

    @field_validator("revenue_categories", "expense_categories", mode="before")
    @classmethod
    def clean_category_lists(cls, value: list[str]) -> list[str]:
        return _normalize_categories(value)

    @model_validator(mode="after")
    def ensure_cash_flow_template(self) -> CompanyFinancials:
        if not self.cash_flow_template:
            self.cash_flow_template = [
                CashFlowTemplateEntry(
                    period="initial",
                    opening_balance=self.opening_balance,
                    net_cash_flow=Decimal("0.00"),
                    closing_balance=self.opening_balance,
                )
            ]
        return self

    @property
    def total_income(self) -> Decimal:
        return sum(
            (record.amount for record in self.records if record.type == "income"),
            Decimal("0.00"),
        )

    @property
    def total_expenses(self) -> Decimal:
        return sum(
            (record.amount for record in self.records if record.type == "expense"),
            Decimal("0.00"),
        )

    @property
    def current_balance(self) -> Decimal:
        return self.opening_balance + self.total_income - self.total_expenses

    def financial_summary(self) -> dict[str, Any]:
        return {
            "initial_budget": self.initial_budget,
            "opening_balance": self.opening_balance,
            "total_income": self.total_income,
            "total_expenses": self.total_expenses,
            "net_cash_flow": self.total_income - self.total_expenses,
            "current_balance": self.current_balance,
            "transaction_count": len(self.records),
        }


class Employee(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    employee_id: str = Field(pattern=r"^EMP-\d{4,}$")
    name: str = Field(min_length=1, max_length=200)
    role: str = Field(min_length=1, max_length=200)
    department: str = Field(min_length=1, max_length=200)
    salary: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    start_date: date
    status: Literal["active", "inactive"] = "active"

    @field_validator("name", "role", "department", mode="before")
    @classmethod
    def clean_employee_text(cls, value: str) -> str:
        return _clean_required_text(value, "employee text")


class CompanyNote(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    id: str = Field(default_factory=lambda: str(uuid4()))
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=100_000)
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)

    @field_validator("title", "content", mode="before")
    @classmethod
    def clean_note_text(cls, value: str) -> str:
        return value.strip()


class CompanyNotes(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    notes: list[CompanyNote] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=_utc_now)


class CompanyWizard:
    def __init__(self, file_handler: FileHandler | None = None) -> None:
        self.file_handler = file_handler or FileHandler()
        self._lock = threading.RLock()

    def _write_model(self, filename: str, model: BaseModel) -> None:
        self.file_handler.write_file(
            filename,
            json.dumps(
                model.model_dump(mode="json"),
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            ),
        )

    def _read_model(self, filename: str, model_type: type[BaseModel]) -> Any:
        return model_type.model_validate_json(self.file_handler.read_file(filename))

    def _existing_template_can_be_replaced(self) -> bool:
        profile_path = self.file_handler.data_dir / "company_profile.json"
        financials_path = self.file_handler.data_dir / "financials.json"
        employees_path = self.file_handler.data_dir / "employees.csv"
        if employees_path.exists():
            return False
        existing_paths = [path for path in (profile_path, financials_path) if path.exists()]
        if not existing_paths:
            return True
        try:
            profile = self._read_model("company_profile.json", CompanyProfile)
            financials = self._read_model("financials.json", CompanyFinancials)
        except (FileNotFoundError, ValueError):
            return False
        return (
            profile.status == "template"
            and financials.status == "template"
            and not financials.records
        )

    def init_company(self, company_name: str, sector: str, budget: float) -> dict[str, Any]:
        clean_company_name = _clean_required_text(company_name, "company_name")
        clean_sector = _clean_required_text(sector, "sector")
        timestamp = _utc_now()
        profile = CompanyProfile(
            company_name=clean_company_name,
            sector=clean_sector,
            established_date=_utc_today(),
            departments=list(DEFAULT_DEPARTMENTS),
            vision=(
                f"{clean_company_name} sektöründe ölçeklenebilir, sürdürülebilir ve "
                "değer üreten çözümler geliştirmek."
            ),
            mission=(
                f"{clean_company_name} müşterilerine güvenilir çözümler sunmak, "
                "çalışanlarını geliştirmek ve toplumsal değer yaratmak."
            ),
            currency="TRY",
            status="active",
            created_at=timestamp,
            updated_at=timestamp,
        )
        budget_value = _normalize_money(budget, "initial_budget")
        financials = CompanyFinancials(
            company_name=clean_company_name,
            currency="TRY",
            initial_budget=budget_value,
            opening_balance=budget_value,
            revenue_categories=list(DEFAULT_REVENUE_CATEGORIES),
            expense_categories=list(DEFAULT_EXPENSE_CATEGORIES),
            records=[],
            status="active",
            created_at=timestamp,
            updated_at=timestamp,
        )
        founder = Employee(
            employee_id="EMP-0001",
            name="Kurucu",
            role="CEO / Kurucu",
            department="Genel Yönetim",
            salary=Decimal("0.00"),
            start_date=_utc_today(),
            status="active",
        )
        employees = pd.DataFrame(
            [
                {
                    "employee_id": founder.employee_id,
                    "name": founder.name,
                    "role": founder.role,
                    "department": founder.department,
                    "salary": float(founder.salary),
                    "start_date": founder.start_date.isoformat(),
                    "status": founder.status,
                }
            ],
            columns=EMPLOYEE_COLUMNS,
        )

        core_files = ("company_profile.json", "financials.json", "employees.csv")
        with self._lock:
            if not self._existing_template_can_be_replaced():
                raise FileExistsError(
                    "company data already exists; move or remove the existing core files before initializing a company"
                )
            existing_contents: dict[str, str] = {}
            for filename in core_files:
                path = self.file_handler.data_dir / filename
                if path.exists():
                    existing_contents[filename] = path.read_text(encoding="utf-8-sig")
            try:
                self._write_model("company_profile.json", profile)
                self._write_model("financials.json", financials)
                self.file_handler.write_file("employees.csv", employees)
            except Exception:
                for filename in core_files:
                    if filename in existing_contents:
                        with contextlib.suppress(OSError, TypeError, ValueError):
                            self.file_handler.write_file(filename, existing_contents[filename])
                    else:
                        (self.file_handler.data_dir / filename).unlink(missing_ok=True)
                raise

        return {
            "message": "Company initialized successfully",
            "company": {
                "name": profile.company_name,
                "sector": profile.sector,
                "established_date": profile.established_date.isoformat(),
                "departments": profile.departments,
                "vision": profile.vision,
                "mission": profile.mission,
                "currency": profile.currency,
            },
            "financials": financials.financial_summary(),
            "created_files": list(core_files),
        }

    def add_financial_record(
        self,
        record_type: str,
        category: str,
        amount: float,
        description: str,
    ) -> dict[str, Any]:
        if not isinstance(record_type, str) or not record_type.strip():
            raise ValueError("type must be 'income' or 'expense'")
        normalized_value = record_type.strip().lower()
        normalized_type: FinancialType | None
        if normalized_value in {"income", "gelir"}:
            normalized_type = "income"
        elif normalized_value in {"expense", "gider"}:
            normalized_type = "expense"
        else:
            normalized_type = None
        if normalized_type is None:
            raise ValueError("type must be 'income' or 'expense'")

        record = FinancialRecord(
            type=normalized_type,
            category=category,
            amount=_normalize_money(amount, "amount"),
            description=description,
        )
        with self._lock:
            financials = self._read_model("financials.json", CompanyFinancials)
            if record.type == "income":
                categories = financials.revenue_categories
            else:
                categories = financials.expense_categories
            if not any(item.casefold() == record.category.casefold() for item in categories):
                categories.append(record.category)
            financials.records.append(record)
            financials.updated_at = _utc_now()
            self._write_model("financials.json", financials)

        return {
            "message": "Financial record added successfully",
            "record": record.model_dump(mode="json"),
            "financials": financials.financial_summary(),
        }

    def _read_employees(self) -> pd.DataFrame:
        try:
            frame = pd.read_csv(
                self.file_handler.data_dir / "employees.csv",
                dtype=object,
                keep_default_na=False,
            )
        except FileNotFoundError as exc:
            raise FileNotFoundError(
                "employees.csv was not found; initialize a company first"
            ) from exc

        mapping = _resolve_employee_columns(list(frame.columns))
        renamed = frame.rename(columns=mapping)
        extra_columns = [column for column in renamed.columns if column not in EMPLOYEE_COLUMNS]

        missing_columns = [
            column for column in REQUIRED_EMPLOYEE_COLUMNS if column not in renamed.columns
        ]
        if missing_columns:
            available = ", ".join(str(column) for column in frame.columns)
            raise ValueError(
                f"employees.csv is missing required columns: {', '.join(missing_columns)}. "
                f"Columns found: {available}. "
                f"Run migrate_company_data to convert an existing file to the current schema."
            )

        for defaulted in DEFAULTED_EMPLOYEE_COLUMNS:
            if defaulted not in renamed.columns:
                renamed[defaulted] = ""

        ordered = renamed[EMPLOYEE_COLUMNS + extra_columns].copy()
        ordered["start_date"] = ordered["start_date"].apply(_default_employee_start_date)
        ordered["status"] = ordered["status"].apply(_default_employee_status)
        ordered["employee_id"] = _assign_employee_ids(ordered["employee_id"])
        return ordered

    def add_employee(
        self,
        name: str,
        role: str,
        department: str,
        salary: float,
    ) -> dict[str, Any]:
        salary_value = _normalize_money(salary, "salary")
        with self._lock:
            frame = self._read_employees()
            numeric_ids = (
                frame["employee_id"]
                .astype(str)
                .str.extract(r"^EMP-(\d+)$", expand=False)
                .dropna()
                .astype(int)
            )
            next_id = int(numeric_ids.max()) + 1 if not numeric_ids.empty else 1
            employee = Employee(
                employee_id=f"EMP-{next_id:04d}",
                name=name,
                role=role,
                department=department,
                salary=salary_value,
                start_date=_utc_today(),
                status="active",
            )
            row: dict[str, Any] = employee.model_dump(mode="json")
            row["salary"] = float(salary_value)
            row["start_date"] = employee.start_date.isoformat()
            new_row = pd.DataFrame([row])
            for extra_column in frame.columns:
                if extra_column not in new_row.columns:
                    new_row[extra_column] = ""
            updated_frame = pd.concat(
                [frame, new_row[frame.columns]],
                ignore_index=True,
            )
            self.file_handler.write_file("employees.csv", updated_frame)

        return {
            "message": "Employee added successfully",
            "employee": employee.model_dump(mode="json"),
        }

    def inspect_data_schema(self) -> dict[str, Any]:
        """Report which company files are readable by the current schema and why not."""
        report: dict[str, Any] = {"data_directory": str(self.file_handler.data_dir), "files": {}}

        employees_path = self.file_handler.data_dir / "employees.csv"
        if employees_path.exists():
            try:
                frame = self._read_employees()
            except (FileNotFoundError, ValueError, OSError) as exc:
                report["files"]["employees.csv"] = {
                    "status": "incompatible",
                    "reason": str(exc),
                }
            else:
                extra = [column for column in frame.columns if column not in EMPLOYEE_COLUMNS]
                report["files"]["employees.csv"] = {
                    "status": "compatible",
                    "row_count": len(frame),
                    "columns": list(frame.columns),
                    "mapped_columns": {
                        str(column): canonical
                        for column, canonical in _resolve_employee_columns(
                            list(pd.read_csv(employees_path, nrows=0).columns)
                        ).items()
                    },
                    "preserved_extra_columns": extra,
                }
        else:
            report["files"]["employees.csv"] = {
                "status": "missing",
                "reason": "employees.csv was not found; initialize a company first",
            }

        for filename, model_type in (
            ("company_profile.json", CompanyProfile),
            ("financials.json", CompanyFinancials),
        ):
            if not (self.file_handler.data_dir / filename).exists():
                report["files"][filename] = {
                    "status": "missing",
                    "reason": f"{filename} was not found; initialize a company first",
                }
                continue
            try:
                self._read_model(filename, model_type)
            except (FileNotFoundError, ValueError) as exc:
                report["files"][filename] = {
                    "status": "incompatible",
                    "reason": str(exc).splitlines()[0],
                }
            else:
                report["files"][filename] = {"status": "compatible"}

        incompatible = [
            name for name, info in report["files"].items() if info["status"] != "compatible"
        ]
        report["ready_for_tools"] = not incompatible
        report["blocking_files"] = incompatible
        return report

    def migrate_company_data(self, apply: bool = False) -> dict[str, Any]:
        """Convert employees.csv to the canonical schema, with a timestamped backup.

        The call is a dry run unless `apply` is True. Unknown columns are preserved
        so caller-defined data is never discarded.
        """
        employees_path = self.file_handler.data_dir / "employees.csv"
        if not employees_path.exists():
            raise FileNotFoundError("employees.csv was not found; initialize a company first")

        try:
            raw = pd.read_csv(employees_path, dtype=object, keep_default_na=False)
        except (ValueError, OSError) as exc:
            raise ValueError(f"employees.csv could not be parsed: {exc}") from exc

        original_columns = [str(column) for column in raw.columns]
        mapping = _resolve_employee_columns(list(raw.columns))
        renamed = raw.rename(columns=mapping)
        extra_columns = [
            column
            for column in renamed.columns
            if column not in EMPLOYEE_COLUMNS and column not in DEFAULTED_EMPLOYEE_COLUMNS
        ]
        missing_columns = [
            column for column in REQUIRED_EMPLOYEE_COLUMNS if column not in renamed.columns
        ]

        if missing_columns:
            raise ValueError(
                f"employees.csv cannot be migrated automatically: missing "
                f"{', '.join(missing_columns)}. Add or rename those columns and retry. "
                f"Columns found: {', '.join(original_columns)}"
            )

        for defaulted in DEFAULTED_EMPLOYEE_COLUMNS:
            if defaulted not in renamed.columns:
                renamed[defaulted] = ""

        columns_need_renaming = any(
            str(column) != canonical_name for column, canonical_name in mapping.items()
        )
        missing_defaults = [
            column for column in DEFAULTED_EMPLOYEE_COLUMNS if column not in raw.columns
        ]

        if not columns_need_renaming and not extra_columns and not missing_defaults:
            return {
                "message": "employees.csv already uses the current schema; no changes needed",
                "changed": False,
                "row_count": len(raw),
                "columns": original_columns,
            }

        canonical = renamed[EMPLOYEE_COLUMNS + extra_columns].copy()
        canonical["start_date"] = canonical["start_date"].apply(_default_employee_start_date)
        canonical["status"] = canonical["status"].apply(_default_employee_status)
        canonical["employee_id"] = _assign_employee_ids(canonical["employee_id"])

        result: dict[str, Any] = {
            "changed": True,
            "applied": apply,
            "row_count": len(canonical),
            "columns_before": original_columns,
            "columns_after": [str(column) for column in canonical.columns],
            "renamed_columns": {
                str(column): canonical_name for column, canonical_name in mapping.items()
            },
            "preserved_extra_columns": [str(column) for column in extra_columns],
        }

        if not apply:
            result["message"] = (
                "Dry run only. Re-run with apply=True to write the migrated file. "
                "The original file is backed up before writing."
            )
            return result

        stamp = _utc_now().strftime("%Y%m%dT%H%M%SZ")
        backup_name = f"employees.backup-{stamp}.csv"
        self.file_handler.write_file(backup_name, raw)
        self.file_handler.write_file("employees.csv", canonical)
        result["applied"] = True
        result["backup_file"] = backup_name
        result["message"] = "employees.csv migrated successfully"
        return result

    def _plan_json_file(
        self,
        filename: str,
        model_type: type[BaseModel],
        rules: dict[str, tuple[str, str]],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        path = self.file_handler.data_dir / filename
        if not path.exists():
            return (
                {
                    "filename": filename,
                    "status": "missing",
                    "reason": f"{filename} was not found; initialize a company first",
                    "_open_items": [],
                    "questions": [],
                },
                {},
            )

        try:
            source = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
            return (
                {
                    "filename": filename,
                    "status": "unreadable",
                    "reason": f"{filename} is not valid JSON: {exc}",
                    "_open_items": [],
                    "questions": [f"Repair the JSON syntax in {filename} before mapping it."],
                },
                {},
            )
        if not isinstance(source, dict):
            return (
                {
                    "filename": filename,
                    "status": "unreadable",
                    "reason": f"{filename} must contain a JSON object at the top level",
                    "_open_items": [],
                    "questions": [f"Restructure {filename} as a single JSON object."],
                },
                {},
            )

        already_valid: dict[str, Any] = {}
        mappable: list[dict[str, Any]] = []
        skipped_conflicts: list[dict[str, Any]] = []
        incompatible_values: list[dict[str, Any]] = []
        no_target: dict[str, str | None] = {}
        candidate: dict[str, Any] = {}

        for raw_field, value in source.items():
            field = str(raw_field)
            key = _normalize_column_key(raw_field)
            if key in model_type.model_fields and _field_accepts_value(model_type, key, value):
                already_valid[key] = value
                candidate.setdefault(key, value)
                continue

            rule = rules.get(key)
            if rule is None:
                if key in model_type.model_fields:
                    incompatible_values.append(
                        {
                            "field": field,
                            "reason": f"the value for {field!r} is not accepted by {key!r}",
                        }
                    )
                else:
                    no_target[field] = UNMAPPED_FIELD_NOTES.get(key)
                continue

            target, transform = rule
            if target in candidate:
                skipped_conflicts.append(
                    {
                        "field": field,
                        "target_field": target,
                        "note": f"{field!r} also targets {target!r}, which is already filled.",
                    }
                )
                continue

            try:
                proposed, note = _apply_transform(transform, value)
            except ValueError as exc:
                incompatible_values.append({"field": field, "reason": str(exc)})
                continue

            candidate[target] = proposed
            mappable.append(
                {
                    "source_field": field,
                    "target_field": target,
                    "transform": transform,
                    "confidence": "needs_confirmation" if note else "auto",
                    "proposed_value": _summarize_value(proposed),
                    "note": note,
                }
            )

        missing_required = [
            field for field in _required_model_fields(model_type) if field not in candidate
        ]

        open_items: list[dict[str, Any]] = [
            {
                "key": f"{filename}:{field}",
                "kind": "missing_required",
                "field": field,
                "note": "This field is required and no source field provides it.",
            }
            for field in missing_required
        ]
        open_items.extend(
            {
                "key": f"{filename}:{item['target_field']}",
                "kind": "confirmation",
                "field": item["target_field"],
                "source_field": item["source_field"],
                "note": item["note"],
            }
            for item in mappable
            if item["confidence"] == "needs_confirmation"
        )

        plan: dict[str, Any] = {
            "status": "needs_decisions",
            "source_fields": [str(raw_field) for raw_field in source],
            "already_valid": already_valid,
            "mappable": mappable,
            "skipped_conflicts": skipped_conflicts,
            "incompatible_values": incompatible_values,
            "no_target": no_target,
            "missing_required": missing_required,
            "_open_items": open_items,
        }
        self._finalize_plan(plan, candidate, filename, model_type)
        return plan, candidate

    def _finalize_plan(
        self,
        plan: dict[str, Any],
        candidate: dict[str, Any],
        filename: str,
        model_type: type[BaseModel],
    ) -> None:
        """Recompute the derived fields of a plan after its open items changed."""
        open_items = [
            {**item, "proposed_value": _summarize_value(candidate.get(item["field"]))}
            for item in plan["_open_items"]
        ]
        plan["_open_items"] = open_items
        plan["filename"] = filename
        plan["open_items"] = open_items
        questions = [_describe_open_item(item) for item in open_items]
        questions.extend(
            f"{item['field']!r} cannot be used as it is: {item['reason']}"
            for item in plan["incompatible_values"]
        )
        plan["questions"] = questions

        validation_error: str | None = None
        validated: BaseModel | None = None
        try:
            validated = model_type.model_validate(candidate)
        except ValueError as exc:
            validation_error = str(exc).splitlines()[0]

        if validation_error is None and not questions:
            # Only hand over a ready-to-use document when nothing is left to decide,
            # so it can never be copied into place without being read first.
            plan["status"] = "compatible"
            plan["proposed_document"] = validated.model_dump(mode="json")  # type: ignore[union-attr]
            plan.pop("blocked_by", None)
        else:
            plan["status"] = "needs_decisions"
            plan["blocked_by"] = questions or [
                f"the proposed document is invalid: {validation_error}"
            ]
            plan.pop("proposed_document", None)

    def _build_migration_plan(
        self,
    ) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
        """Build the read-only plan plus the internal candidate and open-item state."""
        report: dict[str, Any] = {
            "data_directory": str(self.file_handler.data_dir),
            "writes_performed": False,
        }
        profile_plan, profile_candidate = self._plan_json_file(
            "company_profile.json", CompanyProfile, PROFILE_FIELD_RULES
        )
        financial_plan, financial_candidate = self._plan_json_file(
            "financials.json", CompanyFinancials, FINANCIAL_FIELD_RULES
        )
        report["files"] = {
            "company_profile.json": profile_plan,
            "financials.json": financial_plan,
        }

        report["cross_file_questions"] = self._resolve_cross_file_items(
            profile_plan, profile_candidate, financial_plan, financial_candidate
        )
        report["answer_keys"] = sorted(
            {item["key"] for plan in (profile_plan, financial_plan) for item in plan["_open_items"]}
        )
        report["blocking_files"] = [
            name for name, plan in report["files"].items() if plan["status"] != "compatible"
        ]
        report["next_step"] = (
            "Nothing was written. Resolve every entry in open_items by calling "
            "apply_company_data_migration with an answers object keyed by answer_keys, or edit "
            "the two files by hand. employees.csv is handled separately by "
            "migrate_company_data."
        )
        candidates = {
            "company_profile.json": profile_candidate,
            "financials.json": financial_candidate,
        }
        open_items = {
            "company_profile.json": profile_plan["_open_items"],
            "financials.json": financial_plan["_open_items"],
        }
        for plan in report["files"].values():
            plan.pop("_open_items", None)
        return report, candidates, open_items

    def plan_company_data_migration(self) -> dict[str, Any]:
        """Propose a field mapping for files that do not match the canonical schema.

        This never writes. The report is meant to be read by a person who then
        decides how to map the data, because real-world variants carry information
        the canonical models cannot represent.
        """
        return self._build_migration_plan()[0]

    def _resolve_cross_file_items(
        self,
        profile_plan: dict[str, Any],
        profile_candidate: dict[str, Any],
        financial_plan: dict[str, Any],
        financial_candidate: dict[str, Any],
    ) -> list[str]:
        """Turn cross-file problems into answerable open items rather than plain text."""
        notes: list[str] = []
        unusable = {"missing", "unreadable"}
        profile_ok = profile_plan.get("status") not in unusable
        financial_ok = financial_plan.get("status") not in unusable

        profile_name = profile_candidate.get("company_name")
        if (
            profile_ok
            and financial_ok
            and "company_name" in financial_plan.get("missing_required", [])
            and profile_name
        ):
            financial_candidate["company_name"] = profile_name
            financial_plan["missing_required"] = [
                field for field in financial_plan["missing_required"] if field != "company_name"
            ]
            financial_plan["_open_items"] = [
                item for item in financial_plan["_open_items"] if item["field"] != "company_name"
            ]
            financial_plan["_open_items"].append(
                {
                    "key": "financials.json:company_name",
                    "kind": "confirmation",
                    "field": "company_name",
                    "note": (
                        f"financials.json has no company_name, so {profile_name!r} from "
                        "company_profile.json was proposed to keep both files in agreement."
                    ),
                }
            )
            self._finalize_plan(
                financial_plan, financial_candidate, "financials.json", CompanyFinancials
            )

        profile_currency = profile_candidate.get("currency")
        financial_currency = financial_candidate.get("currency")
        if (
            profile_ok
            and financial_ok
            and financial_currency
            and profile_currency != financial_currency
        ):
            if profile_currency is None:
                note = (
                    "company_profile.json has no currency field, so the canonical default "
                    f"'TRY' would be used while the ledger is {financial_currency!r}. The "
                    f"ledger currency {financial_currency!r} was proposed instead."
                )
            else:
                note = (
                    f"company_profile.json is {profile_currency!r} but financials.json is "
                    f"{financial_currency!r}. One currency has to be chosen and the amounts "
                    "confirmed to already use it; this server never applies an exchange rate."
                )
                notes.append(note)
            profile_candidate["currency"] = financial_currency
            profile_plan["_open_items"] = [
                item for item in profile_plan["_open_items"] if item["field"] != "currency"
            ]
            profile_plan["_open_items"].append(
                {
                    "key": "company_profile.json:currency",
                    "kind": "confirmation",
                    "field": "currency",
                    "note": note,
                }
            )
            self._finalize_plan(
                profile_plan, profile_candidate, "company_profile.json", CompanyProfile
            )
        return notes

    def apply_company_data_migration(
        self, answers: dict[str, Any] | None = None, apply: bool = False
    ) -> dict[str, Any]:
        """Write the confirmed company_profile.json and financials.json mapping.

        Every open item reported by plan_company_data_migration must be answered. An
        answer of `true` accepts the proposed value; any other answer is used as the
        final value. Nothing is written unless apply is True, and a timestamped backup
        of each changed file is written first.
        """
        given = {str(key): value for key, value in (answers or {}).items()}
        report, candidates, open_items = self._build_migration_plan()
        models: dict[str, type[BaseModel]] = {
            "company_profile.json": CompanyProfile,
            "financials.json": CompanyFinancials,
        }

        expected: dict[str, dict[str, Any]] = {}
        for filename in models:
            plan = report["files"][filename]
            if plan["status"] in {"missing", "unreadable"}:
                raise ValueError(
                    f"{filename} cannot be migrated: {plan.get('reason', plan['status'])}"
                )
            for item in open_items[filename]:
                expected[item["key"]] = item
            for field in plan.get("missing_required", []):
                key = f"{filename}:{field}"
                if key not in expected:
                    expected[key] = {
                        "kind": "missing_required",
                        "field": field,
                        "note": "This field is required and no source field provides it.",
                    }

        unknown = sorted(set(given) - set(expected))
        if unknown:
            raise ValueError(
                f"Unknown answer keys: {', '.join(unknown)}. Valid keys are: "
                f"{', '.join(sorted(expected)) or 'none'}"
            )
        unresolved = sorted(set(expected) - set(given))
        if unresolved:
            raise ValueError(
                f"{len(unresolved)} open item(s) still need an answer: {', '.join(unresolved)}. "
                "Run plan_company_data_migration to see what each one is."
            )
        unsuggested = sorted(
            key
            for key, answer in given.items()
            if answer is True and expected[key].get("proposed_value") is None
        )
        if unsuggested:
            raise ValueError(
                f"No suggested value for: {', '.join(unsuggested)}. "
                "Pass an explicit value instead of true for these keys."
            )

        documents: dict[str, dict[str, Any]] = {}
        for filename, model_type in models.items():
            candidate = dict(candidates[filename])
            for key, answer in given.items():
                if key.startswith(f"{filename}:") and answer is not True:
                    candidate[key.split(":", 1)[1]] = answer
            try:
                documents[filename] = model_type.model_validate(candidate).model_dump(mode="json")
            except ValueError as exc:
                raise ValueError(
                    f"{filename} is still invalid after applying the answers: "
                    f"{str(exc).splitlines()[0]}"
                ) from exc

        changed = [
            name
            for name, document in documents.items()
            if json.loads((self.file_handler.data_dir / name).read_text(encoding="utf-8"))
            != document
        ]

        result: dict[str, Any] = {
            "applied": apply,
            "changed": changed,
            "resolved": dict(sorted(given.items())),
            "backups": {},
        }

        if not apply:
            result["documents"] = documents
            result["message"] = (
                "Dry run only. Re-run with apply=True to write the files. Each changed file "
                "is backed up before it is replaced."
            )
            return result

        stamp = _utc_now().strftime("%Y%m%dT%H%M%SZ")
        backups: dict[str, str] = result["backups"]
        for name in changed:
            backup_name = name.replace(".json", f".backup-{stamp}.json")
            self.file_handler.write_file(
                backup_name, (self.file_handler.data_dir / name).read_text(encoding="utf-8")
            )
            backups[name] = backup_name
        for name, model_type in models.items():
            self._write_model(name, model_type.model_validate(documents[name]))
        result["backups"] = backups
        result["message"] = (
            "company_profile.json and financials.json migrated successfully"
            if changed
            else "Both files already matched; nothing was written."
        )
        return result

    def update_company_notes(self, note_title: str, content: str) -> dict[str, Any]:
        title = _clean_required_text(note_title, "note_title")
        clean_content = _clean_required_text(content, "content")
        with self._lock:
            try:
                notes = self._read_model("company_notes.json", CompanyNotes)
            except FileNotFoundError:
                notes = CompanyNotes()
            existing_note = next(
                (note for note in notes.notes if note.title.casefold() == title.casefold()),
                None,
            )
            if existing_note is None:
                existing_note = CompanyNote(title=title, content=clean_content)
                notes.notes.append(existing_note)
                action = "created"
            else:
                existing_note.content = clean_content
                existing_note.updated_at = _utc_now()
                action = "updated"
            notes.updated_at = _utc_now()
            self._write_model("company_notes.json", notes)

        return {
            "message": f"Company note {action} successfully",
            "action": action,
            "note": existing_note.model_dump(mode="json"),
        }

    def get_company_overview(self) -> dict[str, Any]:
        with self._lock:
            profile = self._read_model("company_profile.json", CompanyProfile)
            financials = self._read_model("financials.json", CompanyFinancials)
        if profile.company_name != financials.company_name:
            raise ValueError("company name mismatch between profile and financial files")
        return {
            "company": {
                "name": profile.company_name,
                "sector": profile.sector,
                "established_date": profile.established_date.isoformat(),
                "departments": profile.departments,
                "vision": profile.vision,
                "mission": profile.mission,
                "status": profile.status,
            },
            "financials": {
                "currency": financials.currency,
                **financials.financial_summary(),
                "revenue_categories": financials.revenue_categories,
                "expense_categories": financials.expense_categories,
            },
            "generated_at": _utc_now().isoformat(),
        }

    def get_financial_report(self, period: str | None = None) -> dict[str, Any]:
        """Summarize the ledger: totals, per-category splits, and the cash flow periods.

        Read-only. `period` narrows the cash flow table to a single period; omitting it
        reports every period the template holds.
        """
        wanted = period.strip() if period is not None else None
        with self._lock:
            financials = self._read_model("financials.json", CompanyFinancials)

        periods = [
            {
                "period": entry.period,
                "opening_balance": entry.opening_balance,
                "net_cash_flow": entry.net_cash_flow,
                "closing_balance": entry.closing_balance,
            }
            for entry in financials.cash_flow_template
        ]
        if wanted is not None:
            matches = [row for row in periods if row["period"] == wanted]
            if not matches:
                available = ", ".join(repr(row["period"]) for row in periods) or "none"
                raise ValueError(f"unknown period {wanted!r}. Available periods: {available}")
            periods = matches

        total_expenses = financials.total_expenses
        warnings: list[str] = []
        if financials.current_balance < 0:
            warnings.append(f"current balance is negative: {financials.current_balance}")
        if total_expenses > financials.initial_budget:
            warnings.append(
                f"total expenses {total_expenses} exceed the initial budget "
                f"{financials.initial_budget}"
            )
        remaining = financials.initial_budget - total_expenses
        return {
            "company_name": financials.company_name,
            "currency": financials.currency,
            "generated_at": _utc_now().isoformat(),
            "totals": {
                **financials.financial_summary(),
                "remaining_budget": remaining,
                "budget_used_percent": (
                    float(
                        (total_expenses / financials.initial_budget * 100).quantize(Decimal("0.1"))
                    )
                    if financials.initial_budget
                    else None
                ),
            },
            "income_by_category": _category_breakdown(financials, "income"),
            "expenses_by_category": _category_breakdown(financials, "expense"),
            "cash_flow": {
                "period_filter": wanted,
                "periods": periods,
                "period_count": len(periods),
                "total_net_cash_flow": sum(
                    (row["net_cash_flow"] for row in periods), Decimal("0.00")
                ),
            },
            "warnings": warnings,
        }


default_company_wizard = CompanyWizard()


def init_company(company_name: str, sector: str, budget: float) -> dict[str, Any]:
    return default_company_wizard.init_company(company_name, sector, budget)


def add_financial_record(
    type: str,
    category: str,
    amount: float,
    description: str,
) -> dict[str, Any]:
    return default_company_wizard.add_financial_record(type, category, amount, description)


def add_employee(
    name: str,
    role: str,
    department: str,
    salary: float,
) -> dict[str, Any]:
    return default_company_wizard.add_employee(name, role, department, salary)


def update_company_notes(note_title: str, content: str) -> dict[str, Any]:
    return default_company_wizard.update_company_notes(note_title, content)


def get_company_overview() -> dict[str, Any]:
    return default_company_wizard.get_company_overview()
