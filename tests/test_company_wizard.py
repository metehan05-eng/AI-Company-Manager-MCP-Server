"""CompanyWizard business rules: initialization, finances, employees, notes, error messages."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

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
        assert "missing required columns" in str(excinfo.value)


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
