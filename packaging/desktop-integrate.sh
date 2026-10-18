#!/usr/bin/env bash
# Install or remove Meridian as a normal desktop application (menu entry + icons).
# Invoked by AppRun as: AppRun --install | --uninstall
set -euo pipefail

ACTION="${1:-}"

# Prefer AppImage runtime APPDIR; otherwise derive from this script's location.
if [[ -z "${APPDIR:-}" || ! -d "${APPDIR}/usr" ]]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  if [[ -d "$SCRIPT_DIR/../share/applications" ]]; then
    # …/usr/bin → AppDir
    APPDIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
  elif [[ -d "$SCRIPT_DIR/usr/share/applications" ]]; then
    APPDIR="$SCRIPT_DIR"
  else
    echo "Cannot locate AppDir (expected usr/share/applications)." >&2
    exit 1
  fi
fi
export APPDIR

XDG_DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
APPS_DIR="$XDG_DATA_HOME/applications"
ICONS_ROOT="$XDG_DATA_HOME/icons/hicolor"
DESKTOP_ID="io.github.dark1ltg.Meridian.desktop"
ICON_NAME="meridian"
STAMP_DIR="$XDG_DATA_HOME/meridian"
STAMP_FILE="$STAMP_DIR/appimage-path"

resolve_exec() {
  if [[ -n "${APPIMAGE:-}" && -f "$APPIMAGE" ]]; then
    printf '%s\n' "$APPIMAGE"
    return
  fi
  if [[ -x "$APPDIR/AppRun" ]]; then
    printf '%s\n' "$APPDIR/AppRun"
    return
  fi
  if [[ -x "$APPDIR/usr/bin/Meridian" ]]; then
    printf '%s\n' "$APPDIR/usr/bin/Meridian"
    return
  fi
  echo "Cannot find Meridian executable (set APPIMAGE or run from an AppDir)." >&2
  exit 1
}

escape_desktop_exec() {
  local p="$1"
  if [[ "$p" == *" "* ]]; then
    printf '"%s"\n' "$p"
  else
    printf '%s\n' "$p"
  fi
}

install_icons() {
  local src dest rel
  mkdir -p "$ICONS_ROOT"
  if [[ -d "$APPDIR/usr/share/icons/hicolor" ]]; then
    while IFS= read -r -d '' src; do
      rel="${src#"$APPDIR/usr/share/icons/hicolor/"}"
      dest="$ICONS_ROOT/$rel"
      mkdir -p "$(dirname "$dest")"
      install -m 0644 "$src" "$dest"
    done < <(find "$APPDIR/usr/share/icons/hicolor" -type f \( -name "${ICON_NAME}.png" -o -name "${ICON_NAME}.svg" \) -print0 2>/dev/null)
  elif [[ -f "$APPDIR/${ICON_NAME}.png" ]]; then
    mkdir -p "$ICONS_ROOT/256x256/apps"
    install -m 0644 "$APPDIR/${ICON_NAME}.png" "$ICONS_ROOT/256x256/apps/${ICON_NAME}.png"
  fi
}

write_desktop() {
  local exec_path exec_line desktop_src try_exec
  exec_path="$(resolve_exec)"
  exec_line="$(escape_desktop_exec "$exec_path")"
  # TryExec does not support quotes; escape spaces as \s per desktop-entry spec usage.
  try_exec="${exec_path// /\\s}"

  desktop_src="$APPDIR/usr/share/applications/Meridian.desktop"
  if [[ ! -f "$desktop_src" ]]; then
    desktop_src="$APPDIR/Meridian.desktop"
  fi
  if [[ ! -f "$desktop_src" ]]; then
    echo "Missing Meridian.desktop in AppDir." >&2
    exit 1
  fi

  mkdir -p "$APPS_DIR" "$STAMP_DIR"
  awk -v exec="$exec_line" -v tryexec="$try_exec" '
    BEGIN { done_exec=0; done_try=0 }
    /^Exec=/ {
      print "Exec=" exec
      done_exec=1
      next
    }
    /^TryExec=/ {
      print "TryExec=" tryexec
      done_try=1
      next
    }
    { print }
    END {
      if (!done_exec) print "Exec=" exec
      if (!done_try) print "TryExec=" tryexec
    }
  ' "$desktop_src" > "$APPS_DIR/$DESKTOP_ID"

  if grep -q '^Icon=' "$APPS_DIR/$DESKTOP_ID"; then
    sed -i "s|^Icon=.*|Icon=${ICON_NAME}|" "$APPS_DIR/$DESKTOP_ID"
  else
    printf 'Icon=%s\n' "$ICON_NAME" >> "$APPS_DIR/$DESKTOP_ID"
  fi

  printf '%s\n' "$exec_path" > "$STAMP_FILE"
  chmod 0644 "$APPS_DIR/$DESKTOP_ID"
}

refresh_caches() {
  if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APPS_DIR" >/dev/null 2>&1 || true
  fi
  if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -f -t "$ICONS_ROOT" >/dev/null 2>&1 || true
  fi
}

do_install() {
  install_icons
  write_desktop
  refresh_caches
  echo "Installed Meridian to the application menu."
  echo "  Desktop: $APPS_DIR/$DESKTOP_ID"
  echo "  Icons:   $ICONS_ROOT/*/apps/${ICON_NAME}.*"
  echo "  Launch:  $(resolve_exec)"
  if [[ -n "${APPIMAGE:-}" ]]; then
    echo "Remove later with:  \"${APPIMAGE}\" --uninstall"
  else
    echo "Remove later with:  AppRun --uninstall"
  fi
}

do_uninstall() {
  rm -f "$APPS_DIR/$DESKTOP_ID"
  find "$ICONS_ROOT" -type f \( -name "${ICON_NAME}.png" -o -name "${ICON_NAME}.svg" \) -delete 2>/dev/null || true
  find "$ICONS_ROOT" -type d -empty -delete 2>/dev/null || true
  rm -f "$STAMP_FILE"
  rmdir "$STAMP_DIR" 2>/dev/null || true
  refresh_caches
  echo "Removed Meridian from the application menu."
}

case "$ACTION" in
  install|--install)
    do_install
    ;;
  uninstall|--uninstall|remove|--remove)
    do_uninstall
    ;;
  *)
    echo "Usage: $(basename "$0") {install|uninstall}" >&2
    exit 2
    ;;
esac
