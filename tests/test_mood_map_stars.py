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
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication, QGraphicsEllipseItem, QGraphicsLineItem

from meridian.library import Track
from meridian.queue_engine import Quadrant, RankedTrack
from meridian.ui.mood_map import (
    FIELD_SCALE,
    LIVE_STARS_ZOOM,
    LOVED_RING_QCOLOR,
    NEAR_PATCH_SCALE,
    NEAR_PATCH_ZOOM,
    SCENE_W,
    SKY_LINK_MAX,
    STAR_BODY_FLOOR,
    STAR_BODY_FLOOR_DENSE,
    MoodMap,
    TrackStar,
    _star_mag,
    BIN_SPAN,
    constellation_bin_image,
    constellation_groups,
    constellation_segments,
    crowd_ids,
    duration_radius_scale,
    ease_amount,
    heartbeat_amount,
    night_body_radius,
    queue_star_color,
    quiet_star_color,
    sky_haze,
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
        duration_ms=210_000,
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


def test_zero_confidence_draws_as_a_weak_star(qapp) -> None:
    """A stored 0 is rock-bottom, not the neutral 0.5 fill-in for a missing score."""
    zero_star = TrackStar(_ranked(_track(11, mood_confidence=0.0)))
    mid_star = TrackStar(_ranked(_track(12, mood_confidence=0.5)))
    zero_star.apply_style(zero_star.ranked, False)
    mid_star.apply_style(mid_star.ranked, False)
    assert zero_star.brush().color().rgb() == quiet_star_color(0).rgb()
    assert mid_star.brush().color().rgb() == quiet_star_color(1).rgb()
    assert "0.00" in zero_star.toolTip()

    missing = _track(13, mood_confidence=0.5)
    missing.mood_confidence = None
    mood = MoodMap()
    assert mood._confidence_band(_ranked(missing)) == 1
    assert mood._confidence_band(_ranked(_track(14, mood_confidence=0.0))) == 0
    _radius, color, _bloom = mood._night_star(_ranked(_track(15, mood_confidence=0.0)), False)
    assert color.rgb() == quiet_star_color(0).rgb()
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
    assert rebuilds["n"] == 1
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


def test_mood_cross_is_the_only_seam(qapp) -> None:
    mood = MoodMap()
    lines = [item for item in mood._sky_chrome if isinstance(item, QGraphicsLineItem)]
    center = [item for item in lines if abs(item.line().y1() - 298) < 1 and abs(item.line().x2() - 760) < 1]
    quarter = [item for item in lines if abs(item.line().y1() - (36 + 524 / 4)) < 1]
    weather = [item for item in mood._sky_chrome if isinstance(item, QGraphicsEllipseItem)]
    assert center
    assert not quarter
    assert len(weather) >= 4
    assert max(item.pen().color().alpha() for item in center) >= 160


def test_lens_colors_only_the_stars_it_covers(qapp) -> None:
    mood = MoodMap()
    mood.resize(800, 600)
    valence = (420 - 40) / 720
    energy = (560 - 300) / 524
    inside = _ranked(_track(1, valence=valence, energy=energy, mood_confidence=0.95), Quadrant.NOW)
    outside = _ranked(_track(2, valence=0.05, energy=0.05, mood_confidence=0.95), Quadrant.NOW)
    mood.set_tracks([inside, outside], None)
    quiet = quiet_star_color(2)
    baked = mood._night_star(mood._ranked[1], False)[1]
    assert _dist(baked, quiet) < _dist(baked, PLAYLIST_QCOLOR[Quadrant.NOW])
    assert mood.star_in_lens(mood._positions[1])
    assert not mood.star_in_lens(mood._positions[2])
    tint = mood._lens_color.pixmap().toImage()
    pos = mood._positions[1]
    origin = mood._lens_color.pos()
    amber = 0
    for dy in range(-8, 9):
        for dx in range(-8, 9):
            x = int(round((pos.x() - origin.x()) * 2)) + dx
            y = int(round((pos.y() - origin.y()) * 2)) + dy
            if not (0 <= x < tint.width() and 0 <= y < tint.height()):
                continue
            color = tint.pixelColor(x, y)
            if color.red() > 180 and color.green() > 120 and color.blue() < 90 and color.alpha() > 40:
                amber += 1
    assert amber >= 4


def test_queue_route_is_brightest_beside_the_current_song(qapp) -> None:
    mood = MoodMap()
    mood.set_tracks(
        [
            _ranked(_track(1, valence=0.30, energy=0.40)),
            _ranked(_track(2, valence=0.60, energy=0.70)),
            _ranked(_track(3, valence=0.80, energy=0.30)),
        ],
        current_id=1,
    )
    mood.set_queue_route([1, 2, 3])
    image = mood._route.pixmap().toImage()
    assert not image.isNull()

    def peak(a, b) -> int:
        sx = (a.x() + b.x()) * 0.5
        sy = (a.y() + b.y()) * 0.5
        best = 0
        for dy in range(-10, 11):
            for dx in range(-10, 11):
                x = int(round(sx * 2)) + dx
                y = int(round(sy * 2)) + dy
                if 0 <= x < image.width() and 0 <= y < image.height():
                    best = max(best, image.pixelColor(x, y).alpha())
        return best

    first = mood._positions[1]
    second = mood._positions[2]
    third = mood._positions[3]
    assert peak(first, second) > peak(second, third)


def test_zoomed_star_can_show_a_role_chip(qapp) -> None:
    star = TrackStar(_ranked(_track(9, mood_confidence=0.95), Quadrant.DEEP))
    assert not star._role.isVisible()
    star.set_lod(1.0, True)
    assert star._label.isVisible()
    assert star._role.isVisible()
    assert star._role.text() == "DEEP"


def test_song_length_changes_star_size() -> None:
    short = night_body_radius(
        Quadrant.NOW, band=2, mag=0.5, loved=False, pinned=False, current=False, density=False, duration_ms=20_000
    )
    usual = night_body_radius(
        Quadrant.NOW, band=2, mag=0.5, loved=False, pinned=False, current=False, density=False, duration_ms=210_000
    )
    long = night_body_radius(
        Quadrant.NOW, band=2, mag=0.5, loved=False, pinned=False, current=False, density=False, duration_ms=600_000
    )
    assert short < usual < long
    assert duration_radius_scale(None) == 1.0
    assert short < STAR_BODY_FLOOR


def test_heartbeat_is_two_thumps_then_a_rest() -> None:
    assert heartbeat_amount(0.10) > 0.8
    assert heartbeat_amount(0.28) > 0.4
    assert heartbeat_amount(0.7) == 0.0


def test_day_band_is_hazier_than_night() -> None:
    assert sky_haze("night") == 0.0
    assert sky_haze("day") == 1.0
    assert sky_haze("dawn") > sky_haze("dusk") > sky_haze("night")


def test_day_sky_softens_the_star(qapp) -> None:
    view = MoodMap()
    view.resize(480, 360)
    view.set_tracks([_ranked(_track(1, valence=0.5, energy=0.5, duration_ms=210_000))], None)
    view.set_sky_band("night")
    night = view._field.pixmap().toImage()
    view.set_sky_band("day")
    day = view._field.pixmap().toImage()
    pos = view._positions[1]
    x = int(round(pos.x() * 2)) + 8
    y = int(round(pos.y() * 2))
    assert day.pixelColor(x, y).alpha() > night.pixelColor(x, y).alpha()


def test_search_blacks_out_the_sky_and_keeps_the_match(qapp) -> None:
    view = MoodMap()
    view.resize(480, 360)
    view.set_tracks(
        [
            _ranked(_track(1, title="Alpha", valence=0.3, energy=0.4)),
            _ranked(_track(2, title="Other", valence=0.7, energy=0.6)),
        ],
        None,
    )
    view.set_search_query("alpha")
    assert view._field.opacity() < 0.2
    assert view._search_ids == {1}
    assert not view._flare.pixmap().isNull()
    view.set_search_query("")
    assert view._field.opacity() > 0.9
    assert view._search_ids == set()
    assert view._flare.pixmap().isNull()


def test_filename_search_flares_the_song(qapp) -> None:
    view = MoodMap()
    view.resize(480, 360)
    view.set_tracks(
        [_ranked(_track(1, title="Nope", path="/music/rarefolder/clip.mp3"))],
        None,
    )
    view.set_search_query("rarefolder")
    assert view._search_ids == {1}
    assert view._field.opacity() < 0.2


def test_first_sky_does_not_ripple_a_song_already_loaded(qapp) -> None:
    view = MoodMap()
    view.resize(480, 360)
    view.set_tracks([_ranked(_track(1, valence=0.4, energy=0.5))], 1)
    assert view._ripple_origin is None


def test_heartbeat_follows_playback_not_a_loaded_song(qapp) -> None:
    view = MoodMap()
    view.resize(480, 360)
    view.set_tracks([_ranked(_track(1))], 1)
    assert not view._life_timer.isActive()
    view.set_playing(True)
    assert view._life_timer.isActive()
    assert view._pulse_rect() is not None
    assert view._pulse_rect().width() < 80
    view.set_playing(False)
    assert not view._life_timer.isActive()


def test_playing_a_star_starts_a_ripple(qapp) -> None:
    view = MoodMap()
    view.resize(480, 360)
    ranked = [
        _ranked(_track(1, valence=0.4, energy=0.5)),
        _ranked(_track(2, valence=0.42, energy=0.52)),
    ]
    view.set_tracks(ranked, None)
    assert view._ripple_origin is None
    view.set_tracks(ranked, 1)
    assert view._ripple_origin is not None
    assert view._ripple_neighbors


def test_close_view_keeps_small_stars_and_hides_the_pile(qapp) -> None:
    view = MoodMap()
    view.resize(480, 360)
    view.set_tracks([_ranked(_track(1, valence=0.4, energy=0.5))], None)
    view._user_zoom = 6.0
    view._lod_band = -1
    view._sync_live_stars(force=True)
    star = next(iter(view._stars.values()))
    assert star.scale() == pytest.approx(2.4 / 6.0, abs=0.02)
    assert view._field.opacity() == pytest.approx(0.0)
    assert view._regions.opacity() == pytest.approx(0.0)
    view._user_zoom = 2.4
    view._lod_band = -1
    view._sync_live_stars(force=True)
    assert star.scale() == pytest.approx(1.0, abs=0.02)
    view._user_zoom = 1.0
    view._lod_band = -1
    view._sync_live_stars(force=True)
    assert view._field.opacity() == pytest.approx(1.0)
    assert view._regions.opacity() == pytest.approx(1.0)


def test_a_shared_point_stays_one_dot_with_a_count(qapp) -> None:
    view = MoodMap()
    view.resize(480, 360)
    loved = _track(1, title="Loved One", valence=0.55, energy=0.45, loved=True)
    plain = _track(2, title="Plain One", valence=0.55, energy=0.45)
    other = _track(3, title="Elsewhere", valence=0.2, energy=0.8)
    view.set_tracks(
        [
            _ranked(loved),
            _ranked(plain),
            _ranked(other),
        ],
        2,
    )
    view._pointer_scene = view.mood_to_pos(0.55, 0.45)
    view._user_zoom = 4.0
    view._lod_band = -1
    view._sync_live_stars(force=True)
    visible = [star for star in view._stars.values() if star.isVisible()]
    piled = [star for star in visible if len(star._stack_ids) > 1]
    assert len(piled) == 1
    assert set(piled[0]._stack_ids) == {1, 2}
    assert piled[0]._count.text() == "2"
    assert piled[0]._count.isVisible()
    ordered = view._order_stack([1, 2, 3])
    assert ordered[0] == 2
    assert ordered[1] == 1
    view.step_stack(piled[0], 1)
    assert piled[0].ranked.track.id in {1, 2}


def test_close_view_wakes_the_pile_under_the_pointer(qapp, monkeypatch) -> None:
    import meridian.ui.mood_map as mood_map

    monkeypatch.setattr(mood_map, "LIVE_STAR_CAP", 1)
    view = MoodMap()
    view.resize(640, 480)
    near = [_ranked(_track(i, title=f"Near {i}", valence=0.82, energy=0.78)) for i in (1, 2)]
    far = _ranked(_track(9, title="Loved Far", valence=0.12, energy=0.15, loved=True))
    view.set_tracks([*near, far], None)
    view.set_lens(0.82, 0.78, 0.08)
    view._pointer_scene = view.mood_to_pos(0.82, 0.78)
    view._user_zoom = 4.0
    view._lod_band = -1
    view._sync_live_stars(force=True)
    live_ids = set(view._stars)
    assert 9 not in live_ids
    assert live_ids & {1, 2}


def test_a_crowd_drops_its_halo(qapp) -> None:
    bright = next(i for i in range(20, 800) if _star_mag(i) > 0.95)
    view = MoodMap()
    view.resize(640, 480)
    tracks = []
    for n in range(8):
        tid = 3 + n
        x = 400 + (n % 3) * 6
        y = 300 + (n // 3) * 6
        tracks.append(
            _ranked(
                _track(
                    tid,
                    valence=(x - 40) / 720,
                    energy=(560 - y) / 524,
                    pinned=(tid == 4),
                )
            )
        )
    x, y = 412, 312
    tracks.append(
        _ranked(_track(bright, valence=(x - 40) / 720, energy=(560 - y) / 524))
    )
    tracks.append(_ranked(_track(2, valence=0.08, energy=0.9)))
    view.set_tracks(tracks, 3)
    assert bright in view._crowd_ids
    assert 2 not in view._crowd_ids
    assert view._night_star(view._ranked[3], False)[2] > 0
    assert view._night_star(view._ranked[4], False)[2] > 0
    assert view._night_star(view._ranked[bright], False)[2] == 0
    view._crowd_ids.discard(bright)
    assert view._night_star(view._ranked[bright], False)[2] > 0

    view._refresh_crowd()
    view._user_zoom = 4.0
    view._lod_band = -1
    view._pointer_scene = view.mood_to_pos(0.08, 0.9)
    view._sync_live_stars(force=True)
    plain = view._stars[bright]
    assert not plain._glow.isVisible()
    assert view._stars[3]._glow.isVisible()
    view._pointer_scene = view._positions[bright]
    view._lod_band = -1
    view._sync_live_stars(force=True)
    assert view._stars[bright]._glow.isVisible()


def test_closer_lines_gather_a_shape_and_a_long_line_does_not() -> None:
    near = [
        (0.0, 0.0, 30.0, 0.0, 30.0),
        (30.0, 0.0, 58.0, 8.0, 29.0),
        (58.0, 8.0, 40.0, 32.0, 30.0),
        (40.0, 32.0, 8.0, 24.0, 33.0),
    ]
    far = [(0.0, 0.0, 0.0 + BIN_SPAN + 40.0, 0.0, BIN_SPAN + 40.0)]
    other = [
        (400.0, 400.0, 428.0, 400.0, 28.0),
        (428.0, 400.0, 456.0, 412.0, 30.0),
        (456.0, 412.0, 430.0, 434.0, 34.0),
    ]
    groups = constellation_groups(near + far + other)
    assert len(groups) == 2
    flat = {point for group in groups for point in group}
    assert (0.0, 0.0) in flat
    assert (400.0, 400.0) in flat
    assert (round(BIN_SPAN + 40.0, 2), 0.0) not in flat
    pile = [(400.0, 300.0, 402.0, 300.0, 2.0), (402.0, 300.0, 404.0, 301.0, 2.2)]
    assert constellation_groups(pile) == []
    image = constellation_bin_image(groups)
    assert image.width() > 0
    opaque = 0
    for group in groups:
        x, y = group[0]
        color = image.pixelColor(int(round(x * 2)), int(round(y * 2)))
        opaque += color.alpha()
    assert opaque > 40


def test_bins_leave_moods_put_and_fade_when_the_sky_dives(qapp) -> None:
    view = MoodMap()
    view.resize(640, 480)
    spaced = [
        _ranked(_track(index, valence=0.20 + step * 0.04, energy=0.78))
        for index, step in enumerate((0, 1, 2, 3), start=1)
    ]
    pile = [
        _ranked(_track(index, valence=0.62, energy=0.42))
        for index in range(10, 16)
    ]
    stored = [(item.track.id, item.track.valence, item.track.energy) for item in spaced + pile]
    view.set_tracks(spaced + pile, None)
    for tid, valence, energy in stored:
        assert view._ranked[tid].track.valence == valence
        assert view._ranked[tid].track.energy == energy
        pos = view._positions[tid]
        assert pos.x() == pytest.approx(40 + valence * 720)
        assert pos.y() == pytest.approx(560 - energy * 524)
    assert view._links.zValue() > view._bins.zValue() > view._regions.zValue()
    pix = view._bins.pixmap().toImage()
    star = view._positions[1]
    assert pix.pixelColor(int(round(star.x() * 2)), int(round(star.y() * 2))).alpha() > 20
    knot = view._positions[10]
    assert pix.pixelColor(int(round(knot.x() * 2)), int(round(knot.y() * 2))).alpha() == 0
    view._user_zoom = 2.6
    view._lod_band = -1
    view._sync_live_stars(force=True)
    assert view._bins.opacity() == pytest.approx(0.0)
    view._user_zoom = 1.0
    view._lod_band = -1
    view._sync_live_stars(force=True)
    assert view._bins.opacity() == pytest.approx(1.0)


def test_crowd_ids_mark_a_pile_and_leave_a_lone_star() -> None:
    pile = [(i, 400.0, 300.0) for i in range(1, 9)]
    pile.append((99, 80.0, 80.0))
    marked = crowd_ids(pile)
    assert set(range(1, 9)) <= marked
    assert 99 not in marked


def test_zooming_home_from_close_in_falls_back(qapp) -> None:
    view = MoodMap()
    view.resize(480, 360)
    view.show()
    view.set_tracks(
        [
            _ranked(_track(1, valence=0.4, energy=0.5)),
            _ranked(_track(2, valence=0.7, energy=0.3)),
        ],
        1,
    )
    view._user_zoom = 6.0
    view._lod_band = -1
    view._sync_live_stars(force=True)
    assert view._field.opacity() < 0.5
    view.reset_view()
    assert view._falling
    assert view._field.opacity() == 1.0
    assert not any(star.isVisible() for star in view._stars.values())
    view.reset_view()
    assert not view._falling
    assert view._user_zoom == 1.0


def test_ease_amount_starts_still_and_finishes_settled() -> None:
    assert ease_amount(0.0, 1.0) == 0.0
    assert ease_amount(1.0, 1.0) == 1.0
    assert ease_amount(2.0, 1.0) == 1.0
    mid = ease_amount(0.5, 1.0)
    assert 0.4 < mid < 0.6


def _alpha_near(image: QImage, x: float, y: float, radius: int = 3) -> int:
    best = 0
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            px = int(round(x)) + dx
            py = int(round(y)) + dy
            if 0 <= px < image.width() and 0 <= py < image.height():
                best = max(best, image.pixelColor(px, py).alpha())
    return best


def test_listen_nudge_eases_one_star_and_leaves_a_pewter_ghost(qapp) -> None:
    view = MoodMap()
    view.resize(800, 600)
    old = _ranked(_track(1, valence=0.2, energy=0.2, mood_confidence=0.9))
    view.set_tracks([old], None)
    assert view.begin_listen_ease(1, 0.2, 0.2, 0.8, 0.8)
    moved = _ranked(_track(1, valence=0.8, energy=0.8, mood_confidence=0.9))
    view.set_tracks([moved], None)
    start = view.mood_to_pos(0.2, 0.2)
    end = view.mood_to_pos(0.8, 0.8)
    placed = view._positions[1]
    assert abs(placed.x() - start.x()) < 1.0
    assert abs(placed.y() - start.y()) < 1.0
    baked = view._field.pixmap().toImage()
    assert _alpha_near(baked, end.x() * FIELD_SCALE, end.y() * FIELD_SCALE, 4) == 0
    life = QImage(800, 600, QImage.Format.Format_ARGB32_Premultiplied)
    life.fill(Qt.GlobalColor.transparent)
    painter = QPainter(life)
    view.paint_sky_life(painter)
    painter.end()
    assert _alpha_near(life, start.x(), start.y(), 3) > 20
    # A second refresh during the slide does not rebake the whole sky.
    rebuilds = {"n": 0}
    real_rebuild = view._rebuild_starfield

    def wrapped_rebuild() -> None:
        rebuilds["n"] += 1
        real_rebuild()

    view._rebuild_starfield = wrapped_rebuild
    view.set_tracks([moved], None)
    assert rebuilds["n"] == 0
    tid, ease_start, ease_end, t0, _until = view._listen_ease
    view._listen_ease = (tid, ease_start, ease_end, t0, t0)
    view._advance_listen_ease()
    assert view._listen_ease is None
    settled = view._positions[1]
    assert abs(settled.x() - end.x()) < 1.0
    assert abs(settled.y() - end.y()) < 1.0
    baked = view._field.pixmap().toImage()
    assert _alpha_near(baked, end.x() * FIELD_SCALE, end.y() * FIELD_SCALE, 4) > 20
    assert view._nearest_track_id(end, 14) == 1
    assert view._nearest_track_id(start, 14) is None


def test_press_during_the_slide_puts_the_star_back(qapp) -> None:
    view = MoodMap()
    view.resize(800, 600)
    view.set_tracks([_ranked(_track(1, valence=0.2, energy=0.2))], None)
    assert view.begin_listen_ease(1, 0.2, 0.2, 0.8, 0.8)
    view.set_tracks([_ranked(_track(1, valence=0.8, energy=0.8))], None)
    view._sky_hold_id = 1
    view._advance_listen_ease()
    assert view._listen_ease is None
    pos = view._positions[1]
    baked = view._field.pixmap().toImage()
    assert _alpha_near(baked, pos.x() * FIELD_SCALE, pos.y() * FIELD_SCALE, 4) > 20
    assert view._nearest_track_id(pos, 14) == 1


def test_slide_aims_lines_and_tiles_at_the_new_spot(qapp) -> None:
    view = MoodMap()
    view.resize(800, 600)
    view.set_tracks(
        [
            _ranked(_track(1, valence=0.1, energy=0.5)),
            _ranked(_track(2, valence=0.56, energy=0.5)),
        ],
        None,
    )
    assert view.begin_listen_ease(1, 0.1, 0.5, 0.5, 0.5)
    view.set_tracks(
        [
            _ranked(_track(1, valence=0.5, energy=0.5)),
            _ranked(_track(2, valence=0.56, energy=0.5)),
        ],
        None,
    )
    end = view.mood_to_pos(0.5, 0.5)
    start = view.mood_to_pos(0.1, 0.5)
    aimed = False
    for seg in view._link_cache:
        if (abs(seg[0] - end.x()) < 1 and abs(seg[1] - end.y()) < 1) or (
            abs(seg[2] - end.x()) < 1 and abs(seg[3] - end.y()) < 1
        ):
            aimed = True
        assert not (abs(seg[0] - start.x()) < 1 and abs(seg[1] - start.y()) < 1)
        assert not (abs(seg[2] - start.x()) < 1 and abs(seg[3] - start.y()) < 1)
    assert aimed
    key = view._region_position_key()
    assert any(abs(x - end.x()) < 0.2 and abs(y - end.y()) < 0.2 for _tid, x, y in key[2])


def test_a_near_search_clears_the_trip_line(qapp) -> None:
    view = MoodMap()
    view.resize(800, 600)
    view.set_lens(0.8, 0.8, 0.22)
    updates: list[object] = []
    view._life.update = lambda *args, **kwargs: updates.append(args)
    assert view.begin_search_corridor(0.2, 0.2)
    updates.clear()
    view.set_lens(0.2, 0.2, 0.22)
    assert view.begin_search_corridor(0.2, 0.2) is False
    assert view._search_corridor is None
    assert updates


def test_listen_ease_waits_out_a_drag_and_ignores_a_tiny_step(qapp) -> None:
    view = MoodMap()
    view.set_tracks([_ranked(_track(1, valence=0.4, energy=0.4))], None)
    view._panning = True
    assert view.begin_listen_ease(1, 0.4, 0.4, 0.7, 0.7) is False
    view._panning = False
    assert view.begin_listen_ease(1, 0.4, 0.4, 0.4, 0.4) is False


def test_search_corridor_is_not_the_queue_path(qapp) -> None:
    view = MoodMap()
    view.resize(800, 600)
    view.set_tracks(
        [
            _ranked(_track(1, valence=0.15, energy=0.15)),
            _ranked(_track(2, valence=0.85, energy=0.75)),
        ],
        None,
    )
    view.set_lens(0.15, 0.15, 0.22)
    route = view._route.pixmap().cacheKey()
    assert view.begin_search_corridor(0.85, 0.75)
    assert view._route.pixmap().cacheKey() == route
    start, end, _t0, _until = view._search_corridor
    home = view.mood_to_pos(0.15, 0.15)
    assert abs(start.x() - home.x()) < 1.0
    assert abs(start.y() - home.y()) < 1.0
    life = QImage(800, 600, QImage.Format.Format_ARGB32_Premultiplied)
    life.fill(Qt.GlobalColor.transparent)
    painter = QPainter(life)
    view.paint_sky_life(painter)
    painter.end()
    mid_x = (start.x() + end.x()) / 2.0
    mid_y = (start.y() + end.y()) / 2.0
    assert _alpha_near(life, mid_x, mid_y, 6) > 0
    assert view._route.pixmap().cacheKey() == route
    view._search_corridor = (start, end, _t0, _t0)
    view._tick_life()
    assert view._search_corridor is None
    view.set_lens(0.5, 0.5, 0.22)
    assert view.begin_search_corridor(0.5, 0.5) is False


def _settle_zoom(view: MoodMap, zoom: float) -> None:
    view.resize(800, 600)
    view._user_zoom = 1.0
    view._zoom_target = 1.0
    view._zoom_active = False
    view._panning = False
    view._falling = False
    view.fitInView(view._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
    if zoom > 1.01:
        view._apply_zoom_level(zoom, view.viewport().rect().center())
        view._lod_timer.stop()
        view._zoom_active = False
    view._lod_band = -1
    view._sync_live_stars(force=True)


def _patch_alpha(view: MoodMap, track_id: int) -> int:
    rect = view._near_rect
    assert rect is not None
    pos = view._positions[track_id]
    return _alpha_near(
        view._near.pixmap().toImage(),
        (pos.x() - rect.x()) * view._detail_scale,
        (pos.y() - rect.y()) * view._detail_scale,
        5,
    )


def test_wide_sky_stays_one_picture(qapp) -> None:
    view = MoodMap()
    view.set_tracks([_ranked(_track(1, valence=0.5, energy=0.5))], None)
    _settle_zoom(view, 1.0)
    assert view._near.isVisible() is False
    assert view._field.opacity() == pytest.approx(1.0)
    assert view._region_fine is None
    assert view._region_shown is view._region_coarse


def test_medium_zoom_draws_a_sharper_patch(qapp) -> None:
    view = MoodMap()
    view.set_tracks(
        [
            _ranked(_track(1, valence=0.5, energy=0.5, mood_confidence=0.9)),
            _ranked(_track(2, valence=0.02, energy=0.02, mood_confidence=0.9)),
        ],
        None,
    )
    _settle_zoom(view, 2.0)
    assert NEAR_PATCH_ZOOM <= view._user_zoom < LIVE_STARS_ZOOM
    assert view._near.isVisible()
    assert view._field.opacity() == pytest.approx(0.0)
    assert view._near_rect is not None
    assert view._near_rect.width() < SCENE_W - 1
    assert _patch_alpha(view, 1) > 20
    key = view._near.pixmap().cacheKey()
    view._sync_near_patch()
    assert view._near.pixmap().cacheKey() == key
    view._begin_zoom_interaction()
    assert view._near.isVisible() is False
    assert view._field.opacity() == pytest.approx(1.0)
    view._end_zoom_interaction()
    view._sync_near_patch()
    assert view._near.isVisible()
    assert view._field.opacity() == pytest.approx(0.0)
    view._panning = True
    view._show_wide_stars()
    assert view._near.isVisible() is False
    assert view._field.opacity() == pytest.approx(1.0)
    view._panning = False
    view._sync_near_patch()
    assert view._near.isVisible()
    assert view._detail_name == "mid"
    assert view._detail_scale == NEAR_PATCH_SCALE
    assert view._region_shown is view._region_fine


def test_a_tighter_picture_replaces_the_mid_one(qapp) -> None:
    view = MoodMap()
    view.set_tracks(
        [_ranked(_track(1, valence=0.5, energy=0.5, mood_confidence=0.9))],
        None,
    )
    _settle_zoom(view, 2.3)
    assert view._detail_name == "tight"
    assert view._detail_scale > NEAR_PATCH_SCALE
    assert view._near.isVisible()
    assert view._field.opacity() == pytest.approx(0.0)
    assert _patch_alpha(view, 1) > 20
    tight = view._near.pixmap().cacheKey()
    _settle_zoom(view, 1.8)
    assert view._detail_name == "mid"
    assert view._detail_scale == NEAR_PATCH_SCALE
    assert view._near.pixmap().cacheKey() != tight
    assert view._near.isVisible()


def test_a_long_pan_redraws_the_sharper_patch(qapp) -> None:
    view = MoodMap()
    view.set_tracks([_ranked(_track(1, valence=0.5, energy=0.5))], None)
    _settle_zoom(view, 2.0)
    builds = {"n": 0}
    real = view._rebuild_near_patch

    def wrapped(seen) -> None:
        builds["n"] += 1
        real(seen)

    view._rebuild_near_patch = wrapped
    view._sync_near_patch()
    assert builds["n"] == 0
    view.centerOn(36, 40)
    view._sync_near_patch()
    assert builds["n"] == 1


def test_close_zoom_and_search_drop_the_sharper_patch(qapp) -> None:
    view = MoodMap()
    view.set_tracks([_ranked(_track(1, title="Needle", valence=0.5, energy=0.5))], None)
    _settle_zoom(view, 2.0)
    view.set_search_query("needle")
    assert view._near.isVisible() is False
    assert view._field.opacity() < 0.2
    view.set_search_query("")
    assert view._near.isVisible()
    assert view._field.opacity() == pytest.approx(0.0)
    _settle_zoom(view, 3.0)
    assert view._near.isVisible() is False
    assert view._field.opacity() == pytest.approx(0.0)


def test_sliding_star_is_left_out_of_the_sharper_patch(qapp) -> None:
    view = MoodMap()
    view.set_tracks(
        [
            _ranked(_track(1, valence=0.45, energy=0.5, mood_confidence=0.9)),
            _ranked(_track(2, valence=0.55, energy=0.5, mood_confidence=0.9)),
        ],
        None,
    )
    assert view.begin_listen_ease(1, 0.45, 0.5, 0.7, 0.7)
    view.set_tracks(
        [
            _ranked(_track(1, valence=0.7, energy=0.7, mood_confidence=0.9)),
            _ranked(_track(2, valence=0.55, energy=0.5, mood_confidence=0.9)),
        ],
        None,
    )
    _settle_zoom(view, 2.0)
    assert view._listen_ease is not None
    assert _patch_alpha(view, 1) == 0
    assert _patch_alpha(view, 2) > 20
