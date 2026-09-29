from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.company_wizard import CompanyWizard  # noqa: E402
from src.file_handler import FileHandler  # noqa: E402


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "company_data"


@pytest.fixture(autouse=True)
def isolated_data_dir(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("COMPANY_DATA_DIR", str(data_dir))
    yield


@pytest.fixture
def handler(data_dir: Path) -> FileHandler:
    return FileHandler(data_dir=data_dir)


@pytest.fixture
def wizard(handler: FileHandler) -> CompanyWizard:
    return CompanyWizard(handler)


@pytest.fixture
def company(wizard: CompanyWizard) -> CompanyWizard:
    wizard.init_company("Acme", "Yazilim", 10_000.0)
    return wizard


@pytest.fixture
def real_data_dir() -> Path:
    return Path(os.environ.get("REAL_COMPANY_DATA_DIR", PROJECT_ROOT / "company_data"))


def make_pdf_bytes(text: str, pages: int = 1) -> bytes:
    page_count = pages
    kids = " ".join(f"{4 + 2 * index} 0 R" for index in range(page_count))
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {page_count} >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for index in range(page_count):
        content = f"BT /F1 18 Tf 72 700 Td ({text} sayfa {index + 1}) Tj ET"
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {5 + 2 * index} 0 R >>"
        )
        objects.append(f"<< /Length {len(content)} >>\nstream\n{content}\nendstream")

    document = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(document))
        document += f"{number} 0 obj\n{body}\nendobj\n".encode("latin-1")
    xref_offset = len(document)
    document += f"xref\n0 {len(objects) + 1}\n".encode()
    document += b"0000000000 65535 f \n"
    for offset in offsets:
        document += f"{offset:010d} 00000 n \n".encode()
    document += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n"
    ).encode()
    return bytes(document)
