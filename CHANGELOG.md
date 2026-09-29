# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `inspect_company_data` tool: reports whether each core file matches the expected
  schema, which columns were mapped, and which files still block the other tools.
- `migrate_company_data` tool: rewrites an existing `employees.csv` in the canonical
  column layout. Dry run by default; applies with a timestamped backup.
- Flexible `employees.csv` reading: common alternative column names (`id`,
  `full_name`, `monthly_salary_usd`, `hire_date`, `durum`, and others) are mapped to
  the canonical schema. Unrecognized columns such as `performance_score` are preserved
  across reads and writes instead of being dropped.
- Automatic defaults for absent `employee_id`, `start_date` and `status`; `start_date`
  accepts `YYYY-MM-DD`, `DD.MM.YYYY` and `DD/MM/YYYY`.
- Actionable error messages that list the columns actually found and point to
  `migrate_company_data`.

### Not changed

- `company_profile.json` and `financials.json` are still not auto-migrated. Real-world
  variants carry custom fields (`ARR`, `fiscal_year`, runway) that cannot be mapped
  without losing data, so `inspect_company_data` reports them as `incompatible` and
  leaves the decision to the user.

### Planned

- `get_financial_report`, `update_employee`, `delete_financial_record`, and
  `list_notes` tools

## [0.2.0] - 2026-09-29

### Added

- PDF reading with `pypdf`: all pages, `--- Sayfa N ---` headers, OCR warning for
  scanned documents, and clear errors for encrypted or malformed files.
- DOCX reading with `python-docx`: paragraphs and tables.
- `pyproject.toml` with consolidated `ruff`, `mypy`, and `pytest` configuration plus
  an optional `dev` extra.
- `pytest` suite (117 tests) covering file handling, path security, business rules,
  MCP tool contracts, and repository hygiene.
- GitHub Actions CI: lint, type check, tests on Ubuntu/Windows/macOS across
  Python 3.10-3.13, coverage artifact, and an installer smoke test.
- `install.py --check` to verify dependencies and the live server without modifying
  anything.
- `CHANGELOG.md`, `CONTRIBUTING.md`, `SECURITY.md`, and issue/PR templates.
- `ai-company-manager-mcp` console script entry point.

### Fixed

- Single-column CSV files are no longer corrupted: the delimiter sniffer is limited
  to `,`, tab, `;`, and `|` instead of guessing any character (a header such as
  `value` previously produced `Unnamed: 0,alue`).
- Negative numbers written to CSV and XLSX stay numeric instead of being prefixed
  with a quote by spreadsheet escaping. Formula injection protection (`=`, `+`, `-`,
  `@`) is unchanged.
- Money fields reject more than two decimal places with an actionable message, and
  all tools convert Pydantic validation dumps into a single readable line.

## [0.1.0] - 2026-09-26

### Added

- Initial MCP server with seven tools for listing, reading, and writing company
  documents, financial records, employees, and notes.
- Cross-platform installer (`install.py`) with `uv` fast path, atomic MCP config
  merging, and live server verification.
- Portable launchers for Linux/macOS (`run_mcp.sh`) and Windows (`run_mcp.cmd`).
- Turkish and English documentation, MIT license, and git-ignored `company_data`
  template.

[Unreleased]: https://github.com/metehan05-eng/AI-Company-Manager-MCP-Server/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/metehan05-eng/AI-Company-Manager-MCP-Server/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/metehan05-eng/AI-Company-Manager-MCP-Server/releases/tag/v0.1.0
