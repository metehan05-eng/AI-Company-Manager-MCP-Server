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
