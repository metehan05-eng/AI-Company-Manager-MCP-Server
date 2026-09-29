#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import venv
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent
VENV_DIR = ROOT_DIR / ".venv"
REQUIREMENTS_FILE = ROOT_DIR / "requirements.txt"
SERVER_SCRIPT = ROOT_DIR / "src" / "server.py"
SHELL_LAUNCHER = ROOT_DIR / "run_mcp.sh"
WINDOWS_LAUNCHER = ROOT_DIR / "run_mcp.cmd"
SERVER_NAME = "ai-company-manager"
IS_WINDOWS = os.name == "nt"


def say(message: str) -> None:
    try:
        print(message, flush=True)
    except UnicodeEncodeError:
        print(message.encode("ascii", "replace").decode("ascii"), flush=True)


def venv_python() -> Path:
    if IS_WINDOWS:
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def launcher_command() -> list[str]:
    if IS_WINDOWS:
        return ["cmd", "/c", str(WINDOWS_LAUNCHER)]
    return ["bash", str(SHELL_LAUNCHER)]


def find_uv() -> str | None:
    executable = shutil.which("uv")
    if executable:
        return executable
    fallback = Path.home() / ".local" / "bin" / ("uv.exe" if IS_WINDOWS else "uv")
    return str(fallback) if fallback.is_file() else None


def run(command: list[str], label: str) -> None:
    say(f"  {label}")
    completed = subprocess.run(command, cwd=ROOT_DIR, check=False)
    if completed.returncode != 0:
        raise SystemExit(f"Kurulum adimi basarisiz oldu: {' '.join(command)}")


def install_environment(force: bool) -> None:
    python_path = venv_python()
    if python_path.is_file() and not force:
        say(f"Sanal ortam zaten hazir: {python_path}")
        return

    if VENV_DIR.exists():
        say("Eski sanal ortam kaldiriliyor...")
        shutil.rmtree(VENV_DIR)

    uv_binary = find_uv()
    if uv_binary:
        say("uv bulundu; hizli kurulum kullanilacak.")
        run(
            [uv_binary, "venv", "--python", sys.executable, str(VENV_DIR)],
            "sanal ortam olusturuluyor",
        )
        run(
            [
                uv_binary,
                "pip",
                "install",
                "--python",
                str(python_path),
                "-r",
                str(REQUIREMENTS_FILE),
            ],
            "bagimliliklar kuruluyor",
        )
    else:
        say("uv bulunamadi; standart venv + pip kurulumu kullanilacak.")
        venv.create(VENV_DIR, with_pip=True)
        run(
            [str(python_path), "-m", "pip", "install", "-r", str(REQUIREMENTS_FILE)],
            "bagimliliklar kuruluyor",
        )

    if not IS_WINDOWS and SHELL_LAUNCHER.is_file():
        SHELL_LAUNCHER.chmod(0o755)


def absolute_entry() -> dict[str, Any]:
    return {
        "command": str(venv_python()),
        "args": [str(SERVER_SCRIPT)],
        "env": {
            "COMPANY_DATA_DIR": str(ROOT_DIR / "company_data"),
            "COMPANY_MAX_FILE_MB": "10",
        },
    }


def project_entry() -> dict[str, Any]:
    separator = "\\" if IS_WINDOWS else "/"
    if IS_WINDOWS:
        return {
            "command": "cmd",
            "args": ["/c", f"${{workspaceFolder}}{separator}run_mcp.cmd"],
            "env": {
                "COMPANY_DATA_DIR": f"${{workspaceFolder}}{separator}company_data",
                "COMPANY_MAX_FILE_MB": "10",
            },
        }
    return {
        "command": "bash",
        "args": [f"${{workspaceFolder}}{separator}run_mcp.sh"],
        "env": {
            "COMPANY_DATA_DIR": f"${{workspaceFolder}}{separator}company_data",
            "COMPANY_MAX_FILE_MB": "10",
        },
    }


def merge_config(path: Path, entry: dict[str, Any]) -> None:
    config: dict[str, Any] = {}
    if path.is_file():
        raw = path.read_text(encoding="utf-8-sig").strip()
        if raw:
            try:
                loaded = json.loads(raw)
            except json.JSONDecodeError:
                backup = path.with_name(f"{path.name}.bak")
                shutil.copy2(path, backup)
                say(f"  gecersiz JSON yedeklendi: {backup}")
            else:
                if isinstance(loaded, dict):
                    config = loaded
                else:
                    say("  yapilandirma nesne degil; yeni icerik yaziliyor.")

    servers = config.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
    servers[SERVER_NAME] = entry
    config["mcpServers"] = servers

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f"{path.name}.tmp")
    temporary_path.write_text(
        json.dumps(config, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary_path, path)
    say(f"  guncellendi: {path}")


def cursor_global_config() -> Path:
    return Path.home() / ".cursor" / "mcp.json"


def claude_desktop_config() -> Path | None:
    if IS_WINDOWS:
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / "Claude" / "claude_desktop_config.json"
    if sys.platform == "darwin":
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "Claude"
            / "claude_desktop_config.json"
        )
    return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


