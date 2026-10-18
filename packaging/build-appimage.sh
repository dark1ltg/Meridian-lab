#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

APP=Meridian
DIST="$ROOT/dist"
APPDIR="$DIST/${APP}.AppDir"
TOOLS="$ROOT/.appimage-tools"
ARCH="$(uname -m)"
FONTS_SRC="$ROOT/resources/fonts"
# Standalone AppImage path — outside usr/bin / PyInstaller tree (UFL-friendly).
FONTS_DST="$APPDIR/usr/share/fonts/truetype/meridian"
DOC_ROOT="$APPDIR/usr/share/doc/meridian"
DOC_FONTS="$DOC_ROOT/ubuntu-font-licence"
DOC_THIRD="$DOC_ROOT/third-party"
LICENSE_FALLBACKS="$ROOT/resources/licenses"

# Prefer MERIDIAN_PYTHON / MERIDIAN_VENV so portable (older-glibc) builds can
# avoid the host system interpreter (e.g. CachyOS glibc 2.44).
if [[ -n "${MERIDIAN_PYTHON:-}" ]]; then
  PYTHON="$MERIDIAN_PYTHON"
elif [[ -n "${MERIDIAN_VENV:-}" ]]; then
  PYTHON="${MERIDIAN_VENV}/bin/python"
else
  PYTHON="${ROOT}/.venv/bin/python"
fi
if [[ ! -x "$PYTHON" ]]; then
  echo "Create a venv first, e.g.:"
  echo "  python3 -m venv --system-site-packages .venv && .venv/bin/pip install -r requirements.txt"
  echo "Or for Ubuntu 24.04-compatible AppImages: packaging/build-appimage-ubuntu2404.sh"
  exit 1
fi
echo "Using Python: $PYTHON ($("$PYTHON" -c 'import sys; print(sys.version.split()[0])'))"

run_py() {
  env -i \
    HOME="${HOME}" \
    USER="${USER:-}" \
    PATH="${ROOT}/.venv/bin:/usr/bin:/bin:/usr/local/bin" \
    PYTHONPATH="${ROOT}" \
    LANG="${LANG:-C.UTF-8}" \
    QT_QPA_PLATFORM=offscreen \
    "$PYTHON" "$@"
}

# Rasterize SVG → window icon + hicolor theme sizes for desktop integration.
run_py - <<'PY'
from pathlib import Path
from PySide6.QtGui import QImage, QPainter, QGuiApplication
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtCore import QRectF, Qt
import sys

app = QGuiApplication(sys.argv)
svg_bytes = Path("resources/meridian.svg").read_bytes()
renderer = QSvgRenderer(svg_bytes)
sizes = (16, 24, 32, 48, 64, 128, 256, 512)
out_root = Path("resources/icons/hicolor")
for size in sizes:
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    dest_dir = out_root / f"{size}x{size}" / "apps"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "meridian.png"
    image.save(str(dest))
    print(f"wrote {dest}")
# Canonical 256px copy used by the Qt window icon / PyInstaller datas.
Path("resources/meridian.png").write_bytes((out_root / "256x256" / "apps" / "meridian.png").read_bytes())
print("wrote resources/meridian.png")
scalable = Path("resources/icons/hicolor/scalable/apps")
scalable.mkdir(parents=True, exist_ok=True)
(scalable / "meridian.svg").write_bytes(svg_bytes)
print(f"wrote {scalable / 'meridian.svg'}")
PY

# Version stamp is applied to AppDir copies below (do not dirty the git tree).
APP_VERSION="$(run_py -c 'from meridian import __version__; print(__version__)')"

if [[ ! -f "$ROOT/resources/meridian.png" ]] \
  || [[ ! -f "$ROOT/resources/icons/hicolor/scalable/apps/meridian.svg" ]]; then
  echo "ERROR: icon generation failed" >&2
  exit 1
fi

if [[ ! -f "$FONTS_SRC/UbuntuSans[wdth,wght].ttf" ]] \
   || [[ ! -f "$FONTS_SRC/UbuntuCondensed-Regular.ttf" ]]; then
  echo "Bundled fonts missing — fetching…"
  bash "$ROOT/packaging/fetch-fonts.sh"
