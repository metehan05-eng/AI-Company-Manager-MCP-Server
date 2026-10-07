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


def _format_month(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def _shift_month(anchor: date, months: int) -> date:
    """Move a first-of-month date by a whole number of months."""
    total = anchor.year * 12 + (anchor.month - 1) + months
    return date(total // 12, total % 12 + 1, 1)


def _percent_change(current: Decimal, previous: Decimal) -> float | None:
    """Percentage moved from `previous` to `current`, or None when there is no base."""
    if previous == 0:
        return None
    return float(((current - previous) / previous * 100).quantize(Decimal("0.1")))


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


class AuditEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    seq: int = Field(ge=1)
    id: str = Field(default_factory=lambda: str(uuid4()))
    action: str = Field(min_length=1, max_length=100)
    target: str = Field(min_length=1, max_length=300)
    summary: str = Field(min_length=1, max_length=500)
    details: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=_utc_now)


class AuditLog(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    entries: list[AuditEntry] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=_utc_now)


RiskRating = Literal["low", "medium", "high"]
RiskStatus = Literal["open", "mitigating", "closed"]
RiskLevel = Literal["low", "medium", "high", "critical"]

RISK_RATING_SCORE: dict[str, int] = {"low": 1, "medium": 2, "high": 3}
RISK_EDITABLE_FIELDS = (
    "title",
    "description",
    "category",
    "owner",
    "mitigation",
    "likelihood",
    "impact",
    "status",
)


def _risk_score(likelihood: RiskRating, impact: RiskRating) -> int:
    return RISK_RATING_SCORE[likelihood] * RISK_RATING_SCORE[impact]


def _risk_level(score: int) -> RiskLevel:
    if score >= 7:
        return "critical"
    if score >= 5:
        return "high"
    if score >= 3:
        return "medium"
    return "low"


class Risk(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    id: str = Field(default_factory=lambda: str(uuid4()))
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4_000)
    category: str = Field(default="general", min_length=1, max_length=100)
    owner: str = Field(default="", max_length=200)
    mitigation: str = Field(default="", max_length=4_000)
    likelihood: RiskRating
    impact: RiskRating
    status: RiskStatus = "open"
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)

    @field_validator("title", "category", mode="before")
    @classmethod
    def clean_risk_text(cls, value: str) -> str:
        return _clean_required_text(value, "risk text")

    @property
    def score(self) -> int:
        return _risk_score(self.likelihood, self.impact)

    @property
    def level(self) -> RiskLevel:
        return _risk_level(self.score)


def _risk_payload(risk: Risk) -> dict[str, Any]:
    """Serialize a risk with its derived score and level, which are never stored."""
    payload = risk.model_dump(mode="json")
    payload["score"] = risk.score
    payload["level"] = risk.level
    return payload


