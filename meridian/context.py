from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

# Mood-map lens radius in valence/energy units (0..1 space).
LENS_RADIUS_MIN = 0.03
LENS_RADIUS_MAX = 0.48
LENS_RADIUS_DEFAULT = 0.22


class Mode(str, Enum):
    FOCUS = "focus"
    WANDER = "wander"
    CHARGE = "charge"
    DIM = "dim"


MODE_LABELS = {
    Mode.FOCUS: "Focus",
    Mode.WANDER: "Wander",
    Mode.CHARGE: "Charge",
    Mode.DIM: "Dim",
}

MODE_HINTS = {
    Mode.FOCUS: "Steady mid-energy, fewer surprises.",
    Mode.WANDER: "Follow the lens. Let the map wander.",
    Mode.CHARGE: "High kinetic tracks. Skip the still air.",
    Mode.DIM: "Low light, low pulse. Night gravity.",
}


class Band(str, Enum):
    DAWN = "dawn"
    DAY = "day"
    DUSK = "dusk"
    NIGHT = "night"


@dataclass(slots=True)
class Context:
    mode: Mode
    band: Band
    hour: int
    skip_pressure: float
    lens_x: float
    lens_y: float
    lens_radius: float

    @property
    def band_label(self) -> str:
        return {
            Band.DAWN: "Dawn",
            Band.DAY: "Day",
            Band.DUSK: "Dusk",
            Band.NIGHT: "Night",
        }[self.band]


def band_for_hour(hour: int) -> Band:
    if 5 <= hour < 11:
        return Band.DAWN
    if 11 <= hour < 17:
        return Band.DAY
    if 17 <= hour < 21:
        return Band.DUSK
    return Band.NIGHT


def band_bias(band: Band) -> tuple[float, float]:
    """Preferred (valence, energy) gravity for the clock."""
    return {
        Band.DAWN: (0.62, 0.42),
        Band.DAY: (0.58, 0.58),
        Band.DUSK: (0.48, 0.46),
        Band.NIGHT: (0.38, 0.28),
    }[band]


def mode_bias(mode: Mode) -> tuple[float, float, float]:
    """valence shift, energy shift, radius scale."""
    return {
        Mode.FOCUS: (0.02, -0.04, 0.78),
        Mode.WANDER: (0.0, 0.0, 1.12),
        Mode.CHARGE: (0.04, 0.22, 0.92),
        Mode.DIM: (-0.06, -0.22, 0.85),
    }[mode]


def current_hour() -> int:
    return datetime.now().hour


def make_context(
    mode: Mode,
    lens_x: float,
    lens_y: float,
    lens_radius: float,
    skip_pressure: float,
) -> Context:
    hour = current_hour()
    return Context(
        mode=mode,
        band=band_for_hour(hour),
        hour=hour,
        skip_pressure=max(0.0, min(1.0, skip_pressure)),
        lens_x=lens_x,
        lens_y=lens_y,
        lens_radius=lens_radius,
    )
