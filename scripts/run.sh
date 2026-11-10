#!/usr/bin/env bash
# OmniDL 一鍵啟動（macOS / Linux 原始碼版）
set -e
cd "$(dirname "$0")/.."
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
  .venv/bin/pip install -U pip
  .venv/bin/pip install -e .
fi
.venv/bin/python -m omnidl doctor || true
exec .venv/bin/python -m omnidl gui "$@"
