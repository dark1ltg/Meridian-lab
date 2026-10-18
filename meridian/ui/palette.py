from __future__ import annotations

from PySide6.QtGui import QColor

from meridian.queue_engine import Quadrant

# High-contrast playlist colors on the dark map (and matching list text).
PLAYLIST_HEX = {
    Quadrant.NOW: "#FFBF00",
    Quadrant.DEEP: "#00E5FF",
    Quadrant.FILL: "#FF3D8F",
    Quadrant.SHELF: "#8BFF4D",
}

PLAYLIST_QCOLOR = {q: QColor(h) for q, h in PLAYLIST_HEX.items()}

# Graduated confidence on the map (dims only — does not move stars).
# High (>= 0.75): full playlist color
# Mid  (0.45–0.75): same hue, softer
# Low  (< 0.45): cool pewter — contrasts neon amber/cyan/magenta/lime
LOW_TRUST_HEX = "#8B9BB8"
LOW_TRUST_QCOLOR = QColor(LOW_TRUST_HEX)
LOW_TRUST_ALPHA = 150  # baked field fill (0–255)
MID_CONFIDENCE_ALPHA = 200
MID_CONFIDENCE_OPACITY = 0.78
LOW_CONFIDENCE_OPACITY = 0.55

# Pinned stars: bright lock ring so “you placed this” reads at a glance.
PIN_RING_HEX = "#F4F7FF"
PIN_RING_QCOLOR = QColor(PIN_RING_HEX)
PIN_RING_WIDTH = 1.8
PIN_HEAD_RADIUS = 1.6

STAR_RADIUS = {
    Quadrant.NOW: 6.8,
    Quadrant.DEEP: 6.4,
    Quadrant.FILL: 6.2,
    Quadrant.SHELF: 5.2,
}

STAR_Z = {
    Quadrant.NOW: 11,
    Quadrant.DEEP: 10,
    Quadrant.FILL: 9,
    Quadrant.SHELF: 7,
}
