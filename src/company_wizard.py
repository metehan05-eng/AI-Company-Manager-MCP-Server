from __future__ import annotations

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
    revenue_categories: list[str] = Field(
        default_factory=lambda: list(DEFAULT_REVENUE_CATEGORIES)
    )
    expense_categories: list[str] = Field(
        default_factory=lambda: list(DEFAULT_EXPENSE_CATEGORIES)
    )
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
        existing_paths = [
            path for path in (profile_path, financials_path) if path.exists()
        ]
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

    def init_company(
        self, company_name: str, sector: str, budget: float
    ) -> dict[str, Any]:
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
        budget_value = Decimal(str(budget))
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
                        try:
                            self.file_handler.write_file(
                                filename, existing_contents[filename]
                            )
                        except (OSError, TypeError, ValueError):
                            pass
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
            amount=Decimal(str(amount)),
            description=description,
        )
        with self._lock:
            financials = self._read_model("financials.json", CompanyFinancials)
            if record.type == "income":
                categories = financials.revenue_categories
            else:
                categories = financials.expense_categories
            if not any(
                item.casefold() == record.category.casefold() for item in categories
            ):
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
        missing_columns = [
            column for column in EMPLOYEE_COLUMNS if column not in frame.columns
        ]
        if missing_columns:
            raise ValueError(
                f"employees.csv is missing required columns: {', '.join(missing_columns)}"
            )
        return frame[EMPLOYEE_COLUMNS]

    def add_employee(
        self,
        name: str,
        role: str,
        department: str,
        salary: float,
    ) -> dict[str, Any]:
        salary_value = Decimal(str(salary))
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
            updated_frame = pd.concat(
                [frame, pd.DataFrame([row], columns=EMPLOYEE_COLUMNS)],
                ignore_index=True,
            )
            self.file_handler.write_file("employees.csv", updated_frame)

        return {
            "message": "Employee added successfully",
            "employee": employee.model_dump(mode="json"),
        }

    def update_company_notes(self, note_title: str, content: str) -> dict[str, Any]:
        title = _clean_required_text(note_title, "note_title")
        clean_content = _clean_required_text(content, "content")
        with self._lock:
            try:
                notes = self._read_model("company_notes.json", CompanyNotes)
            except FileNotFoundError:
                notes = CompanyNotes()
            existing_note = next(
                (
                    note
                    for note in notes.notes
                    if note.title.casefold() == title.casefold()
                ),
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
            raise ValueError(
                "company name mismatch between profile and financial files"
            )
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
    return default_company_wizard.add_financial_record(
        type, category, amount, description
    )


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
