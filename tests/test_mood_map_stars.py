"""Night-sky stars stay readable: size floor, pewter weak guesses, loved ring."""

from __future__ import annotations

import os
import random
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MERIDIAN_NO_GL", "1")

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QGraphicsLineItem

from meridian.library import Track
from meridian.queue_engine import Quadrant, RankedTrack
from meridian.ui.mood_map import (
    LOVED_RING_QCOLOR,
    SKY_LINK_MAX,
    STAR_BODY_FLOOR,
    STAR_BODY_FLOOR_DENSE,
    MoodMap,
    TrackStar,
    constellation_segments,
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


def _linked(points: list[tuple[int, float, float]], a: int, b: int) -> bool:
    coords = {tid: (x, y) for tid, x, y in points}
    ax, ay = coords[a]
    bx, by = coords[b]
    for x1, y1, x2, y2, _dist in constellation_segments(points):
        if (x1, y1, x2, y2) in ((ax, ay, bx, by), (bx, by, ax, ay)):
            return True
    return False


def test_crowded_cell_links_the_real_neighbor() -> None:
    """A pile of songs must not hide the closest one that is far enough to draw."""
    points = [(1, 400.0, 300.0)]
    for i in range(2, 30):
        points.append((i, 400.0 + (i % 5) * 0.4, 300.0 + (i % 3) * 0.4))
    points.append((99, 428.0, 300.0))
    assert _linked(points, 1, 99)


def test_different_bearing_survives_a_crowd_on_one_side() -> None:
    points = [(0, 400.0, 300.0)]
    for i in range(1, 31):
        points.append((i, 400.0 + 20.0 + i * 0.8, 300.0))
    points.append((50, 400.0, 300.0 + 70.0))
    assert _linked(points, 0, 1)
    assert _linked(points, 0, 50)
    points.append((77, 400.0 + SKY_LINK_MAX + 40.0, 300.0))
    assert not _linked(points, 0, 77)


def test_recolor_reuses_links_and_a_large_map_stays_quick(qapp, monkeypatch) -> None:
    import meridian.ui.mood_map as mood_map

    calls = {"n": 0}
    rebuilds = {"n": 0}
    real = mood_map.constellation_segments
    real_rebuild = MoodMap._rebuild_starfield

    def wrapped(points):
        calls["n"] += 1
        return real(points)

    def wrapped_rebuild(self):
        rebuilds["n"] += 1
        real_rebuild(self)

    monkeypatch.setattr(mood_map, "constellation_segments", wrapped)
    monkeypatch.setattr(MoodMap, "_rebuild_starfield", wrapped_rebuild)
    mood = MoodMap()
    mood.resize(640, 480)
    first = [
        _ranked(_track(i, valence=(i % 40) / 39, energy=((i * 3) % 40) / 39), Quadrant.NOW)
        for i in range(1, 81)
    ]
    mood.set_tracks(first, None)
    assert calls["n"] == 1
    recolored = [
        _ranked(item.track, Quadrant.DEEP if item.track.id % 2 == 0 else Quadrant.NOW)
        for item in first
    ]
    mood.set_tracks(recolored, None)
    assert rebuilds["n"] == 2
    assert calls["n"] == 1

    rng = random.Random(0)
    points = [(i, 40 + rng.random() * 720, 36 + rng.random() * 524) for i in range(4000)]
    started = time.perf_counter()
    real(points)
    assert time.perf_counter() - started < 0.5

    big = MoodMap()
    big.resize(400, 300)
    library = [
        _ranked(_track(i, valence=rng.random(), energy=rng.random()), Quadrant.NOW)
        for i in range(1, 1501)
    ]
    big.set_tracks(library, None)
    recolored = [
        _ranked(item.track, Quadrant.SHELF if item.track.id % 2 == 0 else Quadrant.FILL)
        for item in library
    ]
    started = time.perf_counter()
    big.set_tracks(recolored, None)
    assert time.perf_counter() - started < 0.15


def test_mood_cross_reads_stronger_than_the_quarter_marks(qapp) -> None:
    mood = MoodMap()
    lines = [item for item in mood._sky_chrome if isinstance(item, QGraphicsLineItem)]
    center = [item for item in lines if abs(item.line().y1() - 298) < 1 and abs(item.line().x2() - 760) < 1]
    quarter = [item for item in lines if abs(item.line().y1() - (36 + 524 / 4)) < 1]
    assert center and quarter
    assert max(item.pen().color().alpha() for item in center) >= 160
    assert max(item.pen().color().alpha() for item in center) > quarter[0].pen().color().alpha()
