from __future__ import annotations

import csv
import json
import os
import tempfile
from collections.abc import Mapping
from datetime import date, datetime, timezone
from decimal import Decimal
from io import BytesIO, StringIO
from pathlib import Path
from typing import Any
from uuid import UUID

import pandas as pd
from pydantic import BaseModel

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SUPPORTED_EXTENSIONS = frozenset({".txt", ".md", ".json", ".csv", ".xlsx", ".pdf", ".docx"})
TEXT_EXTENSIONS = frozenset({".txt", ".md"})


class CompanyFileError(ValueError):
    pass


def _read_positive_integer(name: str, default: int, minimum: int) -> int:
    raw_value = os.getenv(name, str(default))
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise CompanyFileError(f"{name} must be an integer") from exc
    if value < minimum:
        raise CompanyFileError(f"{name} must be at least {minimum}")
    return value


def _parse_page_selection(spec: str) -> list[tuple[int, int | None]]:
    """Parse "1-3,7,9-" into inclusive ranges; a None end means "to the last page"."""
    selection: list[tuple[int, int | None]] = []
    for chunk in spec.split(","):
        part = chunk.strip()
        if not part:
            raise CompanyFileError(f"'{spec}' is not a valid page selection; try '1-5' or '3'")
        if "-" in part:
            raw_start, _, raw_end = part.partition("-")
            start_text, end_text = raw_start.strip(), raw_end.strip()
            try:
                start = int(start_text) if start_text else 1
                end = int(end_text) if end_text else None
            except ValueError as exc:
                raise CompanyFileError(
                    f"'{part}' is not a valid page range; try '1-5' or '3'"
                ) from exc
            if start < 1 or (end is not None and end < 1):
                raise CompanyFileError(f"'{part}' is not a valid page range; pages start at 1")
            if end is not None and end < start:
                raise CompanyFileError(
                    f"'{part}' is not a valid page range; the end must not precede the start"
                )
            selection.append((start, end))
            continue
        try:
            page = int(part)
        except ValueError as exc:
            raise CompanyFileError(
                f"'{spec}' is not a valid page selection; try '1-5' or '3'"
            ) from exc
        if page < 1:
            raise CompanyFileError(f"'{part}' is not a valid page range; pages start at 1")
        selection.append((page, page))
    return selection


def _json_default(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, tuple):
        return list(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Invalid JSON number: {value}")


CSV_DELIMITER_CANDIDATES = ",\t;|"


def _is_numeric_text(value: str) -> bool:
    try:
        Decimal(value.strip())
    except ArithmeticError:
        return False
    return True


def _detect_csv_delimiter(sample: str) -> str:
    if not sample.strip():
        return ","
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=CSV_DELIMITER_CANDIDATES)
    except csv.Error:
        return ","
    return dialect.delimiter


def _read_csv_frame(text: str) -> pd.DataFrame:
    return pd.read_csv(
        StringIO(text),
        dtype=object,
        keep_default_na=False,
        sep=_detect_csv_delimiter(text),
    )


def _safe_spreadsheet_value(value: Any) -> Any:
    if isinstance(value, str):
        stripped = value.lstrip()
        if stripped.startswith(("=", "+", "-", "@")) and not _is_numeric_text(stripped):
            return f"'{value}"
    return value


def _safe_dataframe(frame: pd.DataFrame) -> pd.DataFrame:
    safe_frame = frame.copy()
    for column in safe_frame.columns:
        safe_frame[column] = safe_frame[column].map(_safe_spreadsheet_value)
    return safe_frame


