#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${AI_COMPANY_MANAGER_VENV:-$ROOT_DIR/.venv}"
VENV_PYTHON="$VENV_DIR/bin/python"

log() {
  printf '[ai-company-manager] %s\n' "$1" >&2
}

find_base_python() {
  local candidate
  for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      command -v "$candidate"
      return 0
    fi
  done
  return 1
}

install_requirements() {
  local uv_bin
  uv_bin="$(command -v uv 2>/dev/null || true)"
  if [ -z "$uv_bin" ] && [ -x "$HOME/.local/bin/uv" ]; then
    uv_bin="$HOME/.local/bin/uv"
  fi

  if [ -n "$uv_bin" ]; then
    "$uv_bin" venv --python "$BASE_PYTHON" "$VENV_DIR" >&2
    "$uv_bin" pip install --python "$VENV_PYTHON" -r "$ROOT_DIR/requirements.txt" >&2
  else
    "$BASE_PYTHON" -m venv "$VENV_DIR" >&2
    "$VENV_PYTHON" -m pip install --upgrade pip >&2
    "$VENV_PYTHON" -m pip install -r "$ROOT_DIR/requirements.txt" >&2
  fi
}

if [ ! -x "$VENV_PYTHON" ]; then
  BASE_PYTHON="$(find_base_python)" || {
    log "Python 3 bulunamadı. MCP sunucusu için python3 kurulmalı."
    exit 1
  }
  log "İlk çalıştırma: sanal ortam ve bağımlılıklar kuruluyor."
  install_requirements
  log "Kurulum tamamlandı."
fi

export COMPANY_DATA_DIR="${COMPANY_DATA_DIR:-$ROOT_DIR/company_data}"
export COMPANY_MAX_FILE_MB="${COMPANY_MAX_FILE_MB:-10}"

exec "$VENV_PYTHON" "$ROOT_DIR/src/server.py"
