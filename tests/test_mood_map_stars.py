"""Night-sky stars stay readable: size floor, pewter weak guesses, loved ring."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MERIDIAN_NO_GL", "1")

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from meridian.library import Track
from meridian.queue_engine import Quadrant, RankedTrack
from meridian.ui.mood_map import (
    LOVED_RING_QCOLOR,
    STAR_BODY_FLOOR,
    STAR_BODY_FLOOR_DENSE,
    MoodMap,
    TrackStar,
    night_body_radius,
    queue_star_color,
)
from meridian.ui.palette import LOW_TRUST_QCOLOR, PLAYLIST_QCOLOR


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


def _track(i: int, **overrides) -> Track:
    fields = dict(
        id=i,
        path=f"/t/{i}.mp3",
        title="Song",
        artist="Artist",
        album="",
        albumartist="",
        genre="",
        duration_ms=1,
        year=None,
        bpm=None,
        valence=0.4,
        energy=0.6,
        mood_confidence=0.95,
        confidence_note="",
        low_trust=False,
        pinned=False,
        loved=False,
        play_count=0,
        skip_count=0,
        last_played=None,
        added_at=None,
        mtime=None,
        analyzed=True,
    )
    fields.update(overrides)
    return Track(**fields)


def _ranked(track: Track, quadrant: Quadrant = Quadrant.NOW) -> RankedTrack:
    return RankedTrack(track=track, fit=0.5, importance=0.5, urgency=0.5, quadrant=quadrant)


def _dist(a: QColor, b: QColor) -> float:
    return ((a.red() - b.red()) ** 2 + (a.green() - b.green()) ** 2 + (a.blue() - b.blue()) ** 2) ** 0.5


def test_low_trust_leans_pewter_and_high_stays_queue() -> None:
    high = queue_star_color(Quadrant.NOW, loved=False, band=2)
    low = queue_star_color(Quadrant.NOW, loved=False, band=0)
    playlist = PLAYLIST_QCOLOR[Quadrant.NOW]
    assert (high.red(), high.green(), high.blue()) == (playlist.red(), playlist.green(), playlist.blue())
    assert high.alpha() >= 240
    assert _dist(low, LOW_TRUST_QCOLOR) < _dist(low, playlist)
    assert low.blue() > 80
    assert low.alpha() >= 200


def test_body_stays_visible_and_smaller_than_a_coin() -> None:
    for quadrant in Quadrant:
        for mag in (0.0, 0.5, 1.0):
            normal = night_body_radius(
                quadrant, band=2, mag=mag, loved=False, pinned=False, current=False, density=False
            )
            dense = night_body_radius(
                quadrant, band=2, mag=mag, loved=False, pinned=False, current=False, density=True
            )
            assert normal >= STAR_BODY_FLOOR
            assert dense >= STAR_BODY_FLOOR_DENSE
            assert normal < 5.0
            assert dense < 5.0
        weak = night_body_radius(
            quadrant, band=0, mag=0.0, loved=False, pinned=False, current=False, density=True
        )
        assert weak >= STAR_BODY_FLOOR_DENSE * 0.92


def test_loved_star_keeps_a_warm_ring(qapp) -> None:
    star = TrackStar(_ranked(_track(3, loved=True, mood_confidence=0.9)))
    pen = star.pen()
    assert pen.style() != Qt.PenStyle.NoPen
    assert pen.color().name().lower() == LOVED_RING_QCOLOR.name().lower()
    plain = TrackStar(_ranked(_track(4, loved=False, mood_confidence=0.9)))
    assert plain.pen().style() == Qt.PenStyle.NoPen


def test_baked_low_star_is_pewter_and_loved_ring_paints(qapp) -> None:
    mood = MoodMap()
    mood.resize(960, 720)
    mood.set_tracks(
        [
            _ranked(_track(1, valence=0.2, energy=0.8, mood_confidence=0.2), Quadrant.NOW),
            _ranked(_track(2, valence=0.8, energy=0.8, mood_confidence=0.95, loved=True), Quadrant.DEEP),
        ],
        current_id=None,
    )
    low = mood._night_star(mood._ranked[1], False)
    assert _dist(low[1], LOW_TRUST_QCOLOR) < _dist(low[1], PLAYLIST_QCOLOR[Quadrant.NOW])
    assert low[0] >= STAR_BODY_FLOOR * 0.92
    pix = mood._field.pixmap().toImage()
    pos = mood._positions[2]
    gold = 0
    for dy in range(-12, 13):
        for dx in range(-12, 13):
            x, y = int(round(pos.x() * 2)) + dx, int(round(pos.y() * 2)) + dy
            if not (0 <= x < pix.width() and 0 <= y < pix.height()):
                continue
            c = pix.pixelColor(x, y)
            if c.red() > 200 and c.green() > 180 and 140 < c.blue() < 230:
                gold += 1
    assert gold >= 4
