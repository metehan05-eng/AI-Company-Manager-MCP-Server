"""Repository hygiene: real company data must never reach version control."""

from __future__ import annotations

import subprocess
from pathlib import Path

from conftest import PROJECT_ROOT

SECRET_MARKERS = (
    "api" + "_key",
    "api" + "-key",
    "pass" + "word=",
    "secret" + "_key",
    "BEGIN RSA " + "PRIVATE KEY",
    "sk-" + "live",
)

TRACKED_ALLOWLIST = {
    ".cursor/mcp.json",
    ".env.example",
    ".github/ISSUE_TEMPLATE/bug_report.md",
    ".github/ISSUE_TEMPLATE/feature_request.md",
    ".github/PULL_REQUEST_TEMPLATE.md",
    ".github/workflows/ci.yml",
    ".gitignore",
    "LICENSE",
    "README.md",
    "SECURITY.md",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "install.py",
    "mcp.json",
    "pyproject.toml",
    "requirements.txt",
    "run_mcp.cmd",
    "run_mcp.sh",
    "company_data/.gitkeep",
    "company_data/README.md",
    "src/__init__.py",
    "src/company_wizard.py",
    "src/file_handler.py",
    "src/server.py",
    "tests/conftest.py",
    "tests/test_company_wizard.py",
    "tests/test_file_handler_formats.py",
    "tests/test_file_handler_security.py",
    "tests/test_server_tools.py",
    "tests/test_repository.py",
}


def git_tracked_files() -> set[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        check=True,
    )
    return {line for line in result.stdout.split("\n") if line.strip()}


class TestRepositoryHygiene:
    def test_no_unexpected_tracked_files(self) -> None:
        tracked = git_tracked_files()
        unexpected = tracked - TRACKED_ALLOWLIST
        assert not unexpected, f"unexpected tracked files: {sorted(unexpected)}"

    def test_company_data_only_documentation_tracked(self) -> None:
        tracked = git_tracked_files()
        leaked = {path for path in tracked if path.startswith("company_data/")}
        assert leaked <= {"company_data/.gitkeep", "company_data/README.md"}

    def test_venv_not_tracked(self) -> None:
        tracked = git_tracked_files()
        assert not any(path.startswith(".venv/") for path in tracked)

    def test_cache_files_not_tracked(self) -> None:
        tracked = git_tracked_files()
        forbidden = (".pytest_cache/", ".ruff_cache/", ".mypy_cache/", "__pycache__/")
        assert not any(path.startswith(forbidden) or "/__pycache__/" in path for path in tracked)

    def test_no_secrets_in_tracked_files(self) -> None:
        offenders: list[str] = []
        for relative in git_tracked_files():
            path = PROJECT_ROOT / relative
            if not path.is_file() or path.suffix in {".xlsx", ".pdf", ".docx"}:
                continue
            if path == Path(__file__).resolve():
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="ignore").lower()
            except OSError:  # pragma: no cover
                continue
            if any(marker in content for marker in SECRET_MARKERS):
                offenders.append(relative)
        assert not offenders, f"possible secrets in: {offenders}"

    def test_secret_scan_is_not_a_no_op(self) -> None:
        probe = PROJECT_ROOT / "tests" / "_secret_scan_probe.txt"
        probe.write_text("api_key=leaked", encoding="utf-8")
        try:
            content = probe.read_text(encoding="utf-8").lower()
            assert any(marker in content for marker in SECRET_MARKERS)
        finally:
            probe.unlink()

    def test_gitignore_excludes_real_company_data(self) -> None:
        content = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
        assert "company_data/*" in content
        assert "!company_data/.gitkeep" in content

    def test_pytest_and_coverage_ignored(self) -> None:
        content = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
        for pattern in (".pytest_cache/", ".coverage", "htmlcov/"):
            assert pattern in content

    def test_ci_workflow_exists(self) -> None:
        assert (PROJECT_ROOT / ".github" / "workflows" / "ci.yml").is_file()
