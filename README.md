# AI Company Manager MCP Server

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-3776AB.svg)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-1.30%2B-8A2BE2.svg)](https://modelcontextprotocol.io)
[![Platforms](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey.svg)](#quick-install)
[![Transport](https://img.shields.io/badge/transport-stdio%20JSON--RPC-5C4EE5.svg)](#mcp-client-setup)

An AI-powered MCP (Model Context Protocol) server. It exposes company management tools to MCP
clients such as Cursor, Claude Desktop and OpenCode: company profile, financial records, employee
directory and company notes, all stored **in plain local files**. No data leaves your machine, no
API key is required, and it is free to use.

Installs with a single command and runs on Windows, Linux and macOS.

---

## Table of Contents

- [What It Does](#what-it-does)
- [Features](#features)
- [Requirements](#requirements)
- [Quick Install](#quick-install)
- [What the Installer Does](#what-the-installer-does)
- [Installer Options](#installer-options)
- [Manual Install](#manual-install)
- [MCP Client Setup](#mcp-client-setup)
- [MCP Tools](#mcp-tools)
- [Example Client Calls](#example-client-calls)
- [Financial Report](#financial-report)
- [Data Layout](#data-layout)
- [Using an Existing `employees.csv`](#using-an-existing-employeescsv)
- [Mapping an Existing Profile and Ledger](#mapping-an-existing-profile-and-ledger)
- [PDF and DOCX Reading Behavior](#pdf-and-docx-reading-behavior)
- [Configuration](#configuration)
- [Security and Resilience](#security-and-resilience)
- [Project Structure](#project-structure)
- [Development](#development)
- [Roadmap](#roadmap)
- [Contributing](#contributing)
- [License](#license)

---

## What It Does

When you ask an MCP client "what is our cash flow this month?", "add a new employee" or "save the
notes from yesterday's meeting", the server:

1. Exposes the request as a validated MCP tool.
2. Reads or atomically updates files inside the `company_data/` directory.
3. Returns the result to the client as text.

The assistant therefore works on your company data **without unauthorized access** and without any
cloud service. Because every change is written to plain text files, you can back up, review or edit
the data at any time with your favorite tools.

## Features

- **Zero to running company:** profile, financial file, founder employee record and standard
  department template
- **Financial tracking:** income/expense records, automatic cash flow summary, current balance
- **Employee management:** validated employee records with automatic `EMP-0001` style IDs
- **Company notes:** create or update policy, meeting, strategy or vision notes by title
- **Multiple file formats:** read/write TXT, MD, JSON, CSV and XLSX; **read PDF and DOCX**
- **Atomic file writes** and strict path boundaries (symlinks and `..` are rejected)
- **Formula injection protection:** leading `=`, `+`, `-` and `@` characters are neutralized in
  CSV and XLSX cells
- **No credentials required:** never talks to an external service
- **One-command install:** Windows, Linux and macOS
- **MCP `stdio` transport** (JSON-RPC 2.0)

## Requirements

- Python 3.10 or newer
- Internet access (only to download dependencies during the first install)
- If [`uv`](https://docs.astral.sh/uv/) is installed the setup is much faster; otherwise the standard
  `venv` + `pip` path is used. You do not need to install anything else yourself.

Dependencies are pinned in `requirements.txt`:

```text
mcp[cli]>=1.30.0,<2.0.0
pydantic>=2.11.0,<3.0.0
pandas>=2.2.0,<3.0.0
openpyxl>=3.1.5,<4.0.0
pypdf>=3.0.0
python-docx>=0.8.11
```

> **Why `<2`?** The project uses the `FastMCP` API, so the MCP Python SDK is pinned to the
> maintained `>=1.30,<2` range.

## Quick Install

Clone the repository, enter the folder and run a single command.

**Linux / macOS**

```bash
git clone https://github.com/metehan05-eng/AI-Company-Manager-MCP-Server.git
cd AI-Company-Manager-MCP-Server
python3 install.py
```

**Windows (PowerShell or Command Prompt)**

```powershell
git clone https://github.com/metehan05-eng/AI-Company-Manager-MCP-Server.git
cd AI-Company-Manager-MCP-Server
python install.py
```

After the install, toggle the MCP server once in Cursor (off/on). The first run downloads packages
and takes a few seconds; every later start is instant.

## What the Installer Does

`install.py` behaves identically on all three platforms and, in order:

1. **Creates the environment.** Builds a `.venv` inside the project and installs everything in
   `requirements.txt`. Uses `uv` when available (very fast), otherwise falls back to `venv` + `pip`.
2. **Writes the MCP configuration.** Adds the `ai-company-manager` server to
   `<project>/.cursor/mcp.json` and `~/.cursor/mcp.json`. If a Claude Desktop configuration exists,
   it is added there too. Existing servers and settings are preserved; invalid files are backed up
   as `.bak`.
3. **Verifies the installation.** Actually starts the server, sends `initialize` and `tools/list`
   requests and prints how many tools were found, so a "cannot connect" failure shows up during
   setup instead of later.

Typical output (installer messages are in Turkish):

```text
AI Company Manager MCP kurulumu (linux)
uv bulundu; hizli kurulum kullanilacak.
  sanal ortam olusturuluyor
  bagimliliklar kuruluyor
MCP yapilandirmasi yaziliyor...
  guncellendi: .../.cursor/mcp.json
  guncellendi: ~/.cursor/mcp.json
Sunucu dogrulaniyor...
  sunucu: AI Company Manager 1.30.0
  arac sayisi: 7
Bitti (13.5 saniye).
```

## Installer Options

| Option | What it does |
|---|---|
| `--force` | Deletes and recreates the virtual environment. |
| `--skip-deps` | Only writes the MCP configuration (no dependency install). |
| `--no-global` | Leaves `~/.cursor` and the Claude Desktop configuration untouched. |
| `--skip-verify` | Skips the live server verification after install. |

Examples:

```bash
python3 install.py --force        # rebuild a broken virtual environment
python3 install.py --no-global    # only write the project configuration
```

## Manual Install

If you prefer not to use the installer script:

```bash
cd AI-Company-Manager-MCP-Server
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Activate the environment on Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

To try it interactively with MCP Inspector:

```bash
mcp dev src/server.py
```

> `mcp dev` requires Node.js and `npx`. The server speaks over `stdio` and prints nothing to the
> terminal while it waits for an MCP client.

## MCP Client Setup

`python install.py` writes the Cursor and Claude Desktop configuration for you. The files below are
what you would edit manually.

### Cursor

Cursor reads `.cursor/mcp.json` from the folder you open. `${workspaceFolder}` resolves to that
folder, so the same file works unchanged on every machine.

Content written on Linux and macOS:

```json
{
  "mcpServers": {
    "ai-company-manager": {
      "command": "bash",
      "args": ["${workspaceFolder}/run_mcp.sh"],
      "env": {
        "COMPANY_DATA_DIR": "${workspaceFolder}/company_data",
        "COMPANY_MAX_FILE_MB": "10"
      }
    }
  }
}
```

On Windows, `command` is `cmd` and `args` is `["/c", "${workspaceFolder}\\run_mcp.cmd"]`.

To use the server in every project without opening this folder, use the absolute-path entry in
`~/.cursor/mcp.json`; the installer updates both files.

The launcher scripts (`run_mcp.sh`, `run_mcp.cmd`) create the virtual environment if it is missing
and write **all installation output to `stderr`**; `stdout` is reserved for the MCP protocol only.

### Claude Desktop

Claude Desktop usually starts MCP commands from its own working directory, which is why the
installer writes an absolute-path entry into the Claude Desktop configuration. To do it manually:

macOS/Linux:

```json
{
  "mcpServers": {
    "ai-company-manager": {
      "command": "/ABSOLUT/PATH/AI-Company-Manager-MCP-Server/.venv/bin/python",
      "args": ["/ABSOLUT/PATH/AI-Company-Manager-MCP-Server/src/server.py"],
      "env": {
        "COMPANY_DATA_DIR": "/ABSOLUT/PATH/AI-Company-Manager-MCP-Server/company_data",
        "COMPANY_MAX_FILE_MB": "10"
      }
    }
  }
}
```

On Windows, `command` must be `.venv\\Scripts\\python.exe`. Restart Claude Desktop after changing
the configuration.

### OpenCode

Create an `opencode.json` in the project root:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "ai-company-manager": {
      "type": "local",
      "command": [
        "/ABSOLUT/PATH/AI-Company-Manager-MCP-Server/.venv/bin/python",
        "/ABSOLUT/PATH/AI-Company-Manager-MCP-Server/src/server.py"
      ],
      "enabled": true,
      "environment": {
        "COMPANY_DATA_DIR": "/ABSOLUT/PATH/AI-Company-Manager-MCP-Server/company_data",
        "COMPANY_MAX_FILE_MB": "10"
      }
    }
  }
}
```

OpenCode only reads its settings at startup, so restart it after editing.

## MCP Tools

| Tool | Purpose | Required arguments |
|---|---|---|
| `list_company_files` | Lists every file in the data directory with type, size and modification time. | — |
| `get_company_overview` | Returns a summary of profile, budget, income, expenses, net cash flow and current balance. | — |
| `get_financial_report` | Reports the same totals plus income and expense splits by category and the cash flow periods. Read-only. | — (`period` is optional) |
| `read_company_file` | Converts a supported file into text the AI can analyze. | `filename` |
| `create_new_company` | Creates the profile, financial file and founder row. | `company_name`, `sector`, `initial_budget` |
| `add_financial_record` | Appends an `income` or `expense` transaction to `financials.json`. | `type`, `category`, `amount`, `description` |
| `add_employee` | Appends a validated employee record to `employees.csv`. | `name`, `role`, `department`, `salary` |
| `update_company_notes` | Creates or updates a policy, meeting, strategy or vision note. | `note_title`, `content` |
| `inspect_company_data` | Reports whether each core file matches the expected schema, and why not. | — |
| `migrate_company_data` | Converts an existing `employees.csv` to the current column schema. | — (`apply` defaults to `false`) |
| `plan_company_data_migration` | Proposes a field mapping for an existing `company_profile.json` and `financials.json`. Read-only. | — |
| `apply_company_data_migration` | Writes the mapping you confirmed. Answers every open item first. | — (`apply` defaults to `false`) |

All arguments are validated with Pydantic: empty text, a negative budget, a zero-amount financial
record or a description longer than 2,000 characters is rejected.

## Example Client Calls

```text
create_new_company(company_name="Atlas Software", sector="SaaS", initial_budget=2500000)
add_financial_record(type="income", category="services", amount=125000, description="Monthly enterprise subscription")
add_employee(name="Deniz Kaya", role="Senior Developer", department="Engineering", salary=95000)
read_company_file(filename="employees.csv")
```

Natural language examples:

- "Summarize this month's cash flow."
- "Add a 45,000 expense under the infrastructure category."
- "Create a note titled Strategy: we are targeting a European launch in 2026."
- "Read the management report and list the risks."

## Financial Report

`get_company_overview` answers "where do we stand?". `get_financial_report` answers "why?",
and stays read-only.

| Block | Contents |
|---|---|
| `totals` | Budget, opening balance, income, expenses, net cash flow, current balance, transaction count, plus `remaining_budget` and `budget_used_percent`. |
| `income_by_category` | Every category that received income, largest first, with its amount, record count and `share_percent`. |
| `expenses_by_category` | The same split for expenses. |
| `cash_flow` | Every period in `cash_flow_template` with opening balance, net cash flow and closing balance, plus the summed net cash flow. |
| `warnings` | Empty when the ledger is healthy. Otherwise a negative current balance or expenses above the initial budget. |

Categories are reported exactly as they are stored, and the split is per record type: an
`income` under `rent` never inflates the expense split. `share_percent` is `null` when a
split has no amount to divide, and `budget_used_percent` is `null` when the budget is zero.

Pass `period` to narrow the cash flow table to one entry; an unknown period is an error that
lists the available ones rather than an empty table.

```json
{"tool": "get_financial_report", "arguments": {}}
{"tool": "get_financial_report", "arguments": {"period": "2025-Q1"}}
```

## Data Layout

The repository ships **no real company data**: the `company_data/` folder arrives empty and is
ignored by `.gitignore`. The core files are created by the `create_new_company` tool. If a company
with financial records or an `employees.csv` is detected, the tool refuses to create a new company
so that data cannot be lost. Back up the core files before starting another company.

| File | Content |
|---|---|
| `company_data/company_profile.json` | Company name, sector, founding year, departments, vision, mission, metrics |
| `company_data/financials.json` | Budget, income/expense categories, transactions, cash flow summary |
| `company_data/employees.csv` | Founder and employee records (`EMP-0001`, `EMP-0002`, ...) |
| `company_data/company_notes.json` | Company notes (created on the first `update_company_notes` call) |
| `company_data/*.pdf`, `*.docx` | Your own documents (read-only) |

You can drop your own TXT, MD, JSON, CSV, XLSX, PDF or DOCX documents into `company_data/`;
`read_company_file` reads them.

## Using an Existing `employees.csv`

If you already have an `employees.csv` from another system, the tools understand common
column names instead of demanding the exact current schema.

| Current column | Also accepted as |
|---|---|
| `employee_id` | `id`, `employee_code`, `code`, `no` |
| `name` | `full_name`, `fullname`, `ad_soyad`, `employee_name`, `personel_adi` |
| `role` | `title`, `position`, `gorev` |
| `department` | `dept`, `unite`, `team`, `bolum` |
| `salary` | `monthly_salary`, `monthly_salary_usd`, `salary_usd`, `maas`, `ucret` |
| `start_date` | `hire_date`, `hired_at`, `ise_giris`, `start` |
| `status` | `employment_status`, `state`, `durum` |

Only `name`, `role`, `department` and `salary` are strictly required. Missing
`employee_id`, `start_date` and `status` are filled in automatically, dates accept
`YYYY-MM-DD`, `DD.MM.YYYY` and `DD/MM/YYYY`, and any status other than a recognized
"inactive" value is treated as `active`. Columns the tools do not recognize, such as
`performance_score`, are **preserved** on read and on every subsequent write.

Use `inspect_company_data` to see which files match the schema and what is blocking the
others. Use `migrate_company_data` to permanently rewrite `employees.csv` in the canonical
layout; it is a dry run by default and writes a timestamped backup before applying.

```json
{"tool": "inspect_company_data", "arguments": {}}
{"tool": "migrate_company_data", "arguments": {}}
{"tool": "migrate_company_data", "arguments": {"apply": true}}
```

## Mapping an Existing Profile and Ledger

`company_profile.json` and `financials.json` are **not** rewritten automatically. Real-world
variants carry custom fields such as `ARR`, `fiscal_year` or runway metrics, and rewriting
them would destroy data. `inspect_company_data` reports them as `incompatible`;
`plan_company_data_migration` goes one step further and produces a read-only proposal that
`apply_company_data_migration` can then write once you have answered its questions.

Nothing is ever written. The report splits every source field into one of these buckets:

| Bucket | Meaning |
|---|---|
| `already_valid` | The key and value already satisfy the canonical model and are reused unchanged. |
| `mappable` | A rule can convert it. `confidence` is `auto` or `needs_confirmation`. |
| `skipped_conflicts` | Two source fields target the same canonical field; the first one wins. |
| `incompatible_values` | The value cannot be used as it is, with the reason. |
| `no_target` | No canonical field exists. Known risky fields such as `pending_invoices_receivable` come with an explanation. |
| `missing_required` | A required canonical field that no source field provides. |

A proposal is `needs_confirmation` whenever the rule has to **assume or drop something**,
for example `founded_year: 2023` becoming `established_date: 2023-01-01`, or
`status: "Active / Series-A Funded"` being narrowed to `active`. Those become explicit
questions. Cross-file problems are reported separately, such as a ledger with no
`company_name` or a profile that would silently default to `TRY` while the ledger is `USD`.

`proposed_document` is only included when **no question is open**, so a ready-to-use
document can never be copied into place unread. The server never applies an exchange rate.

```json
{"tool": "plan_company_data_migration", "arguments": {}}
```

### Confirming the Mapping

`apply_company_data_migration` is the write step for the same mapping. It replans the files
itself, so it always applies to the current content, and it refuses to write until every open
item has an answer.

Each question carries a `key` of the form `<file>:<field>`, for example
`company_profile.json:mission`. Pass them back in `answers`:

- `true` accepts the suggested value.
- Any other value is used as the final value, so a wrong assumption can be corrected
  (`"company_profile.json:mission": "Ship faster"`).
- A key with **no** suggested value rejects `true` and asks for an explicit value, so a
  missing field can never be confirmed by accident.
- Unknown keys and unanswered keys are both errors, and nothing is written when they occur.

`apply` defaults to `false`, so the first call is a dry run that shows the exact documents
and the list of files that would change. Re-run with `apply: true` to write. Only files that
actually change are touched, and each one is copied to `<name>.backup-<timestamp>.json` first.
A file that is already canonical is left alone, so the call is safe to repeat.

```json
{"tool": "apply_company_data_migration", "arguments": {"answers": {"company_profile.json:mission": true}}}
{"tool": "apply_company_data_migration", "arguments": {"answers": {"company_profile.json:mission": true}, "apply": true}}
```

## PDF and DOCX Reading Behavior

| Case | Behavior |
|---|---|
| PDF with text | Every page is extracted under a `--- Sayfa N ---` header. |
| PDF without text | A warning is returned for the page, noting the file may be a scanned image and that OCR is required. |
| Encrypted PDF | An empty password is attempted; if that fails a clear error is returned. |
| Corrupt PDF/DOCX | A `CompanyFileError` with the file name and technical detail is returned. |
| DOCX | All paragraphs and all tables are extracted (tables under a `--- Tablo N ---` header, cells joined with `\|`). |
| Writing PDF/DOCX | Rejected: these formats are read-only. |
| Missing dependency | A clear error is returned including the install command. |

## Configuration

| Variable | Default | Description |
|---|---|---|
| `COMPANY_DATA_DIR` | `company_data` inside the project | Absolute path, or relative to the project root. |
| `COMPANY_MAX_FILE_MB` | `10` | File size limit for reads and writes (minimum 1). |

`.env.example` is a template only; provide environment variables through the client `env` field or
through the operating system.

## Security and Resilience

- Absolute paths, `..` traversal and symlinks are rejected.
- Every read and write stays inside the data directory.
- Files are updated atomically with a temporary file in the same directory plus `os.replace`.
- Financial and employee changes run under a single wizard lock.
- JSON structure, numeric fields, dates and table columns are validated.
- Formula injection is neutralized in CSV and XLSX cells with a prefix character.
- The installer merges MCP configurations instead of overwriting them.
- Data is plain text: no API keys, no authentication. Take regular backups.

## Project Structure

```text
AI-Company-Manager-MCP-Server/
├── install.py               # One-command installer (Windows/Linux/macOS)
├── run_mcp.sh               # Linux/macOS launcher (bootstraps the virtual environment)
├── run_mcp.cmd              # Windows launcher
├── requirements.txt         # Pinned dependencies
├── mcp.json                 # Example configuration in Cursor format
├── LICENSE                  # MIT
├── .env.example             # Environment variable template
├── .cursor/mcp.json         # Cursor project configuration
├── company_data/            # Company data lives here (and only here)
└── src/
    ├── server.py            # MCP tool definitions (FastMCP)
    ├── file_handler.py      # File I/O, path boundaries, PDF/DOCX
    └── company_wizard.py    # Business rules, company setup, notes
```

## Development

Set up an editable install with the development extras:

```bash
git clone https://github.com/metehan05-eng/AI-Company-Manager-MCP-Server.git
cd AI-Company-Manager-MCP-Server

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
python -m pip install -e ".[dev]"
```

Code style, type checks, and tests:

```bash
ruff format --check --no-cache src tests install.py
ruff check --no-cache src tests install.py
mypy --python-version 3.10 src install.py
pytest
```

With coverage:

```bash
pytest --cov=src --cov-report=term-missing
```

Tests never touch your real `company_data` directory: every test runs in its own
temporary directory, and `tests/test_repository.py` fails the build if real company
data or secrets are ever committed.

Verify a change against a live server without modifying anything:

```bash
python3 install.py --check          # dependency check plus tools/list handshake
```

To actually install, run `python3 install.py`. This writes the configuration, starts
the server and validates the tool list with a `tools/list` call. Use
`python3 install.py --skip-deps` to only rewrite the configuration, and
`mcp dev src/server.py` for interactive testing with MCP Inspector.

## Continuous integration

`.github/workflows/ci.yml` runs on every push and pull request:

- `ruff` lint and format check, `mypy` type check
- `pytest` with coverage on Ubuntu, Windows, and macOS across Python 3.10-3.13
- Installer smoke test (`install.py --check`) on all three operating systems

## Roadmap

- [x] Automated test suite (`pytest`) and CI workflow
- [x] Read existing `employees.csv` with common column names; schema inspection and migration
- [x] Read-only mapping proposals for existing `company_profile.json` and `financials.json`
- [x] Apply a confirmed `company_profile.json` / `financials.json` mapping with a backup
- [x] Period breakdown table for budget and cash flow
- [ ] Update and delete operations for financial records and employees
- [ ] Character budget for `read_company_file` output and PDF page ranges
- [ ] Excel/PPTX reading support
- [ ] Support for multiple company data directories
- [ ] Backup/archive tool

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for the full workflow
and house rules, [CHANGELOG.md](CHANGELOG.md) for the release history, and
[SECURITY.md](SECURITY.md) for reporting a vulnerability privately.

In short: fork, branch, make the change, then make sure these four commands pass
before opening a pull request.

```bash
ruff format --check --no-cache src tests install.py && ruff check --no-cache src tests install.py
mypy --python-version 3.10 src install.py && pytest
```

When you add a new MCP tool, remember to update its docstring in `src/server.py`; that text is sent
to clients as the tool description.

## License

Released under the MIT License. See [LICENSE](LICENSE) for the full text.

```text
Copyright (c) 2026 AI Company Manager Contributors
```
