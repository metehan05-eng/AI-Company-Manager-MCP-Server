"""MCP tool contracts: signatures, schemas, and live stdio protocol behavior.

These tests spawn the real server as a subprocess, so they cover the actual JSON-RPC
surface that Cursor and Claude Desktop talk to, not just Python internals.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from conftest import PROJECT_ROOT
from src.server import mcp

PROTOCOL_VERSION = "2024-11-05"
PYTHON_EXECUTABLE = str(PROJECT_ROOT / ".venv" / "bin" / "python")

if not Path(PYTHON_EXECUTABLE).exists():
    PYTHON_EXECUTABLE = sys.executable


class StdioClient:
    def __init__(self, data_dir: Path) -> None:
        environment = dict(os.environ)
        environment["COMPANY_DATA_DIR"] = str(data_dir)
        environment["PYTHONPATH"] = str(PROJECT_ROOT)
        self.process = subprocess.Popen(
            [PYTHON_EXECUTABLE, "-m", "src.server"],
            cwd=str(PROJECT_ROOT),
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        self.counter = 0

    def _write(self, message: dict[str, Any]) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.counter += 1
        message: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": self.counter,
            "method": method,
        }
        if params is not None:
            message["params"] = params
        self._write(message)
        assert self.process.stdout is not None
        while True:
            line = self.process.stdout.readline()
            if not line:
                msg = "server closed the connection"
                raise RuntimeError(msg)
            payload = json.loads(line)
            if payload.get("id") == self.counter:
                return payload

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        self._write(message)

    def call(self, tool: str, arguments: dict[str, Any]) -> str:
        response = self.request("tools/call", {"name": tool, "arguments": arguments})
        result = response.get("result") or {}
        blocks = result.get("content") or []
        text = " ".join(block.get("text", "") for block in blocks)
        if not text:
            msg = f"empty tool response for {tool}"
            raise AssertionError(msg)
        return text

    def close(self) -> None:
        if self.process.stdin is not None:
            self.process.stdin.close()
        self.process.wait(timeout=15)

    def __enter__(self) -> StdioClient:
        self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "1.0"},
            },
        )
        self.notify("notifications/initialized")
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


@pytest.fixture
def client(data_dir: Path) -> Iterator[StdioClient]:
    with StdioClient(data_dir) as connected:
        yield connected


class TestToolRegistration:
    def test_ten_tools_registered(self) -> None:
        assert len(mcp._tool_manager.list_tools()) == 10

    def test_every_tool_has_description(self) -> None:
        for tool in mcp._tool_manager.list_tools():
            assert tool.description, f"{tool.name} has no description"

    @pytest.mark.parametrize(
        "name",
        [
            "list_company_files",
            "get_company_overview",
            "read_company_file",
            "create_new_company",
            "add_financial_record",
            "add_employee",
            "update_company_notes",
            "inspect_company_data",
            "migrate_company_data",
            "plan_company_data_migration",
        ],
    )
    def test_tool_exists(self, name: str) -> None:
        assert mcp._tool_manager.get_tool(name) is not None


class TestLiveProtocol:
    def test_initialize_and_list_tools(self, client: StdioClient) -> None:
        tools = client.request("tools/list")["result"]["tools"]
        assert len(tools) == 10
        assert {tool["name"] for tool in tools} == {
            "list_company_files",
            "get_company_overview",
            "read_company_file",
            "create_new_company",
            "add_financial_record",
            "add_employee",
            "update_company_notes",
            "inspect_company_data",
            "migrate_company_data",
            "plan_company_data_migration",
        }

    def test_schemas_expose_required_fields(self, client: StdioClient) -> None:
        schemas = {
            tool["name"]: tool["inputSchema"]
            for tool in client.request("tools/list")["result"]["tools"]
        }
        assert set(schemas["add_financial_record"]["required"]) == {
            "type",
            "category",
            "amount",
            "description",
        }
        assert set(schemas["add_employee"]["required"]) == {
            "name",
            "role",
            "department",
            "salary",
        }
        amount = schemas["add_financial_record"]["properties"]["amount"]
        assert amount["type"] == "number"
        assert amount["exclusiveMinimum"] == 0
        salary = schemas["add_employee"]["properties"]["salary"]
        assert salary["minimum"] == 0

    def test_create_company_and_read_back(self, client: StdioClient) -> None:
        created = client.call(
            "create_new_company",
            {
                "company_name": "Acme",
                "sector": "Yazilim",
                "initial_budget": 50_000,
                "vision": "v",
                "mission": "m",
            },
        )
        assert "Company initialized successfully" in created

        listed = client.call("list_company_files", {})
        assert "company_profile.json" in listed
        assert "employees.csv" in listed

        overview = client.call("get_company_overview", {})
        assert '"name": "Acme"' in overview or '"name":"Acme"' in overview

    def test_add_record_and_employee(self, client: StdioClient) -> None:
        client.call(
            "create_new_company",
            {
                "company_name": "Acme",
                "sector": "Yazilim",
                "initial_budget": 50_000,
                "vision": "v",
                "mission": "m",
            },
        )
        record = client.call(
            "add_financial_record",
            {
                "type": "expense",
                "category": "kira",
                "amount": 1500.5,
                "description": "ofis",
            },
        )
        assert "Financial record added successfully" in record
        assert "1500.50" in record

        employee = client.call(
            "add_employee",
            {"name": "Ali", "role": "Dev", "department": "IT", "salary": 4200},
        )
        assert "Employee added successfully" in employee
        assert "EMP-0002" in employee

    def test_note_tool_round_trip(self, client: StdioClient) -> None:
        client.call(
            "create_new_company",
            {
                "company_name": "Acme",
                "sector": "Yazilim",
                "initial_budget": 50_000,
                "vision": "v",
                "mission": "m",
            },
        )
        note = client.call("update_company_notes", {"note_title": "Toplanti", "content": "x"})
        assert "Company note created successfully" in note

    def test_read_company_file(self, client: StdioClient) -> None:
        client.call(
            "create_new_company",
            {
                "company_name": "Acme",
                "sector": "Yazilim",
                "initial_budget": 50_000,
                "vision": "v",
                "mission": "m",
            },
        )
        content = client.call("read_company_file", {"filename": "employees.csv"})
        assert "employee_id" in content
        assert "'" not in content

    @pytest.mark.parametrize(
        ("tool", "arguments", "expected"),
        [
            (
                "add_financial_record",
                {
                    "type": "expense",
                    "category": "kira",
                    "amount": 10.999,
                    "description": "x",
                },
                "at most 2 decimal places",
            ),
            (
                "add_employee",
                {"name": "Ali", "role": "Dev", "department": "IT", "salary": 1234.567},
                "at most 2 decimal places",
            ),
        ],
    )
    def test_readable_validation_errors(
        self, client: StdioClient, tool: str, arguments: dict[str, Any], expected: str
    ) -> None:
        text = client.call(tool, arguments)
        assert expected in text
        assert "1 validation error" not in text
        assert "\n" not in text

    def test_missing_files_gives_readable_error(self, client: StdioClient) -> None:
        text = client.call("get_company_overview", {})
        assert "not found" in text.lower()
        assert "1 validation error" not in text

    def test_reading_missing_file_is_reported(self, client: StdioClient) -> None:
        client.call(
            "create_new_company",
            {
                "company_name": "Acme",
                "sector": "Yazilim",
                "initial_budget": 50_000,
                "vision": "v",
                "mission": "m",
            },
        )
        text = client.call("read_company_file", {"filename": "yok.csv"})
        assert "not found" in text.lower()

    def test_inspect_reports_missing_files(self, client: StdioClient) -> None:
        text = client.call("inspect_company_data", {})
        assert '"ready_for_tools": false' in text
        assert "employees.csv" in text

    def test_inspect_after_init(self, client: StdioClient) -> None:
        client.call(
            "create_new_company",
            {
                "company_name": "Acme",
                "sector": "Yazilim",
                "initial_budget": 50_000,
                "vision": "v",
                "mission": "m",
            },
        )
        text = client.call("inspect_company_data", {})
        assert '"ready_for_tools": true' in text

    def test_migrate_defaults_to_dry_run(self, client: StdioClient) -> None:
        text = client.call("migrate_company_data", {})
        assert "was not found" in text.lower()

    def test_migrate_reports_no_change_for_canonical_file(self, client: StdioClient) -> None:
        client.call(
            "create_new_company",
            {
                "company_name": "Acme",
                "sector": "Yazilim",
                "initial_budget": 50_000,
                "vision": "v",
                "mission": "m",
            },
        )
        text = client.call("migrate_company_data", {})
        assert "already uses the current schema" in text
        assert '"changed": false' in text

    def test_migrate_apply_false_does_not_write(self, client: StdioClient, data_dir: Path) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "employees.csv").write_text(
            "id,full_name,role,department,monthly_salary_usd\nE101,Ali,Dev,Ar-Ge,8500\n",
            encoding="utf-8",
        )
        before = (data_dir / "employees.csv").read_text(encoding="utf-8")
        text = client.call("migrate_company_data", {})
        assert '"applied": false' in text
        assert '"renamed_columns"' in text
        assert (data_dir / "employees.csv").read_text(encoding="utf-8") == before
        assert not list(data_dir.glob("employees.backup-*.csv"))

    def test_migrate_apply_writes_canonical_schema(
        self, client: StdioClient, data_dir: Path
    ) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        original = (
            "id,full_name,role,department,monthly_salary_usd,performance_score\n"
            "E101,Ali,Dev,Ar-Ge,8500,4.9\n"
        )
        (data_dir / "employees.csv").write_text(original, encoding="utf-8")
        text = client.call("migrate_company_data", {"apply": True})
        assert '"applied": true' in text
        assert "migrated successfully" in text
        header = (data_dir / "employees.csv").read_text(encoding="utf-8-sig").split("\n")[0]
        assert header.startswith("employee_id,name,role,department,salary")
        assert "performance_score" in header
        backups = list(data_dir.glob("employees.backup-*.csv"))
        assert len(backups) == 1
        assert backups[0].read_text(encoding="utf-8") == original

    def test_migrate_then_add_employee(self, client: StdioClient, data_dir: Path) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "employees.csv").write_text(
            "id,full_name,role,department,monthly_salary_usd\nE101,Ali,Dev,Ar-Ge,8500\n",
            encoding="utf-8",
        )
        client.call("migrate_company_data", {"apply": True})
        added = client.call(
            "add_employee",
            {"name": "Probe", "role": "QA", "department": "Ar-Ge", "salary": 1000},
        )
        assert "Employee added successfully" in added
        assert "EMP-0102" in added

    def test_plan_reports_missing_files(self, client: StdioClient) -> None:
        text = client.call("plan_company_data_migration", {})
        assert '"writes_performed": false' in text
        assert '"status": "missing"' in text

    def test_plan_is_read_only(self, client: StdioClient, data_dir: Path) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "company_profile.json").write_text(
            '{"company_name": "Aetheris", "founded_year": 2023, "sector": "AI"}',
            encoding="utf-8",
        )
        (data_dir / "financials.json").write_text(
            '{"currency": "USD", "bank_balance": 850000}', encoding="utf-8"
        )
        before = {path.name: path.read_bytes() for path in sorted(data_dir.iterdir())}
        text = client.call("plan_company_data_migration", {})
        after = {path.name: path.read_bytes() for path in sorted(data_dir.iterdir())}
        assert after == before
        assert '"writes_performed": false' in text
        assert "established_date" in text
        assert '"confidence": "needs_confirmation"' in text

    def test_plan_after_init_is_clean(self, client: StdioClient) -> None:
        client.call(
            "create_new_company",
            {
                "company_name": "Acme",
                "sector": "Yazilim",
                "initial_budget": 50_000,
            },
        )
        text = client.call("plan_company_data_migration", {})
        assert '"blocking_files": []' in text
        assert '"proposed_document"' in text

    def test_path_traversal_rejected(self, client: StdioClient) -> None:
        text = client.call("read_company_file", {"filename": "../../etc/passwd"})
        assert "traversal" in text.lower() or "escapes" in text.lower()
