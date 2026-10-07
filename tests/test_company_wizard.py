"""CompanyWizard business rules: initialization, finances, employees, notes, error messages."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from conftest import PROJECT_ROOT
from src.company_wizard import CompanyWizard, _apply_transform
from src.file_handler import FileHandler


class TestInitCompany:
    def test_creates_expected_files(self, wizard: CompanyWizard, data_dir: Path) -> None:
        result = wizard.init_company("Acme", "Yazilim", 10_000.0)
        assert result["created_files"] == [
            "company_profile.json",
            "financials.json",
            "employees.csv",
        ]
        assert {path.name for path in data_dir.iterdir()} == {
            "company_profile.json",
            "financials.json",
            "employees.csv",
            "audit_log.json",
        }
        assert "initialized successfully" in result["message"].lower()

    def test_profile_contents(self, wizard: CompanyWizard) -> None:
        result = wizard.init_company("Acme", "Yazilim", 10_000.0)
        company = result["company"]
        assert company["name"] == "Acme"
        assert company["sector"] == "Yazilim"
        assert company["currency"] == "TRY"
        assert company["departments"]
        assert company["vision"]
        assert company["mission"]

    def test_opening_balance_equals_budget(self, wizard: CompanyWizard) -> None:
        result = wizard.init_company("Acme", "Yazilim", 10_000.0)
        financials = result["financials"]
        assert financials["opening_balance"] == Decimal("10000")
        assert financials["current_balance"] == Decimal("10000")
        assert financials["net_cash_flow"] == Decimal("0.00")
        assert financials["transaction_count"] == 0

    def test_seeds_founder_employee(self, wizard: CompanyWizard) -> None:
        result = wizard.init_company("Acme", "Yazilim", 10_000.0)
        frame_content = (wizard.file_handler.data_dir / "employees.csv").read_text(
            encoding="utf-8-sig"
        )
        assert "employee_id,name,role,department,salary,start_date,status" in frame_content
        assert "Kurucu" in frame_content
        assert result is not None

    def test_refuses_when_data_exists(self, wizard: CompanyWizard) -> None:
        wizard.init_company("Acme", "Yazilim", 10_000.0)
        with pytest.raises(FileExistsError) as excinfo:
            wizard.init_company("Beta", "Satis", 5_000.0)
        assert "already exists" in str(excinfo.value).lower()

    def test_rejects_budget_with_three_decimals(self, wizard: CompanyWizard) -> None:
        with pytest.raises(ValueError) as excinfo:
            wizard.init_company("Acme", "Yazilim", 10_000.555)
        assert "at most 2 decimal places" in str(excinfo.value)

    def test_rejects_empty_company_name(self, wizard: CompanyWizard) -> None:
        with pytest.raises(ValueError):
            wizard.init_company("   ", "Yazilim", 10_000.0)


class TestFinancialRecords:
    def test_expense_updates_balance(self, company: CompanyWizard) -> None:
        result = company.add_financial_record("expense", "kira", 1_500.0, "ofis")
        financials = result["financials"]
        assert financials["total_expenses"] == Decimal("1500.00")
        assert financials["net_cash_flow"] == Decimal("-1500.00")
        assert financials["current_balance"] == Decimal("8500.00")
        assert financials["transaction_count"] == 1

    def test_income_updates_balance(self, company: CompanyWizard) -> None:
        result = company.add_financial_record("income", "satis", 2_000.0, "siparis")
        financials = result["financials"]
        assert financials["total_income"] == Decimal("2000.00")
        assert financials["net_cash_flow"] == Decimal("2000.00")
        assert financials["current_balance"] == Decimal("12000.00")

    def test_mixed_records_accumulate(self, company: CompanyWizard) -> None:
        company.add_financial_record("income", "satis", 3_000.0, "a")
        company.add_financial_record("expense", "kira", 1_000.0, "b")
        company.add_financial_record("expense", "maas", 500.5, "c")
        financials = company.get_company_overview()["financials"]
        assert financials["total_income"] == Decimal("3000.00")
        assert financials["total_expenses"] == Decimal("1500.50")
        assert financials["net_cash_flow"] == Decimal("1499.50")
        assert financials["transaction_count"] == 3

    def test_decimal_precision_preserved(self, company: CompanyWizard) -> None:
        result = company.add_financial_record("expense", "kira", 1500.55, "x")
        assert result["record"]["amount"] == "1500.55"

    def test_new_category_is_added(self, company: CompanyWizard) -> None:
        company.add_financial_record("expense", "OzelGider", 100.0, "x")
        overview = company.get_company_overview()["financials"]
        assert "OzelGider" in overview["expense_categories"]

    def test_rejects_three_decimal_amount(self, company: CompanyWizard) -> None:
        with pytest.raises(ValueError) as excinfo:
            company.add_financial_record("expense", "kira", 10.999, "x")
        assert "at most 2 decimal places" in str(excinfo.value)

    def test_rejects_negative_amount(self, company: CompanyWizard) -> None:
        with pytest.raises(ValueError):
            company.add_financial_record("expense", "kira", -10.0, "x")

    def test_rejects_zero_amount(self, company: CompanyWizard) -> None:
        with pytest.raises(ValueError):
            company.add_financial_record("expense", "kira", 0.0, "x")

    def test_rejects_invalid_type(self, company: CompanyWizard) -> None:
        with pytest.raises(ValueError):
            company.add_financial_record("transfer", "kira", 10.0, "x")

    def test_rejects_empty_category(self, company: CompanyWizard) -> None:
        with pytest.raises(ValueError):
            company.add_financial_record("expense", "  ", 10.0, "x")

    def test_rejects_nan_amount(self, company: CompanyWizard) -> None:
        with pytest.raises(ValueError) as excinfo:
            company.add_financial_record("expense", "kira", float("nan"), "x")
        assert "finite" in str(excinfo.value)

    def test_records_persist_across_instances(self, data_dir: Path) -> None:
        first = CompanyWizard(FileHandler(data_dir=data_dir))
        first.init_company("Acme", "Yazilim", 10_000.0)
        first.add_financial_record("expense", "kira", 1_000.0, "x")
        second = CompanyWizard(FileHandler(data_dir=data_dir))
        assert second.get_company_overview()["financials"]["transaction_count"] == 1


class TestEmployees:
    def test_employee_id_is_sequential(self, company: CompanyWizard) -> None:
        first = company.add_employee("Ali", "Dev", "Bilgi Teknolojileri", 5_000.0)
        second = company.add_employee("Ayse", "Mudur", "Genel Yonetim", 7_000.0)
        assert first["employee"]["employee_id"] == "EMP-0002"
        assert second["employee"]["employee_id"] == "EMP-0003"

    def test_salary_stored_as_text(self, company: CompanyWizard) -> None:
        result = company.add_employee("Ali", "Dev", "Bilgi Teknolojileri", 5_000.0)
        assert result["employee"]["salary"] == "5000.0"

    def test_status_defaults_to_active(self, company: CompanyWizard) -> None:
        result = company.add_employee("Ali", "Dev", "Bilgi Teknolojileri", 5_000.0)
        assert result["employee"]["status"] == "active"

    def test_employees_accumulate_in_csv(self, company: CompanyWizard) -> None:
        company.add_employee("Ali", "Dev", "Bilgi Teknolojileri", 5_000.0)
        company.add_employee("Ayse", "Mudur", "Genel Yonetim", 7_000.0)
        content = (company.file_handler.data_dir / "employees.csv").read_text(encoding="utf-8-sig")
        assert "Ali" in content
        assert "Ayse" in content
        assert content.count("\n") == 4

    def test_rejects_three_decimal_salary(self, company: CompanyWizard) -> None:
        with pytest.raises(ValueError) as excinfo:
            company.add_employee("Ali", "Dev", "Bilgi Teknolojileri", 1234.567)
        assert "at most 2 decimal places" in str(excinfo.value)

    def test_rejects_empty_name(self, company: CompanyWizard) -> None:
        with pytest.raises(ValueError):
            company.add_employee("   ", "Dev", "IT", 100.0)

    @pytest.mark.parametrize("blank_field", ["name", "role", "department"])
    def test_rejects_blank_fields(self, company: CompanyWizard, blank_field: str) -> None:
        args: dict[str, object] = {
            "name": "Ali",
            "role": "Dev",
            "department": "IT",
            "salary": 100.0,
        }
        args[blank_field] = "   "
        with pytest.raises(ValueError):
            company.add_employee(**args)  # type: ignore[arg-type]

    def test_rejects_missing_required_columns(self, wizard: CompanyWizard, data_dir: Path) -> None:
        wizard.init_company("Acme", "Yazilim", 10_000.0)
        (data_dir / "employees.csv").write_text(
            "id,full_name,monthly_salary_usd\n1,Ali,5000\n", encoding="utf-8"
        )
        with pytest.raises(ValueError) as excinfo:
            wizard.add_employee("Ayse", "Mudur", "IT", 1_000.0)
        assert "missing required columns: role, department" in str(excinfo.value)

    def test_error_lists_columns_found(self, wizard: CompanyWizard, data_dir: Path) -> None:
        wizard.init_company("Acme", "Yazilim", 10_000.0)
        (data_dir / "employees.csv").write_text("id,full_name\n1,Ali\n", encoding="utf-8")
        with pytest.raises(ValueError) as excinfo:
            wizard.add_employee("Ayse", "Mudur", "IT", 1_000.0)
        assert "Columns found: id, full_name" in str(excinfo.value)
        assert "migrate_company_data" in str(excinfo.value)


class TestLegacyEmployeeSchema:
    """A real-world employees.csv used different column names and must stay usable."""

    LEGACY = (
        "id,full_name,role,department,monthly_salary_usd,performance_score\n"
        "E101,Metehan Erbasc,CTO,Ar-Ge,8500,4.9\n"
        "E102,Zeynep Sahin,Lead AI Engineer,Ar-Ge,5200,4.8\n"
    )

    def _write_legacy(self, data_dir: Path) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "employees.csv").write_text(self.LEGACY, encoding="utf-8")

    def test_legacy_columns_are_readable(self, wizard: CompanyWizard, data_dir: Path) -> None:
        self._write_legacy(data_dir)
        frame = wizard._read_employees()
        assert list(frame["employee_id"]) == ["EMP-0101", "EMP-0102"]
        assert list(frame["name"]) == ["Metehan Erbasc", "Zeynep Sahin"]
        assert list(frame["salary"]) == ["8500", "5200"]

    def test_extra_columns_are_preserved(self, wizard: CompanyWizard, data_dir: Path) -> None:
        self._write_legacy(data_dir)
        frame = wizard._read_employees()
        assert "performance_score" in frame.columns
        assert list(frame["performance_score"]) == ["4.9", "4.8"]

    def test_add_employee_works_on_legacy_file(self, wizard: CompanyWizard, data_dir: Path) -> None:
        self._write_legacy(data_dir)
        result = wizard.add_employee("Probe", "QA", "Ar-Ge", 1_000.0)
        assert result["employee"]["employee_id"] == "EMP-0103"
        content = (data_dir / "employees.csv").read_text(encoding="utf-8-sig")
        assert "performance_score" in content
        assert "4.9" in content
        assert "EMP-0101" in content

    def test_new_row_fills_extra_columns_with_blank(
        self, wizard: CompanyWizard, data_dir: Path
    ) -> None:
        self._write_legacy(data_dir)
        wizard.add_employee("Probe", "QA", "Ar-Ge", 1_000.0)
        lines = (data_dir / "employees.csv").read_text(encoding="utf-8-sig").strip().split("\n")
        assert lines[0].endswith("performance_score")
        assert lines[-1].endswith(",")

    def test_missing_start_date_defaults_to_today(
        self, wizard: CompanyWizard, data_dir: Path
    ) -> None:
        self._write_legacy(data_dir)
        frame = wizard._read_employees()
        today = date.today().isoformat()
        assert list(frame["start_date"]) == [today, today]

    def test_missing_status_defaults_to_active(self, wizard: CompanyWizard, data_dir: Path) -> None:
        self._write_legacy(data_dir)
        frame = wizard._read_employees()
        assert list(frame["status"]) == ["active", "active"]

    @pytest.mark.parametrize(
        ("header", "value", "expected"),
        [
            ("status", "Inactive", "inactive"),
            ("status", "pasif", "inactive"),
            ("durum", "Pasif", "inactive"),
            ("status", "Active", "active"),
            ("status", "", "active"),
        ],
    )
    def test_status_variants(
        self, wizard: CompanyWizard, data_dir: Path, header: str, value: str, expected: str
    ) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "employees.csv").write_text(
            f"id,full_name,role,department,monthly_salary_usd,{header}\n"
            f"E101,Ali,Dev,Ar-Ge,1000,{value}\n",
            encoding="utf-8",
        )
        frame = wizard._read_employees()
        assert list(frame["status"]) == [expected]

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("2024-03-15", "2024-03-15"),
            ("15.03.2024", "2024-03-15"),
            ("15/03/2024", "2024-03-15"),
            ("", date.today().isoformat()),
        ],
    )
    def test_start_date_formats(
        self, wizard: CompanyWizard, data_dir: Path, value: str, expected: str
    ) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "employees.csv").write_text(
            "id,full_name,role,department,monthly_salary_usd,start_date\n"
            f"E101,Ali,Dev,Ar-Ge,1000,{value}\n",
            encoding="utf-8",
        )
        frame = wizard._read_employees()
        assert list(frame["start_date"]) == [expected]

    def test_canonical_file_still_works(self, company: CompanyWizard) -> None:
        frame = company._read_employees()
        assert "employee_id" in frame.columns
        assert "EMP-0001" in list(frame["employee_id"])


class TestInspectDataSchema:
    def test_reports_all_files(self, company: CompanyWizard) -> None:
        report = company.inspect_data_schema()
        assert set(report["files"]) == {
            "employees.csv",
            "company_profile.json",
            "financials.json",
        }
        assert report["ready_for_tools"] is True
        assert report["blocking_files"] == []

    def test_reports_missing_files(self, wizard: CompanyWizard) -> None:
        report = wizard.inspect_data_schema()
        assert report["ready_for_tools"] is False
        assert set(report["blocking_files"]) == {
            "employees.csv",
            "company_profile.json",
            "financials.json",
        }
        assert report["files"]["employees.csv"]["status"] == "missing"

    def test_legacy_file_reported_compatible_with_mapping(
        self, wizard: CompanyWizard, data_dir: Path
    ) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "employees.csv").write_text(TestLegacyEmployeeSchema.LEGACY, encoding="utf-8")
        report = wizard.inspect_data_schema()
        info = report["files"]["employees.csv"]
        assert info["status"] == "compatible"
        assert info["row_count"] == 2
        assert info["mapped_columns"]["full_name"] == "name"
        assert info["mapped_columns"]["monthly_salary_usd"] == "salary"
        assert info["preserved_extra_columns"] == ["performance_score"]

    def test_incompatible_file_reports_reason(self, wizard: CompanyWizard, data_dir: Path) -> None:
        wizard.init_company("Acme", "Yazilim", 1_000.0)
        (data_dir / "employees.csv").write_text("id,full_name\n1,Ali\n", encoding="utf-8")
        report = wizard.inspect_data_schema()
        assert report["files"]["employees.csv"]["status"] == "incompatible"
        assert "missing required columns" in report["files"]["employees.csv"]["reason"]

    def test_incompatible_json_reported(self, wizard: CompanyWizard, data_dir: Path) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "company_profile.json").write_text(
            '{"company_name": "Aetheris", "founded_year": 2023}', encoding="utf-8"
        )
        report = wizard.inspect_data_schema()
        assert report["files"]["company_profile.json"]["status"] == "incompatible"
        assert "validation errors" in report["files"]["company_profile.json"]["reason"]


class TestMigrateCompanyData:
    def test_dry_run_does_not_write(self, wizard: CompanyWizard, data_dir: Path) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "employees.csv").write_text(TestLegacyEmployeeSchema.LEGACY, encoding="utf-8")
        result = wizard.migrate_company_data()
        assert result["applied"] is False
        assert result["changed"] is True
        assert "Dry run" in result["message"]
        assert (data_dir / "employees.csv").read_text(encoding="utf-8") == (
            TestLegacyEmployeeSchema.LEGACY
        )
        assert not list(data_dir.glob("employees.backup-*.csv"))

    def test_dry_run_reports_planned_changes(self, wizard: CompanyWizard, data_dir: Path) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "employees.csv").write_text(TestLegacyEmployeeSchema.LEGACY, encoding="utf-8")
        result = wizard.migrate_company_data()
        assert result["renamed_columns"]["id"] == "employee_id"
        assert result["renamed_columns"]["full_name"] == "name"
        assert result["preserved_extra_columns"] == ["performance_score"]
        assert "performance_score" in result["columns_after"]
        assert "start_date" in result["columns_after"]
        assert result["row_count"] == 2

    def test_apply_writes_canonical_schema(self, wizard: CompanyWizard, data_dir: Path) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "employees.csv").write_text(TestLegacyEmployeeSchema.LEGACY, encoding="utf-8")
        result = wizard.migrate_company_data(apply=True)
        assert result["applied"] is True
        header = (data_dir / "employees.csv").read_text(encoding="utf-8-sig").split("\n")[0]
        assert header == (
            "employee_id,name,role,department,salary,start_date,status,performance_score"
        )

    def test_apply_creates_backup(self, wizard: CompanyWizard, data_dir: Path) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "employees.csv").write_text(TestLegacyEmployeeSchema.LEGACY, encoding="utf-8")
        result = wizard.migrate_company_data(apply=True)
        backups = list(data_dir.glob("employees.backup-*.csv"))
        assert len(backups) == 1
        assert result["backup_file"] == backups[0].name
        assert backups[0].read_text(encoding="utf-8") == TestLegacyEmployeeSchema.LEGACY

    def test_apply_preserves_row_data(self, wizard: CompanyWizard, data_dir: Path) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "employees.csv").write_text(TestLegacyEmployeeSchema.LEGACY, encoding="utf-8")
        wizard.migrate_company_data(apply=True)
        frame = pd.read_csv(data_dir / "employees.csv", dtype=object, keep_default_na=False)
        assert list(frame["employee_id"]) == ["EMP-0101", "EMP-0102"]
        assert list(frame["name"]) == ["Metehan Erbasc", "Zeynep Sahin"]
        assert list(frame["salary"]) == ["8500", "5200"]
        assert list(frame["performance_score"]) == ["4.9", "4.8"]

    def test_canonical_file_needs_no_migration(self, company: CompanyWizard) -> None:
        result = company.migrate_company_data()
        assert result["changed"] is False
        assert "already uses the current schema" in result["message"]

    def test_unmigratable_file_rejected(self, wizard: CompanyWizard, data_dir: Path) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "employees.csv").write_text("id,full_name\n1,Ali\n", encoding="utf-8")
        with pytest.raises(ValueError) as excinfo:
            wizard.migrate_company_data(apply=True)
        assert "cannot be migrated automatically" in str(excinfo.value)
        assert (data_dir / "employees.csv").read_text(encoding="utf-8") == "id,full_name\n1,Ali\n"

    def test_missing_file(self, wizard: CompanyWizard) -> None:
        with pytest.raises(FileNotFoundError):
            wizard.migrate_company_data(apply=True)

    def test_migrated_file_is_then_writable(self, wizard: CompanyWizard, data_dir: Path) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "employees.csv").write_text(TestLegacyEmployeeSchema.LEGACY, encoding="utf-8")
        wizard.migrate_company_data(apply=True)
        result = wizard.add_employee("Probe", "QA", "Ar-Ge", 1_000.0)
        assert result["employee"]["employee_id"] == "EMP-0103"


REAL_PROFILE = {
    "company_name": "Aetheris Dynamics Tech A.Ş.",
    "founded_year": 2023,
    "sector": "Enterprise Software & AI Solutions",
    "headquarters": "Istanbul, Turkiye",
    "status": "Active / Series-A Funded",
    "vision": "Otonom yapay zeka ajanlari ile sureclerin yuzde 80 hizlanmasi.",
    "metrics": {"arr_usd": 1200000, "total_employees": 24},
    "departments": [
        {"name": "Yazilim ve Yapay Zeka Ar-Ge", "lead": "Metehan (CTO)", "headcount": 10},
        {"name": "Urun Yonetimi (Product)", "lead": "Selin (CPO)", "headcount": 4},
    ],
}

REAL_FINANCIALS = {
    "currency": "USD",
    "fiscal_year": 2026,
    "quarter": "Q3",
    "bank_balance": 850000.00,
    "monthly_runway_months": 14,
    "revenue_breakdown_monthly": [
        {"month": "Haziran", "mrr": 95000, "expenses": 62000, "net_profit": 33000},
        {"month": "Temmuz", "mrr": 102000, "expenses": 65000, "net_profit": 37000},
    ],
    "major_expense_categories": {"payroll_salaries": "%55", "marketing_and_events": "%8"},
    "pending_invoices_receivable": [
        {
            "client": "Global Logistics",
            "amount": 25000,
            "due_date": "2026-10-15",
            "status": "Pending",
        }
    ],
}


def write_json(data_dir: Path, filename: str, payload: dict[str, object]) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / filename).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def write_real_files(data_dir: Path) -> None:
    write_json(data_dir, "company_profile.json", REAL_PROFILE)
    write_json(data_dir, "financials.json", REAL_FINANCIALS)


def write_file(data_dir: Path, filename: str, content: str) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / filename).write_text(content, encoding="utf-8")


def snapshot(data_dir: Path) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in sorted(data_dir.iterdir())}


def _add_cash_flow_period(data_dir: Path, period: str, opening: str, net_cash_flow: str) -> None:
    """Append one projection period to an existing financials.json."""
    path = data_dir / "financials.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["cash_flow_template"].append(
        {
            "period": period,
            "opening_balance": opening,
            "net_cash_flow": net_cash_flow,
            "closing_balance": str(Decimal(opening) + Decimal(net_cash_flow)),
        }
    )
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


class TestPlanCompanyDataMigration:
    def test_never_writes_anything(self, wizard: CompanyWizard, data_dir: Path) -> None:
        write_real_files(data_dir)
        before = snapshot(data_dir)
        report = wizard.plan_company_data_migration()
        assert report["writes_performed"] is False
        assert snapshot(data_dir) == before
        assert sorted(before) == ["company_profile.json", "financials.json"]

    def test_missing_files_reported(self, wizard: CompanyWizard) -> None:
        report = wizard.plan_company_data_migration()
        assert report["files"]["company_profile.json"]["status"] == "missing"
        assert report["files"]["financials.json"]["status"] == "missing"
        assert report["blocking_files"] == [
            "company_profile.json",
            "financials.json",
        ]

    def test_canonical_files_need_no_decisions(self, company: CompanyWizard) -> None:
        report = company.plan_company_data_migration()
        assert report["files"]["company_profile.json"]["status"] == "compatible"
        assert report["files"]["financials.json"]["status"] == "compatible"
        assert report["blocking_files"] == []
        assert report["cross_file_questions"] == []

    def test_valid_fields_are_reused_unchanged(self, wizard: CompanyWizard, data_dir: Path) -> None:
        write_real_files(data_dir)
        plan = wizard.plan_company_data_migration()["files"]["company_profile.json"]
        assert plan["already_valid"] == {
            "company_name": REAL_PROFILE["company_name"],
            "sector": REAL_PROFILE["sector"],
            "vision": REAL_PROFILE["vision"],
        }

    def test_founded_year_needs_confirmation(self, wizard: CompanyWizard, data_dir: Path) -> None:
        write_real_files(data_dir)
        plan = wizard.plan_company_data_migration()["files"]["company_profile.json"]
        item = next(i for i in plan["mappable"] if i["source_field"] == "founded_year")
        assert item["target_field"] == "established_date"
        assert item["proposed_value"] == "2023-01-01"
        assert item["confidence"] == "needs_confirmation"
        assert "founding month" in item["note"]

    def test_department_objects_lose_their_extra_keys(
        self, wizard: CompanyWizard, data_dir: Path
    ) -> None:
        write_real_files(data_dir)
        plan = wizard.plan_company_data_migration()["files"]["company_profile.json"]
        item = next(i for i in plan["mappable"] if i["source_field"] == "departments")
        assert item["proposed_value"] == [
            "Yazilim ve Yapay Zeka Ar-Ge",
            "Urun Yonetimi (Product)",
        ]
        assert "headcount" in item["note"]
        assert "lead" in item["note"]

    def test_free_form_status_is_narrowed(self, wizard: CompanyWizard, data_dir: Path) -> None:
        write_real_files(data_dir)
        plan = wizard.plan_company_data_migration()["files"]["company_profile.json"]
        item = next(i for i in plan["mappable"] if i["source_field"] == "status")
        assert item["proposed_value"] == "active"
        assert "Series-A Funded" in item["note"]

    def test_fields_without_a_target_are_listed_with_a_reason(
        self, wizard: CompanyWizard, data_dir: Path
    ) -> None:
        write_real_files(data_dir)
        report = wizard.plan_company_data_migration()
        assert "metrics" in report["files"]["company_profile.json"]["no_target"]
        financials = report["files"]["financials.json"]["no_target"]
        assert "overstate income" in financials["pending_invoices_receivable"]
        assert "percentage shares" in financials["major_expense_categories"]
        assert financials["monthly_runway_months"]

    def test_missing_mission_is_a_question(self, wizard: CompanyWizard, data_dir: Path) -> None:
        write_real_files(data_dir)
        report = wizard.plan_company_data_migration()
        profile = report["files"]["company_profile.json"]
        assert profile["missing_required"] == ["mission"]
        assert profile["status"] == "needs_decisions"
        assert any("company_profile.json:mission" in question for question in profile["questions"])
        assert "proposed_document" not in profile

    def test_bank_balance_maps_to_opening_balance(
        self, wizard: CompanyWizard, data_dir: Path
    ) -> None:
        write_real_files(data_dir)
        plan = wizard.plan_company_data_migration()["files"]["financials.json"]
        item = next(i for i in plan["mappable"] if i["source_field"] == "bank_balance")
        assert item["target_field"] == "opening_balance"
        assert item["confidence"] == "auto"
        assert item["proposed_value"] == 850000.0

    def test_monthly_breakdown_expands_to_records(
        self, wizard: CompanyWizard, data_dir: Path
    ) -> None:
        write_real_files(data_dir)
        plan = wizard.plan_company_data_migration()["files"]["financials.json"]
        item = next(i for i in plan["mappable"] if i["source_field"] == "revenue_breakdown_monthly")
        assert item["target_field"] == "records"
        assert item["proposed_value"]["items"] == 4
        assert item["proposed_value"]["first_items"][0] == {
            "type": "income",
            "category": "sales",
            "amount": 95000,
            "description": "Monthly revenue - Haziran",
        }
        assert item["confidence"] == "needs_confirmation"

    def test_cross_file_currency_mismatch_detected(
        self, wizard: CompanyWizard, data_dir: Path
    ) -> None:
        write_json(data_dir, "financials.json", dict(REAL_FINANCIALS, currency="EUR"))
        write_json(
            data_dir,
            "company_profile.json",
            dict(REAL_PROFILE, currency="TRY", established_date="2023-05-01"),
        )
        report = wizard.plan_company_data_migration()
        assert any(
            "'TRY'" in question and "'EUR'" in question
            for question in report["cross_file_questions"]
        )

    def test_cross_file_company_name_suggestion_becomes_answerable(
        self, wizard: CompanyWizard, data_dir: Path
    ) -> None:
        write_real_files(data_dir)
        report = wizard.plan_company_data_migration()
        item = next(
            item
            for item in report["files"]["financials.json"]["open_items"]
            if item["key"] == "financials.json:company_name"
        )
        assert "Aetheris" in item["note"]
        assert "company_profile.json" in item["note"]

    def test_cross_file_currency_default_becomes_answerable(
        self, wizard: CompanyWizard, data_dir: Path
    ) -> None:
        write_real_files(data_dir)
        report = wizard.plan_company_data_migration()
        item = next(
            item
            for item in report["files"]["company_profile.json"]["open_items"]
            if item["key"] == "company_profile.json:currency"
        )
        assert "canonical default 'TRY'" in item["note"]

    def test_proposed_document_returned_when_nothing_to_decide(
        self, wizard: CompanyWizard, data_dir: Path
    ) -> None:
        write_json(
            data_dir,
            "company_profile.json",
            {
                "company_name": "Acme",
                "sector": "Yazilim",
                "established_date": "2023-05-01",
                "vision": "v",
                "mission": "m",
                "currency": "USD",
                "departments": ["Ar-Ge", "Finans"],
            },
        )
        plan = wizard.plan_company_data_migration()["files"]["company_profile.json"]
        assert plan["status"] == "compatible"
        assert plan["questions"] == []
        assert plan["proposed_document"]["company_name"] == "Acme"
        assert plan["proposed_document"]["established_date"] == "2023-05-01"

    def test_alias_names_are_recognized(self, wizard: CompanyWizard, data_dir: Path) -> None:
        write_json(
            data_dir,
            "company_profile.json",
            {
                "legal_name": "Acme",
                "industry": "Yazilim",
                "established_at": "2023-05-01T10:00:00Z",
                "purpose": "m",
                "vision": "v",
                "teams": ["Ar-Ge"],
                "status": "Active",
            },
        )
        plan = wizard.plan_company_data_migration()["files"]["company_profile.json"]
        # established_at carries a time, so it can only be proposed, not applied as is.
        assert plan["status"] == "needs_decisions"
        assert "proposed_document" not in plan
        assert any("time part" in question for question in plan["questions"])
        assert "proposed_document" not in plan
        proposed = {item["source_field"]: item for item in plan["mappable"]}
        assert proposed["legal_name"]["target_field"] == "company_name"
        assert proposed["industry"]["target_field"] == "sector"
        assert proposed["established_at"]["proposed_value"] == "2023-05-01"
        assert proposed["purpose"]["target_field"] == "mission"
        assert proposed["purpose"]["confidence"] == "auto"
        assert proposed["teams"]["proposed_value"] == ["Ar-Ge"]
        assert proposed["status"]["proposed_value"] == "active"

    def test_broken_json_is_reported_not_raised(
        self, wizard: CompanyWizard, data_dir: Path
    ) -> None:
        write_file(data_dir, "company_profile.json", "{not json")
        plan = wizard.plan_company_data_migration()["files"]["company_profile.json"]
        assert plan["status"] == "unreadable"
        assert "not valid JSON" in plan["reason"]

    def test_json_array_is_reported(self, wizard: CompanyWizard, data_dir: Path) -> None:
        write_file(data_dir, "financials.json", "[1, 2, 3]")
        plan = wizard.plan_company_data_migration()["files"]["financials.json"]
        assert plan["status"] == "unreadable"
        assert "JSON object at the top level" in plan["reason"]

    def test_conflicting_targets_are_reported(self, wizard: CompanyWizard, data_dir: Path) -> None:
        write_json(
            data_dir,
            "company_profile.json",
            {
                "company_name": "Acme",
                "legal_name": "Acme A.Ş.",
                "sector": "Yazilim",
                "established_date": "2023-05-01",
                "vision": "v",
                "mission": "m",
            },
        )
        plan = wizard.plan_company_data_migration()["files"]["company_profile.json"]
        assert plan["already_valid"]["company_name"] == "Acme"
        assert plan["skipped_conflicts"][0]["field"] == "legal_name"

    def test_unusable_value_is_reported(self, wizard: CompanyWizard, data_dir: Path) -> None:
        write_json(
            data_dir,
            "financials.json",
            {"currency": "USD", "initial_budget": "cok", "opening_balance": 1},
        )
        plan = wizard.plan_company_data_migration()["files"]["financials.json"]
        assert plan["incompatible_values"] == [
            {"field": "initial_budget", "reason": "amount must be a valid number"}
        ]

    def test_non_numeric_ledger_transaction_is_reported(
        self, wizard: CompanyWizard, data_dir: Path
    ) -> None:
        write_json(
            data_dir,
            "financials.json",
            {"currency": "USD", "transactions": [{"type": "income", "amount": 10}]},
        )
        plan = wizard.plan_company_data_migration()["files"]["financials.json"]
        item = plan["mappable"][0]
        assert item["proposed_value"] == [
            {
                "type": "income",
                "category": "",
                "amount": 10,
                "description": "",
            }
        ]
        assert "new id" in item["note"]

    def test_ledger_transaction_without_type_is_reported(
        self, wizard: CompanyWizard, data_dir: Path
    ) -> None:
        write_json(
            data_dir,
            "financials.json",
            {"currency": "USD", "transactions": [{"amount": 10}]},
        )
        plan = wizard.plan_company_data_migration()["files"]["financials.json"]
        assert plan["incompatible_values"] == [
            {
                "field": "transactions",
                "reason": "every transaction needs 'type' set to income or expense",
            }
        ]

    def test_month_without_revenue_is_reported(self, wizard: CompanyWizard, data_dir: Path) -> None:
        write_json(
            data_dir,
            "financials.json",
            {"currency": "USD", "monthly_breakdown": [{"month": "Ocak", "mrr": 10}]},
        )
        plan = wizard.plan_company_data_migration()["files"]["financials.json"]
        assert "revenue and an expense amount" in plan["incompatible_values"][0]["reason"]

    def test_monthly_entry_without_label_is_reported(
        self, wizard: CompanyWizard, data_dir: Path
    ) -> None:
        write_json(
            data_dir,
            "financials.json",
            {"currency": "USD", "monthly_breakdown": [{"mrr": 10, "expenses": 5}]},
        )
        plan = wizard.plan_company_data_migration()["files"]["financials.json"]
        assert "month" in plan["incompatible_values"][0]["reason"]

    def test_empty_monthly_list_is_reported(self, wizard: CompanyWizard, data_dir: Path) -> None:
        write_json(data_dir, "financials.json", {"currency": "USD", "monthly_breakdown": []})
        plan = wizard.plan_company_data_migration()["files"]["financials.json"]
        assert "non-empty list" in plan["incompatible_values"][0]["reason"]

    def test_departments_wrong_shape_is_reported(
        self, wizard: CompanyWizard, data_dir: Path
    ) -> None:
        write_json(
            data_dir,
            "company_profile.json",
            {"company_name": "A", "sector": "B", "departments": 5},
        )
        plan = wizard.plan_company_data_migration()["files"]["company_profile.json"]
        assert "non-empty list" in plan["incompatible_values"][0]["reason"]

    def test_department_item_without_name_is_reported(
        self, wizard: CompanyWizard, data_dir: Path
    ) -> None:
        write_json(
            data_dir,
            "company_profile.json",
            {"company_name": "A", "sector": "B", "departments": [{"lead": "X"}]},
        )
        plan = wizard.plan_company_data_migration()["files"]["company_profile.json"]
        assert "'name' field" in plan["incompatible_values"][0]["reason"]

    def test_bad_year_is_reported(self, wizard: CompanyWizard, data_dir: Path) -> None:
        write_json(
            data_dir,
            "company_profile.json",
            {"company_name": "A", "sector": "B", "founded_year": "1999 yili"},
        )
        plan = wizard.plan_company_data_migration()["files"]["company_profile.json"]
        assert "four-digit year" in plan["incompatible_values"][0]["reason"]

    def test_bad_status_is_reported(self, wizard: CompanyWizard, data_dir: Path) -> None:
        write_json(
            data_dir,
            "company_profile.json",
            {"company_name": "A", "sector": "B", "status": "dondurulmus"},
        )
        plan = wizard.plan_company_data_migration()["files"]["company_profile.json"]
        assert "expected 'template' or 'active'" in plan["incompatible_values"][0]["reason"]

    def test_bad_date_is_reported(self, wizard: CompanyWizard, data_dir: Path) -> None:
        write_json(
            data_dir,
            "company_profile.json",
            {"company_name": "A", "sector": "B", "founded": "Mart 2023"},
        )
        plan = wizard.plan_company_data_migration()["files"]["company_profile.json"]
        assert "expected an ISO date" in plan["incompatible_values"][0]["reason"]

    def test_canonical_field_with_bad_value_is_reported(
        self, wizard: CompanyWizard, data_dir: Path
    ) -> None:
        write_json(
            data_dir,
            "company_profile.json",
            {"company_name": "A", "sector": "B", "status": "acik"},
        )
        plan = wizard.plan_company_data_migration()["files"]["company_profile.json"]
        assert plan["incompatible_values"] == [
            {"field": "status", "reason": "expected 'template' or 'active', received 'acik'"}
        ]

    def test_unknown_transform_guard(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            _apply_transform("yok", 1)
        assert "unknown transform" in str(excinfo.value)

    def test_category_names_from_dict_keys(self, wizard: CompanyWizard, data_dir: Path) -> None:
        write_json(
            data_dir,
            "financials.json",
            {
                "currency": "USD",
                "expense_categories": {"payroll": 1, "rent": 2},
            },
        )
        plan = wizard.plan_company_data_migration()["files"]["financials.json"]
        item = next(i for i in plan["mappable"] if i["source_field"] == "expense_categories")
        assert item["proposed_value"] == ["payroll", "rent"]

    def test_next_step_explains_manual_work(self, wizard: CompanyWizard, data_dir: Path) -> None:
        write_real_files(data_dir)
        report = wizard.plan_company_data_migration()
        assert "Nothing was written" in report["next_step"]
        assert "migrate_company_data" in report["next_step"]

    def test_open_items_are_machine_readable(self, wizard: CompanyWizard, data_dir: Path) -> None:
        write_real_files(data_dir)
        report = wizard.plan_company_data_migration()
        keys = {item["key"] for item in report["files"]["company_profile.json"]["open_items"]}
        assert "company_profile.json:mission" in keys
        assert "company_profile.json:established_date" in keys
        assert report["answer_keys"] == sorted(
            keys | {item["key"] for item in report["files"]["financials.json"]["open_items"]}
        )

    def test_auto_mapped_fields_create_no_open_item(
        self, wizard: CompanyWizard, data_dir: Path
    ) -> None:
        write_real_files(data_dir)
        report = wizard.plan_company_data_migration()
        keys = {item["key"] for item in report["files"]["financials.json"]["open_items"]}
        assert "financials.json:opening_balance" not in keys
        assert "financials.json:currency" not in keys

    def test_cross_file_company_name_becomes_an_open_item(
        self, wizard: CompanyWizard, data_dir: Path
    ) -> None:
        write_real_files(data_dir)
        report = wizard.plan_company_data_migration()
        item = next(
            item
            for item in report["files"]["financials.json"]["open_items"]
            if item["key"] == "financials.json:company_name"
        )
        assert item["kind"] == "confirmation"
        assert item["proposed_value"] == REAL_PROFILE["company_name"]
        assert "company_name" not in report["files"]["financials.json"]["missing_required"]

    def test_currency_question_becomes_an_open_item(
        self, wizard: CompanyWizard, data_dir: Path
    ) -> None:
        write_real_files(data_dir)
        report = wizard.plan_company_data_migration()
        item = next(
            item
            for item in report["files"]["company_profile.json"]["open_items"]
            if item["key"] == "company_profile.json:currency"
        )
        assert item["proposed_value"] == "USD"
        assert "canonical default 'TRY'" in item["note"]


def full_answers() -> dict[str, Any]:
    return {
        "company_profile.json:mission": "Kurumsal surecleri hizlandirmak.",
        "company_profile.json:established_date": True,
        "company_profile.json:status": True,
        "company_profile.json:departments": True,
        "company_profile.json:currency": True,
        "financials.json:initial_budget": 850_000,
        "financials.json:records": True,
        "financials.json:company_name": True,
    }


class TestApplyCompanyDataMigration:
    def test_requires_every_answer(self, wizard: CompanyWizard, data_dir: Path) -> None:
        write_real_files(data_dir)
        with pytest.raises(ValueError) as excinfo:
            wizard.apply_company_data_migration(answers={})
        assert "still need an answer" in str(excinfo.value)
        assert "company_profile.json:mission" in str(excinfo.value)

    def test_rejects_unknown_answer_keys(self, wizard: CompanyWizard, data_dir: Path) -> None:
        write_real_files(data_dir)
        answers = full_answers()
        answers["financials.json:gecersiz"] = 1
        with pytest.raises(ValueError) as excinfo:
            wizard.apply_company_data_migration(answers=answers)
        assert "Unknown answer keys: financials.json:gecersiz" in str(excinfo.value)

    def test_dry_run_writes_nothing(self, wizard: CompanyWizard, data_dir: Path) -> None:
        write_real_files(data_dir)
        before = snapshot(data_dir)
        result = wizard.apply_company_data_migration(answers=full_answers())
        assert result["applied"] is False
        assert snapshot(data_dir) == before
        assert not list(data_dir.glob("*.backup-*.json"))
        assert "Dry run only" in result["message"]

    def test_apply_writes_canonical_documents(self, wizard: CompanyWizard, data_dir: Path) -> None:
        write_real_files(data_dir)
        result = wizard.apply_company_data_migration(answers=full_answers(), apply=True)
        assert result["applied"] is True
        assert result["changed"] == ["company_profile.json", "financials.json"]
        profile = json.loads((data_dir / "company_profile.json").read_text(encoding="utf-8"))
        assert profile["company_name"] == REAL_PROFILE["company_name"]
        assert profile["mission"] == "Kurumsal surecleri hizlandirmak."
        assert profile["established_date"] == "2023-01-01"
        assert profile["status"] == "active"
        assert profile["currency"] == "USD"
        assert profile["departments"] == [
            "Yazilim ve Yapay Zeka Ar-Ge",
            "Urun Yonetimi (Product)",
        ]
        financials = json.loads((data_dir / "financials.json").read_text(encoding="utf-8"))
        assert financials["company_name"] == REAL_PROFILE["company_name"]
        assert financials["currency"] == "USD"
        assert Decimal(financials["opening_balance"]) == Decimal("850000")
        assert Decimal(financials["initial_budget"]) == Decimal("850000")
        assert len(financials["records"]) == 4
        assert financials["cash_flow_template"]

    def test_apply_creates_backups_of_originals(
        self, wizard: CompanyWizard, data_dir: Path
    ) -> None:
        write_real_files(data_dir)
        originals = snapshot(data_dir)
        result = wizard.apply_company_data_migration(answers=full_answers(), apply=True)
        assert set(result["backups"]) == {"company_profile.json", "financials.json"}
        for name, backup in result["backups"].items():
            assert (data_dir / backup).read_bytes() == originals[name]

    def test_apply_is_idempotent(self, wizard: CompanyWizard, data_dir: Path) -> None:
        write_real_files(data_dir)
        wizard.apply_company_data_migration(answers=full_answers(), apply=True)
        result = wizard.apply_company_data_migration(answers={}, apply=True)
        assert result["changed"] == []
        assert result["backups"] == {}
        assert "already matched" in result["message"]

    def test_explicit_value_overrides_proposal(self, wizard: CompanyWizard, data_dir: Path) -> None:
        write_real_files(data_dir)
        answers = full_answers()
        answers["company_profile.json:established_date"] = "2023-06-15"
        answers["company_profile.json:departments"] = ["Sadece Ar-Ge"]
        wizard.apply_company_data_migration(answers=answers, apply=True)
        profile = json.loads((data_dir / "company_profile.json").read_text(encoding="utf-8"))
        assert profile["established_date"] == "2023-06-15"
        assert profile["departments"] == ["Sadece Ar-Ge"]

    def test_invalid_answer_is_rejected_before_writing(
        self, wizard: CompanyWizard, data_dir: Path
    ) -> None:
        write_real_files(data_dir)
        before = snapshot(data_dir)
        answers = full_answers()
        answers["financials.json:initial_budget"] = "cok"
        with pytest.raises(ValueError) as excinfo:
            wizard.apply_company_data_migration(answers=answers, apply=True)
        assert "still invalid after applying the answers" in str(excinfo.value)
        assert snapshot(data_dir) == before

    def test_true_is_rejected_where_nothing_is_suggested(
        self, wizard: CompanyWizard, data_dir: Path
    ) -> None:
        write_real_files(data_dir)
        before = snapshot(data_dir)
        answers = full_answers()
        answers["financials.json:initial_budget"] = True
        with pytest.raises(ValueError) as excinfo:
            wizard.apply_company_data_migration(answers=answers, apply=True)
        assert "No suggested value for: financials.json:initial_budget" in str(excinfo.value)
        assert snapshot(data_dir) == before

    def test_missing_file_is_rejected(self, wizard: CompanyWizard, data_dir: Path) -> None:
        write_json(data_dir, "company_profile.json", dict(REAL_PROFILE))
        with pytest.raises(ValueError) as excinfo:
            wizard.apply_company_data_migration(answers={}, apply=True)
        assert "financials.json cannot be migrated" in str(excinfo.value)

    def test_canonical_files_need_no_answers(self, company: CompanyWizard, data_dir: Path) -> None:
        before = snapshot(data_dir)
        result = company.apply_company_data_migration(answers={}, apply=True)
        assert result["changed"] == []
        assert snapshot(data_dir) == before
        assert result["backups"] == {}

    def test_result_echoes_resolved_answers(self, wizard: CompanyWizard, data_dir: Path) -> None:
        write_real_files(data_dir)
        result = wizard.apply_company_data_migration(answers=full_answers())
        assert result["resolved"]["company_profile.json:mission"] == (
            "Kurumsal surecleri hizlandirmak."
        )
        assert result["resolved"]["financials.json:records"] is True


class TestNotes:
    def test_creates_note_in_notes_file(self, company: CompanyWizard, data_dir: Path) -> None:
        result = company.update_company_notes("Toplanti Notu", "Gundem maddeleri")
        assert result["action"] == "created"
        assert result["note"]["title"] == "Toplanti Notu"
        notes_file = data_dir / "company_notes.json"
        assert notes_file.exists()
        assert "Toplanti Notu" in notes_file.read_text(encoding="utf-8")

    def test_updates_existing_note(self, company: CompanyWizard) -> None:
        first = company.update_company_notes("Politika", "Ilk surum")
        second = company.update_company_notes("Politika", "Guncellenmis surum")
        assert first["action"] == "created"
        assert second["action"] == "updated"
        assert "Guncellenmis surum" in second["note"]["content"]

    def test_rejects_empty_title(self, company: CompanyWizard) -> None:
        with pytest.raises(ValueError):
            company.update_company_notes("  ", "icerik")

    def test_multiple_notes_are_kept(self, company: CompanyWizard, data_dir: Path) -> None:
        company.update_company_notes("Toplanti Notu", "birinci")
        company.update_company_notes("Vizyon Notu", "ikinci")
        notes_file = (data_dir / "company_notes.json").read_text(encoding="utf-8")
        assert "Toplanti Notu" in notes_file
        assert "Vizyon Notu" in notes_file
        assert "birinci" in notes_file
        assert "ikinci" in notes_file

    def test_notes_survive_across_instances(self, data_dir: Path) -> None:
        first = CompanyWizard(FileHandler(data_dir=data_dir))
        first.init_company("Acme", "Yazilim", 10_000.0)
        first.update_company_notes("Toplanti Notu", "kalici icerik")
        second = CompanyWizard(FileHandler(data_dir=data_dir))
        second.update_company_notes("Vizyon Notu", "ikinci not")
        notes_file = (data_dir / "company_notes.json").read_text(encoding="utf-8")
        assert "kalici icerik" in notes_file
        assert "Toplanti Notu" in notes_file
        assert "Vizyon Notu" in notes_file


class TestOverview:
    def test_reports_company_and_financials(self, company: CompanyWizard) -> None:
        overview = company.get_company_overview()
        assert overview["company"]["name"] == "Acme"
        assert overview["financials"]["currency"] == "TRY"
        assert overview["generated_at"]

    def test_missing_data_raises_readable_error(self, wizard: CompanyWizard) -> None:
        with pytest.raises(FileNotFoundError) as excinfo:
            wizard.get_company_overview()
        assert "not found" in str(excinfo.value).lower()

    def test_corrupt_financials_raises(self, company: CompanyWizard, data_dir: Path) -> None:
        (data_dir / "financials.json").write_text("{bozuk", encoding="utf-8")
        with pytest.raises(ValueError):
            company.get_company_overview()


class TestGetFinancialReport:
    def test_totals_and_budget_usage(self, company: CompanyWizard) -> None:
        company.add_financial_record("income", "sales", 4_000.0, "satis")
        company.add_financial_record("expense", "rent", 1_500.0, "kira")
        totals = company.get_financial_report()["totals"]
        assert totals["total_income"] == Decimal("4000.00")
        assert totals["total_expenses"] == Decimal("1500.00")
        assert totals["remaining_budget"] == Decimal("8500.00")
        assert totals["budget_used_percent"] == 15.0
        assert totals["transaction_count"] == 2

    def test_income_split_by_category_largest_first(self, company: CompanyWizard) -> None:
        company.add_financial_record("income", "services", 1_000.0, "a")
        company.add_financial_record("income", "sales", 3_000.0, "b")
        company.add_financial_record("income", "sales", 500.0, "c")
        rows = company.get_financial_report()["income_by_category"]
        assert [row["category"] for row in rows] == ["sales", "services"]
        assert rows[0]["amount"] == Decimal("3500.00")
        assert rows[0]["record_count"] == 2
        assert rows[0]["share_percent"] == 77.8
        assert rows[1]["share_percent"] == 22.2

    def test_expenses_split_is_separate_from_income(self, company: CompanyWizard) -> None:
        company.add_financial_record("income", "rent", 900.0, "yanlis yon")
        company.add_financial_record("expense", "rent", 400.0, "dogru yon")
        report = company.get_financial_report()
        assert report["income_by_category"] == [
            {
                "category": "rent",
                "amount": Decimal("900.00"),
                "record_count": 1,
                "share_percent": 100.0,
            }
        ]
        assert report["expenses_by_category"][0]["amount"] == Decimal("400.00")

    def test_empty_ledger_has_empty_splits(self, company: CompanyWizard) -> None:
        report = company.get_financial_report()
        assert report["income_by_category"] == []
        assert report["expenses_by_category"] == []
        assert report["totals"]["budget_used_percent"] == 0.0
        assert report["warnings"] == []

    def test_zero_budget_leaves_percentage_empty(self, wizard: CompanyWizard) -> None:
        wizard.init_company("Acme", "Yazilim", 0.0)
        assert wizard.get_financial_report()["totals"]["budget_used_percent"] is None

    def test_cash_flow_periods_are_listed(self, company: CompanyWizard) -> None:
        cash_flow = company.get_financial_report()["cash_flow"]
        assert cash_flow["period_count"] == 1
        assert cash_flow["period_filter"] is None
        assert cash_flow["periods"] == [
            {
                "period": "initial",
                "opening_balance": Decimal("10000.0"),
                "net_cash_flow": Decimal("0.00"),
                "closing_balance": Decimal("10000.0"),
            }
        ]

    def test_period_filter_narrows_the_table(self, company: CompanyWizard, data_dir: Path) -> None:
        _add_cash_flow_period(data_dir, "2025-Q1", "12000.00", "1500.00")
        report = company.get_financial_report(period="2025-Q1")
        assert report["cash_flow"]["period_filter"] == "2025-Q1"
        assert [row["period"] for row in report["cash_flow"]["periods"]] == ["2025-Q1"]
        assert report["cash_flow"]["total_net_cash_flow"] == Decimal("1500.00")

    def test_all_periods_are_reported_without_a_filter(
        self, company: CompanyWizard, data_dir: Path
    ) -> None:
        _add_cash_flow_period(data_dir, "2025-Q1", "12000.00", "1500.00")
        _add_cash_flow_period(data_dir, "2025-Q2", "13500.00", "2000.00")
        cash_flow = company.get_financial_report()["cash_flow"]
        assert cash_flow["period_count"] == 3
        assert cash_flow["total_net_cash_flow"] == Decimal("3500.00")

    def test_period_filter_is_trimmed(self, company: CompanyWizard, data_dir: Path) -> None:
        _add_cash_flow_period(data_dir, "2025-Q1", "12000.00", "1500.00")
        report = company.get_financial_report(period="  2025-Q1  ")
        assert report["cash_flow"]["period_filter"] == "2025-Q1"

    def test_unknown_period_lists_the_available_ones(
        self, company: CompanyWizard, data_dir: Path
    ) -> None:
        _add_cash_flow_period(data_dir, "2025-Q1", "12000.00", "1500.00")
        with pytest.raises(ValueError) as excinfo:
            company.get_financial_report(period="2026-Q1")
        assert "unknown period '2026-Q1'" in str(excinfo.value)
        assert "'initial'" in str(excinfo.value)
        assert "'2025-Q1'" in str(excinfo.value)

    def test_warns_when_expenses_exceed_the_budget(self, company: CompanyWizard) -> None:
        company.add_financial_record("expense", "payroll", 12_000.0, "maas")
        warnings = company.get_financial_report()["warnings"]
        assert any("exceed the initial budget" in warning for warning in warnings)

    def test_warns_when_the_balance_is_negative(self, wizard: CompanyWizard) -> None:
        wizard.init_company("Acme", "Yazilim", 0.0)
        wizard.add_financial_record("expense", "rent", 500.0, "kira")
        warnings = wizard.get_financial_report()["warnings"]
        assert any("current balance is negative" in warning for warning in warnings)

    def test_healthy_ledger_has_no_warnings(self, company: CompanyWizard) -> None:
        company.add_financial_record("income", "sales", 20_000.0, "satis")
        assert company.get_financial_report()["warnings"] == []

    def test_report_carries_company_and_currency(self, company: CompanyWizard) -> None:
        report = company.get_financial_report()
        assert report["company_name"] == "Acme"
        assert report["currency"] == "TRY"
        assert report["generated_at"]

    def test_report_does_not_write(self, company: CompanyWizard, data_dir: Path) -> None:
        company.add_financial_record("income", "sales", 1_000.0, "satis")
        before = snapshot(data_dir)
        company.get_financial_report()
        company.get_financial_report(period="initial")
        assert snapshot(data_dir) == before

    def test_missing_ledger_raises_readable_error(self, wizard: CompanyWizard) -> None:
        with pytest.raises(FileNotFoundError) as excinfo:
            wizard.get_financial_report()
        assert "not found" in str(excinfo.value).lower()

    def test_works_on_a_migrated_real_ledger(self, wizard: CompanyWizard, data_dir: Path) -> None:
        write_json(data_dir, "company_profile.json", dict(REAL_PROFILE))
        write_json(data_dir, "financials.json", dict(REAL_FINANCIALS))
        wizard.apply_company_data_migration(answers=full_answers(), apply=True)
        report = wizard.get_financial_report()
        totals = report["totals"]
        assert report["currency"] == "USD"
        assert totals["opening_balance"] == Decimal("850000.0")
        assert totals["transaction_count"] == 4
        assert totals["current_balance"] == (totals["opening_balance"] + totals["net_cash_flow"])
        assert report["expenses_by_category"]
        assert report["income_by_category"][0]["amount"] > Decimal("0")
        assert report["cash_flow"]["period_count"] == 1


class TestEmployeeIdDefaults:
    """Regression tests: a legacy file without usable ids must still get stable ones."""

    NO_ID = (
        "full_name,role,department,monthly_salary_usd\n"
        "Deniz Kaya,Developer,Ar-Ge,90000\n"
        "Asli Yilmaz,Designer,Ar-Ge,80000\n"
        "Mert Aydin,Product Owner,Growth,70000\n"
    )

    def _write(self, data_dir: Path, content: str) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "employees.csv").write_text(content, encoding="utf-8")

    def test_missing_id_column_gets_sequential_ids(
        self, wizard: CompanyWizard, data_dir: Path
    ) -> None:
        self._write(data_dir, self.NO_ID)
        frame = wizard._read_employees()
        assert list(frame["employee_id"]) == ["EMP-0001", "EMP-0002", "EMP-0003"]

    def test_migration_of_a_file_without_ids_writes_them(
        self, wizard: CompanyWizard, data_dir: Path
    ) -> None:
        self._write(data_dir, self.NO_ID)
        wizard.migrate_company_data(apply=True)
        frame = pd.read_csv(data_dir / "employees.csv", dtype=object, keep_default_na=False)
        assert list(frame["employee_id"]) == ["EMP-0001", "EMP-0002", "EMP-0003"]
        assert list(frame["name"]) == ["Deniz Kaya", "Asli Yilmaz", "Mert Aydin"]

    def test_blank_id_cells_get_sequential_ids(self, wizard: CompanyWizard, data_dir: Path) -> None:
        self._write(
            data_dir,
            "id,full_name,role,department,monthly_salary_usd\n"
            ",Deniz Kaya,Developer,Ar-Ge,90000\n"
            ",Asli Yilmaz,Designer,Ar-Ge,80000\n",
        )
        frame = wizard._read_employees()
        assert list(frame["employee_id"]) == ["EMP-0001", "EMP-0002"]

    def test_existing_ids_are_kept(self, wizard: CompanyWizard, data_dir: Path) -> None:
        self._write(
            data_dir,
            "id,full_name,role,department,monthly_salary_usd\n"
            "EMP-0007,Deniz Kaya,Developer,Ar-Ge,90000\n"
            "EMP-0008,Asli Yilmaz,Designer,Ar-Ge,80000\n",
        )
        frame = wizard._read_employees()
        assert list(frame["employee_id"]) == ["EMP-0007", "EMP-0008"]

    def test_assigned_ids_do_not_collide_with_existing_ones(
        self, wizard: CompanyWizard, data_dir: Path
    ) -> None:
        self._write(
            data_dir,
            "id,full_name,role,department,monthly_salary_usd\n"
            "EMP-0001,Deniz Kaya,Developer,Ar-Ge,90000\n"
            ",Asli Yilmaz,Designer,Ar-Ge,80000\n"
            "EMP-0003,Mert Aydin,Product Owner,Growth,70000\n",
        )
        frame = wizard._read_employees()
        assert list(frame["employee_id"]) == ["EMP-0001", "EMP-0002", "EMP-0003"]
        assert len(set(frame["employee_id"])) == 3

    def test_legacy_digit_ids_are_normalized(self, wizard: CompanyWizard, data_dir: Path) -> None:
        self._write(
            data_dir,
            "id,full_name,role,department,monthly_salary_usd\nE101,Metehan Erbasc,CTO,Ar-Ge,8500\n",
        )
        frame = wizard._read_employees()
        assert list(frame["employee_id"]) == ["EMP-0101"]

    def test_value_without_digits_is_replaced(self, wizard: CompanyWizard, data_dir: Path) -> None:
        self._write(
            data_dir,
            "id,full_name,role,department,monthly_salary_usd\nDeniz Kaya,Developer,Ar-Ge,90000\n",
        )
        frame = wizard._read_employees()
        assert list(frame["employee_id"]) == ["EMP-0001"]

    def test_ids_are_reproducible_for_the_same_input(
        self, wizard: CompanyWizard, data_dir: Path, tmp_path: Path
    ) -> None:
        first = data_dir / "employees.csv"
        self._write(data_dir, self.NO_ID)
        wizard.migrate_company_data(apply=True)
        first_bytes = first.read_bytes()

        other = tmp_path / "second"
        other.mkdir()
        (other / "employees.csv").write_text(self.NO_ID, encoding="utf-8")
        second = CompanyWizard(FileHandler(data_dir=other))
        second.migrate_company_data(apply=True)
        assert (other / "employees.csv").read_bytes() == first_bytes

    def test_ids_are_identical_in_a_fresh_interpreter(self) -> None:
        """A hash-based id would change with PYTHONHASHSEED, so compare two processes."""
        script = (
            "import sys\n"
            f"sys.path.insert(0, {str(PROJECT_ROOT)!r})\n"
            "import pandas as pd\n"
            "from src.company_wizard import _assign_employee_ids\n"
            "values = pd.Series(['Deniz Kaya', 'Asli Yilmaz'], dtype=object)\n"
            "print(','.join(_assign_employee_ids(values)))\n"
        )

        def run_with_seed(seed: str) -> str:
            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=str(PROJECT_ROOT),
                env={**os.environ, "PYTHONHASHSEED": seed},
                capture_output=True,
                text=True,
                check=True,
            )
            return result.stdout.strip()

        assert run_with_seed("1") == run_with_seed("2") == "EMP-0001,EMP-0002"

    def test_second_migration_leaves_the_file_untouched(
        self, wizard: CompanyWizard, data_dir: Path
    ) -> None:
        self._write(data_dir, self.NO_ID)
        wizard.migrate_company_data(apply=True)
        after_first = (data_dir / "employees.csv").read_bytes()
        result = wizard.migrate_company_data(apply=True)
        assert result["changed"] is False
        assert (data_dir / "employees.csv").read_bytes() == after_first

    def test_added_employee_continues_the_assigned_sequence(
        self, wizard: CompanyWizard, data_dir: Path
    ) -> None:
        self._write(data_dir, self.NO_ID)
        result = wizard.add_employee("Zeynep Sahin", "Lead AI", "Ar-Ge", 5200.0)
        assert result["employee"]["employee_id"] == "EMP-0004"
        frame = pd.read_csv(data_dir / "employees.csv", dtype=object, keep_default_na=False)
        assert list(frame["employee_id"]) == ["EMP-0001", "EMP-0002", "EMP-0003", "EMP-0004"]

    def test_added_employee_after_existing_high_id(
        self, wizard: CompanyWizard, data_dir: Path
    ) -> None:
        self._write(
            data_dir,
            "id,full_name,role,department,monthly_salary_usd\n"
            "EMP-0102,Deniz Kaya,Developer,Ar-Ge,90000\n"
            ",Asli Yilmaz,Designer,Ar-Ge,80000\n",
        )
        result = wizard.add_employee("Mert Aydin", "Product Owner", "Growth", 7000.0)
        assert result["employee"]["employee_id"] == "EMP-0103"


def spread_records_across(data_dir: Path, months: list[str]) -> None:
    """Move the existing financial records one per listed YYYY-MM month, in order."""
    path = data_dir / "financials.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    for record, month in zip(payload["records"], months, strict=True):
        record["recorded_at"] = f"{month}-01T12:00:00+00:00"
    write_json(data_dir, "financials.json", payload)


class TestAuditTrail:
    def test_init_company_journals_the_first_entry(
        self, company: CompanyWizard, data_dir: Path
    ) -> None:
        result = company.list_audit_entries()
        assert result["total_entries"] == 1
        assert result["filtered_total"] == 1
        entry = result["entries"][0]
        assert entry["seq"] == 1
        assert entry["action"] == "company_initialized"
        assert entry["target"] == "company_profile.json"
        assert "Acme" in entry["summary"]
        assert entry["details"]["company_name"] == "Acme"
        assert float(entry["details"]["initial_budget"]) == 10_000.0
        assert (data_dir / "audit_log.json").exists()

    def test_entries_are_newest_first_with_a_gapless_sequence(self, company: CompanyWizard) -> None:
        company.add_financial_record("income", "services", 1000.0, "g")
        company.add_financial_record("expense", "rent", 400.0, "g")
        company.update_company_notes("Strateji", "Büyüme")
        company.add_risk("Kur riski", "high", "medium")
        seqs = [entry["seq"] for entry in company.list_audit_entries()["entries"]]
        assert seqs == [5, 4, 3, 2, 1]
        assert company.list_audit_entries()["entries"][0]["action"] == "risk_added"

    def test_every_mutation_records_an_entry(self, company: CompanyWizard) -> None:
        company.add_financial_record("expense", "rent", 500.0, "kira")
        company.add_employee("Zeynep Sahin", "Lead AI", "Ar-Ge", 5200.0)
        company.update_company_notes("Toplanti", "Gundem")
        company.update_company_notes("Toplanti", "Yeni gundem")
        company.add_risk("Tedarikçi iflası", "medium", "high")
        actions = {entry["action"] for entry in company.list_audit_entries()["entries"]}
        assert actions == {
            "company_initialized",
            "financial_record_added",
            "employee_added",
            "note_created",
            "note_updated",
            "risk_added",
        }

    def test_financial_record_entry_carries_the_record(self, company: CompanyWizard) -> None:
        result = company.add_financial_record("expense", "kira", 1500.5, "ofis")
        entry = company.list_audit_entries(action="financial_record_added")["entries"][0]
        assert entry["details"]["record"]["id"] == result["record"]["id"]
        assert float(entry["details"]["record"]["amount"]) == 1500.5
        assert entry["details"]["record"]["category"] == "kira"
        assert entry["details"]["category_added"] is True
        assert "1500.5 TRY" in entry["summary"]
        assert entry["target"].startswith("financials.json:")

    def test_note_entry_records_both_versions(self, company: CompanyWizard) -> None:
        company.update_company_notes("Politika", "Ilk surum")
        second = company.update_company_notes("Politika", "Guncellenmis surum")
        entry = company.list_audit_entries(action="note_updated")["entries"][0]
        assert entry["details"]["content_before"] == "Ilk surum"
        assert entry["details"]["content_after"] == "Guncellenmis surum"
        assert entry["details"]["content_length_before"] == 9
        assert entry["details"]["content_length_after"] == 18
        assert entry["details"]["note_id"] == second["note"]["id"]
        assert entry["target"] == f"company_notes.json:{second['note']['id']}"

    def test_long_values_are_clipped_but_the_note_is_stored_whole(
        self, company: CompanyWizard, data_dir: Path
    ) -> None:
        company.update_company_notes("Uzun Not", "ilk surum")
        content = "x" * 600
        company.update_company_notes("Uzun Not", content)
        entry = company.list_audit_entries(action="note_updated")["entries"][0]
        stored = entry["details"]["content_after"]
        assert len(stored) == 500
        assert stored.endswith("…")
        saved = json.loads((data_dir / "company_notes.json").read_text(encoding="utf-8"))
        assert saved["notes"][0]["content"] == content

    def test_dry_runs_write_no_journal_entry(self, company: CompanyWizard) -> None:
        company.add_financial_record("expense", "rent", 500.0, "a")
        before = company.list_audit_entries()["total_entries"]
        company.plan_company_data_migration()
        company.migrate_company_data()
        company.apply_company_data_migration(answers={}, apply=False)
        company.run_scenario(horizon_months=3)
        assert company.list_audit_entries()["total_entries"] == before

    def test_applied_data_migration_is_journaled_with_its_backups(
        self, wizard: CompanyWizard, data_dir: Path
    ) -> None:
        write_real_files(data_dir)
        result = wizard.apply_company_data_migration(answers=full_answers(), apply=True)
        entry = wizard.list_audit_entries(action="data_migration_applied")["entries"][0]
        assert entry["details"]["changed_files"] == sorted(result["backups"])
        assert entry["details"]["backups"] == result["backups"]
        assert entry["details"]["resolved"] == result["resolved"]

    def test_applied_employee_migration_is_journaled(
        self, wizard: CompanyWizard, data_dir: Path
    ) -> None:
        write_file(data_dir, "employees.csv", TestLegacyEmployeeSchema.LEGACY)
        wizard.migrate_company_data(apply=True)
        entry = wizard.list_audit_entries(action="employees_migrated")["entries"][0]
        assert entry["details"]["backup_file"].startswith("employees.backup-")
        assert entry["details"]["row_count"] == 2
        assert "(2 rows)" in entry["summary"]

    def test_failed_write_removes_the_entry_again(
        self, company: CompanyWizard, data_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        before = company.list_audit_entries()["total_entries"]
        original = company.file_handler.write_file

        def failing(filename: str, content: object) -> dict[str, Any]:
            if filename == "financials.json":
                msg = "disk full"
                raise OSError(msg)
            return original(filename, content)

        monkeypatch.setattr(company.file_handler, "write_file", failing)
        with pytest.raises(OSError, match="disk full"):
            company.add_financial_record("expense", "rent", 500.0, "kira")
        assert company.list_audit_entries()["total_entries"] == before
        financials = json.loads((data_dir / "financials.json").read_text(encoding="utf-8"))
        assert financials["records"] == []

    def test_unknown_action_filter_lists_what_exists(self, company: CompanyWizard) -> None:
        with pytest.raises(ValueError) as excinfo:
            company.list_audit_entries(action="made_up")
        message = str(excinfo.value)
        assert "unknown action 'made_up'" in message
        assert "company_initialized" in message

    def test_paging_and_bounds(self, company: CompanyWizard) -> None:
        company.add_financial_record("income", "services", 100.0, "a")
        company.add_financial_record("income", "services", 200.0, "b")
        first = company.list_audit_entries(limit=1, offset=0)
        second = company.list_audit_entries(limit=1, offset=1)
        assert [entry["seq"] for entry in first["entries"]] == [3]
        assert [entry["seq"] for entry in second["entries"]] == [2]
        assert first["has_more"] is True
        assert first["filtered_total"] == first["total_entries"] == 3
        with pytest.raises(ValueError, match="between 1 and 500"):
            company.list_audit_entries(limit=0)
        with pytest.raises(ValueError, match="between 1 and 500"):
            company.list_audit_entries(limit=501)
        with pytest.raises(ValueError, match="must not be negative"):
            company.list_audit_entries(offset=-1)

    def test_risk_actions_are_journaled(self, company: CompanyWizard) -> None:
        created = company.add_risk("Kur riski", "high", "high")
        risk_id = created["risk"]["id"]
        company.update_risk(risk_id, {"status": "mitigating"})
        company.delete_risk(risk_id)
        entries = company.list_audit_entries()["entries"]
        assert [entry["action"] for entry in entries[:3]] == [
            "risk_deleted",
            "risk_updated",
            "risk_added",
        ]
        assert entries[1]["details"]["changed_fields"] == ["status"]
        assert entries[2]["details"]["risk"]["level"] == "critical"


class TestVarianceReport:
    def test_budget_block_and_single_month(self, company: CompanyWizard) -> None:
        company.add_financial_record("income", "services", 5000.0, "g")
        company.add_financial_record("expense", "rent", 3000.0, "g")
        report = company.get_variance_report()
        assert report["company_name"] == "Acme"
        assert report["currency"] == "TRY"
        assert report["month_filter"] is None
        assert report["month_count"] == 1
        assert report["record_count"] == 2
        assert report["budget"] == {
            "initial_budget": Decimal("10000"),
            "spent": Decimal("3000.00"),
            "remaining": Decimal("7000.00"),
            "over_budget": False,
            "used_percent": 30.0,
        }
        assert report["warnings"] == []
        row = report["monthly"][0]
        assert row["month"] == report["generated_at"][:7]
        assert row["income"] == Decimal("5000.00")
        assert row["expense"] == Decimal("3000.00")
        assert row["net"] == Decimal("2000.00")
        assert row["change_vs_previous"] is None
        assert row["change_percent_vs_previous"] is None

    def test_month_over_month_change_and_percentages(
        self, company: CompanyWizard, data_dir: Path
    ) -> None:
        company.add_financial_record("income", "services", 5000.0, "a")
        company.add_financial_record("expense", "rent", 5000.0, "a")
        company.add_financial_record("income", "services", 6000.0, "b")
        company.add_financial_record("expense", "rent", 5000.0, "b")
        spread_records_across(data_dir, ["2026-01", "2026-01", "2026-02", "2026-02"])
        report = company.get_variance_report()
        assert [row["month"] for row in report["monthly"]] == ["2026-01", "2026-02"]
        first, second = report["monthly"]
        assert first["change_vs_previous"] is None
        assert second["change_vs_previous"] == {
            "income": Decimal("1000.00"),
            "expense": Decimal("0.00"),
            "net": Decimal("1000.00"),
        }
        assert second["change_percent_vs_previous"] == {
            "income": 20.0,
            "expense": 0.0,
            "net": None,
        }
        assert report["budget"]["spent"] == Decimal("10000.00")
        assert report["budget"]["used_percent"] == 100.0
        assert report["budget"]["over_budget"] is False

    def test_month_filter_returns_only_that_month(
        self, company: CompanyWizard, data_dir: Path
    ) -> None:
        company.add_financial_record("income", "services", 5000.0, "a")
        company.add_financial_record("expense", "rent", 1000.0, "b")
        spread_records_across(data_dir, ["2026-01", "2026-02"])
        report = company.get_variance_report(month="2026-02")
        assert report["month_filter"] == "2026-02"
        assert [row["month"] for row in report["monthly"]] == ["2026-02"]
        assert report["month_count"] == 2

    def test_unknown_month_lists_available_months(
        self, company: CompanyWizard, data_dir: Path
    ) -> None:
        company.add_financial_record("income", "services", 5000.0, "a")
        spread_records_across(data_dir, ["2026-01"])
        with pytest.raises(ValueError) as excinfo:
            company.get_variance_report(month="2025-12")
        assert "unknown month '2025-12'" in str(excinfo.value)
        assert "2026-01" in str(excinfo.value)

    def test_malformed_month_is_rejected(self, company: CompanyWizard) -> None:
        for bad in ("2026-13", "2026-1", "October 2026"):
            with pytest.raises(ValueError, match="formatted as YYYY-MM"):
                company.get_variance_report(month=bad)

    def test_over_budget_and_negative_balance_are_flagged(self, company: CompanyWizard) -> None:
        company.add_financial_record("expense", "equipment", 12000.0, "x")
        report = company.get_variance_report()
        assert report["budget"]["over_budget"] is True
        assert report["budget"]["remaining"] == Decimal("-2000.00")
        assert report["budget"]["used_percent"] == 120.0
        warnings = " ".join(report["warnings"])
        assert "exceed the initial budget" in warnings
        assert "negative" in warnings

    def test_company_without_records_warns_but_does_not_fail(self, company: CompanyWizard) -> None:
        report = company.get_variance_report()
        assert report["monthly"] == []
        assert report["month_count"] == 0
        assert report["budget"]["spent"] == Decimal("0.00")
        assert report["budget"]["used_percent"] == 0.0
        assert any("no financial records" in item for item in report["warnings"])

    def test_report_never_writes_to_disk(self, company: CompanyWizard, data_dir: Path) -> None:
        company.add_financial_record("expense", "rent", 500.0, "x")
        before = snapshot(data_dir)
        company.get_variance_report()
        company.get_variance_report(month=None)
        assert snapshot(data_dir) == before


class TestScenario:
    def test_requires_at_least_one_record(self, company: CompanyWizard) -> None:
        with pytest.raises(ValueError, match="needs at least one income or expense record"):
            company.run_scenario()

    def test_baseline_is_the_monthly_average(self, company: CompanyWizard, data_dir: Path) -> None:
        company.add_financial_record("income", "services", 4000.0, "a")
        company.add_financial_record("expense", "rent", 1000.0, "a")
        company.add_financial_record("income", "services", 6000.0, "b")
        company.add_financial_record("expense", "rent", 3000.0, "b")
        spread_records_across(data_dir, ["2026-01", "2026-01", "2026-02", "2026-02"])
        result = company.run_scenario(horizon_months=6)
        assert result["baseline"] == {
            "months_of_history": 2,
            "monthly_income": Decimal("5000.00"),
            "monthly_expense": Decimal("2000.00"),
            "starting_balance": Decimal("16000.00"),
        }
        assert result["assumptions"]["horizon_months"] == 6
        assert result["assumptions"]["category_changes"] == {}
        assert len(result["months"]) == 6
        assert result["summary"]["runway_months"] is None
        assert result["summary"]["closing_balance"] > 0

    def test_projection_reports_the_month_the_balance_turns_negative(
        self, company: CompanyWizard
    ) -> None:
        company.add_financial_record("income", "services", 1000.0, "a")
        company.add_financial_record("expense", "payroll", 6000.0, "a")
        result = company.run_scenario(horizon_months=6)
        months = result["months"]
        assert months[0]["opening_balance"] == Decimal("5000.00")
        assert months[0]["closing_balance"] == Decimal("0.00")
        assert months[1]["closing_balance"] == Decimal("-5000.00")
        assert result["summary"]["runway_months"] == 2
        assert "turns negative in month 2" in " ".join(result["warnings"])
        assert re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", months[0]["month"])

    def test_runway_is_null_when_the_balance_stays_positive(self, company: CompanyWizard) -> None:
        company.add_financial_record("income", "services", 5000.0, "a")
        company.add_financial_record("expense", "rent", 1000.0, "a")
        result = company.run_scenario(horizon_months=3)
        assert result["summary"]["runway_months"] is None
        assert result["warnings"] == []
        assert result["summary"]["lowest_balance"] > 0
        assert result["summary"]["net_change"] > 0

    def test_one_time_expense_hits_the_first_month_only(self, company: CompanyWizard) -> None:
        company.add_financial_record("income", "services", 5000.0, "a")
        company.add_financial_record("expense", "rent", 1000.0, "a")
        result = company.run_scenario(horizon_months=3, one_time_expense=2000.0)
        months = result["months"]
        assert months[0]["expense"] == Decimal("3000.00")
        assert months[1]["expense"] == Decimal("1000.00")
        assert months[2]["expense"] == Decimal("1000.00")
        assert months[0]["net_flow"] == Decimal("2000.00")
        assert result["summary"]["total_expense"] == Decimal("5000.00")
        assert result["summary"]["total_income"] == Decimal("15000.00")

    def test_category_changes_add_to_the_monthly_expense(self, company: CompanyWizard) -> None:
        company.add_financial_record("income", "services", 5000.0, "a")
        company.add_financial_record("expense", "rent", 1000.0, "a")
        result = company.run_scenario(
            horizon_months=2, category_changes={"rent": 500.0}, income_change_percent=10.0
        )
        assert result["assumptions"]["category_changes"] == {"rent": Decimal("500.00")}
        assert result["months"][0]["income"] == Decimal("5500.00")
        assert result["months"][0]["expense"] == Decimal("1500.00")
        assert result["months"][1]["expense"] == Decimal("1500.00")

    def test_unknown_category_lists_the_valid_ones(self, company: CompanyWizard) -> None:
        company.add_financial_record("expense", "rent", 1000.0, "a")
        with pytest.raises(ValueError) as excinfo:
            company.run_scenario(category_changes={"gyro": 100.0})
        message = str(excinfo.value)
        assert "unknown expense category 'gyro'" in message
        assert "rent" in message
        assert "payroll" in message

    def test_category_changes_cannot_make_expense_negative(self, company: CompanyWizard) -> None:
        company.add_financial_record("expense", "rent", 1000.0, "a")
        with pytest.raises(ValueError, match="would be negative"):
            company.run_scenario(category_changes={"rent": -5000.0})

    @pytest.mark.parametrize(
        ("argument", "value", "expected"),
        [
            ("horizon_months", 0, "horizon_months must be between 1 and 120"),
            ("horizon_months", 121, "horizon_months must be between 1 and 120"),
            ("income_change_percent", -100.5, "between -100 and 10000"),
            ("expense_change_percent", 10_001, "between -100 and 10000"),
            ("one_time_expense", -1.0, "must not be negative"),
        ],
    )
    def test_assumption_bounds_are_enforced(
        self, company: CompanyWizard, argument: str, value: float, expected: str
    ) -> None:
        company.add_financial_record("expense", "rent", 1000.0, "a")
        with pytest.raises(ValueError, match=re.escape(expected)):
            company.run_scenario(**{argument: value})

    def test_scenario_leaves_the_directory_untouched(
        self, company: CompanyWizard, data_dir: Path
    ) -> None:
        company.add_financial_record("expense", "rent", 1000.0, "a")
        before = snapshot(data_dir)
        entries_before = company.list_audit_entries()["total_entries"]
        company.run_scenario(horizon_months=4, one_time_expense=500.0)
        assert snapshot(data_dir) == before
        assert company.list_audit_entries()["total_entries"] == entries_before


class TestRisks:
    @pytest.mark.parametrize(
        ("likelihood", "impact", "score", "level"),
        [
            ("low", "low", 1, "low"),
            ("low", "medium", 2, "low"),
            ("low", "high", 3, "medium"),
            ("medium", "medium", 4, "medium"),
            ("high", "medium", 6, "high"),
            ("high", "high", 9, "critical"),
        ],
    )
    def test_score_and_level_are_derived(
        self, company: CompanyWizard, likelihood: str, impact: str, score: int, level: str
    ) -> None:
        result = company.add_risk("Bir risk", likelihood, impact)
        assert result["risk"]["score"] == score
        assert result["risk"]["level"] == level
        assert result["risk"]["status"] == "open"
        assert result["risk"]["id"]

    def test_register_file_stores_the_risk_without_derived_fields(
        self, company: CompanyWizard, data_dir: Path
    ) -> None:
        result = company.add_risk("Kur riski", "high", "high", category="finans", owner="CFO")
        payload = json.loads((data_dir / "risk_register.json").read_text(encoding="utf-8"))
        stored = payload["risks"][0]
        assert stored["id"] == result["risk"]["id"]
        assert stored["title"] == "Kur riski"
        assert stored["category"] == "finans"
        assert "score" not in stored
        assert "level" not in stored

    @pytest.mark.parametrize("likelihood", ["certain", "HIGH", ""])
    def test_invalid_ratings_list_the_allowed_ones(
        self, company: CompanyWizard, likelihood: str
    ) -> None:
        with pytest.raises(ValueError) as excinfo:
            company.add_risk("Risk", likelihood, "low")
        message = str(excinfo.value)
        assert "Input should be 'low', 'medium' or 'high'" in message
        assert repr(likelihood) in message

    def test_empty_title_is_rejected(self, company: CompanyWizard) -> None:
        with pytest.raises(ValueError, match="non-empty string"):
            company.add_risk("   ", "low", "low")

    def test_update_changes_values_and_recomputes_the_level(self, company: CompanyWizard) -> None:
        created = company.add_risk("Tedarikçi riski", "high", "high")
        risk_id = created["risk"]["id"]
        result = company.update_risk(
            risk_id, {"likelihood": "low", "status": "mitigating", "owner": "Ops"}
        )
        assert result["changed_fields"] == ["owner", "likelihood", "status"]
        assert result["risk"]["score"] == 3
        assert result["risk"]["level"] == "medium"
        assert result["risk"]["status"] == "mitigating"
        assert result["risk"]["owner"] == "Ops"
        assert result["risk"]["title"] == "Tedarikçi riski"

    def test_update_rejects_unknown_fields(self, company: CompanyWizard) -> None:
        created = company.add_risk("Risk", "low", "low")
        with pytest.raises(ValueError) as excinfo:
            company.update_risk(created["risk"]["id"], {"score": "9"})
        message = str(excinfo.value)
        assert "unknown field(s): score" in message
        assert "mitigation" in message

    def test_update_without_an_actual_change_is_refused(self, company: CompanyWizard) -> None:
        created = company.add_risk("Risk", "low", "high")
        with pytest.raises(ValueError, match="no changes"):
            company.update_risk(created["risk"]["id"], {"impact": "high"})

    def test_update_of_an_unknown_id_is_refused(self, company: CompanyWizard) -> None:
        with pytest.raises(ValueError, match="unknown risk id"):
            company.update_risk("does-not-exist", {"status": "closed"})

    def test_update_rejects_an_invalid_status(self, company: CompanyWizard) -> None:
        created = company.add_risk("Risk", "low", "low")
        with pytest.raises(ValueError, match="open"):
            company.update_risk(created["risk"]["id"], {"status": "done"})

    def test_delete_removes_the_risk_and_keeps_the_journal(
        self, company: CompanyWizard, data_dir: Path
    ) -> None:
        created = company.add_risk("Risk", "low", "low")
        risk_id = created["risk"]["id"]
        payload = json.loads((data_dir / "risk_register.json").read_text(encoding="utf-8"))
        assert len(payload["risks"]) == 1
        result = company.delete_risk(risk_id)
        assert result["risk"]["id"] == risk_id
        payload = json.loads((data_dir / "risk_register.json").read_text(encoding="utf-8"))
        assert payload["risks"] == []
        actions = {entry["action"] for entry in company.list_audit_entries()["entries"]}
        assert {"risk_added", "risk_deleted"} <= actions
        with pytest.raises(ValueError, match="unknown risk id"):
            company.delete_risk(risk_id)

    def test_list_filters_sorts_and_counts(self, company: CompanyWizard) -> None:
        company.add_risk("Düşük etki", "low", "low", category="operasyon")
        company.add_risk("Kritik etki", "high", "high", category="finans")
        company.add_risk("Orta etki", "medium", "medium", category="finans")
        listing = company.list_risks()
        assert [item["title"] for item in listing["risks"]] == [
            "Kritik etki",
            "Orta etki",
            "Düşük etki",
        ]
        assert listing["count"] == listing["total_count"] == 3
        assert listing["open_count"] == 3
        assert listing["by_level"] == {"critical": 1, "high": 0, "medium": 1, "low": 1}
        assert listing["categories"] == ["finans", "operasyon"]
        by_title = company.list_risks(sort="title")
        assert [item["title"] for item in by_title["risks"]] == [
            "Düşük etki",
            "Kritik etki",
            "Orta etki",
        ]
        only_finans = company.list_risks(category="FINANS")
        assert only_finans["count"] == 2
        assert only_finans["total_count"] == 3

    def test_status_filter_and_closed_risks_leave_the_open_count(
        self, company: CompanyWizard
    ) -> None:
        first = company.add_risk("Risk A", "low", "low")
        company.add_risk("Risk B", "high", "high")
        company.update_risk(first["risk"]["id"], {"status": "closed"})
        assert company.list_risks()["open_count"] == 1
        closed = company.list_risks(status="closed")
        assert closed["count"] == 1
        assert [item["title"] for item in closed["risks"]] == ["Risk A"]

    @pytest.mark.parametrize("filters", [{"status": "unknown"}, {"sort": "urgency"}])
    def test_invalid_list_options(self, company: CompanyWizard, filters: dict[str, str]) -> None:
        with pytest.raises(ValueError):
            company.list_risks(**filters)

    def test_register_is_read_back_from_disk(self, company: CompanyWizard) -> None:
        company.add_risk("Kalıcı risk", "medium", "high", mitigation="İkinci tedarikçi")
        reloaded = CompanyWizard(company.file_handler).list_risks()
        assert reloaded["count"] == 1
        assert reloaded["risks"][0]["mitigation"] == "İkinci tedarikçi"
        assert reloaded["risks"][0]["level"] == "high"
