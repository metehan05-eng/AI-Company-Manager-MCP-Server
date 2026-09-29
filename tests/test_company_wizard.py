"""CompanyWizard business rules: initialization, finances, employees, notes, error messages."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from src.company_wizard import CompanyWizard
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
