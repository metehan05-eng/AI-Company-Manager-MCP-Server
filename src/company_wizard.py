from __future__ import annotations

import contextlib
import json
import re
import threading
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Literal
from uuid import uuid4

import pandas as pd
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
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


def _default_employee_id(value: object) -> str:
    text = str(value).strip()
    if not text:
        return ""
    if re.fullmatch(r"EMP-\d{4,}", text):
        return text
    digits = re.findall(r"\d+", text)
    if digits:
        return f"EMP-{int(digits[-1]):04d}"
    return f"EMP-{abs(hash(text)) % 9000 + 1000:04d}"


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
        ordered["employee_id"] = ordered["employee_id"].apply(_default_employee_id)
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
        canonical["employee_id"] = canonical["employee_id"].apply(_default_employee_id)

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
