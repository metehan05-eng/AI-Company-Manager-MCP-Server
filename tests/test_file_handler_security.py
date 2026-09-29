"""Path traversal, extension allowlist, and file size guard tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.file_handler import CompanyFileError, FileHandler


class TestResolvePathSecurity:
    @pytest.mark.parametrize(
        "candidate",
        [
            "../../etc/passwd",
            "../company_data.json",
            "/etc/passwd",
            "sub/../../escape.csv",
            "./../../outside.txt",
        ],
    )
    def test_rejects_traversal(self, handler: FileHandler, candidate: str) -> None:
        with pytest.raises(CompanyFileError):
            handler.resolve_path(candidate)

    @pytest.mark.parametrize("candidate", [".exe", ".sh", ".bat", ".py", ""])
    def test_rejects_unsupported_extension(self, handler: FileHandler, candidate: str) -> None:
        with pytest.raises(CompanyFileError):
            handler.resolve_path(candidate or "no_extension")

    def test_rejects_absolute_path_outside_data_dir(self, handler: FileHandler) -> None:
        with pytest.raises(CompanyFileError):
            handler.resolve_path("/tmp/outside.csv")

    def test_accepts_supported_extensions(self, handler: FileHandler) -> None:
        for name in ("a.txt", "a.md", "a.json", "a.csv", "a.xlsx", "a.pdf", "a.docx"):
            relative, target = handler.resolve_path(name)
            assert str(relative) == name
            assert target.parent == handler.data_dir

    def test_nested_path_under_data_dir_allowed(self, handler: FileHandler) -> None:
        relative, target = handler.resolve_path("arsiv/rapor.csv")
        assert str(relative) == "arsiv/rapor.csv"
        assert target.parent == handler.data_dir / "arsiv"

    @pytest.mark.skipif(not Path("/").joinpath("proc/self").exists(), reason="needs /proc")
    def test_rejects_symlink_escape(self, handler: FileHandler, tmp_path: Path) -> None:
        outside = tmp_path / "outside.csv"
        outside.write_text("a,b\n1,2\n", encoding="utf-8")
        link = handler.data_dir / "linked.csv"
        handler.data_dir.mkdir(parents=True, exist_ok=True)
        link.symlink_to(outside)
        with pytest.raises(CompanyFileError):
            handler.resolve_path("linked.csv")


class TestReadOnlyFormats:
    @pytest.mark.parametrize("extension", [".pdf", ".docx"])
    def test_write_rejected(self, handler: FileHandler, extension: str) -> None:
        with pytest.raises(CompanyFileError) as excinfo:
            handler.write_file(f"belge{extension}", "content")
        assert "read-only" in str(excinfo.value).lower()


class TestFileSizeLimit:
    def test_rejects_oversized_read(self, data_dir: Path) -> None:
        handler = FileHandler(data_dir=data_dir, max_file_size_mb=1)
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "big.txt").write_text("x" * (2 * 1024 * 1024), encoding="utf-8")
        with pytest.raises(CompanyFileError) as excinfo:
            handler.read_file("big.txt")
        assert "size limit" in str(excinfo.value).lower()

    def test_rejects_oversized_write(self, data_dir: Path) -> None:
        handler = FileHandler(data_dir=data_dir, max_file_size_mb=1)
        with pytest.raises(CompanyFileError):
            handler.write_file("big.txt", "x" * (2 * 1024 * 1024))

    def test_missing_file_message(self, handler: FileHandler) -> None:
        with pytest.raises(FileNotFoundError) as excinfo:
            handler.read_file("yok.csv")
        assert "not found" in str(excinfo.value).lower()