def write_configs(include_global: bool) -> None:
    say("MCP yapilandirmasi yaziliyor...")
    merge_config(ROOT_DIR / ".cursor" / "mcp.json", project_entry())

    parent = ROOT_DIR.parent
    if parent != ROOT_DIR and parent != Path.home():
        merge_config(parent / ".cursor" / "mcp.json", project_entry())

    if not include_global:
        return

    merge_config(cursor_global_config(), absolute_entry())
    claude_config = claude_desktop_config()
    if claude_config is not None and claude_config.is_file():
        merge_config(claude_config, absolute_entry())
    elif claude_config is not None:
        say(f"  Claude Desktop yapilandirmasi bulunamadi, atlandi: {claude_config}")


def await_response(responses: queue.Queue[str], request_id: int, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"yanit beklenemedi (id={request_id})")
        try:
            line = responses.get(timeout=remaining)
        except queue.Empty as exc:
            raise TimeoutError(f"yanit beklenemedi (id={request_id})") from exc
        if not line.strip():
            continue
        message = json.loads(line)
        if message.get("id") == request_id:
            return message


def verify_server(timeout: float = 90.0) -> int:
    process = subprocess.Popen(
        launcher_command(),
        cwd=ROOT_DIR,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    responses: queue.Queue[str] = queue.Queue()
    errors: list[str] = []

    def drain_stdout() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            responses.put(line)

    def drain_stderr() -> None:
        assert process.stderr is not None
        for line in process.stderr:
            errors.append(line.rstrip())

    stdout_thread = threading.Thread(target=drain_stdout, daemon=True)
    stderr_thread = threading.Thread(target=drain_stderr, daemon=True)
    stdout_thread.start()
    stderr_thread.start()

    def send(payload: dict[str, Any]) -> None:
        assert process.stdin is not None
        process.stdin.write(json.dumps(payload) + "\n")
        process.stdin.flush()

    try:
        send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "install.py", "version": "1.0.0"},
                },
            }
        )
        server_info = await_response(responses, 1, timeout)["result"]["serverInfo"]
        say(f"  sunucu: {server_info['name']} {server_info['version']}")

        send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        tools = await_response(responses, 2, timeout)["result"]["tools"]
        say(f"  arac sayisi: {len(tools)}")
        for tool in tools:
            say(f"    - {tool['name']}")
        return 0
    except (TimeoutError, KeyError, json.JSONDecodeError, AssertionError) as exc:
        say(f"  dogrulama basarisiz: {exc}")
        for line in errors[-8:]:
            say(f"    sunucu: {line}")
        return 1
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


def check_environment() -> int:
    python = venv_python()
    if not python.exists():
        say(f"Sanal ortam bulunamadi: {python}")
        say("Once 'python install.py' calistirin.")
        return 1

    required = ("mcp", "pydantic", "pandas", "openpyxl", "pypdf", "docx")
    probe = (
        "import importlib.util\n"
        f"missing = [name for name in {required!r} "
        "if importlib.util.find_spec(name) is None]\n"
        "print('|'.join(missing))\n"
    )
    result = subprocess.run(
        [str(python), "-c", probe],
        cwd=ROOT_DIR,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        say(f"Bagimlilik kontrolu basarisiz: {result.stderr.strip()}")
        return 1
    missing = [item for item in result.stdout.strip().split("|") if item]
    if missing:
        say(f"Eksik bagimliliklar: {', '.join(missing)}")
        return 1
    say("  tum bagimliliklar hazir")

    say("Sunucu dogrulaniyor...")
    return verify_server()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="AI Company Manager MCP kurulumu (Windows, Linux, macOS)."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Sanal ortami bastan olustur.",
    )
    parser.add_argument(
        "--skip-deps",
        action="store_true",
        help="Yalnizca MCP yapilandirmasini yaz.",
    )
    parser.add_argument(
        "--no-global",
        action="store_true",
        help="Kullanici geneli Cursor/Claude yapilandirmasina dokunma.",
    )
    parser.add_argument(
        "--skip-verify",
        action="store_true",
        help="Kurulum sonrasi canli sunucu dogrulamasini atla.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Ortami degistirmeden yalnizca bagimliliklari ve sunucuyu dogrula.",
    )
    arguments = parser.parse_args()

    if arguments.check:
        started = time.monotonic()
        say(f"AI Company Manager MCP kontrolu ({sys.platform})")
        status = check_environment()
        elapsed = time.monotonic() - started
        say(f"Kontrol bitti ({elapsed:.1f} saniye).")
        return status

    started = time.monotonic()
    say(f"AI Company Manager MCP kurulumu ({sys.platform})")

    if not arguments.skip_deps:
        install_environment(arguments.force)

    write_configs(include_global=not arguments.no_global)

    status = 0
    if not arguments.skip_verify and not arguments.skip_deps:
        say("Sunucu dogrulaniyor...")
        status = verify_server()

    elapsed = time.monotonic() - started
    say(f"Bitti ({elapsed:.1f} saniye).")
    if status == 0:
        say("Simdi Cursor'da AI Company Manager MCP sunucusunu yeniden yukleyin.")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
