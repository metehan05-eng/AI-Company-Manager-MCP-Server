"""Path traversal, extension allowlist, and file size guard tests."""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from conftest import PROJECT_ROOT
from src.file_handler import CompanyFileError, CrossProcessLock, FileHandler


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


class TestCrossProcessLock:
    def test_lock_file_lives_outside_the_data_dir(self, handler: FileHandler) -> None:
        lock = CrossProcessLock(handler.data_dir)
        assert lock.lock_path == handler.cross_process_lock().lock_path
        assert lock.lock_path.parent != handler.data_dir
        assert lock.lock_path.name.startswith("ai-company-manager-")
        assert lock.lock_path.suffix == ".lock"

    def test_same_data_dir_shares_one_lock_path(self, data_dir: Path) -> None:
        first = FileHandler(data_dir=data_dir).cross_process_lock()
        second = FileHandler(data_dir=data_dir).cross_process_lock()
        assert first.lock_path == second.lock_path

    def test_different_data_dirs_use_different_lock_files(self, tmp_path: Path) -> None:
        first = FileHandler(data_dir=tmp_path / "a").cross_process_lock()
        second = FileHandler(data_dir=tmp_path / "b").cross_process_lock()
        assert first.lock_path != second.lock_path

    def test_reentrant_within_one_thread(self, handler: FileHandler) -> None:
        with handler.cross_process_lock(), handler.cross_process_lock():
            assert handler.cross_process_lock().lock_path.exists()

    def test_second_thread_waits_for_the_first_to_release(self, handler: FileHandler) -> None:
        first_lock = handler.cross_process_lock()
        second_lock = handler.cross_process_lock()
        held = threading.Event()
        release = threading.Event()
        order: list[str] = []

        def first() -> None:
            with first_lock:
                held.set()
                release.wait(5)
                order.append("first")

        def second() -> None:
            held.wait(5)
            with second_lock:
                order.append("second")

        first_thread = threading.Thread(target=first)
        second_thread = threading.Thread(target=second)
        first_thread.start()
        second_thread.start()
        assert held.wait(5)
        time.sleep(0.2)
        assert order == []
        release.set()
        first_thread.join(5)
        second_thread.join(5)
        assert order == ["first", "second"]

    def test_different_data_dirs_do_not_block_each_other(self, tmp_path: Path) -> None:
        first = FileHandler(data_dir=tmp_path / "a")
        second = FileHandler(data_dir=tmp_path / "b")
        acquired = threading.Event()

        def worker() -> None:
            with second.cross_process_lock():
                acquired.set()

        with first.cross_process_lock():
            thread = threading.Thread(target=worker)
            thread.start()
            thread.join(5)
            assert acquired.is_set()

    def test_another_process_blocks_until_release(
        self, handler: FileHandler, tmp_path: Path
    ) -> None:
        marker = tmp_path / "child_saw_lock"
        script = (
            "import pathlib, sys\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "from src.file_handler import CrossProcessLock\n"
            "print('ready', flush=True)\n"
            "with CrossProcessLock(sys.argv[2]):\n"
            "    pathlib.Path(sys.argv[3]).write_text('got', encoding='utf-8')\n"
        )
        with handler.cross_process_lock():
            child = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    script,
                    str(PROJECT_ROOT),
                    str(handler.data_dir),
                    str(marker),
                ],
                stdout=subprocess.PIPE,
                text=True,
            )
            assert child.stdout is not None
            assert child.stdout.readline().strip() == "ready"
            time.sleep(0.3)
            assert not marker.exists()
        assert child.wait(15) == 0
        assert marker.read_text(encoding="utf-8") == "got"
