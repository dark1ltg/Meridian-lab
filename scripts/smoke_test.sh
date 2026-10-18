#!/usr/bin/env bash
# Standalone smoke checks (no pytest required).
# Same coverage as tests/ via tests/smoke_checks.py.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# Prefer real CPython — some environments wrap `python` as the Cursor AppImage.
if [[ -x /usr/bin/python3.14 ]]; then
  PYTHON=/usr/bin/python3.14
elif [[ -x "${ROOT}/.venv/bin/python3.14" ]]; then
  PYTHON="${ROOT}/.venv/bin/python3.14"
else
  PYTHON="${ROOT}/.venv/bin/python"
fi
SITE="${ROOT}/.venv/lib/python3.14/site-packages"
exec env -i \
  HOME="${HOME}" \
  USER="${USER:-}" \
  PATH="/usr/bin:/bin:${ROOT}/.venv/bin" \
  PYTHONPATH="${ROOT}:${SITE}" \
  QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}" \
  MERIDIAN_NO_GL="${MERIDIAN_NO_GL:-1}" \
  "$PYTHON" "${ROOT}/scripts/smoke_test.py" "$@"