def _read_pdf(path: Path, selection: list[tuple[int, int | None]] | None = None) -> tuple[str, int]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise CompanyFileError(
            'PDF support requires pypdf. Install it with: pip install "pypdf>=3.0.0"'
        ) from exc

    sections: list[str] = []
    has_text = False
    page_count = 0
    try:
        with path.open("rb") as file_handle:
            reader = PdfReader(file_handle)
            if reader.is_encrypted and not reader.decrypt(""):
                raise CompanyFileError(
                    "PDF is password-protected and cannot be read without a password"
                )
            pages = list(reader.pages)
            page_count = len(pages)
            wanted = _resolve_page_selection(selection, page_count)
            for page_number in wanted:
                page_text = (pages[page_number - 1].extract_text() or "").strip()
                if page_text:
                    has_text = True
                    sections.append(f"--- Sayfa {page_number} ---\n{page_text}")
                else:
                    sections.append(
                        f"--- Sayfa {page_number} ---\n[Bu sayfada metin içeriği bulunamadı.]"
                    )
    except CompanyFileError:
        raise
    except Exception as exc:
        raise CompanyFileError(f"PDF dosyası okunamadı: {exc}") from exc

    if not has_text:
        sections.append(
            "[UYARI: PDF metin içeriği bulunamadı. Belge taranmış görsel olabilir; "
            "metin almak için OCR uygulanmalıdır.]"
        )
    return "\n\n".join(sections), page_count


def _resolve_page_selection(
    selection: list[tuple[int, int | None]] | None, page_count: int
) -> list[int]:
    """Expand a parsed selection into page numbers, rejecting pages that do not exist."""
    if selection is None:
        return list(range(1, page_count + 1))

    wanted: list[int] = []
    for start, end in selection:
        last = page_count if end is None else end
        if start > page_count:
            raise CompanyFileError(
                f"page {start} does not exist; the document has {page_count} page(s)"
            )
        for page_number in range(start, min(last, page_count) + 1):
            if page_number not in wanted:
                wanted.append(page_number)
    if not wanted:
        raise CompanyFileError(
            f"the page selection matches none of the {page_count} page(s) in the document"
        )
    return wanted


def _read_docx(path: Path) -> str:
    try:
        from docx import Document
    except ImportError as exc:
        raise CompanyFileError(
            'Word support requires python-docx. Install it with: pip install "python-docx>=0.8.11"'
        ) from exc

    try:
        document = Document(str(path))
    except Exception as exc:
        raise CompanyFileError(f"Word belgesi okunamadı: {exc}") from exc

    sections = [
        paragraph.text.strip() for paragraph in document.paragraphs if paragraph.text.strip()
    ]
    for table_number, table in enumerate(document.tables, start=1):
        sections.append(f"--- Tablo {table_number} ---")
        table_rows = [
            " | ".join(" ".join(cell.text.split()) for cell in row.cells)
            for row in table.rows
            if any(cell.text.strip() for cell in row.cells)
        ]
        sections.extend(table_rows or ["[Tablo boş.]"])

    if not sections:
        return "[UYARI: Word belgesinde metin veya tablo içeriği bulunamadı.]"
    return "\n\n".join(sections)


