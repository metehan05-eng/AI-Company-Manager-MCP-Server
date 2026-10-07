"""Read and write tests for every supported file format, including CSV regression fixes."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
from docx import Document

from conftest import make_pdf_bytes
from src.file_handler import CompanyFileError, FileHandler


class TestTextFormats:
    def test_txt_round_trip(self, handler: FileHandler) -> None:
        handler.write_file("not.txt", "Merhaba Dunya")
        assert handler.read_file("not.txt") == "Merhaba Dunya"

    def test_md_round_trip(self, handler: FileHandler) -> None:
        content = "# Baslik\n\n- bir\n- iki\n"
        handler.write_file("sop.md", content)
        assert handler.read_file("sop.md") == content

    def test_write_reports_metadata(self, handler: FileHandler) -> None:
        result = handler.write_file("rapor.txt", "icerik")
        assert result["filename"] == "rapor.txt"
        assert result["size_bytes"] > 0

    def test_strips_bom_on_read(self, handler: FileHandler, data_dir: Path) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "bom.txt").write_bytes("\ufeffmerhaba".encode())
        assert handler.read_file("bom.txt") == "merhaba"


class TestJsonFormat:
    def test_round_trip(self, handler: FileHandler) -> None:
        payload = {"ad": "Acme", "butce": 1000, "aktif": True}
        handler.write_file("profil.json", payload)
        assert json.loads(handler.read_file("profil.json")) == payload

    def test_rejects_invalid_json(self, handler: FileHandler) -> None:
        with pytest.raises(ValueError) as excinfo:
            handler.write_file("bozuk.json", "{invalid")
        assert "invalid JSON" in str(excinfo.value)

    def test_rejects_nan_constant(self, handler: FileHandler) -> None:
        with pytest.raises(ValueError) as excinfo:
            handler.write_file("nan.json", '{"a": NaN}')
        assert "NaN" in str(excinfo.value)

    def test_reads_pretty_formatted(self, handler: FileHandler) -> None:
        handler.write_file("profil.json", {"a": 1, "b": [1, 2, 3]})
        content = handler.read_file("profil.json")
        assert "\n" in content
        assert json.loads(content)["b"] == [1, 2, 3]


class TestCsvDelimiterRegression:
    """Fix 1: single column CSVs were corrupted because sep=None picked a letter as delimiter."""

    def test_single_column_round_trips_unchanged(self, handler: FileHandler) -> None:
        content = "value\n1.5\n2.5\n3.75"
        handler.write_file("tek.csv", content)
        assert handler.read_file("tek.csv") == content

    def test_header_with_letter_a_stays_one_column(self, handler: FileHandler) -> None:
        handler.write_file("value.csv", "value\n1.5\n2.5\n")
        assert handler.read_file("value.csv") == "value\n1.5\n2.5"

    def test_two_column_comma_csv(self, handler: FileHandler) -> None:
        content = "name,score\nali,90\nayse,85"
        handler.write_file("iki.csv", content)
        assert handler.read_file("iki.csv") == content

    @pytest.mark.parametrize(
        ("filename", "raw"),
        [
            ("tab.csv", "name\tscore\nali\t90\n"),
            ("semi.csv", "name;score\nali;90\n"),
            ("pipe.csv", "name|score\nali|90\n"),
        ],
    )
    def test_alternative_delimiters_parsed_as_two_columns(
        self, handler: FileHandler, filename: str, raw: str
    ) -> None:
        handler.write_file(filename, raw)
        assert handler.read_file(filename) == "name,score\nali,90"

    def test_single_numeric_column(self, handler: FileHandler) -> None:
        handler.write_file("tutar.csv", "amount\n1000\n2000\n")
        assert handler.read_file("tutar.csv") == "amount\n1000\n2000"

    def test_headerless_numeric_csv(self, handler: FileHandler) -> None:
        handler.write_file("sadece.csv", "1.5\n2.5\n")
        assert handler.read_file("sadece.csv") == "1.5\n2.5"


class TestNegativeNumbersRegression:
    """Fix 2: negative numbers were turned into text by spreadsheet escaping."""

    def test_negative_numbers_stay_numeric(self, handler: FileHandler) -> None:
        handler.write_file("ledger.csv", "kategori,tutar\nkira,-500\nmaas,-1200.5\n")
        content = handler.read_file("ledger.csv")
        assert "'" not in content
        assert "-500" in content
        assert "-1200.5" in content

    @pytest.mark.parametrize(
        ("payload", "expected"),
        [
            ("=cmd|'/c calc'!A0", "'=cmd"),
            ("+1+1", "'+1+1"),
            ("@SUM(A1)", "'@SUM"),
            ("-1+1", "'-1+1"),
            ("=1+1", "'=1+1"),
        ],
    )
    def test_formula_injection_still_escaped(
        self, handler: FileHandler, payload: str, expected: str
    ) -> None:
        handler.write_file("formul.csv", f"ad,deger\nsatir,{payload}\n")
        assert expected in handler.read_file("formul.csv")

    def test_plain_negative_number_not_escaped(self, handler: FileHandler) -> None:
        handler.write_file("formul.csv", "ad,deger\nsayi,-42\n")
        content = handler.read_file("formul.csv")
        assert ",-42" in content
        assert "',-42" not in content

    def test_xlsx_negatives_stay_numeric(self, handler: FileHandler, data_dir: Path) -> None:
        handler.write_file(
            "sayilar.xlsx",
            {"Sayfa1": [{"ad": "kira", "tutar": -500.0}, {"ad": "maas", "tutar": -1200.5}]},
        )
        frame = pd.read_excel(data_dir / "sayilar.xlsx")
        values = frame["tutar"].tolist()
        assert all(isinstance(value, (int, float)) and value < 0 for value in values)


class TestXlsxFormat:
    def test_round_trip_with_sheet_mapping(self, handler: FileHandler) -> None:
        handler.write_file("rapor.xlsx", {"S1": [{"a": 1, "b": 2}, {"a": 3, "b": 4}]})
        content = handler.read_file("rapor.xlsx")
        assert "a" in content
        assert "S1" in content or "Sayfa" in content

    def test_accepts_dataframe(self, handler: FileHandler) -> None:
        frame = pd.DataFrame([{"urun": "A", "adet": 5}])
        handler.write_file("tablo.xlsx", frame)
        assert "urun" in handler.read_file("tablo.xlsx")

    def test_rejects_unsupported_content(self, handler: FileHandler) -> None:
        with pytest.raises(TypeError):
            handler.write_file("bozuk.xlsx", [1, 2, 3])


class TestPdfRead:
    def test_multi_page_with_headers(self, handler: FileHandler, data_dir: Path) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "rapor.pdf").write_bytes(make_pdf_bytes("Sirket Raporu", pages=3))
        content = handler.read_file("rapor.pdf")
        assert content.count("--- Sayfa ") == 3
        assert "Sirket Raporu" in content
        assert "sayfa 3" in content

    def test_malformed_pdf_raises_clean_error(self, handler: FileHandler, data_dir: Path) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "bozuk.pdf").write_bytes(b"bu bir pdf degil")
        with pytest.raises(CompanyFileError):
            handler.read_file("bozuk.pdf")

    def test_scanned_pdf_warns_about_ocr(self, handler: FileHandler, data_dir: Path) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "taranmis.pdf").write_bytes(make_pdf_bytes("x", pages=2))
        content = handler.read_file("taranmis.pdf")
        assert "Sayfa 2" in content


class TestDocxRead:
    def test_reads_paragraphs_and_tables(self, handler: FileHandler, data_dir: Path) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        document = Document()
        document.add_heading("Rapor", 0)
        document.add_paragraph("Bu bir test paragrafidir.")
        table = document.add_table(rows=2, cols=2)
        table.cell(0, 0).text = "Kalem"
        table.cell(0, 1).text = "Tutar"
        table.cell(1, 0).text = "Kira"
        table.cell(1, 1).text = "1500"
        document.save(data_dir / "rapor.docx")

        content = handler.read_file("rapor.docx")
        assert "Rapor" in content
        assert "Bu bir test paragrafidir." in content
        assert "Kalem" in content
        assert "1500" in content


class TestListFiles:
    def test_lists_metadata(self, handler: FileHandler) -> None:
        handler.write_file("a.txt", "x")
        handler.write_file("b.json", {"k": 1})
        files = handler.list_files()
        names = {item["filename"] for item in files}
        assert {"a.txt", "b.json"} <= names
        for item in files:
            assert {"filename", "extension", "type", "size_bytes", "modified_at"} <= set(item)

    def test_empty_directory(self, handler: FileHandler) -> None:
        assert handler.list_files() == []


class TestReadBudget:
    """A large file must never be returned whole, and must stay readable page by page."""

    @pytest.fixture
    def long_notes(self, handler: FileHandler) -> str:
        handler.write_file("notlar.md", "".join(f"satir {index}\n" for index in range(1, 201)))
        return handler.read_file("notlar.md")

    def test_read_file_is_never_trimmed(self, handler: FileHandler, long_notes: str) -> None:
        assert handler.read_report("notlar.md", max_chars=50)["total_chars"] == len(long_notes)
        assert handler.read_file("notlar.md") == long_notes

    def test_max_chars_zero_means_no_limit(self, handler: FileHandler, long_notes: str) -> None:
        report = handler.read_report("notlar.md", max_chars=0)
        assert report["content"] == long_notes
        assert report["truncated"] is False
        assert report["next_start_char"] == len(long_notes)

    @pytest.mark.usefixtures("long_notes")
    def test_oversized_output_is_capped_and_announced(self, handler: FileHandler) -> None:
        report = handler.read_report("notlar.md", max_chars=100)
        assert report["truncated"] is True
        assert report["total_chars"] > 100
        assert report["content"].startswith("satir 1\n")
        assert "kisaltildi" in report["content"]
        assert report["next_start_char"] == 100

    def test_output_within_budget_is_untouched(self, handler: FileHandler) -> None:
        handler.write_file("kisa.txt", "kisa bir not")
        report = handler.read_report("kisa.txt", max_chars=100_000)
        assert report["truncated"] is False
        assert "kisaltildi" not in report["content"]

    @pytest.mark.usefixtures("long_notes")
    def test_paging_reads_the_whole_file_without_gaps(self, handler: FileHandler) -> None:
        whole = handler.read_file("notlar.md")
        collected = ""
        offset = 0
        for _ in range(len(whole)):
            report = handler.read_report("notlar.md", start_char=offset, max_chars=120)
            collected += report["content"]
            offset = report["next_start_char"]
            if not report["truncated"]:
                break
        assert offset == len(whole)
        assert "satir 200" in collected
        assert not collected.rstrip().endswith("]")

    @pytest.mark.usefixtures("long_notes")
    def test_start_char_past_the_end_returns_nothing(self, handler: FileHandler) -> None:
        report = handler.read_report("notlar.md", start_char=999_999)
        assert report["content"] == ""
        assert report["start_char"] == report["total_chars"]
        assert report["truncated"] is False

    @pytest.mark.usefixtures("long_notes")
    def test_negative_arguments_are_rejected(self, handler: FileHandler) -> None:
        with pytest.raises(CompanyFileError):
            handler.read_report("notlar.md", start_char=-1)
        with pytest.raises(CompanyFileError):
            handler.read_report("notlar.md", max_chars=-5)

    @pytest.mark.usefixtures("long_notes")
    def test_configured_default_is_used(
        self, monkeypatch: pytest.MonkeyPatch, data_dir: Path
    ) -> None:
        monkeypatch.setenv("COMPANY_READ_MAX_CHARS", "1000")
        small = FileHandler(data_dir=data_dir)
        assert small.max_read_chars == 1000
        assert small.read_report("notlar.md")["truncated"] is True

    def test_default_budget_is_generous(self, handler: FileHandler) -> None:
        assert handler.max_read_chars == 100_000

    def test_non_integer_configured_budget_is_rejected(
        self, monkeypatch: pytest.MonkeyPatch, data_dir: Path
    ) -> None:
        monkeypatch.setenv("COMPANY_READ_MAX_CHARS", "cok-fazla")
        with pytest.raises(CompanyFileError) as excinfo:
            FileHandler(data_dir=data_dir)
        assert "must be an integer" in str(excinfo.value)

    def test_too_small_configured_budget_is_rejected(
        self, monkeypatch: pytest.MonkeyPatch, data_dir: Path
    ) -> None:
        monkeypatch.setenv("COMPANY_READ_MAX_CHARS", "10")
        with pytest.raises(CompanyFileError):
            FileHandler(data_dir=data_dir)


class TestPdfPageSelection:
    @pytest.fixture
    def report(self, handler: FileHandler, data_dir: Path) -> FileHandler:
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "rapor.pdf").write_bytes(make_pdf_bytes("Sirket Raporu", pages=6))
        return handler

    def test_page_range_limits_the_output(self, report: FileHandler) -> None:
        content = report.read_report("rapor.pdf", pages="1-2", max_chars=0)["content"]
        assert content.count("--- Sayfa ") == 2
        assert "--- Sayfa 1 ---" in content
        assert "sayfa 2" in content
        assert "--- Sayfa 3 ---" not in content

    def test_total_pages_is_reported(self, report: FileHandler) -> None:
        assert report.read_report("rapor.pdf", max_chars=0)["total_pages"] == 6
        assert report.read_report("rapor.pdf", pages="2", max_chars=0)["total_pages"] == 6

    def test_open_ended_range_runs_to_the_last_page(self, report: FileHandler) -> None:
        content = report.read_report("rapor.pdf", pages="5-", max_chars=0)["content"]
        assert content.count("--- Sayfa ") == 2
        assert "--- Sayfa 6 ---" in content

    def test_single_page(self, report: FileHandler) -> None:
        content = report.read_report("rapor.pdf", pages="4", max_chars=0)["content"]
        assert content.count("--- Sayfa ") == 1
        assert "sayfa 4" in content

    def test_multiple_ranges_and_duplicates(self, report: FileHandler) -> None:
        content = report.read_report("rapor.pdf", pages="1,3,4-5,3", max_chars=0)["content"]
        assert content.count("--- Sayfa ") == 4
        assert "--- Sayfa 5 ---" in content
        assert "--- Sayfa 2 ---" not in content

    def test_whitespace_in_selection_is_tolerated(self, report: FileHandler) -> None:
        content = report.read_report("rapor.pdf", pages=" 1 - 2 , 4 ", max_chars=0)["content"]
        assert content.count("--- Sayfa ") == 3

    def test_pages_are_echoed_back(self, report: FileHandler) -> None:
        assert report.read_report("rapor.pdf", pages="1-2", max_chars=0)["pages"] == "1-2"
        assert report.read_report("rapor.pdf", max_chars=0)["pages"] is None

    @pytest.mark.parametrize(
        "selection",
        ["0", "3-0", "5-2", "abc", "1-a", "13", "9-", "1-0", ""],
    )
    def test_invalid_selection_is_rejected(self, report: FileHandler, selection: str) -> None:
        with pytest.raises(CompanyFileError):
            report.read_report("rapor.pdf", pages=selection, max_chars=0)

    def test_out_of_range_message_states_the_page_count(self, report: FileHandler) -> None:
        with pytest.raises(CompanyFileError) as excinfo:
            report.read_report("rapor.pdf", pages="9", max_chars=0)
        assert "page 9 does not exist" in str(excinfo.value)
        assert "6 page(s)" in str(excinfo.value)

    def test_pages_on_a_non_pdf_is_rejected(self, report: FileHandler) -> None:
        report.write_file("notlar.md", "not")
        with pytest.raises(CompanyFileError) as excinfo:
            report.read_report("notlar.md", pages="1-2")
        assert "pages only applies to PDF files" in str(excinfo.value)

    def test_trimmed_text_is_taken_before_the_pdf_is_cut(self, report: FileHandler) -> None:
        whole = report.read_report("rapor.pdf", max_chars=0)["total_chars"]
        part = report.read_report("rapor.pdf", pages="1-2", max_chars=0)["total_chars"]
        assert part < whole

    def test_selection_survives_the_budget(self, report: FileHandler) -> None:
        result = report.read_report("rapor.pdf", pages="1", max_chars=10)
        assert result["truncated"] is True
        assert result["total_pages"] == 6
        assert result["start_char"] == 0

    def test_empty_selection_is_refused(self) -> None:
        from src.file_handler import _resolve_page_selection

        with pytest.raises(CompanyFileError) as excinfo:
            _resolve_page_selection([], 3)
        assert "matches none of the 3 page(s)" in str(excinfo.value)
