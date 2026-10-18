#!/usr/bin/env bash
# Fetch Ubuntu Sans + Ubuntu Condensed as standalone Font Software (UFL 1.0).
# These files are installed into the AppImage filesystem; they are never
# compiled into the Meridian executable.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT/resources/fonts"
BASE_SANS="https://raw.githubusercontent.com/google/fonts/main/ufl/ubuntusans"
BASE_COND="https://raw.githubusercontent.com/google/fonts/main/ufl/ubuntucondensed"

mkdir -p "$DEST"

curl -L --fail -o "$DEST/UbuntuSans[wdth,wght].ttf" \
  "${BASE_SANS}/UbuntuSans%5Bwdth%2Cwght%5D.ttf"
curl -L --fail -o "$DEST/UbuntuCondensed-Regular.ttf" \
  "${BASE_COND}/UbuntuCondensed-Regular.ttf"

# UFL requires the licence (and copyright notice) to accompany the Font Software.
curl -L --fail -o "$DEST/UBUNTU-FONT-LICENCE-1.0.txt" \
  "${BASE_COND}/UFL.txt"
curl -L --fail -o "$DEST/LICENCE.txt" \
  "${BASE_COND}/LICENCE.txt"
# Keep a second well-known alias used by some Ubuntu packaging layouts.
cp -f "$DEST/UBUNTU-FONT-LICENCE-1.0.txt" "$DEST/UFL.txt"

cat > "$DEST/COPYRIGHT.txt" <<'EOF'
Copyright 2010–2011 Canonical Ltd.
Copyright 2011, 2022, 2023 Canonical Ltd. (Ubuntu Sans)

This directory contains Ubuntu Font Family / Ubuntu Sans Font Software
distributed under the Ubuntu Font Licence Version 1.0.

See UBUNTU-FONT-LICENCE-1.0.txt (also UFL.txt / LICENCE.txt) in this directory.
Upstream: https://github.com/canonical/Ubuntu-Sans-fonts
           https://design.ubuntu.com/font/
EOF

cat > "$DEST/README.txt" <<'EOF'
Meridian ships these Ubuntu fonts as standalone files (not embedded in the
application binary) so recipients can inspect, copy, and redistribute the
Font Software under the Ubuntu Font Licence 1.0.

Files:
  UbuntuSans[wdth,wght].ttf
  UbuntuCondensed-Regular.ttf
  UBUNTU-FONT-LICENCE-1.0.txt
  UFL.txt
  LICENCE.txt
  COPYRIGHT.txt
EOF

# Drop obsolete duplicate licence name if present from earlier fetches.
rm -f "$DEST/LICENCE-ubuntu-classic.txt"

echo "Fonts ready in $DEST"
ls -la "$DEST"
