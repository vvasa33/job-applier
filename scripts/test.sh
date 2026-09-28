#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
backend="$root/backend"

if [[ ! -x "$backend/.venv/bin/pytest" ]]; then
  python3 -m venv "$backend/.venv"
  "$backend/.venv/bin/pip" install -e "$backend[dev]"
fi

cd "$backend"
exec "$backend/.venv/bin/pytest"
