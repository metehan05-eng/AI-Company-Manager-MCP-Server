# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `get_financial_report` tool: a read-only breakdown of `financials.json`. Reports the
  totals, remaining budget and budget usage, splits income and expenses by category
  (largest first, with record count and share), and lists the `cash_flow_template`
  periods with their opening balance, net cash flow and closing balance. Pass `period` to
  narrow the cash flow table; an unknown period lists the available ones. `warnings` is
  empty for a healthy ledger and otherwise reports a negative balance or expenses above
  the initial budget.
- `apply_company_data_migration` tool: writes the mapping that
  `plan_company_data_migration` proposed. Every open item is answerable by key
  (`<file>:<field>`); `true` accepts the suggestion and any other value replaces it.
  Unanswered or unknown keys are refused before anything is written, and a key with no
  suggestion rejects `true` so a missing field cannot be confirmed by accident.
  Dry run by default; applies with a timestamped backup of every changed file. Files that
  already match the canonical schema are left untouched, so the call is safe to repeat.
- `plan_company_data_migration` tool: a **read-only** proposal for mapping an existing
  `company_profile.json` and `financials.json` onto the canonical models. Every source
  field lands in one of `already_valid`, `mappable`, `skipped_conflicts`,
  `incompatible_values`, `no_target` or `missing_required`, and each answer is turned into
  a question the user has to resolve. Nothing is written.
- `proposed_document` is only returned when no question is open, so a ready-to-use
  document cannot be copied into place without being read first.
- Cross-file checks: a ledger without `company_name` is matched against the profile, and
  a profile that would default to `TRY` while the ledger is in another currency is
  reported. The server never applies an exchange rate.
- Fields that look convertible but are not are reported with a reason, e.g.
  `pending_invoices_receivable` (would overstate income) and percentage-based
  `major_expense_categories` (not amounts).
- `inspect_company_data` tool: reports whether each core file matches the expected
  schema, which columns were mapped, and which files still block the other tools.
- `migrate_company_data` tool: rewrites an existing `employees.csv` in the canonical
  column layout. Dry run by default; applies with a timestamped backup.
- Flexible `employees.csv` reading: common alternative column names (`id`,
  `full_name`, `monthly_salary_usd`, `hire_date`, `durum`, and others) are mapped to
  the canonical schema. Unrecognized columns such as `performance_score` are preserved
  across reads and writes instead of being dropped.
- Automatic defaults for absent `start_date` and `status`; `start_date`
  accepts `YYYY-MM-DD`, `DD.MM.YYYY` and `DD/MM/YYYY`.
- Actionable error messages that list the columns actually found and point to
  `migrate_company_data`.

### Changed

- Cross-file problems in the migration plan are now answerable open items
  (`company_profile.json:currency`, `financials.json:company_name`) instead of plain text
  questions, so they can be confirmed with `apply_company_data_migration`.

### Fixed

- Migrating an `employees.csv` with no usable `id` column left `employee_id` **empty** for
  every row, so employees had no stable identifier. Missing, blank, and digit-free ids are
  now filled from the `EMP-0001` series in row order, skipping numbers already used in the
  file. `add_employee` continues that series instead of restarting it.
- Generated employee ids came from `hash()`, which Python randomizes per process, so the
  same file migrated twice produced different ids. Ids are now derived from row order, and
  migrating the same input twice is byte-identical. Covered by a test that compares two
  interpreters with different `PYTHONHASHSEED` values.

### Not changed

- `company_profile.json` and `financials.json` are still not written automatically.
  Real-world variants carry custom fields (`ARR`, `fiscal_year`, runway) that cannot be
  mapped without losing data, so the server proposes and lets the user decide. Writing
  only happens through `apply_company_data_migration`, and only for the confirmed fields.

### Planned

- `update_employee`, `delete_financial_record`, and `list_notes` tools
- Character budget for `read_company_file` output and PDF page ranges
- Excel and PPTX reading support
- Support for multiple company data directories
- Backup/archive tool

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