class RiskRegister(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    risks: list[Risk] = Field(default_factory=list)
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

    def _load_audit(self) -> AuditLog:
        try:
            return self._read_model("audit_log.json", AuditLog)
        except FileNotFoundError:
            return AuditLog()

    @classmethod
    def _audit_value(cls, value: Any, limit: int = 500) -> Any:
        """Keep audit details readable: long strings are clipped instead of stored whole."""
        if isinstance(value, str):
            return value if len(value) <= limit else value[: limit - 1] + "…"
        if isinstance(value, dict):
            return {key: cls._audit_value(item, limit) for key, item in value.items()}
        if isinstance(value, list):
            return [cls._audit_value(item, limit) for item in value]
        return value

    def _commit(
        self,
        action: str,
        target: str,
        summary: str,
        details: dict[str, Any],
        write: Callable[[], None],
    ) -> None:
        """Journal a change first, then apply it, rolling the entry back if the write fails.

        Journaling before the data write keeps the log from claiming a change that never
        landed; a failed write removes the entry again, so the log only ever describes
        changes that are on disk. The lock is reentrant, so this is safe to call from
        methods that already hold it.
        """
        with self._lock:
            log = self._load_audit()
            entry = AuditEntry(
                seq=len(log.entries) + 1,
                action=action,
                target=target,
                summary=summary,
                details={key: self._audit_value(value) for key, value in details.items()},
            )
            log.entries.append(entry)
            log.updated_at = _utc_now()
            self._write_model("audit_log.json", log)
            try:
                write()
            except Exception:
                log.entries.pop()
                log.updated_at = _utc_now()
                with contextlib.suppress(Exception):
                    self._write_model("audit_log.json", log)
                raise

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

            def _write_core_files() -> None:
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

            self._commit(
                action="company_initialized",
                target="company_profile.json",
                summary=(
                    f"Initialized company {profile.company_name} "
                    f"with an initial budget of {budget_value}"
                ),
                details={
                    "company_name": profile.company_name,
                    "sector": profile.sector,
                    "currency": profile.currency,
                    "initial_budget": budget_value,
                    "created_files": list(core_files),
                },
                write=_write_core_files,
            )

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
            category_added = not any(
                item.casefold() == record.category.casefold() for item in categories
            )
            if category_added:
                categories.append(record.category)
            financials.records.append(record)
            financials.updated_at = _utc_now()
            self._commit(
                action="financial_record_added",
                target=f"financials.json:{record.id}",
                summary=(
                    f"Added {record.type} record of {record.amount} "
                    f"{financials.currency} under {record.category}"
                ),
                details={
                    "record": record.model_dump(mode="json"),
                    "category_added": category_added,
                },
                write=lambda: self._write_model("financials.json", financials),
            )

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

            def _write_employees() -> None:
                self.file_handler.write_file("employees.csv", updated_frame)

            self._commit(
                action="employee_added",
                target=f"employees.csv:{employee.employee_id}",
                summary=(
                    f"Added employee {employee.name} as {employee.role} in {employee.department}"
                ),
                details={"employee": employee.model_dump(mode="json")},
                write=_write_employees,
            )

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

        def _write_migration() -> None:
            self.file_handler.write_file(backup_name, raw)
            self.file_handler.write_file("employees.csv", canonical)

        self._commit(
            action="employees_migrated",
            target="employees.csv",
            summary=f"Migrated employees.csv to the current schema ({len(canonical)} rows)",
            details={
                "row_count": len(canonical),
                "backup_file": backup_name,
                "renamed_columns": result["renamed_columns"],
            },
            write=_write_migration,
        )
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
        backup_plan = {name: name.replace(".json", f".backup-{stamp}.json") for name in changed}

        if not changed:
            for name, model_type in models.items():
                self._write_model(name, model_type.model_validate(documents[name]))
            result["message"] = "Both files already matched; nothing was written."
            return result

        def _write_documents() -> None:
            for name, backup_name in backup_plan.items():
                self.file_handler.write_file(
                    backup_name,
                    (self.file_handler.data_dir / name).read_text(encoding="utf-8"),
                )
                backups[name] = backup_name
            for name, model_type in models.items():
                self._write_model(name, model_type.model_validate(documents[name]))

        self._commit(
            action="data_migration_applied",
            target=", ".join(changed),
            summary=(
                f"Applied the confirmed mapping to {len(changed)} file(s) "
                "with a timestamped backup each"
            ),
            details={
                "changed_files": changed,
                "backups": backup_plan,
                "resolved": result["resolved"],
            },
            write=_write_documents,
        )
        result["backups"] = backups
        result["message"] = "company_profile.json and financials.json migrated successfully"
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
            previous_content: str | None
            if existing_note is None:
                existing_note = CompanyNote(title=title, content=clean_content)
                notes.notes.append(existing_note)
                action = "created"
                previous_content = None
            else:
                previous_content = existing_note.content
                existing_note.content = clean_content
                existing_note.updated_at = _utc_now()
                action = "updated"
            notes.updated_at = _utc_now()
            details: dict[str, Any] = {
                "note_id": existing_note.id,
                "title": title,
                "content_length_before": (
                    len(previous_content) if previous_content is not None else None
                ),
                "content_length_after": len(clean_content),
            }
            if previous_content is not None:
                details["content_before"] = previous_content
                details["content_after"] = clean_content
            self._commit(
                action=f"note_{action}",
                target=f"company_notes.json:{existing_note.id}",
                summary=f"Company note {action}: {title}",
                details=details,
                write=lambda: self._write_model("company_notes.json", notes),
            )

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

    def list_audit_entries(
        self,
        action: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Read the append-only change journal, newest entry first. Read-only."""
        if limit < 1 or limit > 500:
            raise ValueError("limit must be between 1 and 500")
        if offset < 0:
            raise ValueError("offset must not be negative")
        wanted = action.strip() if isinstance(action, str) and action.strip() else None
        with self._lock:
            log = self._load_audit()

        available = sorted({entry.action for entry in log.entries})
        if wanted is not None and wanted not in available:
            listed = ", ".join(repr(name) for name in available) or "none"
            raise ValueError(f"unknown action {wanted!r}. Available actions: {listed}")

        newest_first = list(reversed(log.entries))
        if wanted is not None:
            newest_first = [entry for entry in newest_first if entry.action == wanted]
        filtered_total = len(newest_first)
        page = newest_first[offset : offset + limit]
        return {
            "entries": [entry.model_dump(mode="json") for entry in page],
            "total_entries": len(log.entries),
            "filtered_total": filtered_total,
            "action_filter": wanted,
            "actions": available,
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(page) < filtered_total,
        }

    def get_variance_report(self, month: str | None = None) -> dict[str, Any]:
        """Compare spending against the budget and each month against the last. Read-only."""
        wanted = month.strip() if isinstance(month, str) and month.strip() else None
        if wanted is not None and re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", wanted) is None:
            raise ValueError(f"month {wanted!r} must be formatted as YYYY-MM, for example 2026-10")
        with self._lock:
            financials = self._read_model("financials.json", CompanyFinancials)

        monthly: dict[str, dict[str, Decimal]] = {}
        for record in financials.records:
            key = _format_month(record.recorded_at.year, record.recorded_at.month)
            bucket = monthly.setdefault(
                key, {"income": Decimal("0.00"), "expense": Decimal("0.00")}
            )
            bucket[record.type] += record.amount
        available_months = sorted(monthly)
        if wanted is not None and wanted not in monthly:
            listed = ", ".join(available_months) or "none"
            raise ValueError(f"unknown month {wanted!r}. Available months: {listed}")

        rows: list[dict[str, Any]] = []
        previous: dict[str, Decimal] | None = None
        for key in available_months:
            current = monthly[key]
            current_net = current["income"] - current["expense"]
            change: dict[str, Any] | None = None
            change_percent: dict[str, Any] | None = None
            if previous is not None:
                previous_net = previous["income"] - previous["expense"]
                change = {
                    "income": current["income"] - previous["income"],
                    "expense": current["expense"] - previous["expense"],
                    "net": current_net - previous_net,
                }
                change_percent = {
                    "income": _percent_change(current["income"], previous["income"]),
                    "expense": _percent_change(current["expense"], previous["expense"]),
                    "net": _percent_change(current_net, previous_net),
                }
            rows.append(
                {
                    "month": key,
                    "income": current["income"],
                    "expense": current["expense"],
                    "net": current_net,
                    "change_vs_previous": change,
                    "change_percent_vs_previous": change_percent,
                }
            )
            previous = {**current, "net": current_net}
        returned = [row for row in rows if row["month"] == wanted] if wanted else rows

        total_expenses = financials.total_expenses
        over_budget = total_expenses > financials.initial_budget
        remaining = financials.initial_budget - total_expenses
        warnings: list[str] = []
        if financials.current_balance < 0:
            warnings.append(f"current balance is negative: {financials.current_balance}")
        if over_budget:
            warnings.append(
                f"total expenses {total_expenses} exceed the initial budget "
                f"{financials.initial_budget}"
            )
        if not financials.records:
            warnings.append("no financial records to compare yet")

        return {
            "company_name": financials.company_name,
            "currency": financials.currency,
            "generated_at": _utc_now().isoformat(),
            "month_filter": wanted,
            "budget": {
                "initial_budget": financials.initial_budget,
                "spent": total_expenses,
                "remaining": remaining,
                "over_budget": over_budget,
                "used_percent": (
                    float(
                        (total_expenses / financials.initial_budget * 100).quantize(Decimal("0.1"))
                    )
                    if financials.initial_budget
                    else None
                ),
            },
            "monthly": returned,
            "month_count": len(rows),
            "record_count": len(financials.records),
            "warnings": warnings,
        }

    def run_scenario(
        self,
        horizon_months: int = 12,
        income_change_percent: float = 0.0,
        expense_change_percent: float = 0.0,
        category_changes: dict[str, float] | None = None,
        one_time_expense: float = 0.0,
    ) -> dict[str, Any]:
        """Project the balance forward under stated assumptions. Read-only: nothing is saved.

        The baseline is the historical monthly average of every recorded month.
        `category_changes` adds (or removes) a fixed monthly amount per expense category;
        `one_time_expense` is charged once, in the first projected month.
        """
        if horizon_months < 1 or horizon_months > 120:
            raise ValueError("horizon_months must be between 1 and 120")
        for name, value in (
            ("income_change_percent", income_change_percent),
            ("expense_change_percent", expense_change_percent),
        ):
            if value < -100 or value > 10_000:
                raise ValueError(f"{name} must be between -100 and 10000")
        one_time = _normalize_money(one_time_expense, "one_time_expense")
        if one_time < 0:
            raise ValueError("one_time_expense must not be negative")

        deltas: dict[str, Decimal] = {}
        if category_changes:
            with self._lock:
                financials = self._read_model("financials.json", CompanyFinancials)
            known = {name.casefold(): name for name in financials.expense_categories}
            for raw_name, raw_value in category_changes.items():
                name = _clean_required_text(raw_name, "category_changes key")
                canonical = known.get(name.casefold())
                if canonical is None:
                    listed = ", ".join(financials.expense_categories)
                    raise ValueError(f"unknown expense category {name!r}. Available: {listed}")
                deltas[canonical] = deltas.get(canonical, Decimal("0.00")) + _normalize_money(
                    raw_value, f"category_changes[{name!r}]"
                )
        else:
            with self._lock:
                financials = self._read_model("financials.json", CompanyFinancials)

        history: dict[str, dict[str, Decimal]] = {}
        for record in financials.records:
            key = _format_month(record.recorded_at.year, record.recorded_at.month)
            bucket = history.setdefault(
                key, {"income": Decimal("0.00"), "expense": Decimal("0.00")}
            )
            bucket[record.type] += record.amount
        if not history:
            raise ValueError(
                "a scenario needs at least one income or expense record; "
                "add records with add_financial_record first"
            )

        observed_months = Decimal(len(history))
        baseline_income = (
            sum((bucket["income"] for bucket in history.values()), Decimal("0.00"))
            / observed_months
        ).quantize(Decimal("0.01"))
        baseline_expense = (
            sum((bucket["expense"] for bucket in history.values()), Decimal("0.00"))
            / observed_months
        ).quantize(Decimal("0.01"))

        income_factor = 1 + Decimal(str(income_change_percent)) / 100
        expense_factor = 1 + Decimal(str(expense_change_percent)) / 100
        monthly_income = (baseline_income * income_factor).quantize(Decimal("0.01"))
        monthly_expense = (
            baseline_expense * expense_factor + sum(deltas.values(), Decimal("0.00"))
        ).quantize(Decimal("0.01"))
        if monthly_expense < 0:
            raise ValueError(
                f"projected monthly expense would be negative ({monthly_expense}); "
                "reduce the category_changes amounts"
            )

        starting_balance = financials.current_balance
        anchor = _utc_today().replace(day=1)
        opening = starting_balance
        months: list[dict[str, Any]] = []
        total_income = Decimal("0.00")
        total_expense = Decimal("0.00")
        lowest = starting_balance
        runway_months: int | None = None
        for index in range(1, horizon_months + 1):
            label = _shift_month(anchor, index - 1)
            expense = monthly_expense + (one_time if index == 1 else Decimal("0.00"))
            closing = opening + monthly_income - expense
            if runway_months is None and closing < 0:
                runway_months = index
            if closing < lowest:
                lowest = closing
            months.append(
                {
                    "month_index": index,
                    "month": _format_month(label.year, label.month),
                    "opening_balance": opening,
                    "income": monthly_income,
                    "expense": expense,
                    "net_flow": monthly_income - expense,
                    "closing_balance": closing,
                }
            )
            total_income += monthly_income
            total_expense += expense
            opening = closing

        warnings: list[str] = []
        if starting_balance < 0:
            warnings.append(f"starting balance is already negative: {starting_balance}")
        if runway_months is not None:
            warnings.append(
                f"balance turns negative in month {runway_months} "
                f"({months[runway_months - 1]['month']})"
            )

        return {
            "company_name": financials.company_name,
            "currency": financials.currency,
            "generated_at": _utc_now().isoformat(),
            "baseline": {
                "months_of_history": len(history),
                "monthly_income": baseline_income,
                "monthly_expense": baseline_expense,
                "starting_balance": starting_balance,
            },
            "assumptions": {
                "horizon_months": horizon_months,
                "income_change_percent": income_change_percent,
                "expense_change_percent": expense_change_percent,
                "category_changes": dict(deltas),
                "one_time_expense": one_time,
            },
            "months": months,
            "summary": {
                "closing_balance": opening,
                "total_income": total_income,
                "total_expense": total_expense,
                "net_change": opening - starting_balance,
                "lowest_balance": lowest,
                "runway_months": runway_months,
            },
            "warnings": warnings,
        }

    def _read_register(self) -> RiskRegister:
        try:
            return self._read_model("risk_register.json", RiskRegister)
        except FileNotFoundError:
            return RiskRegister()

    def list_risks(
        self,
        status: str | None = None,
        category: str | None = None,
        sort: str = "score",
    ) -> dict[str, Any]:
        """List registered risks with their severity. Read-only."""
        wanted_status = status.strip() if isinstance(status, str) and status.strip() else None
        if wanted_status is not None and wanted_status not in ("open", "mitigating", "closed"):
            raise ValueError("status must be 'open', 'mitigating' or 'closed'")
        if sort not in ("score", "created", "title"):
            raise ValueError("sort must be 'score', 'created' or 'title'")
        wanted_category = (
            category.strip() if isinstance(category, str) and category.strip() else None
        )
        with self._lock:
            register = self._read_register()

        selected = list(register.risks)
        if wanted_status is not None:
            selected = [risk for risk in selected if risk.status == wanted_status]
        if wanted_category is not None:
            selected = [
                risk for risk in selected if risk.category.casefold() == wanted_category.casefold()
            ]
        if sort == "score":
            selected.sort(key=lambda risk: (-risk.score, risk.title.casefold()))
        elif sort == "created":
            selected.sort(key=lambda risk: risk.created_at, reverse=True)
        else:
            selected.sort(key=lambda risk: risk.title.casefold())

        by_level: dict[str, int] = {"critical": 0, "high": 0, "medium": 0, "low": 0}
        for risk in register.risks:
            by_level[risk.level] += 1
        return {
            "risks": [_risk_payload(risk) for risk in selected],
            "count": len(selected),
            "total_count": len(register.risks),
            "open_count": sum(1 for risk in register.risks if risk.status != "closed"),
            "by_level": by_level,
            "categories": sorted({risk.category for risk in register.risks}, key=str.casefold),
            "sort": sort,
            "generated_at": _utc_now().isoformat(),
        }

    def add_risk(
        self,
        title: str,
        likelihood: str,
        impact: str,
        description: str = "",
        category: str = "general",
        owner: str = "",
        mitigation: str = "",
    ) -> dict[str, Any]:
        risk = Risk(
            title=title,
            likelihood=likelihood,
            impact=impact,
            description=description,
            category=category,
            owner=owner,
            mitigation=mitigation,
        )
        with self._lock:
            register = self._read_register()
            register.risks.append(risk)
            register.updated_at = _utc_now()
            self._commit(
                action="risk_added",
                target=f"risk_register.json:{risk.id}",
                summary=f"Added risk {risk.title} ({risk.level}, score {risk.score})",
                details={"risk": _risk_payload(risk)},
                write=lambda: self._write_model("risk_register.json", register),
            )
        return {"message": "Risk added successfully", "risk": _risk_payload(risk)}

    def update_risk(self, risk_id: str, changes: dict[str, str]) -> dict[str, Any]:
        if not isinstance(changes, dict) or not changes:
            raise ValueError("changes must not be empty")
        editable = set(RISK_EDITABLE_FIELDS)
        unknown = sorted(key for key in changes if key not in editable)
        if unknown:
            raise ValueError(
                f"unknown field(s): {', '.join(str(key) for key in unknown)}. "
                f"Editable fields: {', '.join(RISK_EDITABLE_FIELDS)}"
            )
        with self._lock:
            register = self._read_register()
            risk = next((item for item in register.risks if item.id == risk_id), None)
            if risk is None:
                raise ValueError(f"unknown risk id {risk_id!r}; call list_risks to see ids")
            before = _risk_payload(risk)
            for field, value in changes.items():
                setattr(risk, field, value)
            risk.updated_at = _utc_now()
            after = _risk_payload(risk)
            changed = [field for field in RISK_EDITABLE_FIELDS if before[field] != after[field]]
            if not changed:
                raise ValueError(
                    f"no changes: the provided values already match this risk ({risk.title})"
                )
            register.updated_at = _utc_now()
            self._commit(
                action="risk_updated",
                target=f"risk_register.json:{risk.id}",
                summary=f"Updated risk {risk.title}: {', '.join(changed)}",
                details={
                    "risk_id": risk.id,
                    "changed_fields": changed,
                    "before": {field: before[field] for field in changed},
                    "after": {field: after[field] for field in changed},
                },
                write=lambda: self._write_model("risk_register.json", register),
            )
        return {
            "message": "Risk updated successfully",
            "risk": after,
            "changed_fields": changed,
        }

    def delete_risk(self, risk_id: str) -> dict[str, Any]:
        with self._lock:
            register = self._read_register()
            risk = next((item for item in register.risks if item.id == risk_id), None)
            if risk is None:
                raise ValueError(f"unknown risk id {risk_id!r}; call list_risks to see ids")
            register.risks.remove(risk)
            register.updated_at = _utc_now()
            self._commit(
                action="risk_deleted",
                target=f"risk_register.json:{risk.id}",
                summary=f"Deleted risk {risk.title} ({risk.level}, score {risk.score})",
                details={"risk": _risk_payload(risk)},
                write=lambda: self._write_model("risk_register.json", register),
            )
        return {"message": "Risk deleted successfully", "risk": _risk_payload(risk)}


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


def list_audit_entries(
    action: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    return default_company_wizard.list_audit_entries(action=action, limit=limit, offset=offset)


def get_variance_report(month: str | None = None) -> dict[str, Any]:
    return default_company_wizard.get_variance_report(month=month)


def run_scenario(
    horizon_months: int = 12,
    income_change_percent: float = 0.0,
    expense_change_percent: float = 0.0,
    category_changes: dict[str, float] | None = None,
    one_time_expense: float = 0.0,
) -> dict[str, Any]:
    return default_company_wizard.run_scenario(
        horizon_months=horizon_months,
        income_change_percent=income_change_percent,
        expense_change_percent=expense_change_percent,
        category_changes=category_changes,
        one_time_expense=one_time_expense,
    )


def list_risks(
    status: str | None = None,
    category: str | None = None,
    sort: str = "score",
) -> dict[str, Any]:
    return default_company_wizard.list_risks(status=status, category=category, sort=sort)


def add_risk(
    title: str,
    likelihood: str,
    impact: str,
    description: str = "",
    category: str = "general",
    owner: str = "",
    mitigation: str = "",
) -> dict[str, Any]:
    return default_company_wizard.add_risk(
        title,
        likelihood,
        impact,
        description=description,
        category=category,
        owner=owner,
        mitigation=mitigation,
    )


def update_risk(risk_id: str, changes: dict[str, str]) -> dict[str, Any]:
    return default_company_wizard.update_risk(risk_id, changes)


def delete_risk(risk_id: str) -> dict[str, Any]:
    return default_company_wizard.delete_risk(risk_id)
