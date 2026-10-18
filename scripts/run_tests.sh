#!/usr/bin/env bash
# Run the pytest suite (installs pytest from requirements-dev.txt if missing).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [[ -x /usr/bin/python3.14 ]]; then
  PYTHON=/usr/bin/python3.14
elif [[ -x "${ROOT}/.venv/bin/python3.14" ]]; then
  PYTHON="${ROOT}/.venv/bin/python3.14"
else
  PYTHON="${ROOT}/.venv/bin/python"
fi
SITE="${ROOT}/.venv/lib/python3.14/site-packages"
if ! env -i HOME="${HOME}" PATH="/usr/bin:/bin:${ROOT}/.venv/bin" \
  PYTHONPATH="${SITE}" "$PYTHON" -c "import pytest" 2>/dev/null; then
  echo "Installing pytest (requirements-dev.txt)…"
  env -i HOME="${HOME}" PATH="/usr/bin:/bin" \
    PYTHONPATH="${SITE}" \
    "$PYTHON" -m pip install --prefix="${ROOT}/.venv" -r "${ROOT}/requirements-dev.txt"
fi
exec env -i \
  HOME="${HOME}" \
  USER="${USER:-}" \
  PATH="/usr/bin:/bin:${ROOT}/.venv/bin" \
  PYTHONPATH="${ROOT}:${SITE}" \
  QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}" \
  MERIDIAN_NO_GL="${MERIDIAN_NO_GL:-1}" \
  "$PYTHON" -m pytest "${ROOT}/tests" "$@"
