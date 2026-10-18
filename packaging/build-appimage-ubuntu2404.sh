#!/usr/bin/env bash
# Build Meridian-x86_64.AppImage compatible with Ubuntu 24.04 (glibc 2.39).
#
# Order of preference:
#   1. Docker/Podman (ubuntu:24.04)
#   2. Local Ubuntu 24.04 rootfs via proot (no root/Docker required)
#   3. --local portable uv CPython (may fail if aubio must compile)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

DIST="$ROOT/dist"
OUT_NAME="Meridian-x86_64.AppImage"
OUT_COMPAT="${DIST}/Meridian-x86_64-ubuntu2404.AppImage"
VENV="${ROOT}/.venv-ubuntu2404"
ROOTFS="${ROOT}/.u2404-rootfs"
PROOT_BIN="${ROOT}/.appimage-tools/proot"
MAX_GLIBC="2.39"
INSIDE=0
FORCE_LOCAL=0
FORCE_PROOT=0

for arg in "$@"; do
  case "$arg" in
    --inside-container|--inside-proot) INSIDE=1 ;;
    --local|--no-docker) FORCE_LOCAL=1 ;;
    --proot) FORCE_PROOT=1 ;;
    -h|--help)
      cat <<'EOF'
Build an Ubuntu 24.04-compatible Meridian AppImage.

  packaging/build-appimage-ubuntu2404.sh          # auto: docker → proot → local
  packaging/build-appimage-ubuntu2404.sh --proot  # force Ubuntu 24.04 rootfs + proot
  packaging/build-appimage-ubuntu2404.sh --local  # portable uv Python (no container)
EOF
      exit 0
      ;;
  esac
done

have_container() {
  command -v docker >/dev/null 2>&1 || command -v podman >/dev/null 2>&1
}

container_bin() {
  if command -v docker >/dev/null 2>&1; then
    echo docker
  else
    echo podman
  fi
}

ensure_proot_rootfs() {
  mkdir -p "${ROOT}/.appimage-tools"
  if [[ ! -x "$PROOT_BIN" ]]; then
    echo "Downloading proot…"
    curl -fL -o "$PROOT_BIN" 'https://github.com/proot-me/proot/releases/download/v5.4.1/proot'
    chmod +x "$PROOT_BIN"
  fi
  if [[ ! -f "$ROOTFS/etc/os-release" ]]; then
    echo "Downloading Ubuntu 24.04 minimal rootfs…"
    local tar=/tmp/ubuntu-24.04-root.tar.xz
    curl -fL --retry 3 -o "$tar" \
      'https://cloud-images.ubuntu.com/minimal/releases/noble/release/ubuntu-24.04-minimal-cloudimg-amd64-root.tar.xz'
    mkdir -p "$ROOTFS"
    tar -xJf "$tar" -C "$ROOTFS" --exclude='dev' --exclude='./dev' \
      --no-same-owner --no-same-permissions
    mkdir -p "$ROOTFS/dev" "$ROOTFS/proc" "$ROOTFS/sys" "$ROOTFS/tmp" "$ROOTFS/run"
  fi
  rm -f "$ROOTFS/etc/resolv.conf"
  printf 'nameserver 1.1.1.1\nnameserver 8.8.8.8\n' > "$ROOTFS/etc/resolv.conf"
}

run_in_proot() {
  "$PROOT_BIN" -0 \
    -r "$ROOTFS" \
    -b /dev \
    -b /proc \
    -b /sys \
    -b "$ROOT:/src" \
    -w /src \
    "$@"
}

bootstrap_proot_packages() {
  run_in_proot /usr/bin/bash -lc '
    set -e
    export DEBIAN_FRONTEND=noninteractive
    if ! python3 -c "import aubio" >/dev/null 2>&1; then
      apt-get update
      apt-get install -y --no-install-recommends \
        python3 python3-venv python3-pip python3-dev \
        build-essential pkg-config curl ca-certificates \
        libaubio-dev \
        libgl1 libegl1 libglib2.0-0 libxkbcommon0 libdbus-1-3 \
        libxcb-cursor0 libxcb-xinerama0 libx11-xcb1 \
        libxi6 libxrender1 libxext6 libsm6 libice6 \
        libfontconfig1 libfreetype6 \
        libxkbcommon-x11-0 libxcb-icccm4 libxcb-image0 \
        libxcb-keysyms1 libxcb-render-util0 libxcb-shape0 \
        squashfs-tools file binutils
    fi
  '
}