class FileHandler:
    def __init__(
        self,
        data_dir: str | Path | None = None,
        max_file_size_mb: int | None = None,
    ) -> None:
        configured_dir = data_dir or os.getenv("COMPANY_DATA_DIR")
        raw_dir = (
            Path(configured_dir).expanduser() if configured_dir else PROJECT_ROOT / "company_data"
        )
        if not raw_dir.is_absolute():
            raw_dir = PROJECT_ROOT / raw_dir
        self.data_dir = raw_dir.resolve()
        if max_file_size_mb is None:
            self.max_file_size_mb = _read_positive_integer("COMPANY_MAX_FILE_MB", 10, 1)
        elif max_file_size_mb < 1:
            raise CompanyFileError("max_file_size_mb must be at least 1")
        else:
            self.max_file_size_mb = max_file_size_mb
        self.max_file_size_bytes = self.max_file_size_mb * 1024 * 1024
        self.max_read_chars = _read_positive_integer("COMPANY_READ_MAX_CHARS", 100_000, 1_000)
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def resolve_path(self, filename: str) -> tuple[Path, Path]:
        if not isinstance(filename, str) or not filename.strip():
            raise CompanyFileError("filename must be a non-empty string")
        if "\x00" in filename:
            raise CompanyFileError("filename must not contain null characters")

        relative_path = Path(filename.strip())
        if relative_path.is_absolute() or relative_path.drive:
            raise CompanyFileError("absolute paths are not allowed")
        if ".." in relative_path.parts:
            raise CompanyFileError("parent directory traversal is not allowed")

        extension = relative_path.suffix.lower()
        if extension not in SUPPORTED_EXTENSIONS:
            allowed = ", ".join(sorted(SUPPORTED_EXTENSIONS))
            raise CompanyFileError(
                f"unsupported file extension '{extension or 'none'}'; allowed: {allowed}"
            )

        candidate = self.data_dir.joinpath(*relative_path.parts)
        current = self.data_dir
        for part in relative_path.parts:
            current = current / part
            if current.is_symlink():
                raise CompanyFileError("symbolic links are not allowed")

        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(self.data_dir)
        except ValueError as exc:
            raise CompanyFileError("path escapes the company data directory") from exc

        return relative_path, candidate

    def list_files(self) -> list[dict[str, Any]]:
        files: list[dict[str, Any]] = []
        if not self.data_dir.exists():
            return files

        for root, directory_names, filenames in os.walk(self.data_dir, followlinks=False):
            root_path = Path(root)
            directory_names[:] = sorted(
                name for name in directory_names if not (root_path / name).is_symlink()
            )
            for name in sorted(filenames):
                path = root_path / name
                if path.is_symlink() or not path.is_file():
                    continue
                extension = path.suffix.lower()
                if extension in TEXT_EXTENSIONS:
                    file_type = "text"
                elif extension == ".json":
                    file_type = "json"
                elif extension == ".csv":
                    file_type = "csv"
                elif extension == ".xlsx":
                    file_type = "excel"
                elif extension == ".pdf":
                    file_type = "pdf"
                elif extension == ".docx":
                    file_type = "word"
                else:
                    file_type = "unsupported"
                try:
                    stat = path.stat()
                except FileNotFoundError:
                    continue
                files.append(
                    {
                        "filename": path.relative_to(self.data_dir).as_posix(),
                        "extension": extension,
                        "type": file_type,
                        "supported": extension in SUPPORTED_EXTENSIONS,
                        "size_bytes": stat.st_size,
                        "modified_at": datetime.fromtimestamp(
                            stat.st_mtime, timezone.utc
                        ).isoformat(),
                    }
                )

        return sorted(files, key=lambda item: item["filename"].casefold())

    def _ensure_readable_file(self, path: Path) -> None:
        if not path.exists():
            raise FileNotFoundError(f"company file not found: {path.name}")
        if not path.is_file():
            raise IsADirectoryError(f"company path is not a file: {path.name}")
        file_size = path.stat().st_size
        if file_size > self.max_file_size_bytes:
            limit = self.max_file_size_mb
            raise CompanyFileError(f"file exceeds the {limit} MB size limit")

    def read_file(self, filename: str) -> str:
        """Read a file in full. Used for internal parsing, so nothing is ever trimmed."""
        return self.read_report(filename, max_chars=0)["content"]

    def read_report(
        self,
        filename: str,
        start_char: int = 0,
        max_chars: int | None = None,
        pages: str | None = None,
    ) -> dict[str, Any]:
        """Read a file for a caller that has to fit a context budget.

        `max_chars` of 0 means "no limit". Trimming is always announced in `content`
        and reflected in `truncated`, so a caller can never mistake a trimmed file for
        the whole file.
        """
        relative_path, path = self.resolve_path(filename)
        self._ensure_readable_file(path)
        extension = path.suffix.lower()

        if start_char < 0:
            raise CompanyFileError("start_char must not be negative")
        budget = self.max_read_chars if max_chars is None else max_chars
        if budget < 0:
            raise CompanyFileError("max_chars must not be negative")

        selection: list[tuple[int, int | None]] | None = None
        if pages is not None:
            if extension != ".pdf":
                raise CompanyFileError(
                    f"pages only applies to PDF files; {relative_path.name} is {extension}"
                )
            selection = _parse_page_selection(pages)

        content, page_count = self._extract_content(path, extension, selection)
        total_chars = len(content)
        offset = min(start_char, total_chars)
        window = content[offset:]
        truncated = bool(budget) and len(window) > budget
        if truncated:
            window = window[:budget]
            window += self._trim_notice(relative_path.name, offset, window, total_chars)
        consumed = offset + (budget if truncated else len(window))
        next_start = min(consumed, total_chars)

        return {
            "filename": relative_path.as_posix(),
            "format": extension.lstrip("."),
            "size_bytes": path.stat().st_size,
            "content": window,
            "total_chars": total_chars,
            "start_char": offset,
            "next_start_char": next_start,
            "truncated": truncated,
            "pages": pages,
            "total_pages": page_count,
        }

    @staticmethod
    def _trim_notice(filename: str, offset: int, window: str, total_chars: int) -> str:
        return (
            f"\n\n[... {filename} kisaltildi: {total_chars} karakterin "
            f"{offset + 1}-{offset + len(window)} arasi gosterildi. Devamini okumak icin "
            f"start_char={offset + len(window)} ile tekrar cagirin.]"
        )

    def _extract_content(
        self,
        path: Path,
        extension: str,
        selection: list[tuple[int, int | None]] | None,
    ) -> tuple[str, int | None]:
        if extension in TEXT_EXTENSIONS:
            return path.read_text(encoding="utf-8-sig"), None
        if extension == ".json":
            with path.open("r", encoding="utf-8-sig") as file_handle:
                data = json.load(
                    file_handle,
                    parse_constant=_reject_json_constant,
                )
            return (
                json.dumps(
                    data,
                    ensure_ascii=False,
                    indent=2,
                    allow_nan=False,
                    default=_json_default,
                ),
                None,
            )
        if extension == ".csv":
            frame = _read_csv_frame(path.read_text(encoding="utf-8-sig"))
            text = frame.to_csv(index=False, lineterminator="\n").rstrip("\n")
            return text, None
        if extension == ".xlsx":
            sections: list[str] = []
            with pd.ExcelFile(path, engine="openpyxl") as workbook:
                for sheet_name in workbook.sheet_names:
                    frame = pd.read_excel(
                        workbook,
                        sheet_name=sheet_name,
                        dtype=object,
                        keep_default_na=False,
                    )
                    csv_text = frame.to_csv(index=False, lineterminator="\n").rstrip("\n")
                    sections.append(f"## {sheet_name}\n{csv_text}")
            return "\n\n".join(sections), None
        if extension == ".pdf":
            return _read_pdf(path, selection)
        if extension == ".docx":
            return _read_docx(path), None

        raise CompanyFileError(f"unsupported file extension: {extension}")

    def _serialize_content(self, extension: str, content: Any) -> bytes:
        if extension in TEXT_EXTENSIONS:
            if not isinstance(content, str):
                raise TypeError("TXT and MD content must be a string")
            return content.encode("utf-8")

        if extension == ".json":
            if isinstance(content, (bytes, bytearray)):
                try:
                    text = bytes(content).decode("utf-8-sig")
                except UnicodeDecodeError as exc:
                    raise ValueError("JSON content must be UTF-8 encoded") from exc
            elif isinstance(content, str):
                text = content
            elif isinstance(content, (Mapping, list, tuple, BaseModel)):
                text = json.dumps(
                    content,
                    ensure_ascii=False,
                    indent=2,
                    allow_nan=False,
                    default=_json_default,
                )
            else:
                raise TypeError(
                    "JSON content must be JSON text, a mapping, a list, or a Pydantic model"
                )
            try:
                json.loads(
                    text,
                    parse_constant=_reject_json_constant,
                )
            except (json.JSONDecodeError, ValueError) as exc:
                raise ValueError(f"invalid JSON content: {exc}") from exc
            return text.encode("utf-8")

        if extension == ".csv":
            if isinstance(content, pd.DataFrame):
                return (
                    _safe_dataframe(content)
                    .to_csv(index=False, lineterminator="\n")
                    .encode("utf-8")
                )
            if isinstance(content, str):
                _read_csv_frame(content)
                rows = [
                    [_safe_spreadsheet_value(cell) for cell in row]
                    for row in csv.reader(StringIO(content))
                ]
                output = StringIO()
                csv.writer(output, lineterminator="\n").writerows(rows)
                return output.getvalue().encode("utf-8")
            if isinstance(content, (Mapping, list)):
                frame = pd.DataFrame(content)
                return (
                    _safe_dataframe(frame).to_csv(index=False, lineterminator="\n").encode("utf-8")
                )
            raise TypeError("CSV content must be text, a DataFrame, a mapping, or a list")

        if extension == ".xlsx":
            if isinstance(content, pd.DataFrame):
                sheets: Mapping[str, pd.DataFrame] = {"Sheet1": content}
            elif isinstance(content, Mapping):
                sheets = content
            else:
                raise TypeError("XLSX content must be a DataFrame or a sheet mapping")
            if not sheets:
                raise ValueError("an XLSX workbook must contain at least one sheet")

            buffer = BytesIO()
            with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
                for sheet_name, sheet_content in sheets.items():
                    if not isinstance(sheet_name, str) or not sheet_name.strip():
                        raise ValueError("sheet names must be non-empty strings")
                    if isinstance(sheet_content, pd.DataFrame):
                        frame = sheet_content
                    elif isinstance(sheet_content, (Mapping, list)):
                        frame = pd.DataFrame(sheet_content)
                    else:
                        raise TypeError(f"worksheet '{sheet_name}' must contain tabular data")
                    _safe_dataframe(frame).to_excel(
                        writer,
                        sheet_name=sheet_name,
                        index=False,
                    )
            return buffer.getvalue()
        if extension in {".pdf", ".docx"}:
            raise CompanyFileError("PDF and DOCX files are read-only")

        raise CompanyFileError(f"unsupported file extension: {extension}")

    def _atomic_write_bytes(self, target: Path, payload: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=target.parent,
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as file_handle:
                file_handle.write(payload)
                file_handle.flush()
                os.fsync(file_handle.fileno())
            os.replace(temporary_path, target)
            if os.name != "nt":
                directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                directory_descriptor = os.open(target.parent, directory_flags)
                try:
                    os.fsync(directory_descriptor)
                finally:
                    os.close(directory_descriptor)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise

    def write_file(self, filename: str, content: Any) -> dict[str, Any]:
        relative_path, target = self.resolve_path(filename)
        if target.exists() and not target.is_file():
            raise IsADirectoryError(f"target is not a file: {relative_path.as_posix()}")

        payload = self._serialize_content(relative_path.suffix.lower(), content)
        if len(payload) > self.max_file_size_bytes:
            limit = self.max_file_size_mb
            raise CompanyFileError(f"file exceeds the {limit} MB size limit")

        existed = target.exists()
        self._atomic_write_bytes(target, payload)
        return {
            "filename": relative_path.as_posix(),
            "size_bytes": len(payload),
            "created": not existed,
            "updated": existed,
        }


default_file_handler = FileHandler()


def list_files() -> list[dict[str, Any]]:
    return default_file_handler.list_files()


def read_file(filename: str) -> str:
    return default_file_handler.read_file(filename)


def write_file(filename: str, content: Any) -> dict[str, Any]:
    return default_file_handler.write_file(filename, content)