fi

# Ensure UFL text is present next to the font files before packaging.
if [[ ! -f "$FONTS_SRC/UBUNTU-FONT-LICENCE-1.0.txt" ]]; then
  if [[ -f "$FONTS_SRC/LICENCE.txt" ]]; then
    cp -f "$FONTS_SRC/LICENCE.txt" "$FONTS_SRC/UBUNTU-FONT-LICENCE-1.0.txt"
  else
    echo "Missing Ubuntu Font Licence text in $FONTS_SRC" >&2
    exit 1
  fi
fi
if [[ ! -f "$FONTS_SRC/COPYRIGHT.txt" ]]; then
  bash "$ROOT/packaging/fetch-fonts.sh"
fi

for required in LICENSE COPYING COPYRIGHT.txt THIRD_PARTY_NOTICES.txt SOURCE_OFFER.txt; do
  if [[ ! -f "$ROOT/$required" ]]; then
    echo "Missing required project licence file: $required" >&2
    exit 1
  fi
done

run_py -m PyInstaller --noconfirm --clean "$ROOT/packaging/meridian.spec"

rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/share/applications" "$APPDIR/usr/share/metainfo"
mkdir -p "$APPDIR/usr/share/icons/hicolor"
mkdir -p "$FONTS_DST" "$DOC_FONTS" "$DOC_THIRD"

cp -a "$DIST/$APP/." "$APPDIR/usr/bin/"