reclaim_dist_ownership() {
  # Container/proot runs as root and can leave dist unwritable to the host user
  # (breaks CI steps that tee checksums into dist/).
  if [[ ! -d "$DIST" ]]; then
    return 0
  fi
  if [[ "$(id -u)" -eq 0 ]]; then
    return 0
  fi
  if [[ -O "$DIST" ]] && { [[ ! -e "${DIST}/${OUT_NAME}" ]] || [[ -O "${DIST}/${OUT_NAME}" ]]; }; then
    return 0
  fi
  if command -v sudo >/dev/null 2>&1; then
    sudo chown -R "$(id -u):$(id -g)" "$DIST" || true
  fi
}

run_container_build() {
  local bin
  bin="$(container_bin)"
  echo "Building inside Ubuntu 24.04 via ${bin}…"
  "$bin" build -f packaging/Dockerfile.ubuntu2404 -t meridian-u2404-build "$ROOT"
  "$bin" run --rm \
    -v "$ROOT:/src:Z" \
    -w /src \
    -e HOME=/tmp \
    meridian-u2404-build \
    bash packaging/build-appimage-ubuntu2404.sh --inside-container
  reclaim_dist_ownership
}

run_proot_build() {
  echo "Building inside Ubuntu 24.04 rootfs via proot…"
  ensure_proot_rootfs
  bootstrap_proot_packages
  run_in_proot /usr/bin/bash -lc 'bash packaging/build-appimage-ubuntu2404.sh --inside-proot'
  reclaim_dist_ownership
}

setup_venv_inside() {
  if [[ ! -x "${VENV}/bin/python" ]]; then
    # Fresh venv (no distro aubio/numpy) — we rebuild aubio against numpy 2.
    python3 -m venv "$VENV"
  fi
  # shellcheck disable=SC1091
  source "${VENV}/bin/activate"
  pip install --upgrade pip
  pip install 'PySide6>=6.7' 'mutagen>=1.47' 'numpy>=2.0' 'pyinstaller>=6.10'
  # aubio has no Linux wheels; compile the Python module against Ubuntu libaubio5
  # (glibc 2.39). GCC 14-style pointer errors need -Wno-incompatible-pointer-types.
  CFLAGS="${CFLAGS:+$CFLAGS }-Wno-incompatible-pointer-types" \
    pip install --no-binary=aubio 'aubio>=0.4.9'
  python - <<'PY'
import aubio, numpy, PySide6, mutagen, PyInstaller, sys
print("ok", sys.version.split()[0], "aubio", aubio.version, "numpy", numpy.__version__)
PY
}

setup_venv_local_uv() {
  export PATH="${HOME}/.local/bin:/usr/bin:/bin:${PATH:-}"
  if ! command -v uv >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
  fi
  export PATH="${HOME}/.local/bin:${PATH}"
  uv python install 3.12
  if [[ ! -x "${VENV}/bin/python" ]]; then
    uv venv --python 3.12 "$VENV"
  fi
  uv pip install --python "${VENV}/bin/python" -r requirements.txt
}

audit_appdir() {
  local appdir="$DIST/Meridian.AppDir"
  local py="${VENV}/bin/python"
  if [[ ! -d "$appdir" ]]; then
    echo "WARN: AppDir missing for audit: $appdir" >&2
    return 0
  fi
  if [[ ! -x "$py" ]]; then
    py=python3
  fi
  "$py" "$ROOT/packaging/audit-appimage-glibc.py" "$appdir" --max "$MAX_GLIBC"
}

build_with_venv() {
  if [[ -f "${DIST}/${OUT_NAME}" ]]; then
    mv -f "${DIST}/${OUT_NAME}" "${DIST}/${OUT_NAME}.prev" || true
  fi
  export MERIDIAN_VENV="$VENV"
  export APPIMAGE_EXTRACT_AND_RUN=1
  bash "$ROOT/packaging/build-appimage.sh"
  cp -f "${DIST}/${OUT_NAME}" "$OUT_COMPAT"
  chmod +x "$OUT_COMPAT"
  echo "Wrote $OUT_COMPAT"
  audit_appdir
  echo
  echo "Ubuntu 24.04-oriented AppImage:"
  echo "  $OUT_COMPAT"
  echo "Also updated:"
  echo "  ${DIST}/${OUT_NAME}"
}

# --- entry ---
if [[ "$INSIDE" -eq 1 ]]; then
  setup_venv_inside
  build_with_venv
  exit 0
fi

if [[ "$FORCE_LOCAL" -eq 1 ]]; then
  setup_venv_local_uv
  build_with_venv
  exit 0
fi

if [[ "$FORCE_PROOT" -eq 1 ]]; then
  run_proot_build
  exit 0
fi

if have_container; then
  run_container_build
  exit 0
fi

if [[ -x "$PROOT_BIN" || ! -e "$PROOT_BIN" ]]; then
  # Prefer proot Ubuntu 24.04 over a fragile host aubio compile.
  run_proot_build
  exit 0
fi

setup_venv_local_uv
build_with_venv
