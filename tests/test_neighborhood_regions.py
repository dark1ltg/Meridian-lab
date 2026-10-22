"""Unlabelled neighborhood tiles break up a piled sky without moving songs."""

from __future__ import annotations

import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MERIDIAN_NO_GL", "1")

pytest.importorskip("PySide6")

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from meridian.library import Track
from meridian.queue_engine import Quadrant, RankedTrack
from meridian.ui.mood_map import (
    FIELD_SCALE,
    REGION_MARGIN,
    SCENE_H,
    SCENE_W,
    MoodMap,
    _nearest_site_ids,
    neighborhood_region_image,
    neighborhood_sites,
)


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


def _track(i: int, valence: float, energy: float) -> Track:
    return Track(
        id=i,
        path=f"/t/{i}.mp3",
        title="Song",
        artist="Artist",
        album="",
        albumartist="",
        genre="",
        duration_ms=180_000,
        year=None,
        bpm=None,
        valence=valence,
        energy=energy,
        mood_confidence=0.9,
        confidence_note="pcm",
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


def _ranked(track: Track) -> RankedTrack:
    return RankedTrack(track=track, fit=0.4, importance=0.4, urgency=0.4, quadrant=Quadrant.DEEP)


def test_nearest_sites_match_a_direct_search() -> None:
    rng = np.random.default_rng(3)
    screen = rng.uniform(0, 90, size=(28, 2)).astype(np.float32)
    width, height = 96, 64
    got = _nearest_site_ids(screen, width, height)
    xs = np.arange(width, dtype=np.float32)
    ys = np.arange(height, dtype=np.float32)
    dist = (xs[None, None, :] - screen[:, 0, None, None]) ** 2 + (
        ys[None, :, None] - screen[:, 1, None, None]
    ) ** 2
    assert np.array_equal(got, dist.argmin(axis=0))


def test_a_pile_becomes_neighborhoods_and_a_dive_splits_them() -> None:
    rng = np.random.default_rng(4)
    pile = np.column_stack(
        [
            rng.normal(400, 28, 500),
            rng.normal(300, 24, 500),
        ]
    ).astype(np.float32)
    arms = rng.uniform([40, 40], [760, 560], size=(80, 2)).astype(np.float32)
    points = np.vstack([pile, arms])
    coarse = neighborhood_sites(points, min_span=20, max_count=14, limit=900)
    fine = neighborhood_sites(points, min_span=8, max_count=3, limit=2000)
    assert 8 < len(coarse) < 120
    assert len(fine) > len(coarse)
    assert len(fine) < len(points)


def test_region_image_keeps_the_axis_margin_clear() -> None:
    points = np.array([[180.0, 160.0], [520.0, 420.0], [400.0, 300.0]], dtype=np.float32)
    image = neighborhood_region_image(points, min_span=20, max_count=1, limit=900)
    margin = int(REGION_MARGIN * FIELD_SCALE)
    assert image.width() == SCENE_W * FIELD_SCALE
    assert image.height() == SCENE_H * FIELD_SCALE
    corner = QColor.fromRgba(image.pixel(4, 4))
    assert corner.alpha() == 0
    body = QColor.fromRgba(image.pixel(image.width() // 2, margin + 8))
    assert body.alpha() > 0
    raw = np.frombuffer(image.constBits(), dtype=np.uint8, count=image.height() * image.bytesPerLine())
    pixels = raw.reshape(image.height(), image.bytesPerLine() // 4, 4)
    body = pixels[margin:-margin, margin:-margin]
    # Dark stroke: low red, high alpha.
    assert np.any((body[:, :, 0] < 80) & (body[:, :, 3] > 160))


def _densest(points: np.ndarray, sites: np.ndarray) -> int:
    dist = (points[:, None, 0] - sites[None, :, 0]) ** 2 + (
        points[:, None, 1] - sites[None, :, 1]
    ) ** 2
    return int(np.bincount(dist.argmin(axis=1), minlength=len(sites)).max())


def test_a_tight_knot_and_a_shared_dot_are_sliced() -> None:
    rng = np.random.default_rng(7)
    knot = np.column_stack(
        [rng.normal(400, 3, 140), rng.normal(300, 3, 140)]
    ).astype(np.float32)
    sites = neighborhood_sites(knot, min_span=20, max_count=14, limit=2000)
    assert len(sites) > 4
    assert _densest(knot, sites) < 40
    same = np.full((140, 2), [400.0, 300.0], dtype=np.float32)
    shared = neighborhood_sites(same, min_span=20, max_count=14, limit=2000)
    assert len(shared) > 4
    assert float(np.ptp(shared[:, 0])) > 4.0


def test_a_round_pile_is_not_a_square_waffle() -> None:
    rng = np.random.default_rng(4)
    pile = np.column_stack(
        [rng.normal(400, 28, 500), rng.normal(300, 24, 500)]
    ).astype(np.float32)
    sites = neighborhood_sites(pile, min_span=20, max_count=14, limit=2000)
    assert len(np.unique(np.round(sites[:, 0]))) > 15
    assert len(np.unique(np.round(sites[:, 1]))) > 15


def test_daytime_softens_tile_lines_and_a_pin_is_not_sliced() -> None:
    points = np.array([[180.0, 160.0], [520.0, 420.0], [400.0, 300.0]], dtype=np.float32)
    night = neighborhood_region_image(points, min_span=20, max_count=1, limit=900, haze=0.0)
    day = neighborhood_region_image(points, min_span=20, max_count=1, limit=900, haze=1.0)
    raw = np.frombuffer(night.constBits(), dtype=np.uint8, count=night.height() * night.bytesPerLine())
    pixels = raw.reshape(night.height(), night.bytesPerLine() // 4, 4)
    dark = np.argwhere((pixels[:, :, 0] < 80) & (pixels[:, :, 3] > 160))
    assert len(dark) > 0
    y, x = (int(dark[len(dark) // 2][0]), int(dark[len(dark) // 2][1]))
    day_raw = np.frombuffer(day.constBits(), dtype=np.uint8, count=day.height() * day.bytesPerLine())
    day_px = day_raw.reshape(day.height(), day.bytesPerLine() // 4, 4)
    assert int(day_px[y, x, 3]) < int(pixels[y, x, 3])
    scene = np.array([[x / FIELD_SCALE, y / FIELD_SCALE]], dtype=np.float32)
    spared = neighborhood_region_image(
        points, min_span=20, max_count=1, limit=900, protect=scene
    )
    spared_raw = np.frombuffer(
        spared.constBits(), dtype=np.uint8, count=spared.height() * spared.bytesPerLine()
    )
    spared_px = spared_raw.reshape(spared.height(), spared.bytesPerLine() // 4, 4)
    assert not (int(spared_px[y, x, 0]) < 80 and int(spared_px[y, x, 3]) > 160)


def test_lens_move_reuses_tiles_and_a_dive_uses_finer_ones(qapp) -> None:
    sky = MoodMap()
    sky.resize(960, 720)
    tracks = [
        _ranked(_track(1, 0.32, 0.70)),
        _ranked(_track(2, 0.48, 0.46)),
        _ranked(_track(3, 0.70, 0.30)),
        _ranked(_track(4, 0.55, 0.62)),
        _ranked(_track(5, 0.40, 0.22)),
    ]
    sky.set_tracks(tracks, None)
    coarse = sky._region_coarse
    assert coarse is not None and not coarse.isNull()
    assert sky._region_fine is None
    sky.set_lens(0.2, 0.8, 0.12)
    assert sky._region_coarse is coarse
    sky._user_zoom = 2.6
    sky._ensure_regions()
    assert sky._region_fine is not None
    assert sky._regions.pixmap().cacheKey() != coarse.cacheKey()
    sky.set_search_query("zzz-no-match")
    assert sky._regions.opacity() == pytest.approx(0.0)
    sky.set_search_query("")
    sky._lod_band = -1
    sky._sync_live_stars(force=True)
    assert sky._regions.opacity() == pytest.approx(0.0)
    assert sky._region_fine is not None
    assert sky._links.zValue() > sky._regions.zValue()
    calls: list[object] = []
    sky._regions.setPixmap = lambda pix: calls.append(pix)
    sky._user_zoom = 2.6
    sky.lens_moved()
    assert calls == []
    shown = sky._region_shown
    sky._falling = True
    for zoom in (2.4, 1.8, 1.2):
        sky._user_zoom = zoom
        sky._ensure_regions()
    assert sky._region_shown is shown
    sky._falling = False
    sky._user_zoom = 1.0
    sky._ensure_regions()
    assert sky._region_shown is sky._region_coarse