# GPL-2.0-only x264 must not ship inside the GPL-3 AppImage. Use the host
# libx264 (same soname) so Qt's bundled libavcodec can resolve it at runtime.
mapfile -t _x264_hits < <(find "$APPDIR" -name 'libx264.so*' 2>/dev/null || true)
if ((${#_x264_hits[@]})); then
  printf 'Removing bundled libx264 (use system library):\n'
  printf '  %s\n' "${_x264_hits[@]}"
  rm -f "${_x264_hits[@]}"
fi
if find "$APPDIR" -name 'libx264.so*' 2>/dev/null | grep -q .; then
  echo "ERROR: libx264 still present under AppDir after purge" >&2
  find "$APPDIR" -name 'libx264.so*' >&2
  exit 1
fi
echo "OK: no bundled libx264 (host library required for H.264 via FFmpeg plugin)"

cp "$ROOT/resources/meridian.desktop" "$APPDIR/usr/share/applications/${APP}.desktop"
cp "$ROOT/resources/meridian.desktop" "$APPDIR/${APP}.desktop"
cp "$ROOT/resources/metainfo/io.github.dark1ltg.Meridian.metainfo.xml" \
  "$APPDIR/usr/share/metainfo/io.github.dark1ltg.Meridian.metainfo.xml"
# Stamp version into AppDir desktop + AppStream metadata only.
sed -i "s/^X-AppImage-Version=.*/X-AppImage-Version=${APP_VERSION}/" \
  "$APPDIR/usr/share/applications/${APP}.desktop" \
  "$APPDIR/${APP}.desktop"
TODAY="$(date -u +%Y-%m-%d)"
# Stamp only the newest <release> row — do not rewrite older release history.
sed -i "0,/version=\"[0-9.]*\" date=\"[0-9-]*\"/s//version=\"${APP_VERSION}\" date=\"${TODAY}\"/" \
  "$APPDIR/usr/share/metainfo/io.github.dark1ltg.Meridian.metainfo.xml"
# Full hicolor icon theme (PNG sizes + scalable SVG) for menus / AppImageLauncher.
cp -a "$ROOT/resources/icons/hicolor/." "$APPDIR/usr/share/icons/hicolor/"
cp "$ROOT/resources/meridian.png" "$APPDIR/meridian.png"
ln -sf meridian.png "$APPDIR/.DirIcon"
install -m 0755 "$ROOT/packaging/desktop-integrate.sh" "$APPDIR/usr/bin/meridian-desktop-integrate"

# --- Meridian GPL-3 + notices (required for binary redistribution) ---
install -m 0644 "$ROOT/LICENSE" "$DOC_ROOT/LICENSE"
install -m 0644 "$ROOT/COPYING" "$DOC_ROOT/COPYING"
install -m 0644 "$ROOT/COPYRIGHT.txt" "$DOC_ROOT/COPYRIGHT.txt"
install -m 0644 "$ROOT/THIRD_PARTY_NOTICES.txt" "$DOC_ROOT/THIRD_PARTY_NOTICES.txt"
install -m 0644 "$ROOT/SOURCE_OFFER.txt" "$DOC_ROOT/SOURCE_OFFER.txt"
# Also at AppDir root for easy discovery when extracting the AppImage.
install -m 0644 "$ROOT/LICENSE" "$APPDIR/LICENSE"
install -m 0644 "$ROOT/THIRD_PARTY_NOTICES.txt" "$APPDIR/THIRD_PARTY_NOTICES.txt"
install -m 0644 "$ROOT/SOURCE_OFFER.txt" "$APPDIR/SOURCE_OFFER.txt"

# Collect licence texts from installed wheels / site-packages.
run_py "$ROOT/packaging/collect_third_party_licenses.py" "$DOC_THIRD" \
  || echo "WARN: some package licence files missing; installing fallbacks" >&2

install -m 0644 "$LICENSE_FALLBACKS/qt-for-python-NOTICE.txt" "$DOC_THIRD/qt-for-python-NOTICE.txt"
if [[ ! -d "$DOC_THIRD/mutagen" ]] || [[ -z "$(ls -A "$DOC_THIRD/mutagen" 2>/dev/null || true)" ]]; then
  mkdir -p "$DOC_THIRD/mutagen"
  install -m 0644 "$LICENSE_FALLBACKS/mutagen-NOTICE.txt" "$DOC_THIRD/mutagen/NOTICE.txt"
fi
if [[ ! -d "$DOC_THIRD/numpy" ]] || [[ -z "$(ls -A "$DOC_THIRD/numpy" 2>/dev/null || true)" ]]; then
  mkdir -p "$DOC_THIRD/numpy"
  install -m 0644 "$LICENSE_FALLBACKS/numpy-NOTICE.txt" "$DOC_THIRD/numpy/NOTICE.txt"
fi
if [[ ! -d "$DOC_THIRD/aubio" ]] || [[ -z "$(ls -A "$DOC_THIRD/aubio" 2>/dev/null || true)" ]]; then
  mkdir -p "$DOC_THIRD/aubio"
  install -m 0644 "$LICENSE_FALLBACKS/aubio-NOTICE.txt" "$DOC_THIRD/aubio/NOTICE.txt"
fi
if [[ ! -d "$DOC_THIRD/pyinstaller" ]] || [[ -z "$(ls -A "$DOC_THIRD/pyinstaller" 2>/dev/null || true)" ]]; then
  mkdir -p "$DOC_THIRD/pyinstaller"
  install -m 0644 "$LICENSE_FALLBACKS/pyinstaller-NOTICE.txt" "$DOC_THIRD/pyinstaller/NOTICE.txt"
fi
if [[ ! -d "$DOC_THIRD/pyside6" ]] || [[ -z "$(ls -A "$DOC_THIRD/pyside6" 2>/dev/null || true)" ]]; then
  mkdir -p "$DOC_THIRD/pyside6"
  install -m 0644 "$LICENSE_FALLBACKS/qt-for-python-NOTICE.txt" "$DOC_THIRD/pyside6/NOTICE.txt"
fi

# Full LGPL/GPL texts + inventory of bundled native libs (compliance minimum).
SPDX_DIR="$ROOT/resources/licenses/spdx"
if [[ ! -f "$SPDX_DIR/LGPL-3.0-only.txt" ]]; then
  echo "ERROR: missing $SPDX_DIR/LGPL-3.0-only.txt" >&2
  exit 1
fi
run_py "$ROOT/packaging/collect_bundled_licenses.py" "$APPDIR" "$SPDX_DIR"
install -m 0644 "$LICENSE_FALLBACKS/qt-for-python-NOTICE.txt" "$DOC_THIRD/qt-for-python-NOTICE.txt"
if [[ ! -f "$DOC_THIRD/qt6/LGPL-3.0-only.txt" ]] || [[ ! -f "$DOC_THIRD/BUILD_LIBRARIES.txt" ]]; then
  echo "ERROR: bundled licence collection incomplete under $DOC_THIRD" >&2
  ls -la "$DOC_THIRD" >&2 || true
  exit 1
fi

# Install Font Software as ordinary files in the AppImage filesystem.
# Do not place these under the PyInstaller onedir (usr/bin) or embed in the EXE.
install -m 0644 "$FONTS_SRC/"*.ttf "$FONTS_DST/"
install -m 0644 "$FONTS_SRC/UBUNTU-FONT-LICENCE-1.0.txt" "$FONTS_DST/"
install -m 0644 "$FONTS_SRC/UBUNTU-FONT-LICENCE-1.0.txt" "$DOC_FONTS/"
if [[ -f "$FONTS_SRC/UFL.txt" ]]; then
  install -m 0644 "$FONTS_SRC/UFL.txt" "$FONTS_DST/"
  install -m 0644 "$FONTS_SRC/UFL.txt" "$DOC_FONTS/"
fi
if [[ -f "$FONTS_SRC/LICENCE.txt" ]]; then
  install -m 0644 "$FONTS_SRC/LICENCE.txt" "$FONTS_DST/"
  install -m 0644 "$FONTS_SRC/LICENCE.txt" "$DOC_FONTS/"
fi
install -m 0644 "$FONTS_SRC/COPYRIGHT.txt" "$FONTS_DST/"
install -m 0644 "$FONTS_SRC/COPYRIGHT.txt" "$DOC_FONTS/"
if [[ -f "$FONTS_SRC/README.txt" ]]; then
  install -m 0644 "$FONTS_SRC/README.txt" "$FONTS_DST/"
fi

# Guard: refuse to ship if fonts somehow landed inside the PyInstaller tree.
if find "$APPDIR/usr/bin" \( -iname '*ubuntu*.ttf' -o -iname '*UbuntuSans*' \) 2>/dev/null | grep -q .; then
  echo "ERROR: Ubuntu fonts must not be inside usr/bin (PyInstaller tree)" >&2
  find "$APPDIR/usr/bin" \( -iname '*ubuntu*.ttf' -o -iname '*UbuntuSans*' \) >&2
  exit 1
fi
if [[ ! -f "$FONTS_DST/UbuntuCondensed-Regular.ttf" ]] \
   || [[ ! -f "$FONTS_DST/UBUNTU-FONT-LICENCE-1.0.txt" ]] \
   || [[ ! -f "$FONTS_DST/COPYRIGHT.txt" ]]; then
  echo "ERROR: incomplete standalone font install in $FONTS_DST" >&2
  ls -la "$FONTS_DST" >&2 || true
  exit 1
fi

# Guard: Meridian + third-party docs must be present.
for f in \
  "$DOC_ROOT/LICENSE" \
  "$DOC_ROOT/COPYRIGHT.txt" \
  "$DOC_ROOT/THIRD_PARTY_NOTICES.txt" \
  "$DOC_ROOT/SOURCE_OFFER.txt" \
  "$DOC_THIRD/qt-for-python-NOTICE.txt" \
  "$APPDIR/usr/share/applications/${APP}.desktop" \
  "$APPDIR/usr/share/metainfo/io.github.dark1ltg.Meridian.metainfo.xml" \
  "$APPDIR/usr/share/icons/hicolor/256x256/apps/meridian.png" \
  "$APPDIR/usr/share/icons/hicolor/scalable/apps/meridian.svg" \
  "$APPDIR/usr/bin/meridian-desktop-integrate" \
  "$DOC_THIRD/qt6/LGPL-3.0-only.txt" \
  "$DOC_THIRD/BUILD_LIBRARIES.txt" \
  "$DOC_ROOT/SOURCE_OFFER.txt"
do
  if [[ ! -f "$f" ]]; then
    echo "ERROR: missing documentation file in AppDir: $f" >&2
    exit 1
  fi
done
if [[ -z "$(ls -A "$DOC_THIRD/mutagen" 2>/dev/null || true)" ]] \
   || [[ -z "$(ls -A "$DOC_THIRD/numpy" 2>/dev/null || true)" ]] \
   || [[ -z "$(ls -A "$DOC_THIRD/aubio" 2>/dev/null || true)" ]] \
   || [[ -z "$(ls -A "$DOC_THIRD/pyinstaller" 2>/dev/null || true)" ]]; then
  echo "ERROR: incomplete third-party licence tree under $DOC_THIRD" >&2
  find "$DOC_THIRD" -type f >&2 || true
  exit 1
fi

cat > "$APPDIR/AppRun" <<'EOF'
#!/usr/bin/env bash
HERE="$(dirname "$(readlink -f "$0")")"
export APPDIR="${APPDIR:-$HERE}"
export MERIDIAN_FONTS_DIR="${HERE}/usr/share/fonts/truetype/meridian"
export QT_PLUGIN_PATH="${HERE}/usr/bin/PySide6/Qt/plugins:${QT_PLUGIN_PATH:-}"
export QT_QPA_PLATFORM_PLUGIN_PATH="${HERE}/usr/bin/PySide6/Qt/plugins/platforms"
export QT_MEDIA_BACKEND="${QT_MEDIA_BACKEND:-ffmpeg}"

case "${1:-}" in
  --install|install)
    exec "${HERE}/usr/bin/meridian-desktop-integrate" install
    ;;
  --uninstall|uninstall|--remove|remove)
    exec "${HERE}/usr/bin/meridian-desktop-integrate" uninstall
    ;;
  --help|-h)
    cat <<'HELP'
