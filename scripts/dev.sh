#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
backend="$root/backend"
frontend="$root/frontend"

if [[ ! -d "$backend/.venv" ]]; then
  python3 -m venv "$backend/.venv"
fi

"$backend/.venv/bin/pip" install -e "$backend[dev]"

if [[ ! -d "$frontend/node_modules" ]]; then
  npm --prefix "$frontend" install
fi

exec "$backend/.venv/bin/jobhunter" dev
