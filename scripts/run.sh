#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${ROOT}/.venv/bin/python"
exec env -i \
  HOME="${HOME}" \
  USER="${USER:-}" \
  DISPLAY="${DISPLAY:-}" \
  WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-}" \
  XAUTHORITY="${XAUTHORITY:-}" \
  XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-}" \
  PATH="${ROOT}/.venv/bin:/usr/bin:/bin" \
  PYTHONPATH="${ROOT}" \
  QT_MEDIA_BACKEND="${QT_MEDIA_BACKEND:-ffmpeg}" \
  "$PYTHON" -m meridian "$@"