Meridian — offline mood-map music player

Usage:
  Meridian.AppImage              Launch the app
  Meridian.AppImage --install    Add menu entry + icons (like a normal app)
  Meridian.AppImage --uninstall  Remove menu entry + icons
  Meridian.AppImage --help       Show this help

Host tip: install ffmpeg for mood analysis.
HELP
    exit 0
    ;;
esac

exec "${HERE}/usr/bin/Meridian" "$@"
EOF
chmod +x "$APPDIR/AppRun" "$APPDIR/usr/bin/Meridian" "$APPDIR/usr/bin/meridian-desktop-integrate"

mkdir -p "$TOOLS"
TOOL_APPIMAGE="$TOOLS/appimagetool-${ARCH}.AppImage"
TOOL_EXTRACT="$TOOLS/appimagetool-extract/AppRun"
if [[ ! -x "$TOOL_APPIMAGE" ]]; then
  URL="https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-${ARCH}.AppImage"
  echo "Downloading appimagetool…"
  curl -L --fail -o "$TOOL_APPIMAGE" "$URL"
  chmod +x "$TOOL_APPIMAGE"
fi
# Prefer an extracted appimagetool: nested AppImages break under proot / some hosts.
if [[ ! -x "$TOOL_EXTRACT" ]]; then
  echo "Extracting appimagetool…"
  (
    cd "$TOOLS"
    rm -rf appimagetool-extract squashfs-root
    env -u APPDIR -u APPIMAGE -u ARGV0 -u OWD APPIMAGE_EXTRACT_AND_RUN=1 \
      "./appimagetool-${ARCH}.AppImage" --appimage-extract
    mv squashfs-root appimagetool-extract
  )
fi

export ARCH
export APPIMAGE_EXTRACT_AND_RUN=1
# Avoid host AppImage/Cursor cwd quirks when packaging.
env -u APPDIR -u APPIMAGE -u ARGV0 -u OWD HOME="${HOME:-/tmp}" \
  "$TOOL_EXTRACT" "$APPDIR" "$DIST/${APP}-${ARCH}.AppImage"
chmod +x "$DIST/${APP}-${ARCH}.AppImage"
echo "Built $DIST/${APP}-${ARCH}.AppImage"
echo "Desktop:   ${DIST}/${APP}-${ARCH}.AppImage --install"
echo "Licences:  usr/share/doc/meridian/  (+ LICENSE at AppDir root)"
echo "Fonts/UFL: usr/share/fonts/truetype/meridian/"
