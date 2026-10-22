from __future__ import annotations

from math import atan2, cos, hypot, log2, pi, sin
from time import monotonic

import numpy as np

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QImage,
    QMouseEvent,
    QNativeGestureEvent,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QGestureEvent,
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QMenu,
    QPinchGesture,
)

from meridian.context import LENS_RADIUS_DEFAULT, LENS_RADIUS_MAX, LENS_RADIUS_MIN
from meridian.queue_engine import QUADRANT_TITLE, Quadrant, RankedTrack
from meridian.ui.fonts import sans
from meridian.features import CONFIDENCE_HIGH, CONFIDENCE_LOW, read_mood_confidence, track_is_unplaced
from meridian.ui.palette import (
    LOW_TRUST_QCOLOR,
    MID_CONFIDENCE_OPACITY,
    PIN_HEAD_RADIUS,
    PIN_RING_QCOLOR,
    PIN_RING_WIDTH,
    PLAYLIST_QCOLOR,
    STAR_RADIUS,
    STAR_Z,
)

# Night sky. The field is quiet: white, or pewter for a weak guess.
# Playlist color is only for stars inside the lens.
LOVED_RING_QCOLOR = QColor("#fff4c2")
# Readable at fit-to-widget, still smaller than the old ~6px playlist coin.
STAR_BODY_SCALE = 0.50
STAR_BODY_FLOOR = 2.45
STAR_BODY_FLOOR_DENSE = 2.15


def _star_mag(track_id: int) -> float:
    return ((track_id * 2654435761) & 0xFFFF) / 65535.0


def apply_timbre_mark(
    color: QColor,
    radius: float,
    bloom: int,
    timbre: str | None,
    *,
    current: bool,
) -> tuple[QColor, float, int]:
    """A clear tone stays a hard point. A noisy tone is softer paint, same size."""
    if current or timbre not in ("tonal", "noisy"):
        return color, radius, bloom
    marked = QColor(color)
    if timbre == "tonal":
        marked.setAlpha(min(255, max(marked.alpha(), 235)))
        return marked, radius, 0
    # Softer paint only. Same size as every other star, and no extra glow.
    marked.setAlpha(max(70, int(marked.alpha() * 0.72)))
    return marked, radius, int(bloom)


def quiet_star_color(band: int) -> QColor:
    """Library star outside the lens. Trust is white versus pewter, not a queue hue."""
    if band <= 0:
        color = QColor(LOW_TRUST_QCOLOR)
        color.setAlpha(170)
        return color
    if band == 1:
        return QColor(214, 216, 228, 200)
    return QColor(236, 238, 246, 230)


def queue_star_color(quadrant: Quadrant, *, loved: bool, band: int) -> QColor:
    """Playlist hue for a star inside the lens. Low trust leans pewter."""
    playlist = QColor(PLAYLIST_QCOLOR[quadrant])
    if band <= 0:
        pewter = QColor(LOW_TRUST_QCOLOR)
        color = QColor(
            int(pewter.red() * 0.72 + playlist.red() * 0.28),
            int(pewter.green() * 0.72 + playlist.green() * 0.28),
            int(pewter.blue() * 0.72 + playlist.blue() * 0.28),
        )
        color.setAlpha(210)
        return color
    color = playlist
    if loved:
        color = color.lighter(118)
    color.setAlpha(250 if band >= 2 else 220)
    return color


def night_body_radius(
    quadrant: Quadrant,
    *,
    band: int,
    mag: float,
    loved: bool,
    pinned: bool,
    current: bool,
    density: bool,
    duration_ms: int | None = None,
) -> float:
    """Scene-pixel radius. Large libraries stay visible without turning back into coins."""
    radius = STAR_RADIUS[quadrant] * STAR_BODY_SCALE * (0.86 + 0.22 * mag)
    if band <= 0:
        radius *= 0.90
    elif band >= 2:
        radius *= 1.06
    if loved:
        radius += 0.35
    if pinned:
        radius += 0.28
    if current:
        radius = max(radius, 3.5)
    if density:
        radius *= 0.94
    span = duration_radius_scale(duration_ms)
    radius *= span
    floor = STAR_BODY_FLOOR_DENSE if density else STAR_BODY_FLOOR
    if band <= 0:
        floor *= 0.92
    # A short song may fall below the usual floor so it reads as a speck.
    if span < 1.0:
        floor *= span
    return max(floor, radius)


def duration_radius_scale(duration_ms: int | None) -> float:
    """Map length onto star size. About 3.5 minutes stays at 1; shorts shrink, longs grow."""
    if duration_ms is None or duration_ms <= 0:
        return 1.0
    scale = (max(1, duration_ms) / 210_000) ** 0.45
    return max(0.36, min(1.65, scale))


def sky_haze(band: str) -> float:
    """0 is a crisp night. 1 is a hazy day. Dawn and dusk sit between."""
    return {"night": 0.0, "dusk": 0.38, "dawn": 0.5, "day": 1.0}.get((band or "night").lower(), 0.0)


# One second of slide after a finish or skip. A search trip fades sooner.
LISTEN_EASE_S = 1.0
SEARCH_CORRIDOR_S = 0.65


def ease_amount(elapsed: float, duration: float) -> float:
    """Smooth 0..1. The star eases, then settles. It does not jump."""
    if duration <= 0.0:
        return 1.0
    t = max(0.0, min(1.0, float(elapsed) / float(duration)))
    return t * t * (3.0 - 2.0 * t)


def heartbeat_amount(phase: float) -> float:
    """Lub-dub strength, 0 to 1. ``phase`` runs 0..1 across one beat."""
    phase = phase % 1.0

    def hump(center: float, width: float, height: float) -> float:
        d = abs(phase - center) / width
        if d >= 1.0:
            return 0.0
        return height * (0.5 + 0.5 * cos(d * pi))

    return hump(0.10, 0.09, 1.0) + hump(0.28, 0.07, 0.62)


NIGHT_BG = QColor("#07010f")
SKY_LINK_K = 3
SKY_LINK_MIN = 14.0
SKY_LINK_MAX = 118.0
# Reject a second link that leaves the same star along almost the same bearing.
SKY_LINK_ANGLE = 0.65


def constellation_segments(
    points: list[tuple[int, float, float]],
) -> list[tuple[float, float, float, float, float]]:
    """Nearest-neighbor links, as (x1, y1, x2, y2, distance).

    A cell is one max-link wide, so the 3×3 around a star holds every song
    close enough to connect. Crowded cells are searched in full.
    """
    count = len(points)
    if count < 2:
        return []
    ids = [point[0] for point in points]
    xs = np.empty(count, dtype=np.float64)
    ys = np.empty(count, dtype=np.float64)
    for index, (_, x, y) in enumerate(points):
        xs[index] = x
        ys[index] = y
    cell = SKY_LINK_MAX
    buckets: dict[tuple[int, int], list[int]] = {}
    for index in range(count):
        key = (int(xs[index] // cell), int(ys[index] // cell))
        buckets.setdefault(key, []).append(index)
    seen: set[tuple[int, int]] = set()
    segments: list[tuple[float, float, float, float, float]] = []
    for (gx, gy), members in buckets.items():
        neigh: list[int] = []
        for ox in (-1, 0, 1):
            for oy in (-1, 0, 1):
                neigh.extend(buckets.get((gx + ox, gy + oy), ()))
        if len(neigh) < 2:
            continue
        neigh_idx = np.asarray(neigh, dtype=np.int32)
        member_idx = np.asarray(members, dtype=np.int32)
        dist = np.hypot(
            xs[member_idx, None] - xs[neigh_idx],
            ys[member_idx, None] - ys[neigh_idx],
        )
        dist[(dist < SKY_LINK_MIN) | (dist > SKY_LINK_MAX)] = np.inf
        order = np.argsort(dist, axis=1)
        for row, mi in enumerate(member_idx):
            bearings: list[float] = []
            kept = 0
            px = float(xs[mi])
            py = float(ys[mi])
            tid = ids[int(mi)]
            for col in order[row]:
                span = float(dist[row, col])
                if span == np.inf:
                    break
                other_i = int(neigh_idx[int(col)])
                bearing = atan2(float(ys[other_i]) - py, float(xs[other_i]) - px)
                if any(
                    abs((bearing - prev + pi) % (2 * pi) - pi) < SKY_LINK_ANGLE
                    for prev in bearings
                ):
                    continue
                bearings.append(bearing)
                other = ids[other_i]
                edge = (tid, other) if tid < other else (other, tid)
                if edge not in seen:
                    seen.add(edge)
                    segments.append(
                        (px, py, float(xs[other_i]), float(ys[other_i]), span)
                    )
                kept += 1
                if kept >= SKY_LINK_K:
                    break
    return segments


# A closer line gathers a shape. A long line stays a line, so two figures do not become one.
BIN_SPAN = 56.0
BIN_MIN = 4
BIN_REACH = 26.0
BIN_WASHES = (
    (186, 164, 230, 108),
    (142, 176, 214, 104),
    (214, 164, 176, 100),
    (154, 196, 186, 102),
    (176, 176, 214, 106),
)


def _bin_key(x: float, y: float) -> tuple[float, float]:
    return (round(float(x), 2), round(float(y), 2))


def _find_bin(parent: dict[tuple[float, float], tuple[float, float]], key: tuple[float, float]) -> tuple[float, float]:
    while parent[key] != key:
        parent[key] = parent[parent[key]]
        key = parent[key]
    return key


def constellation_groups(
    segments: list[tuple[float, float, float, float, float]],
) -> list[list[tuple[float, float]]]:
    """Songs a closer constellation line already joins.

    Nothing here is moved. Songs closer than the lines are willing to draw
    stay out, and a long line does not glue two figures into one shape.
    """
    parent: dict[tuple[float, float], tuple[float, float]] = {}

    def adopt(key: tuple[float, float]) -> tuple[float, float]:
        parent.setdefault(key, key)
        return _find_bin(parent, key)

    for x1, y1, x2, y2, dist in segments:
        if dist < SKY_LINK_MIN or dist > BIN_SPAN:
            continue
        left = _bin_key(x1, y1)
        right = _bin_key(x2, y2)
        if left == right:
            continue
        root_left = adopt(left)
        root_right = adopt(right)
        if root_left != root_right:
            parent[root_right] = root_left
    grouped: dict[tuple[float, float], list[tuple[float, float]]] = {}
    for key in parent:
        grouped.setdefault(_find_bin(parent, key), []).append(key)
    figures = [points for points in grouped.values() if len(points) >= BIN_MIN]
    figures.sort(
        key=lambda points: (
            sum(point[0] for point in points) / len(points),
            sum(point[1] for point in points) / len(points),
        )
    )
    return figures


def constellation_bin_image(
    groups: list[list[tuple[float, float]]],
    haze: float = 0.0,
) -> QImage:
    """Unmarked washes. Overlaps stay one strength, and no name is drawn."""
    width = SCENE_W * FIELD_SCALE
    height = SCENE_H * FIELD_SCALE
    image = np.zeros((height, width, 4), dtype=np.uint8)
    soften = 1.0 - 0.62 * float(np.clip(haze, 0.0, 1.0))
    if groups and soften > 0.04:
        reach = BIN_REACH * FIELD_SCALE
        rim = 4.0 * FIELD_SCALE
        span = int(np.ceil(reach + rim)) + 1
        yy, xx = np.ogrid[-span : span + 1, -span : span + 1]
        dist2 = xx.astype(np.float32) ** 2 + yy.astype(np.float32) ** 2
        inner = dist2 <= reach * reach
        outer = (dist2 <= (reach + rim) ** 2) & ~inner
        for index, points in enumerate(groups):
            red, green, blue, alpha = BIN_WASHES[index % len(BIN_WASHES)]
            fill_a = int(round(alpha * soften))
            rim_a = int(round(fill_a * 0.45))
            stamp = np.zeros((span * 2 + 1, span * 2 + 1, 4), dtype=np.uint8)
            stamp[outer] = (red, green, blue, rim_a)
            stamp[inner] = (red, green, blue, fill_a)
            for x, y in points:
                cx = int(round(x * FIELD_SCALE))
                cy = int(round(y * FIELD_SCALE))
                x0 = cx - span
                y0 = cy - span
                x1 = cx + span + 1
                y1 = cy + span + 1
                sx0 = 0
                sy0 = 0
                if x0 < 0:
                    sx0 = -x0
                    x0 = 0
                if y0 < 0:
                    sy0 = -y0
                    y0 = 0
                if x1 > width:
                    x1 = width
                if y1 > height:
                    y1 = height
                if x0 >= x1 or y0 >= y1:
                    continue
                patch = stamp[sy0 : sy0 + (y1 - y0), sx0 : sx0 + (x1 - x0)]
                dest = image[y0:y1, x0:x1]
                stronger = patch[:, :, 3] > dest[:, :, 3]
                dest[stronger] = patch[stronger]
    qimg = QImage(image.data, width, height, int(image.strides[0]), QImage.Format.Format_RGBA8888)
    return qimg.copy()


# View magnification on top of fit-to-widget (1 = whole night sky).
VIEW_ZOOM_MIN = 1.0
VIEW_ZOOM_MAX = 14.0
VIEW_WHEEL_FACTOR = 1.06
VIEW_NATIVE_OUTLIER = 1.25
# Below this zoom, stars are one baked texture (smooth pinch). Above: live items in view.
LIVE_STARS_ZOOM = 2.4
LOD_LABEL_START = 2.6
LOD_GLOW_START = 2.4
# Wide-sky furniture (weather, cross, axis labels, constellations) is gone
# once live stars and their names take over.
LOD_CHROME_FADE_START = 1.15
LOD_CHROME_FADE_END = LIVE_STARS_ZOOM
CLUSTER_PULL = 0.42
CLUSTER_GRID = 48.0
LOD_DEFER_MS = 48
LABEL_CAP = 48
LIVE_STAR_CAP = 220          # max interactive stars while zoomed in
# Songs closer than this share one dot. Zoom cannot pull an exact overlap apart.
STACK_POINT = 1.0
# A halo this close to this many other songs is what turns a pile into fog.
CROWD_RADIUS = 18.0
CROWD_NEIGHBORS = 6
FIELD_SCALE = 2              # bake retina-ish starfield
SCENE_W = 800
SCENE_H = 600
# Neighborhood tiles. Coarse on the whole sky, finer only after the dive.
# A cell is a group of songs, so the white pile breaks into areas instead of slivers.
REGION_COARSE_SPAN = 20.0
REGION_COARSE_COUNT = 14
REGION_COARSE_LIMIT = 2000
REGION_FINE_SPAN = 8.0
REGION_FINE_COUNT = 3
REGION_FINE_LIMIT = 4000
REGION_MARGIN = 28.0
HIT_RADIUS_SKY = 14.0
# Tighter than hover/snap — avoids turning a nearby pan into an accidental pin.
HIT_RADIUS_SKY_GRAB = 7.0
SKY_DRAG_SLOP = 8.0


def _span_of(chunk: np.ndarray) -> float:
    if chunk.shape[0] == 0:
        return 0.0
    return float(
        max(
            float(chunk[:, 0].max() - chunk[:, 0].min()),
            float(chunk[:, 1].max() - chunk[:, 1].min()),
        )
    )


def _jitter_site(center: np.ndarray) -> np.ndarray:
    """Nudge a lone neighborhood so a round pile does not become graph paper."""
    hx = int(round(float(center[0]) * 10.0))
    hy = int(round(float(center[1]) * 10.0))
    mix = (hx * 73856093) ^ (hy * 19349663)
    ang = ((mix & 1023) / 1023.0) * (2.0 * pi)
    mag = 1.2 + ((mix >> 10) & 255) / 255.0 * 2.2
    return center + np.array([np.cos(ang), np.sin(ang)], dtype=np.float32) * np.float32(mag)


def _fan_sites(chunk: np.ndarray, max_count: int) -> list[np.ndarray]:
    """Slice a stack the grid could not split. Songs that share one dot get a ring of sites."""
    count = int(chunk.shape[0])
    groups = int(np.ceil(count / max(1, max_count)))
    groups = max(2, min(16, groups))
    center = chunk.mean(axis=0).astype(np.float32)
    if _span_of(chunk) < 1.5:
        radius = float(min(11.0, 3.6 + 1.15 * np.sqrt(groups)))
        return [
            center
            + np.array([np.cos(ang), np.sin(ang)], dtype=np.float32) * np.float32(radius)
            for index in range(groups)
            for ang in (2.0 * pi * (index + 0.5) / groups,)
        ]
    delta = chunk - center
    order = np.argsort(np.arctan2(delta[:, 1], delta[:, 0]))
    edges = np.linspace(0, count, groups + 1, dtype=np.int32)
    return [chunk[order[edges[i] : edges[i + 1]]].mean(axis=0).astype(np.float32) for i in range(groups)]


def crowd_ids(
    points: list[tuple[int, float, float]],
    *,
    radius: float = CROWD_RADIUS,
    minimum: int = CROWD_NEIGHBORS,
) -> set[int]:
    """Ids with at least `minimum` other songs inside `radius` scene pixels."""
    if len(points) <= minimum:
        return set()
    ids = [tid for tid, _x, _y in points]
    pts = np.array([[x, y] for _tid, x, y in points], dtype=np.float32)
    cell = float(radius)
    buckets: dict[tuple[int, int], list[int]] = {}
    for index, (x, y) in enumerate(pts):
        buckets.setdefault((int(x // cell), int(y // cell)), []).append(index)
    reach = cell * cell
    crowded: set[int] = set()
    for index, (x, y) in enumerate(pts):
        cx, cy = int(x // cell), int(y // cell)
        near = 0
        for gx in range(cx - 1, cx + 2):
            for gy in range(cy - 1, cy + 2):
                for other in buckets.get((gx, gy), ()):
                    if other == index:
                        continue
                    dx = float(pts[other, 0] - x)
                    dy = float(pts[other, 1] - y)
                    if dx * dx + dy * dy <= reach:
                        near += 1
                        if near >= minimum:
                            crowded.add(ids[index])
                            break
                if ids[index] in crowded:
                    break
        if near >= minimum:
            crowded.add(ids[index])
    return crowded


def neighborhood_sites(
    points: np.ndarray,
    *,
    min_span: float,
    max_count: int,
    limit: int,
) -> np.ndarray:
    """One site per neighborhood. Crowded stacks get several sites so a line can cross them."""
    if points.size == 0:
        return np.zeros((0, 2), dtype=np.float32)
    pts = np.asarray(points, dtype=np.float32).reshape(-1, 2)

    def split(span_now: float, budget: int) -> list[np.ndarray]:
        stack: list[tuple[np.ndarray, int]] = [(pts, 0)]
        found: list[np.ndarray] = []
        while stack:
            chunk, depth = stack.pop()
            count = int(chunk.shape[0])
            if count == 0:
                continue
            center = chunk.mean(axis=0).astype(np.float32)
            if count <= budget or _span_of(chunk) <= span_now or depth >= 12:
                if count > budget:
                    found.extend(_fan_sites(chunk, budget))
                else:
                    found.append(_jitter_site(center))
                continue
            axis = int(np.argmax(chunk.max(axis=0) - chunk.min(axis=0)))
            order = np.argpartition(chunk[:, axis], count // 2)
            mid = count // 2
            stack.append((chunk[order[mid:]], depth + 1))
            stack.append((chunk[order[:mid]], depth + 1))
        return found

    span = float(min_span)
    budget = int(max_count)
    found = split(span, budget)
    while len(found) > limit and budget < 64:
        budget = int(budget * 1.35) + 1
        found = split(span, budget)
    if not found:
        return np.zeros((0, 2), dtype=np.float32)
    return np.vstack(found).astype(np.float32)


def _nearest_site_ids(screen: np.ndarray, width: int, height: int) -> np.ndarray:
    """Pixel → nearest site. Buckets first, then widen until the nearest site is inside the search."""
    count = int(screen.shape[0])
    ids = np.zeros((height, width), dtype=np.int32)
    if count <= 1 or width <= 0 or height <= 0:
        return ids
    bucket = 64
    n_bx = (width + bucket - 1) // bucket
    n_by = (height + bucket - 1) // bucket
    buckets: list[list[int]] = [[] for _ in range(n_bx * n_by)]
    clipped_x = np.clip(screen[:, 0], 0, width - 1)
    clipped_y = np.clip(screen[:, 1], 0, height - 1)
    cell_x = np.minimum(clipped_x.astype(np.int32) // bucket, n_bx - 1)
    cell_y = np.minimum(clipped_y.astype(np.int32) // bucket, n_by - 1)
    for index, (cx, cy) in enumerate(zip(cell_x.tolist(), cell_y.tolist())):
        buckets[int(cy) * n_bx + int(cx)].append(index)
    max_radius = max(n_bx, n_by)

    def gather(tx: int, ty: int, radius: int) -> list[int]:
        found: list[int] = []
        x0 = max(0, tx - radius)
        x1 = min(n_bx - 1, tx + radius)
        y0 = max(0, ty - radius)
        y1 = min(n_by - 1, ty + radius)
        for y in range(y0, y1 + 1):
            row = y * n_bx
            for x in range(x0, x1 + 1):
                found.extend(buckets[row + x])
        return found

    for ty in range(n_by):
        y0 = ty * bucket
        y1 = min(height, y0 + bucket)
        ys = np.arange(y0, y1, dtype=np.float32)
        for tx in range(n_bx):
            x0 = tx * bucket
            x1 = min(width, x0 + bucket)
            xs = np.arange(x0, x1, dtype=np.float32)
            chosen: np.ndarray | None = None
            radius = 1
            while radius <= max_radius:
                cand = gather(tx, ty, radius)
                if not cand:
                    radius += 1
                    continue
                cand_index = np.asarray(cand, dtype=np.int32)
                pts = screen[cand_index]
                dist = (xs[None, None, :] - pts[:, 0, None, None]) ** 2 + (
                    ys[None, :, None] - pts[:, 1, None, None]
                ) ** 2
                local = dist.argmin(axis=0)
                near = np.take_along_axis(dist, local[None, ...], axis=0)[0]
                sx0 = max(0, (tx - radius) * bucket)
                sx1 = min(width, (tx + radius + 1) * bucket)
                sy0 = max(0, (ty - radius) * bucket)
                sy1 = min(height, (ty + radius + 1) * bucket)
                left = xs - sx0 if sx0 > 0 else np.full(xs.shape, np.inf)
                right = sx1 - xs if sx1 < width else np.full(xs.shape, np.inf)
                top = ys - sy0 if sy0 > 0 else np.full(ys.shape, np.inf)
                bottom = sy1 - ys if sy1 < height else np.full(ys.shape, np.inf)
                clearance = np.minimum(
                    np.minimum(left, right)[None, :],
                    np.minimum(top, bottom)[:, None],
                )
                if bool(np.all(near <= np.square(clearance) + 1e-3)):
                    chosen = cand_index[local]
                    break
                radius += 1
            if chosen is None:
                dist = (xs[None, None, :] - screen[:, 0, None, None]) ** 2 + (
                    ys[None, :, None] - screen[:, 1, None, None]
                ) ** 2
                chosen = dist.argmin(axis=0)
            ids[y0:y1, x0:x1] = chosen
    return ids


def neighborhood_region_image(
    points: np.ndarray,
    *,
    min_span: float,
    max_count: int,
    limit: int,
    haze: float = 0.0,
    protect: np.ndarray | None = None,
) -> QImage:
    """Unlabelled mood tiles. Dark lines cut a white pile; light lines read on the night sky."""
    width = SCENE_W * FIELD_SCALE
    height = SCENE_H * FIELD_SCALE
    image = np.zeros((height, width, 4), dtype=np.uint8)
    sites = neighborhood_sites(points, min_span=min_span, max_count=max_count, limit=limit)
    if sites.shape[0] == 0:
        qimg = QImage(image.data, width, height, int(image.strides[0]), QImage.Format.Format_RGBA8888)
        return qimg.copy()
    screen = np.empty_like(sites)
    screen[:, 0] = sites[:, 0] * FIELD_SCALE
    screen[:, 1] = sites[:, 1] * FIELD_SCALE
    ids = _nearest_site_ids(screen, width, height)
    haze = float(np.clip(haze, 0.0, 1.0))
    soften = 1.0 - 0.68 * haze
    mist = np.array([206.0, 214.0, 232.0], dtype=np.float32)
    valence = np.clip((sites[:, 0] - 40.0) / 720.0, 0.0, 1.0)
    energy = np.clip((560.0 - sites[:, 1]) / 524.0, 0.0, 1.0)
    fill = np.zeros((sites.shape[0], 4), dtype=np.uint8)
    rgb = np.stack(
        (
            np.clip(168 + 58 * valence, 0, 255),
            np.clip(162 + 24 * energy, 0, 255),
            np.clip(214 - 46 * valence + 16 * energy, 0, 255),
        ),
        axis=1,
    ).astype(np.float32)
    rgb = rgb * (1.0 - 0.7 * haze) + mist * (0.7 * haze)
    fill[:, :3] = np.clip(rgb, 0, 255).astype(np.uint8)
    fill[:, 3] = np.uint8(np.clip(round(62 * soften), 18, 62))
    painted = fill[ids]
    image[:] = painted
    if sites.shape[0] > 1:
        edge = np.zeros((height, width), dtype=bool)
        edge[:, 1:] = ids[:, 1:] != ids[:, :-1]
        edge[1:, :] |= ids[1:, :] != ids[:-1, :]
        thick = edge.copy()
        thick[:, 1:] |= edge[:, :-1]
        thick[1:, :] |= edge[:-1, :]
        if protect is not None and len(protect):
            radius = 8.0 * FIELD_SCALE
            reach = int(radius) + 2
            for sx, sy in np.asarray(protect, dtype=np.float32).reshape(-1, 2):
                cx = float(sx) * FIELD_SCALE
                cy = float(sy) * FIELD_SCALE
                x0 = max(0, int(cx) - reach)
                x1 = min(width, int(cx) + reach + 1)
                y0 = max(0, int(cy) - reach)
                y1 = min(height, int(cy) + reach + 1)
                if x1 <= x0 or y1 <= y0:
                    continue
                xs = np.arange(x0, x1, dtype=np.float32)
                ys = np.arange(y0, y1, dtype=np.float32)
                disc = (xs[None, :] - cx) ** 2 + (ys[:, None] - cy) ** 2 <= radius * radius
                edge[y0:y1, x0:x1] &= ~disc
                thick[y0:y1, x0:x1] &= ~disc
        light = (
            int(232 * (1.0 - haze) + 206 * haze),
            int(226 * (1.0 - haze) + 214 * haze),
            int(255 * (1.0 - haze) + 232 * haze),
            int(210 * soften),
        )
        dark = (
            int(24 * (1.0 - haze) + 90 * haze),
            int(16 * (1.0 - haze) + 98 * haze),
            int(42 * (1.0 - haze) + 120 * haze),
            int(185 * soften),
        )
        image[thick] = light
        image[edge] = dark
    margin = int(REGION_MARGIN * FIELD_SCALE)
    if margin > 0:
        image[:margin, :, 3] = 0
        image[-margin:, :, 3] = 0
        image[:, :margin, 3] = 0
        image[:, -margin:, 3] = 0
    qimg = QImage(image.data, width, height, int(image.strides[0]), QImage.Format.Format_RGBA8888)
    return qimg.copy()


class TrackStar(QGraphicsEllipseItem):
    def __init__(self, ranked: RankedTrack, parent=None) -> None:
        r = STAR_RADIUS[ranked.quadrant]
        super().__init__(-r, -r, r * 2, r * 2, parent)
        self.ranked = ranked
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setAcceptHoverEvents(True)
        self.setToolTip(ranked.track.label)
        self._map: MoodMap | None = None
        self._press_scene: QPointF | None = None

        self._glow = QGraphicsEllipseItem(self)
        self._glow.setPen(QPen(Qt.PenStyle.NoPen))
        self._glow.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._glow.setZValue(-1)
        self._glow.hide()

        # Lock ring + tack head — visible only when the track is pinned.
        self._pin_ring = QGraphicsEllipseItem(self)
        self._pin_ring.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        self._pin_ring.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._pin_ring.setZValue(2)
        self._pin_ring.hide()
        self._pin_head = QGraphicsEllipseItem(self)
        self._pin_head.setPen(QPen(Qt.PenStyle.NoPen))
        self._pin_head.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._pin_head.setZValue(3)
        self._pin_head.hide()

        self._label = QGraphicsSimpleTextItem(self)
        self._label.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
        # Accept clicks so double-click on the title still reaches the star.
        self._label.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
        self._label.setZValue(30)
        self._label.setBrush(QBrush(QColor("#e8ecf7")))
        self._label.setFont(sans(10))
        self._label.hide()
        self._count = QGraphicsSimpleTextItem(self)
        self._count.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
        self._count.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._count.setZValue(32)
        self._count.setBrush(QBrush(QColor("#f7f4ff")))
        self._count.setFont(sans(9, medium=True))
        self._count.hide()
        self._role = QGraphicsSimpleTextItem(self)
        self._role.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
        self._role.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
        self._role.setZValue(31)
        self._role.setFont(sans(8, medium=True))
        self._role.hide()
        self._lod_key = (-1, False, False)
        self._body_scale = 1.0
        self._hovering = False
        self._search_hot = False
        self._stack_ids: list[int] = []
        self._stack_focus_id: int | None = None

        self.apply_style(ranked, current=False)

    def set_body_scale(self, scale: float, *, hot: bool | None = None) -> None:
        """Keep a zoomed-in star the size it had at the dive. The map grows, the dot does not."""
        self._body_scale = max(0.05, float(scale))
        if hot is not None:
            self._search_hot = hot
        factor = 1.0
        if self._hovering:
            factor = 1.45
        elif self._search_hot:
            factor = 1.35
        self.setScale(self._body_scale * factor)

    def set_stack(self, ids: list[int]) -> None:
        """One dot for songs that share a point, with a count when there is more than one."""
        self._stack_ids = list(ids)
        if len(self._stack_ids) < 2:
            self._count.hide()
            return
        count = len(self._stack_ids)
        self._count.setText(str(count) if count < 100 else "99+")
        box = self._count.boundingRect()
        radius = self.rect().width() * 0.5
        self._count.setPos(radius * 0.15, -radius - box.height() * 0.15)
        self._count.show()

    def shape(self) -> QPainterPath:
        # Live stars: hit close to the drawn body so dense clusters stay distinguishable.
        # (Sky-mode picking uses HIT_RADIUS_SKY on the baked field, not this shape.)
        path = QPainterPath()
        r = self.rect().width() * 0.5 + 4.0
        path.addEllipse(QPointF(0.0, 0.0), r, r)
        return path

    def apply_style(self, ranked: RankedTrack, current: bool) -> None:
        self.ranked = ranked
        conf = read_mood_confidence(ranked.track)
        # Pins are locked truth — always draw as high-trust body color.
        if ranked.track.pinned or current:
            band = "high"
        elif conf >= CONFIDENCE_HIGH:
            band = "high"
        elif conf >= CONFIDENCE_LOW:
            band = "mid"
        else:
            band = "low"
        band_n = {"low": 0, "mid": 1, "high": 2}[band]
        # Same body as the baked sky: visible at fit zoom, not a 6px coin.
        r = night_body_radius(
            ranked.quadrant,
            band=band_n,
            mag=_star_mag(ranked.track.id),
            loved=bool(ranked.track.loved),
            pinned=bool(ranked.track.pinned),
            current=current,
            density=False,
            duration_ms=getattr(ranked.track, "duration_ms", None),
        )
        self.setRect(-r, -r, r * 2, r * 2)
        inside = self._inside_lens(ranked)
        color = (
            queue_star_color(ranked.quadrant, loved=bool(ranked.track.loved), band=band_n)
            if inside
            else quiet_star_color(band_n)
        )
        if current:
            color.setAlpha(255)
        # Pewter is the trust signal. A light opacity ease keeps a weak guess quieter
        # without the old half-opacity cut that hid the body.
        if band == "low":
            self.setOpacity(0.9)
        elif band == "mid":
            self.setOpacity(MID_CONFIDENCE_OPACITY)
        else:
            self.setOpacity(1.0)
        host = getattr(self, "_map", None)
        haze = sky_haze(getattr(host, "_sky_band", "night") if host is not None else "night")
        if haze > 0.05:
            self.setOpacity(self.opacity() * (1.0 - 0.28 * haze))
        timbre = getattr(ranked.track, "timbre_flag", None)
        if timbre == "tonal" and not current:
            self.setOpacity(min(1.0, max(self.opacity(), 0.95)))
        elif timbre == "noisy" and not current:
            self.setOpacity(self.opacity() * 0.72)
        self.setBrush(QBrush(color))

        pinned = bool(ranked.track.pinned)
        if current:
            pen = QPen(QColor("#ffffff"), 1.2)
            pen.setCosmetic(True)
            self.setPen(pen)
            self.setZValue(14)
        elif pinned:
            self.setPen(QPen(Qt.PenStyle.NoPen))
            self.setZValue(STAR_Z[ranked.quadrant] + 3)
        elif ranked.track.loved:
            pen = QPen(LOVED_RING_QCOLOR, 1.35)
            pen.setCosmetic(True)
            self.setPen(pen)
            self.setZValue(STAR_Z[ranked.quadrant] + 1)
        elif band == "low":
            self.setPen(QPen(Qt.PenStyle.NoPen))
            self.setZValue(max(1, STAR_Z[ranked.quadrant] - 2))
        else:
            self.setPen(QPen(Qt.PenStyle.NoPen))
            self.setZValue(STAR_Z[ranked.quadrant])

        if pinned:
            ring_pad = 3.2 if current else 2.6
            self._pin_ring.setRect(-r - ring_pad, -r - ring_pad, (r + ring_pad) * 2, (r + ring_pad) * 2)
            ring_pen = QPen(PIN_RING_QCOLOR, PIN_RING_WIDTH)
            ring_pen.setCosmetic(True)
            self._pin_ring.setPen(ring_pen)
            self._pin_ring.show()
            hr = PIN_HEAD_RADIUS
            self._pin_head.setRect(-hr, -r - ring_pad - hr * 2.2, hr * 2, hr * 2)
            self._pin_head.setBrush(QBrush(PIN_RING_QCOLOR))
            self._pin_head.show()
        else:
            self._pin_ring.hide()
            self._pin_head.hide()

        tip = f"{ranked.track.label}\n{ranked.quadrant.value.upper()}"
        tip += f"\nconfidence {conf:.2f}"
        if pinned:
            tip += " — pinned (locked by you)"
        elif band == "low":
            tip += " — weak guess"
        elif band == "mid":
            tip += " — partial evidence"
        note = (getattr(ranked.track, "confidence_note", "") or "").strip()
        if note:
            tip += f"\n{note}"
        self.setToolTip(tip)

        glow_r = r * (3.8 if pinned else 3.4) * (1.0 + 0.55 * haze)
        glow_c = QColor(color)
        if pinned:
            glow_c.setAlpha(110)
        else:
            glow_c.setAlpha(40 if band == "low" else (65 if band == "mid" else 90))
        if timbre == "tonal" and not current and not pinned:
            glow_c.setAlpha(0)
        self._glow.setRect(-glow_r, -glow_r, glow_r * 2, glow_r * 2)
        self._glow.setBrush(QBrush(glow_c))

        title = ranked.track.label
        if " — " in title:
            title = title.split(" — ", 1)[-1]
        if len(title) > 28:
            title = title[:27] + "…"
        self._label.setText(title)
        role = QUADRANT_TITLE[ranked.quadrant]
        self._role.setText(role)
        role_box = self._role.boundingRect()
        self._role.setPos(-role_box.width() / 2, r + (8 if pinned else 3))
        role_ink = QColor(PLAYLIST_QCOLOR[ranked.quadrant])
        self._role.setBrush(QBrush(role_ink))
        br = self._label.boundingRect()
        self._label.setPos(-br.width() / 2, r + role_box.height() + (6 if pinned else 2))
        if inside:
            label_ink = QColor(PLAYLIST_QCOLOR[ranked.quadrant])
            if current or pinned:
                label_ink = label_ink.lighter(140)
        elif band == "low":
            label_ink = QColor("#9AA8C0")
        else:
            label_ink = QColor("#e8ecf7")
        self._label.setBrush(QBrush(label_ink))

    def _inside_lens(self, ranked: RankedTrack) -> bool:
        if self._map is None:
            return False
        pos = self._map._positions.get(ranked.track.id)
        if pos is None:
            return False
        return self._map.star_in_lens(pos)

    def set_lod(self, glow_a: float, label_on: bool) -> None:
        show_glow = glow_a > 0.02
        key = (int(glow_a * 20), show_glow, label_on)
        if key == self._lod_key:
            return
        self._lod_key = key
        if show_glow:
            self._glow.setOpacity(0.55 * glow_a)
            self._glow.show()
        else:
            self._glow.hide()
        if label_on:
            self._label.show()
            self._role.show()
        else:
            self._label.hide()
            self._role.hide()

    def hoverEnterEvent(self, event) -> None:
        self._hovering = True
        self.set_body_scale(self._body_scale)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:
        self._hovering = False
        self.set_body_scale(self._body_scale)
        super().hoverLeaveEvent(event)

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange and self.scene():
            pos: QPointF = value
            pos.setX(max(40.0, min(760.0, pos.x())))
            pos.setY(max(36.0, min(560.0, pos.y())))
            return pos
        if (
            change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged
            and self._map is not None
            and self._press_scene is not None
        ):
            self._map._update_drag_position(self.ranked.track.id, self.pos())
        return super().itemChange(change, value)

    def mousePressEvent(self, event) -> None:
        self._press_scene = event.scenePos()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        if not self._map:
            return
        dragged = False
        if self._press_scene is not None:
            delta = event.scenePos() - self._press_scene
            dragged = hypot(delta.x(), delta.y()) > 8
        self._press_scene = None
        if dragged:
            self._map.star_moved(self)
        elif len(self._stack_ids) > 1 and self._map._user_zoom >= LIVE_STARS_ZOOM:
            self._map.prompt_stack(self, event.screenPos())
        else:
            self._map.snap_lens_to_star(self)


class LensItem(QGraphicsEllipseItem):
    def __init__(self) -> None:
        super().__init__(-90, -90, 180, 180)
        # Soft pool plus a wide glow, then a bright ring and a fixed center dot
        # so the marker still reads when stars and links cross it.
        self.setBrush(QBrush(QColor(232, 224, 255, 36)))
        glow = QPen(QColor(214, 204, 245, 110), 9.0)
        glow.setCosmetic(True)
        glow.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        glow.setCapStyle(Qt.PenCapStyle.RoundCap)
        self.setPen(glow)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        # Below live stars (z≥7) so stars under the lens stay clickable / draggable.
        self.setZValue(6)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self._map: MoodMap | None = None
        ring = QPen(QColor(255, 252, 255, 255), 2.6)
        ring.setCosmetic(True)
        ring.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        ring.setCapStyle(Qt.PenCapStyle.RoundCap)
        self._halo = QGraphicsEllipseItem(-90, -90, 180, 180, self)
        self._halo.setPen(ring)
        self._halo.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        self._halo.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._halo.setZValue(1)
        self._mark = QGraphicsEllipseItem(-4.0, -4.0, 8.0, 8.0, self)
        self._mark.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
        mark_pen = QPen(QColor(18, 10, 32, 180), 1.2)
        mark_pen.setCosmetic(True)
        self._mark.setPen(mark_pen)
        self._mark.setBrush(QBrush(QColor(255, 252, 255, 245)))
        self._mark.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._mark.setZValue(2)

    def setRect(self, *args) -> None:
        super().setRect(*args)
        self._halo.setRect(self.rect())

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange:
            pos: QPointF = value
            # Keep the lens center on the map panel (ellipse may spill past edges).
            pos.setX(max(40.0, min(760.0, pos.x())))
            pos.setY(max(36.0, min(560.0, pos.y())))
            return pos
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged and self._map:
            self._map.lens_moved()
        return super().itemChange(change, value)


class SkyLifeOverlay(QGraphicsItem):
    """Heartbeat, play ripple, and search flares. Drawn over the baked sky."""

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setZValue(5.85)
        self._host: MoodMap | None = None

    def boundingRect(self) -> QRectF:
        return QRectF(0, 0, SCENE_W, SCENE_H)

    def shape(self) -> QPainterPath:
        return QPainterPath()

    def contains(self, point: QPointF) -> bool:
        return False

    def paint(self, painter: QPainter, option, widget=None) -> None:
        if self._host is not None:
            self._host.paint_sky_life(painter)


class MoodMap(QGraphicsView):
    """Mood sky: baked starfield for smooth pinch; live stars only when zoomed in."""

    lens_changed = Signal(float, float, float)
    track_pinned = Signal(int, float, float)
    track_activated = Signal(int)
    track_hovered = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setBackgroundBrush(QBrush(NIGHT_BG))
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.grabGesture(Qt.GestureType.PinchGesture)
        self._gpu_enabled = self._enable_opengl_viewport()
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Highlight, QColor("#6D4AFF"))
        pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#0b1020"))
        self.setPalette(pal)
        self._scene = QGraphicsScene(0, 0, SCENE_W, SCENE_H, self)
        self._scene.setPalette(pal)
        self.setScene(self._scene)
        self._ranked: dict[int, RankedTrack] = {}
        self._positions: dict[int, QPointF] = {}
        self._crowd_ids: set[int] = set()
        self._current_id: int | None = None
        self._sky_band = "night"
        self._search_query = ""
        self._search_ids: set[int] = set()
        self._playing = False
        self._tracks_shown = False
        self._life_prev: QRectF | None = None
        self._ripple_origin: QPointF | None = None
        self._ripple_neighbors: list[tuple[QPointF, float]] = []
        self._ripple_start = 0.0
        self._ripple_until = 0.0
        # track id, start scene point, end scene point, began, until
        self._listen_ease: tuple[int, QPointF, QPointF, float, float] | None = None
        self._search_corridor: tuple[QPointF, QPointF, float, float] | None = None
        self._falling = False
        self._fall_from = VIEW_ZOOM_MIN
        self._fall_t = 0.0
        self._stars: dict[int, TrackStar] = {}
        self._sky_chrome: list[QGraphicsItem] = []
        self._star_grid: dict[tuple[int, int], list[tuple[int, QPointF]]] = {}
        self._radius = LENS_RADIUS_DEFAULT
        self._radius_scale = 1.0  # mode bias; ring shows radius * scale
        self._user_zoom = VIEW_ZOOM_MIN
        self._pointer_scene: QPointF | None = None
        self._zoom_target = VIEW_ZOOM_MIN
        self._zoom_anchor = QPoint()
        self._pinch_zoom0 = VIEW_ZOOM_MIN
        self._prefer_native_zoom = False
        self._native_zoom_clear = None
        self._panning = False
        self._hand_drag_armed = False  # ScrollHandDrag press still needs a matching release
        self._zoom_active = False
        self._lod_band = -1
        self._cluster_once = True
        # Keep one live star visible in sky mode while the user presses/drags it.
        self._sky_hold_id: int | None = None
        self._sky_press_scene: QPointF | None = None
        self._sky_dragged = False
        self._sky_candidate_id: int | None = None
        # Press on star core — wait for move before pan vs pin (avoids viewport nudge).
        self._sky_defer_pan = False
        self._field_sig: object | None = None
        self._region_pos_key: tuple | None = None
        self._region_coarse: QPixmap | None = None
        self._region_fine: QPixmap | None = None
        self._region_shown: QPixmap | None = None
        self._link_cache_key: tuple | None = None
        self._link_cache: list[tuple[float, float, float, float, float]] = []
        self._baked_link_key: tuple | None = None
        self._baked_links: QImage | None = None
        self._pending_snap_id: int | None = None
        self._snap_timer = QTimer(self)
        self._snap_timer.setSingleShot(True)
        self._snap_timer.setInterval(220)
        self._snap_timer.timeout.connect(self._flush_pending_snap)
        self._lod_timer = QTimer(self)
        self._lod_timer.setSingleShot(True)
        self._lod_timer.setInterval(LOD_DEFER_MS)
        self._lod_timer.timeout.connect(self._flush_zoom_lod)
        self._pointer_timer = QTimer(self)
        self._pointer_timer.setSingleShot(True)
        self._pointer_timer.setInterval(80)
        self._pointer_timer.timeout.connect(self._sync_pointer_stars)
        self._life_timer = QTimer(self)
        self._life_timer.setInterval(33)
        self._life_timer.timeout.connect(self._tick_life)
        self._fall_timer = QTimer(self)
        self._fall_timer.setInterval(16)
        self._fall_timer.timeout.connect(self._tick_fall)
        self._sky_bg = None
        self._draw_backdrop()
        self._links = QGraphicsPixmapItem()
        self._links.setZValue(5.76)
        self._links.setShapeMode(QGraphicsPixmapItem.ShapeMode.BoundingRectShape)
        self._links.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._scene.addItem(self._links)
        self._bins = QGraphicsPixmapItem()
        # Between the tiles and the lines: a shape, then the lines that gathered it.
        self._bins.setZValue(5.755)
        self._bins.setShapeMode(QGraphicsPixmapItem.ShapeMode.BoundingRectShape)
        self._bins.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._scene.addItem(self._bins)
        self._field = QGraphicsPixmapItem()
        self._field.setZValue(5)
        self._field.setShapeMode(QGraphicsPixmapItem.ShapeMode.BoundingRectShape)
        self._field.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._scene.addItem(self._field)
        self._regions = QGraphicsPixmapItem()
        # Above the baked stars and the lens pool, so the lines cut the white pile.
        self._regions.setZValue(5.75)
        self._regions.setShapeMode(QGraphicsPixmapItem.ShapeMode.BoundingRectShape)
        self._regions.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._scene.addItem(self._regions)
        self._route = QGraphicsPixmapItem()
        self._route.setZValue(5.78)
        self._route.setShapeMode(QGraphicsPixmapItem.ShapeMode.BoundingRectShape)
        self._route.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._scene.addItem(self._route)
        self._lens_color = QGraphicsPixmapItem()
        self._lens_color.setZValue(5.6)
        self._lens_color.setShapeMode(QGraphicsPixmapItem.ShapeMode.BoundingRectShape)
        self._lens_color.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._scene.addItem(self._lens_color)
        self._flare = QGraphicsPixmapItem()
        self._flare.setZValue(5.82)
        self._flare.setShapeMode(QGraphicsPixmapItem.ShapeMode.BoundingRectShape)
        self._flare.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._scene.addItem(self._flare)
        self._life = SkyLifeOverlay()
        self._life._host = self
        self._scene.addItem(self._life)
        self._queue_order: list[int] = []
        self.lens = LensItem()
        self._scene.addItem(self.lens)
        self.lens.setPos(420, 300)
        self.lens._map = self
        self._apply_lens_rect()

    def _enable_opengl_viewport(self) -> bool:
        import os

        from meridian.gl_viewport import ensure_hardware_gl

        # Default is hardware. ensure_hardware_gl probes in a child process and
        # sets MERIDIAN_NO_GL when the driver cannot create a context.
        if not ensure_hardware_gl():
            return False
        try:
            from PySide6.QtGui import QSurfaceFormat
            from PySide6.QtOpenGLWidgets import QOpenGLWidget
        except ImportError:
            os.environ["MERIDIAN_NO_GL"] = "1"
            os.environ["MERIDIAN_GL_PROBE"] = "fail"
            return False
        try:
            gl = QOpenGLWidget(self)
            fmt = QSurfaceFormat()
            fmt.setRenderableType(QSurfaceFormat.RenderableType.OpenGL)
            fmt.setSwapBehavior(QSurfaceFormat.SwapBehavior.DoubleBuffer)
            fmt.setSwapInterval(1)
            fmt.setSamples(4)
            gl.setFormat(fmt)
            self.setViewport(gl)
            self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
            self.setOptimizationFlag(QGraphicsView.OptimizationFlag.DontSavePainterState, True)
            self.setOptimizationFlag(
                QGraphicsView.OptimizationFlag.DontAdjustForAntialiasing, True
            )
            return True
        except Exception:
            os.environ["MERIDIAN_NO_GL"] = "1"
            os.environ["MERIDIAN_GL_PROBE"] = "fail"
            return False

    def release_gpu_viewport(self) -> None:
        """Swap out QOpenGLWidget before window teardown to avoid driver abort on quit."""
        if not getattr(self, "_gpu_enabled", False):
            return
        from PySide6.QtWidgets import QWidget

        try:
            plain = QWidget()
            self.setViewport(plain)
            self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.MinimalViewportUpdate)
        except Exception:
            pass
        self._gpu_enabled = False

    def _draw_backdrop(self) -> None:
        """Violet night sky. Corners carry the mood weather; the cross is the only seam."""
        for item in self._sky_chrome:
            self._scene.removeItem(item)
        self._sky_chrome.clear()
        if self._sky_bg is not None:
            self._scene.removeItem(self._sky_bg)
            self._sky_bg = None
        sky = QRadialGradient(SCENE_W * 0.5, SCENE_H * 0.48, SCENE_W * 0.78)
        sky.setColorAt(0.0, QColor("#1b0c32"))
        sky.setColorAt(0.55, QColor("#11071c"))
        sky.setColorAt(1.0, QColor("#07010f"))
        item = self._scene.addRect(
            QRectF(0, 0, SCENE_W, SCENE_H),
            QPen(Qt.PenStyle.NoPen),
            QBrush(sky),
        )
        item.setZValue(0)
        item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._sky_bg = item

        def add_weather(cx: float, cy: float, radius: float, color: QColor) -> None:
            grad = QRadialGradient(cx, cy, radius)
            grad.setColorAt(0.0, color)
            edge = QColor(color)
            edge.setAlpha(0)
            grad.setColorAt(1.0, edge)
            pool = self._scene.addEllipse(
                cx - radius,
                cy - radius,
                radius * 2,
                radius * 2,
                QPen(Qt.PenStyle.NoPen),
                QBrush(grad),
            )
            pool.setZValue(0.4)
            pool.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            self._sky_chrome.append(pool)

        # Shadow/Still is the cold corner. Glow/Kinetic is the warm horizon.
        add_weather(130, 500, 280, QColor(64, 108, 168, 82))
        add_weather(690, 100, 300, QColor(206, 124, 62, 74))
        add_weather(140, 110, 220, QColor(46, 28, 96, 58))
        add_weather(670, 500, 240, QColor(128, 64, 96, 50))

        # The middle cross is the Shadow|Glow and Still|Kinetic split.
        cross_glow = QPen(QColor(220, 214, 240, 80), 6.0)
        cross_glow.setCosmetic(True)
        cross_glow.setCapStyle(Qt.PenCapStyle.RoundCap)
        cross = QPen(QColor(236, 230, 248, 190), 1.6)
        cross.setCosmetic(True)
        cross.setCapStyle(Qt.PenCapStyle.RoundCap)

        def add_guide(x1: float, y1: float, x2: float, y2: float, pen: QPen) -> None:
            guide = self._scene.addLine(x1, y1, x2, y2, pen)
            guide.setZValue(1)
            guide.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            self._sky_chrome.append(guide)

        y = 36 + 524 * 2 / 4
        x = 40 + 720 * 2 / 4
        add_guide(40, y, 760, y, cross_glow)
        add_guide(x, 36, x, 560, cross_glow)
        add_guide(40, y, 760, y, cross)
        add_guide(x, 36, x, 560, cross)
        font = sans(9)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.8)
        ink = QBrush(QColor(201, 192, 224, 150))
        for x, y, text in ((400, 18, "KINETIC  ↑"), (400, 584, "STILL  ↓")):
            label = self._scene.addSimpleText(text, font)
            label.setBrush(ink)
            label.setPos(x - label.boundingRect().width() / 2, y - 8)
            label.setZValue(2)
            label.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            self._sky_chrome.append(label)
        for x, y, text, angle in ((16, 298, "SHADOW", -90), (784, 298, "GLOW", 90)):
            label = self._scene.addSimpleText(text, font)
            label.setBrush(ink)
            bounds = label.boundingRect()
            label.setTransformOriginPoint(bounds.center())
            label.setRotation(angle)
            label.setPos(x - bounds.width() / 2, y - bounds.height() / 2)
            label.setZValue(2)
            label.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            self._sky_chrome.append(label)

    def mood_to_pos(self, valence: float, energy: float) -> QPointF:
        return QPointF(40 + valence * 720, 560 - energy * 524)

    def pos_to_mood(self, pos: QPointF) -> tuple[float, float]:
        valence = (pos.x() - 40) / 720
        energy = (560 - pos.y()) / 524
        return max(0.0, min(1.0, valence)), max(0.0, min(1.0, energy))

    def set_tracks(self, ranked: list[RankedTrack], current_id: int | None) -> None:
        previous = self._current_id
        self._current_id = current_id
        placed = [
            r
            for r in ranked
            if r.track.id == current_id or not track_is_unplaced(r.track)
        ]
        self._ranked = {r.track.id: r for r in placed}
        ranked = placed
        # Keep in-progress pin drags so play/nudge/refresh cannot snap the star back.
        locked = self._drag_locked_ids()
        preserved: dict[int, QPointF] = {}
        for tid in locked:
            star = self._stars.get(tid)
            if star is not None:
                preserved[tid] = QPointF(star.pos())
            elif tid in self._positions:
                preserved[tid] = QPointF(self._positions[tid])
        # Drop live stars that vanished; keep others until LOD sync.
        for tid in list(self._stars):
            if tid not in self._ranked:
                self._scene.removeItem(self._stars.pop(tid))
                if self._sky_hold_id == tid:
                    self._sky_hold_id = None
        # Never keep drag positions for tracks that left the ranked set (KeyError guard).
        self._positions = {
            r.track.id: self.mood_to_pos(r.track.valence, r.track.energy) for r in ranked
        }
        for tid, pos in preserved.items():
            if tid in self._ranked:
                self._positions[tid] = pos
            elif self._sky_hold_id == tid:
                self._sky_hold_id = None
            elif self._sky_candidate_id == tid:
                self._sky_candidate_id = None
                self._sky_press_scene = None
                self._sky_defer_pan = False
        self._hold_listen_ease()
        self._rebuild_star_grid()
        self._refresh_crowd()
        sig = self._field_signature()
        if sig != self._field_sig:
            self._field_sig = sig
            self._rebuild_starfield()
        self._refresh_lens_colors()
        self._refresh_route()
        self._lod_band = -1
        self._sync_live_stars(force=True)
        # The first draw can already have a song loaded. That is not a new play.
        if (
            self._tracks_shown
            and current_id is not None
            and current_id != previous
            and current_id in self._positions
        ):
            self._begin_ripple(current_id)
        self._tracks_shown = True
        if self._search_query:
            self._rematch_search()
            self._apply_search_veil()
        self._rebuild_search_flares()
        self._arm_life()

    def set_sky_band(self, band: str) -> None:
        """Night stays crisp. Day softens the stars. Dawn and dusk sit between."""
        band = (band or "night").strip().lower()
        if band == self._sky_band:
            return
        self._sky_band = band
        if not self._ranked:
            return
        sig = self._field_signature()
        if sig != self._field_sig:
            self._field_sig = sig
            self._rebuild_starfield()
            self._refresh_lens_colors()
        self._lod_band = -1
        self._sync_live_stars(force=True)

    def set_search_query(self, text: str) -> None:
        """A query blacks out the sky and flares the matches. Blank restores it."""
        query = text.strip().lower()
        if query == self._search_query:
            return
        self._search_query = query
        self._rematch_search()
        self._lod_band = -1
        self._sync_live_stars(force=True)
        if self._ranked:
            self._refresh_lens_colors()
            self._refresh_route()
        self._rebuild_search_flares()
        self._arm_life()

    def _rematch_search(self) -> None:
        self._search_ids = set()
        query = self._search_query
        if not query:
            return
        for item in self._ranked.values():
            track = item.track
            blob = f"{track.title}\n{track.artist}\n{track.album}\n{track.path}".lower()
            if query in blob:
                self._search_ids.add(track.id)

    def _apply_search_veil(self) -> None:
        if not self._search_query:
            for star in self._stars.values():
                if star._search_hot:
                    star.set_body_scale(star._body_scale, hot=False)
            self._hide_close_smear()
            return
        self._field.setOpacity(0.06)
        self._regions.setOpacity(0.06)
        self._links.setOpacity(0.04)
        self._bins.setOpacity(0.04)
        self._lens_color.setOpacity(0.16)
        self._route.setOpacity(0.2)
        for star in self._stars.values():
            if star.ranked.track.id in self._search_ids:
                star.setOpacity(1.0)
                star.set_body_scale(star._body_scale, hot=True)
            else:
                star.setOpacity(0.05)
                star.set_body_scale(star._body_scale, hot=False)
        self._hide_close_smear()

    def _hide_close_smear(self) -> None:
        """Up close, drop the faded pile and the tile lines so the live stars stand alone."""
        if self._user_zoom < LIVE_STARS_ZOOM or self._zoom_active or self._falling:
            return
        self._field.setOpacity(0.0)
        self._regions.setOpacity(0.0)
        self._links.setOpacity(0.0)
        self._bins.setOpacity(0.0)
        self._lens_color.setOpacity(0.0)

    def set_playing(self, playing: bool) -> None:
        """The heartbeat follows sound, not merely which song is loaded."""
        playing = bool(playing)
        if playing == self._playing:
            return
        self._playing = playing
        self._arm_life()

    def _rebuild_search_flares(self) -> None:
        """Bake match flares once. A broad search must not redraw them every frame."""
        if not self._search_query or not self._search_ids:
            self._flare.setPixmap(QPixmap())
            self._flare.hide()
            return
        image = QImage(SCENE_W, SCENE_H, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        for tid in self._search_ids:
            pos = self._positions.get(tid)
            item = self._ranked.get(tid)
            if pos is None or item is None:
                continue
            radius, color, _bloom = self._night_star(item, False)
            flare = QColor(color)
            flare.setAlpha(90)
            painter.setBrush(QBrush(flare))
            painter.drawEllipse(pos, radius * 7.5, radius * 7.5)
            hot = QColor(255, 248, 236, 235)
            painter.setBrush(QBrush(hot))
            painter.drawEllipse(pos, max(2.4, radius * 1.2), max(2.4, radius * 1.2))
        painter.end()
        self._flare.setPixmap(QPixmap.fromImage(image))
        self._flare.setPos(0, 0)
        self._flare.show()

    def _pulse_rect(self) -> QRectF | None:
        if not self._playing or self._current_id is None:
            return None
        pos = self._positions.get(self._current_id)
        item = self._ranked.get(self._current_id)
        if pos is None or item is None:
            return None
        radius, _color, _bloom = self._night_star(item, False)
        pad = radius * 5.2 + 6.0
        return QRectF(pos.x() - pad, pos.y() - pad, pad * 2.0, pad * 2.0)

    def _ripple_rect(self) -> QRectF | None:
        if self._ripple_origin is None or monotonic() >= self._ripple_until:
            return None
        reach = 170.0
        origin = self._ripple_origin
        return QRectF(origin.x() - reach, origin.y() - reach, reach * 2.0, reach * 2.0)

    def _span_rect(self, a: QPointF, b: QPointF, pad: float) -> QRectF:
        left = min(a.x(), b.x()) - pad
        top = min(a.y(), b.y()) - pad
        return QRectF(left, top, abs(b.x() - a.x()) + pad * 2.0, abs(b.y() - a.y()) + pad * 2.0)

    def _ease_rect(self) -> QRectF | None:
        if self._listen_ease is None:
            return None
        _tid, start, end, _t0, _until = self._listen_ease
        return self._span_rect(start, end, 18.0)

    def _corridor_rect(self) -> QRectF | None:
        if self._search_corridor is None:
            return None
        start, end, _t0, _until = self._search_corridor
        return self._span_rect(start, end, 12.0)

    def _invalidate_life(self) -> None:
        """Redraw only the pulse, ripple, ease, and search trip, not the whole sky."""
        pulse = self._pulse_rect()
        ripple = self._ripple_rect()
        current: QRectF | None = None
        for piece in (pulse, ripple, self._ease_rect(), self._corridor_rect()):
            if piece is None:
                continue
            current = piece if current is None else current.united(piece)
        previous = self._life_prev
        self._life_prev = current
        if current is None and previous is None:
            return
        dirty = current if previous is None else previous if current is None else current.united(previous)
        self._life.update(dirty)

    def _begin_ripple(self, track_id: int) -> None:
        origin = self._positions.get(track_id)
        if origin is None:
            return
        neighbors: list[tuple[QPointF, float]] = []
        for other, pos in self._positions.items():
            if other == track_id:
                continue
            dist = hypot(pos.x() - origin.x(), pos.y() - origin.y())
            if dist <= 130.0:
                neighbors.append((QPointF(pos), dist))
        neighbors.sort(key=lambda pair: pair[1])
        self._ripple_origin = QPointF(origin)
        self._ripple_neighbors = neighbors[:40]
        self._ripple_start = monotonic()
        self._ripple_until = self._ripple_start + 0.62

    def _life_busy(self) -> bool:
        now = monotonic()
        pulsing = self._playing and self._current_id is not None
        rippling = now < self._ripple_until
        easing = self._listen_ease is not None
        corridor = self._search_corridor is not None and now < self._search_corridor[3]
        return pulsing or rippling or easing or corridor

    def _arm_life(self) -> None:
        if self._life_busy():
            if not self._life_timer.isActive():
                self._life_timer.start()
        else:
            self._life_timer.stop()
        self._invalidate_life()

    def _tick_life(self) -> None:
        self._advance_listen_ease()
        if self._search_corridor is not None and monotonic() >= self._search_corridor[3]:
            self._search_corridor = None
        if not self._life_busy():
            self._life_timer.stop()
        self._invalidate_life()

    def paint_sky_life(self, painter: QPainter) -> None:
        """Pulse, ripple, a nudged star, and the search trip. Flares stay a still picture."""
        now = monotonic()
        self._paint_listen_ease(painter, now)
        self._paint_search_corridor(painter, now)
        painter.setPen(Qt.PenStyle.NoPen)
        if self._ripple_origin is not None and now < self._ripple_until:
            span = max(0.001, self._ripple_until - self._ripple_start)
            age = now - self._ripple_start
            t = min(1.0, age / span)
            ring = QPen(QColor(232, 236, 248, int(170 * (1.0 - t))), 1.5)
            ring.setCosmetic(True)
            painter.setPen(ring)
            painter.setBrush(QBrush(Qt.BrushStyle.NoBrush))
            painter.drawEllipse(self._ripple_origin, 8.0 + t * 140.0, 8.0 + t * 140.0)
            painter.setPen(Qt.PenStyle.NoPen)
            for pos, dist in self._ripple_neighbors:
                local = age - dist / 250.0
                if local < 0.0 or local > 0.28:
                    continue
                bump = sin(local / 0.28 * pi)
                painter.setBrush(QBrush(QColor(255, 255, 255, int(130 * bump))))
                painter.drawEllipse(pos, 1.5 + 3.0 * bump, 1.5 + 3.0 * bump)
        searching_past = self._search_query and self._current_id not in self._search_ids
        if self._playing and self._current_id and not searching_past:
            pos = self._positions.get(self._current_id)
            item = self._ranked.get(self._current_id)
            if pos is not None and item is not None:
                beat = heartbeat_amount((now % 1.15) / 1.15)
                radius, color, _bloom = self._night_star(item, False)
                glow = QColor(color)
                glow.setAlpha(int(36 + 150 * beat))
                painter.setBrush(QBrush(glow))
                painter.drawEllipse(
                    pos,
                    radius * (2.3 + 2.4 * beat),
                    radius * (2.3 + 2.4 * beat),
                )

    def _blocks_listen_ease(self) -> bool:
        """A drag already owns the sky. Let the star land without a slide."""
        return bool(self._drag_locked_ids()) or self._panning or self._sky_dragged or self._hand_drag_armed

    def begin_listen_ease(
        self,
        track_id: int,
        old_valence: float,
        old_energy: float,
        new_valence: float,
        new_energy: float,
    ) -> bool:
        """Slide one unpinned star from the spot it just left. Pins never call this."""
        if self._blocks_listen_ease():
            return False
        start = self.mood_to_pos(old_valence, old_energy)
        end = self.mood_to_pos(new_valence, new_energy)
        if hypot(end.x() - start.x(), end.y() - start.y()) < 0.8:
            return False
        now = monotonic()
        self._listen_ease = (int(track_id), start, end, now, now + LISTEN_EASE_S)
        self._arm_life()
        return True

    def _hold_listen_ease(self) -> None:
        """Keep a nudged star on its slide when the sky refreshes."""
        ease = self._listen_ease
        if ease is None:
            return
        tid, start, end, t0, until = ease
        if tid not in self._positions or tid in self._drag_locked_ids():
            self._listen_ease = None
            return
        now = monotonic()
        if now >= until:
            self._positions[tid] = QPointF(end)
            self._listen_ease = None
            return
        t = ease_amount(now - t0, until - t0)
        self._positions[tid] = QPointF(
            start.x() + (end.x() - start.x()) * t,
            start.y() + (end.y() - start.y()) * t,
        )

    def _advance_listen_ease(self) -> None:
        ease = self._listen_ease
        if ease is None:
            return
        tid, start, end, t0, until = ease
        if tid not in self._positions:
            self._listen_ease = None
            self._rebuild_star_grid()
            return
        if tid in self._drag_locked_ids():
            # The press owns the star. Paint it back where it is, or the sky loses it.
            self._listen_ease = None
            self._stamp_eased_star(tid)
            self._rebuild_star_grid()
            self._field_sig = self._field_signature()
            return
        now = monotonic()
        if now >= until:
            self._positions[tid] = QPointF(end)
            self._listen_ease = None
            star = self._stars.get(tid)
            if star is not None:
                star.setPos(end)
            self._stamp_eased_star(tid)
            self._rebuild_star_grid()
            self._refresh_lens_colors()
            self._field_sig = self._field_signature()
            return
        t = ease_amount(now - t0, until - t0)
        pos = QPointF(
            start.x() + (end.x() - start.x()) * t,
            start.y() + (end.y() - start.y()) * t,
        )
        self._positions[tid] = pos
        self._rebuild_star_grid()
        star = self._stars.get(tid)
        if star is not None:
            star.setPos(pos)

    def _paint_listen_ease(self, painter: QPainter, now: float) -> None:
        ease = self._listen_ease
        if ease is None:
            return
        tid, start, end, t0, until = ease
        t = ease_amount(now - t0, until - t0)
        pos = QPointF(
            start.x() + (end.x() - start.x()) * t,
            start.y() + (end.y() - start.y()) * t,
        )
        ghost = QColor(LOW_TRUST_QCOLOR)
        ghost.setAlpha(int(36 + 150 * (1.0 - t)))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(ghost))
        painter.drawEllipse(start, 2.2, 2.2)
        star = self._stars.get(tid)
        if star is not None and star.isVisible():
            return
        item = self._ranked.get(tid)
        if item is None:
            return
        self._paint_baked_star(painter, item, pos, len(self._ranked) > 1200)
        if self.star_in_lens(pos):
            radius, _quiet, _bloom = self._night_star(item, False)
            band = self._confidence_band(item)
            color = queue_star_color(item.quadrant, loved=bool(item.track.loved), band=band)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(color))
            painter.drawEllipse(pos, radius, radius)
            if item.track.loved and not item.track.pinned and tid != self._current_id:
                ring = QPen(LOVED_RING_QCOLOR, 1.2)
                painter.setPen(ring)
                painter.setBrush(QBrush(Qt.BrushStyle.NoBrush))
                painter.drawEllipse(pos, radius + 1.35, radius + 1.35)

    def begin_search_corridor(self, valence: float, energy: float) -> bool:
        """A short trip from the lens to the song search just chose. Not the queue path."""
        start = QPointF(self.lens.pos())
        end = self.mood_to_pos(valence, energy)
        if hypot(end.x() - start.x(), end.y() - start.y()) < 6.0:
            # Drop a line that is already on screen, and repaint that spot.
            self._search_corridor = None
            self._invalidate_life()
            return False
        now = monotonic()
        self._search_corridor = (start, end, now, now + SEARCH_CORRIDOR_S)
        self._arm_life()
        return True

    def _paint_search_corridor(self, painter: QPainter, now: float) -> None:
        trip = self._search_corridor
        if trip is None:
            return
        start, end, t0, until = trip
        if now >= until:
            return
        t = ease_amount(now - t0, until - t0)
        ink = QColor(168, 206, 214, int(210 * (1.0 - t)))
        pen = QPen(ink, 1.35)
        pen.setCosmetic(True)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setDashPattern([2.0, 5.0])
        painter.setPen(pen)
        painter.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        painter.drawLine(start, end)

    def _model_pos(self, tid: int) -> QPointF:
        """Lines, tiles, and the crowd use the spot the slide is heading to."""
        ease = self._listen_ease
        if ease is not None and tid == ease[0]:
            return ease[2]
        return self._positions[tid]

    def _paint_baked_star(self, painter: QPainter, item: RankedTrack, pos: QPointF, density: bool) -> None:
        """One quiet star: body, loved ring, pin. Scene coordinates."""
        radius, color, bloom = self._night_star(item, density)
        haze = sky_haze(self._sky_band)
        painter.setPen(Qt.PenStyle.NoPen)
        if haze > 0.05:
            mist = QColor(206, 214, 232)
            mist.setAlpha(int(22 + 50 * haze))
            painter.setBrush(QBrush(mist))
            painter.drawEllipse(pos, radius * (2.4 + 1.8 * haze), radius * (2.4 + 1.8 * haze))
            color = QColor(color)
            color.setAlpha(max(48, int(color.alpha() * (1.0 - 0.32 * haze))))
        if bloom:
            glow = QColor(color)
            glow.setAlpha(bloom)
            painter.setBrush(QBrush(glow))
            painter.drawEllipse(pos, radius * 3.4, radius * 3.4)
        painter.setBrush(QBrush(color))
        painter.drawEllipse(pos, radius, radius)
        if item.track.loved and not item.track.pinned and item.track.id != self._current_id:
            loved_ring = QPen(LOVED_RING_QCOLOR, 1.2)
            painter.setPen(loved_ring)
            painter.setBrush(QBrush(Qt.BrushStyle.NoBrush))
            painter.drawEllipse(pos, radius + 1.35, radius + 1.35)
            painter.setPen(Qt.PenStyle.NoPen)
        if item.track.pinned:
            ring = QPen(PIN_RING_QCOLOR, 1.05)
            painter.setPen(ring)
            painter.setBrush(QBrush(Qt.BrushStyle.NoBrush))
            painter.drawEllipse(pos, radius + 2.2, radius + 2.2)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(PIN_RING_QCOLOR))
            painter.drawEllipse(
                QPointF(pos.x(), pos.y() - radius - 2.6),
                PIN_HEAD_RADIUS * 0.85,
                PIN_HEAD_RADIUS * 0.85,
            )

    def _stamp_eased_star(self, tid: int) -> None:
        """Paint one settled star onto the sky. Avoids redrawing the whole field."""
        item = self._ranked.get(tid)
        pos = self._positions.get(tid)
        pix = self._field.pixmap()
        if item is None or pos is None or pix.isNull():
            if tid in self._ranked:
                self._rebuild_starfield()
            return
        image = pix.toImage()
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.scale(FIELD_SCALE, FIELD_SCALE)
        self._paint_baked_star(painter, item, pos, len(self._ranked) > 1200)
        painter.end()
        self._field.setPixmap(QPixmap.fromImage(image))

    def _field_signature(self) -> object:
        """Cheap fingerprint so play/nudge/lens refresh can skip a full rebake."""
        parts: list[tuple] = []
        easing_id = self._listen_ease[0] if self._listen_ease is not None else None
        for tid in sorted(self._ranked):
            r = self._ranked[tid]
            # The slide moves every frame. The bake stays put until the star settles.
            if self._listen_ease is not None and tid == easing_id:
                p = self._listen_ease[2]
            else:
                p = self._positions[tid]
            conf = read_mood_confidence(r.track)
            parts.append(
                (
                    tid,
                    round(p.x(), 1),
                    round(p.y(), 1),
                    bool(r.track.loved),
                    bool(r.track.pinned),
                    round(conf, 2),
                    int(getattr(r.track, "duration_ms", 0) or 0),
                    str(getattr(r.track, "timbre_flag", "") or ""),
                    tid == easing_id,
                )
            )
        return (self._current_id, self._sky_band, easing_id, tuple(parts))

    def _drag_locked_ids(self) -> set[int]:
        """Track ids currently being pressed/dragged — positions must not reset."""
        locked: set[int] = set()
        if self._sky_hold_id is not None:
            locked.add(self._sky_hold_id)
        if self._sky_candidate_id is not None:
            locked.add(self._sky_candidate_id)
        for tid, star in self._stars.items():
            if star._press_scene is not None:
                locked.add(tid)
        return locked

    def _rebuild_starfield(self) -> None:
        """Bake every track into one pixmap — pinch then only scales this texture."""
        w, h = SCENE_W * FIELD_SCALE, SCENE_H * FIELD_SCALE
        img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        painter = QPainter(img)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.scale(FIELD_SCALE, FIELD_SCALE)
        painter.setPen(Qt.PenStyle.NoPen)
        n_ranked = len(self._ranked)
        # Large libraries: a soft milky-way knot instead of shrinking stars away.
        density_mode = n_ranked > 1200

        # Draw lower-confidence stars first so bright / high-trust read on top.
        def _conf_band(r: RankedTrack) -> int:
            if r.track.pinned:
                return 2
            c = read_mood_confidence(r.track)
            if c >= CONFIDENCE_HIGH:
                return 2
            if c >= CONFIDENCE_LOW:
                return 1
            return 0

        order = sorted(
            self._ranked.values(),
            key=lambda r: (
                _conf_band(r),
                STAR_Z[r.quadrant] + (2 if r.track.loved else 0),
            ),
        )
        # Cheap density underlay so stacked cohorts read as a faint cloud, not emptiness.
        if density_mode and n_ranked:
            cell = 10.0
            counts: dict[tuple[int, int], int] = {}
            for item in order:
                pos = self._model_pos(item.track.id)
                key = (int(pos.x() // cell), int(pos.y() // cell))
                counts[key] = counts.get(key, 0) + 1
            for (cx, cy), count in counts.items():
                if count < 3:
                    continue
                # log2 so a 500-stack glows without blowing out the whole map.
                alpha = min(72, 12 + int(14 * log2(count)))
                glow = QColor(176, 156, 220, alpha)
                painter.setBrush(QBrush(glow))
                radius = 4.0 + min(10.0, count * 0.05)
                painter.drawEllipse(
                    QPointF(cx * cell + cell * 0.5, cy * cell + cell * 0.5),
                    radius,
                    radius,
                )
        painter.setPen(Qt.PenStyle.NoPen)
        easing_id = self._listen_ease[0] if self._listen_ease is not None else None
        for item in order:
            if item.track.id == easing_id:
                continue
            self._paint_baked_star(painter, item, self._positions[item.track.id], density_mode)
        painter.end()
        pix = QPixmap.fromImage(img)
        self._field.setPixmap(pix)
        self._field.setScale(1.0 / FIELD_SCALE)
        self._field.setPos(0, 0)
        self._field.setVisible(True)
        links = QPixmap.fromImage(self._baked_link_image())
        self._links.setPixmap(links)
        self._links.setScale(1.0 / FIELD_SCALE)
        self._links.setPos(0, 0)
        bins = QPixmap.fromImage(self._baked_bin_image())
        self._bins.setPixmap(bins)
        self._bins.setScale(1.0 / FIELD_SCALE)
        self._bins.setPos(0, 0)
        self._field_sig = self._field_signature()
        self._ensure_regions()

    def _region_position_key(self) -> tuple:
        protected = tuple(
            sorted(
                tid
                for tid, item in self._ranked.items()
                if item.track.pinned or item.track.loved
            )
        )
        positions = tuple(
            (tid, round(self._model_pos(tid).x(), 1), round(self._model_pos(tid).y(), 1))
            for tid in sorted(self._positions)
        )
        return (self._sky_band, protected, positions)

    def _protected_region_points(self) -> np.ndarray:
        pts = [
            (self._model_pos(tid).x(), self._model_pos(tid).y())
            for tid, item in self._ranked.items()
            if tid in self._positions and (item.track.pinned or item.track.loved)
        ]
        if not pts:
            return np.zeros((0, 2), dtype=np.float32)
        return np.asarray(pts, dtype=np.float32)

    def _region_pixmap(self, *, fine: bool) -> QPixmap:
        if not self._positions:
            return QPixmap()
        points = np.array(
            [[self._model_pos(tid).x(), self._model_pos(tid).y()] for tid in self._positions],
            dtype=np.float32,
        )
        protect = self._protected_region_points()
        haze = sky_haze(self._sky_band)
        if fine:
            image = neighborhood_region_image(
                points,
                min_span=REGION_FINE_SPAN,
                max_count=REGION_FINE_COUNT,
                limit=REGION_FINE_LIMIT,
                haze=haze,
                protect=protect,
            )
        else:
            image = neighborhood_region_image(
                points,
                min_span=REGION_COARSE_SPAN,
                max_count=REGION_COARSE_COUNT,
                limit=REGION_COARSE_LIMIT,
                haze=haze,
                protect=protect,
            )
        return QPixmap.fromImage(image)

    def _show_region_pixmap(self, pixmap: QPixmap) -> None:
        if pixmap is self._region_shown:
            return
        self._region_shown = pixmap
        self._regions.setPixmap(pixmap)
        self._regions.setScale(1.0 / FIELD_SCALE)
        self._regions.setPos(0, 0)
        self._regions.setVisible(True)

    def _ensure_regions(self) -> None:
        """Show neighborhood tiles. Positions bake both levels. Zoom only swaps them."""
        key = self._region_position_key()
        if key != self._region_pos_key:
            self._region_pos_key = key
            self._region_coarse = None
            self._region_fine = None
            self._region_shown = None
        if not self._positions:
            self._show_region_pixmap(QPixmap())
            return
        if self._falling and self._region_shown is not None:
            return
        if self._region_coarse is None:
            self._region_coarse = self._region_pixmap(fine=False)
        if self._region_fine is None:
            self._region_fine = self._region_pixmap(fine=True)
        zoomed = self._user_zoom >= LIVE_STARS_ZOOM and not self._zoom_active
        self._show_region_pixmap(self._region_fine if zoomed else self._region_coarse)

    def _night_star(self, item: RankedTrack, density: bool) -> tuple[float, QColor, int]:
        """Scene-pixel radius, quiet-sky color, and bloom alpha for one baked star."""
        mag = _star_mag(item.track.id)
        if item.track.pinned or item.track.id == self._current_id:
            band = 2
        else:
            conf = read_mood_confidence(item.track)
            if conf >= CONFIDENCE_HIGH:
                band = 2
            elif conf >= CONFIDENCE_LOW:
                band = 1
            else:
                band = 0
        color = quiet_star_color(band)
        if item.track.id == self._current_id:
            color.setAlpha(255)
        radius = night_body_radius(
            item.quadrant,
            band=band,
            mag=mag,
            loved=bool(item.track.loved),
            pinned=bool(item.track.pinned),
            current=item.track.id == self._current_id,
            density=density,
            duration_ms=getattr(item.track, "duration_ms", None),
        )
        bloom = 0
        if item.track.id == self._current_id:
            bloom = 78
        elif band >= 2 and mag > 0.93:
            bloom = 70
        elif band >= 2 and mag > 0.78:
            bloom = 32
        elif band == 1 and mag > 0.9:
            bloom = 28
        if item.track.pinned and item.track.id != self._current_id:
            bloom = max(bloom, 34)
        color, radius, bloom = apply_timbre_mark(
            color,
            radius,
            bloom,
            getattr(item.track, "timbre_flag", None),
            current=item.track.id == self._current_id,
        )
        if (
            item.track.id in self._crowd_ids
            and item.track.id != self._current_id
            and not item.track.pinned
        ):
            bloom = 0
        return radius, color, bloom

    def _refresh_crowd(self) -> None:
        self._crowd_ids = crowd_ids(
            [(tid, self._model_pos(tid).x(), self._model_pos(tid).y()) for tid in self._positions]
        )

    def _keeps_glow(self, tid: int, item: RankedTrack) -> bool:
        """A crowd loses its halo. The song playing, a pin, and the star under the pointer keep one."""
        if tid == self._current_id or item.track.pinned:
            return True
        if tid not in self._crowd_ids:
            return True
        if self._pointer_scene is None:
            return False
        pos = self._positions.get(tid)
        if pos is None:
            return False
        return hypot(pos.x() - self._pointer_scene.x(), pos.y() - self._pointer_scene.y()) <= CROWD_RADIUS

    def _constellation_segments(self) -> list[tuple[float, float, float, float, float]]:
        """Links for the baked sky. Reused while stars stay put, so a lens recolor is cheap."""
        points = [(tid, self._model_pos(tid).x(), self._model_pos(tid).y()) for tid in self._positions]
        if len(points) < 2:
            self._link_cache_key = None
            self._link_cache = []
            return []
        key = tuple(points)
        if key == self._link_cache_key:
            return self._link_cache
        segments = constellation_segments(points)
        self._link_cache_key = key
        self._link_cache = segments
        return segments

    def _baked_link_image(self) -> QImage:
        """Baked lines. Reused while stars stay put so a recolor does not redraw them."""
        self._constellation_segments()
        if self._baked_links is not None and self._baked_link_key == self._link_cache_key:
            return self._baked_links
        width, height = SCENE_W * FIELD_SCALE, SCENE_H * FIELD_SCALE
        image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.scale(FIELD_SCALE, FIELD_SCALE)
        link_pen = QPen(QColor(206, 196, 230, 64))
        link_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        span = SKY_LINK_MAX - SKY_LINK_MIN
        for ax, ay, bx, by, dist in self._link_cache:
            fade = (dist - SKY_LINK_MIN) / span if span else 0.0
            link_pen.setColor(QColor(186, 178, 214, int(54 - 18 * fade)))
            link_pen.setWidthF(0.55 if dist < 52 else 0.4)
            painter.setPen(link_pen)
            painter.drawLine(QPointF(ax, ay), QPointF(bx, by))
        painter.end()
        self._baked_links = image
        self._baked_link_key = self._link_cache_key
        return image

    def _baked_bin_image(self) -> QImage:
        """Unmarked shapes for songs the closer lines already join."""
        groups = constellation_groups(self._link_cache)
        return constellation_bin_image(groups, sky_haze(self._sky_band))

    def _rebuild_star_grid(self) -> None:
        grid: dict[tuple[int, int], list[tuple[int, QPointF]]] = {}
        cell = CLUSTER_GRID
        for tid, p in self._positions.items():
            key = (int(p.x() // cell), int(p.y() // cell))
            grid.setdefault(key, []).append((tid, p))
        self._star_grid = grid

    def _nearest_track_id(self, scene_pos: QPointF, max_dist: float) -> int | None:
        cell = CLUSTER_GRID
        cx0 = int(scene_pos.x() // cell)
        cy0 = int(scene_pos.y() // cell)
        span = max(1, int(max_dist // cell) + 1)
        best_id: int | None = None
        best_d = max_dist
        for gx in range(cx0 - span, cx0 + span + 1):
            for gy in range(cy0 - span, cy0 + span + 1):
                for tid, p in self._star_grid.get((gx, gy), ()):
                    d = hypot(p.x() - scene_pos.x(), p.y() - scene_pos.y())
                    if d < best_d:
                        best_d = d
                        best_id = tid
        return best_id

    def star_in_lens(self, pos: QPointF) -> bool:
        center = self.lens.pos()
        rect = self.lens.rect()
        rx = max(1.0, rect.width() * 0.5)
        ry = max(1.0, rect.height() * 0.5)
        dx = (pos.x() - center.x()) / rx
        dy = (pos.y() - center.y()) / ry
        return dx * dx + dy * dy <= 1.0

    def set_queue_route(self, order: list[int]) -> None:
        """Committed context queue, in play order. The path redraws from these ids."""
        self._queue_order = [int(tid) for tid in order]
        self._refresh_route()

    def _confidence_band(self, item: RankedTrack) -> int:
        if item.track.pinned or item.track.id == self._current_id:
            return 2
        conf = read_mood_confidence(item.track)
        if conf >= CONFIDENCE_HIGH:
            return 2
        if conf >= CONFIDENCE_LOW:
            return 1
        return 0

    def _refresh_lens_colors(self) -> None:
        """Playlist color only for stars the lens is covering."""
        if not self._ranked or not self._positions:
            self._lens_color.setPixmap(QPixmap())
            return
        center = self.lens.pos()
        rect = self.lens.rect()
        rx = rect.width() * 0.5
        ry = rect.height() * 0.5
        pad = 10.0
        x0 = max(0.0, center.x() - rx - pad)
        y0 = max(0.0, center.y() - ry - pad)
        x1 = min(float(SCENE_W), center.x() + rx + pad)
        y1 = min(float(SCENE_H), center.y() + ry + pad)
        width = max(1, int(round((x1 - x0) * FIELD_SCALE)))
        height = max(1, int(round((y1 - y0) * FIELD_SCALE)))
        image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.scale(FIELD_SCALE, FIELD_SCALE)
        painter.translate(-x0, -y0)
        density = len(self._ranked) > 1200
        painter.setPen(Qt.PenStyle.NoPen)
        easing_id = self._listen_ease[0] if self._listen_ease is not None else None
        for item in self._ranked.values():
            if item.track.id == easing_id:
                continue
            pos = self._positions.get(item.track.id)
            if pos is None or not self.star_in_lens(pos):
                continue
            radius, _quiet, bloom = self._night_star(item, density)
            band = self._confidence_band(item)
            color = queue_star_color(item.quadrant, loved=bool(item.track.loved), band=band)
            if item.track.id == self._current_id:
                color.setAlpha(255)
            haze = sky_haze(self._sky_band)
            if haze > 0.05:
                mist = QColor(206, 214, 232)
                mist.setAlpha(int(22 + 50 * haze))
                painter.setBrush(QBrush(mist))
                painter.drawEllipse(pos, radius * (2.4 + 1.8 * haze), radius * (2.4 + 1.8 * haze))
                color = QColor(color)
                color.setAlpha(max(48, int(color.alpha() * (1.0 - 0.32 * haze))))
            if bloom:
                glow = QColor(color)
                glow.setAlpha(min(110, bloom + 20))
                painter.setBrush(QBrush(glow))
                painter.drawEllipse(pos, radius * 3.2, radius * 3.2)
            painter.setBrush(QBrush(color))
            painter.drawEllipse(pos, radius, radius)
            if item.track.loved and not item.track.pinned and item.track.id != self._current_id:
                ring = QPen(LOVED_RING_QCOLOR, 1.2)
                painter.setPen(ring)
                painter.setBrush(QBrush(Qt.BrushStyle.NoBrush))
                painter.drawEllipse(pos, radius + 1.35, radius + 1.35)
                painter.setPen(Qt.PenStyle.NoPen)
        painter.end()
        self._lens_color.setPixmap(QPixmap.fromImage(image))
        self._lens_color.setScale(1.0 / FIELD_SCALE)
        self._lens_color.setPos(x0, y0)
        self._apply_search_veil()

    def _refresh_route(self) -> None:
        """Open path through the committed queue. Brightest on the song that is playing."""
        points: list[tuple[int, QPointF]] = []
        for tid in self._queue_order:
            if tid not in self._positions:
                continue
            points.append((tid, self._model_pos(tid)))
        if len(points) < 2:
            self._route.setPixmap(QPixmap())
            return
        image = QImage(
            SCENE_W * FIELD_SCALE,
            SCENE_H * FIELD_SCALE,
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.scale(FIELD_SCALE, FIELD_SCALE)
        current_index = 0
        for index, (tid, _pos) in enumerate(points):
            if tid == self._current_id:
                current_index = index
                break
        for index in range(len(points) - 1):
            hop = min(abs(index - current_index), abs(index + 1 - current_index))
            if hop == 0:
                color = QColor(255, 246, 214, 230)
                width = 2.5
                soft = QPen(QColor(255, 236, 180, 80), 6.0)
                soft.setCapStyle(Qt.PenCapStyle.RoundCap)
                painter.setPen(soft)
                painter.drawLine(points[index][1], points[index + 1][1])
            else:
                color = QColor(214, 206, 232, max(64, 170 - hop * 26))
                width = 1.35
            pen = QPen(color, width)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawLine(points[index][1], points[index + 1][1])
        painter.end()
        self._route.setPixmap(QPixmap.fromImage(image))
        self._route.setScale(1.0 / FIELD_SCALE)
        self._route.setPos(0, 0)
        self._apply_search_veil()

    def lens_mood(self) -> tuple[float, float, float]:
        vx, ey = self.pos_to_mood(self.lens.pos())
        return vx, ey, self._radius

    def set_radius_scale(self, scale: float) -> None:
        """Mode radius multiplier so the drawn ring matches queue selection."""
        self._radius_scale = max(0.05, float(scale))
        self._apply_lens_rect()

    def _effective_radius(self) -> float:
        return max(LENS_RADIUS_MIN, min(LENS_RADIUS_MAX, self._radius * self._radius_scale))

    def _apply_lens_rect(self) -> None:
        """Lens ellipse in scene px ≡ mood radius on valence (720) / energy (524) axes."""
        r = self._effective_radius()
        rx = r * 720.0
        ry = r * 524.0
        self.lens.setRect(-rx, -ry, rx * 2.0, ry * 2.0)

    def set_lens(self, valence: float, energy: float, radius: float) -> None:
        self._radius = max(LENS_RADIUS_MIN, min(LENS_RADIUS_MAX, radius))
        self.lens.setPos(self.mood_to_pos(valence, energy))
        self._apply_lens_rect()

    def snap_lens_to_star(self, star: TrackStar) -> None:
        track = star.ranked.track
        pos = self.mood_to_pos(track.valence, track.energy)
        self._positions[track.id] = pos
        star.setPos(pos)
        self.set_lens(track.valence, track.energy, self._radius)

    def lens_moved(self) -> None:
        self._refresh_lens_colors()
        if self._user_zoom >= LIVE_STARS_ZOOM:
            self._lod_band = -1
            self._sync_live_stars(force=True)
        x, y, r = self.lens_mood()
        self.lens_changed.emit(x, y, r)

    def star_moved(self, star: TrackStar) -> None:
        v, e = self.pos_to_mood(star.pos())
        self._positions[star.ranked.track.id] = QPointF(star.pos())
        self._rebuild_star_grid()
        self._refresh_crowd()
        self._rebuild_starfield()
        self._refresh_lens_colors()
        self._refresh_route()
        self.track_pinned.emit(star.ranked.track.id, v, e)

    def _update_drag_position(self, tid: int, pos: QPointF) -> None:
        """Keep grid in sync while a star is dragged (hover / nearest stay accurate)."""
        self._positions[tid] = QPointF(pos)
        self._rebuild_star_grid()

    def reset_view(self) -> None:
        self._end_zoom_interaction()
        self._cluster_once = True
        if self._falling:
            # A second ask finishes the trip home instead of waiting it out.
            self._fall_timer.stop()
            self._falling = False
            self._settle_home()
            return
        if self._user_zoom <= VIEW_ZOOM_MIN + 0.05:
            self._settle_home()
            return
        self._fall_from = self._user_zoom
        self._fall_t = 0.0
        self._falling = True
        self._present_home_sky()
        self._fall_timer.start()

    def _settle_home(self) -> None:
        self._user_zoom = VIEW_ZOOM_MIN
        self._zoom_target = VIEW_ZOOM_MIN
        self._pinch_zoom0 = VIEW_ZOOM_MIN
        self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self._lod_band = -1
        self._sync_live_stars(force=True)

    def _tick_fall(self) -> None:
        """Zoom out quickly, then settle, so the sky rushes away like falling backward."""
        self._fall_t += 16.0 / 460.0
        t = min(1.0, self._fall_t)
        ease = 1.0 - (1.0 - t) ** 2
        zoom = self._fall_from + (VIEW_ZOOM_MIN - self._fall_from) * ease
        if t < 0.45:
            kick = sin(t / 0.45 * pi) * 0.14 * max(0.0, self._fall_from - 1.0)
            zoom = max(VIEW_ZOOM_MIN, zoom - kick)
        self._user_zoom = zoom
        self._zoom_target = zoom
        done = t >= 1.0
        if done:
            self._falling = False
            self._fall_timer.stop()
            self._settle_home()
            return
        center = self.mapToScene(self.viewport().rect().center())
        self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self.scale(self._user_zoom, self._user_zoom)
        self.centerOn(center)
        if self._search_query:
            self._apply_search_veil()
        else:
            self._update_chrome(self._user_zoom)

    def _present_home_sky(self) -> None:
        """Show the whole baked sky while zooming home, so the end does not blink."""
        locked = self._drag_locked_ids()
        for tid, star in self._stars.items():
            star.set_body_scale(1.0, hot=False)
            if tid in locked:
                continue
            star.hide()
            star.set_stack([])
        self._field.setOpacity(1.0)
        self._regions.setOpacity(1.0)
        self._lens_color.setOpacity(1.0)
        self._route.setOpacity(1.0)
        self._ensure_regions()
        self._update_chrome(self._user_zoom)
        self._apply_search_veil()

    def _apply_base_fit(self) -> None:
        center = self.mapToScene(self.viewport().rect().center())
        self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        if self._user_zoom > VIEW_ZOOM_MIN + 1e-3:
            self.scale(self._user_zoom, self._user_zoom)
            self.centerOn(center)
        self._lod_band = -1
        self._sync_live_stars(force=True)

    def _begin_zoom_interaction(self) -> None:
        if self._zoom_active:
            return
        self._zoom_active = True
        self._cluster_once = True
        # Hide live stars during the gesture — keep an in-progress pin visible.
        locked = self._drag_locked_ids()
        for tid, star in self._stars.items():
            if tid in locked:
                star.show()
            else:
                star.hide()
        self.setRenderHint(QPainter.RenderHint.Antialiasing, False)

    def _end_zoom_interaction(self) -> None:
        self._zoom_active = False
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    def _schedule_zoom_lod(self) -> None:
        self._lod_timer.start()

    def _flush_zoom_lod(self) -> None:
        self._end_zoom_interaction()
        self._sync_live_stars(force=True)

    def _update_chrome(self, zoom: float) -> None:
        if zoom <= LOD_CHROME_FADE_START:
            chrome = 1.0
        elif zoom >= LOD_CHROME_FADE_END:
            chrome = 0.0
        else:
            t = (zoom - LOD_CHROME_FADE_START) / (LOD_CHROME_FADE_END - LOD_CHROME_FADE_START)
            chrome = 1.0 - t
        for item in self._sky_chrome:
            item.setOpacity(chrome)
        self._links.setOpacity(chrome)
        self._bins.setOpacity(chrome)

    def _ensure_interactive_star(self, tid: int) -> TrackStar | None:
        """Materialize one movable star (used for sky-mode click/drag)."""
        item = self._ranked.get(tid)
        if item is None or tid not in self._positions:
            return None
        star = self._stars.get(tid)
        if star is None:
            star = TrackStar(item)
            star._map = self
            self._scene.addItem(star)
            self._stars[tid] = star
        else:
            star.apply_style(item, current=tid == self._current_id)
        star.setPos(self._positions[tid])
        star.setZValue(40)
        star.set_body_scale(1.0, hot=False)
        star.set_stack([])
        star.show()
        return star

    def _live_focus(self) -> QPointF:
        """Where the close view should wake stars: under the pointer, else the middle of the view."""
        if self._pointer_scene is not None:
            view_pt = self.mapFromScene(self._pointer_scene)
            if self.viewport().rect().contains(view_pt):
                return self._pointer_scene
        return self.mapToScene(self.viewport().rect().center())

    def _live_priority(self, tid: int, item: RankedTrack, pos: QPointF, focus: QPointF, locked: set[int]) -> float:
        distance = hypot(pos.x() - focus.x(), pos.y() - focus.y())
        score = max(0.0, 2000.0 - distance)
        if self.star_in_lens(pos):
            score += 80.0
        if item.track.loved:
            score += 25.0
        if tid == self._current_id:
            score += 50.0
        if item.track.pinned:
            score += 15.0
        if tid in locked:
            score += 5000.0
        score += float(item.fit) * 10.0
        return score

    def _order_stack(self, ids: list[int]) -> list[int]:
        """Playing, then loved, then pinned, then the name."""

        def sort_key(tid: int) -> tuple:
            item = self._ranked[tid]
            return (
                0 if tid == self._current_id else 1,
                0 if item.track.loved else 1,
                0 if item.track.pinned else 1,
                item.track.label.lower(),
            )

        return sorted((tid for tid in ids if tid in self._ranked), key=sort_key)

    def _stack_representative(self, ids: list[int]) -> int:
        for tid in ids:
            star = self._stars.get(tid)
            if star is not None and star._stack_focus_id in ids:
                return int(star._stack_focus_id)
        return self._order_stack(ids)[0]

    def _close_groups(self, locked: set[int]) -> list[list[int]]:
        """Songs in view, nearest the pointer first. Exact overlaps become one group."""
        focus = self._live_focus()
        vis = self.mapToScene(self.viewport().rect()).boundingRect()
        vis.adjust(-60, -60, 60, 60)
        scored: list[tuple[float, int, QPointF]] = []
        for tid, pos in self._positions.items():
            if not vis.contains(pos) and tid not in locked:
                continue
            item = self._ranked.get(tid)
            if item is None:
                continue
            scored.append((self._live_priority(tid, item, pos, focus, locked), tid, pos))
        scored.sort(key=lambda row: row[0], reverse=True)
        pool = scored[: max(LIVE_STAR_CAP * 6, LIVE_STAR_CAP)]
        seen = {tid for _score, tid, _pos in pool}
        for score, tid, pos in scored:
            if tid in locked and tid not in seen:
                pool.append((score, tid, pos))
                seen.add(tid)
        groups: list[dict] = []
        for _score, tid, pos in pool:
            for group in groups:
                if hypot(pos.x() - group["x"], pos.y() - group["y"]) <= STACK_POINT:
                    group["ids"].append(tid)
                    break
            else:
                groups.append({"x": pos.x(), "y": pos.y(), "ids": [tid]})
        chosen = groups[:LIVE_STAR_CAP]
        chosen_ids = {tid for group in chosen for tid in group["ids"]}
        for group in groups[LIVE_STAR_CAP:]:
            if any(tid in locked and tid not in chosen_ids for tid in group["ids"]):
                chosen.append(group)
                chosen_ids.update(group["ids"])
        return [group["ids"] for group in chosen]

    def _adopt_stack_focus(self, star: TrackStar, tid: int) -> None:
        if tid not in self._ranked or tid not in star._stack_ids:
            return
        star._stack_focus_id = tid
        old = star.ranked.track.id
        if self._stars.get(old) is star:
            del self._stars[old]
        item = self._ranked[tid]
        star.apply_style(item, current=tid == self._current_id)
        star.set_stack(star._stack_ids)
        self._stars[tid] = star
        self.track_hovered.emit(item.track.label)
        self.snap_lens_to_star(star)

    def prompt_stack(self, star: TrackStar, screen_pos: QPointF) -> None:
        """Click a piled dot: list the songs, playing and loved first."""
        ids = [tid for tid in star._stack_ids if tid in self._ranked]
        if len(ids) < 2:
            self.snap_lens_to_star(star)
            return
        menu = QMenu(self)
        for tid in self._order_stack(ids):
            item = self._ranked[tid]
            marks: list[str] = []
            if tid == self._current_id:
                marks.append("playing")
            if item.track.loved:
                marks.append("loved")
            if item.track.pinned:
                marks.append("pinned")
            label = item.track.label
            if marks:
                label = f"{label}  ·  {' · '.join(marks)}"
            action = menu.addAction(label)
            action.setData(tid)
        chosen = menu.exec(screen_pos.toPoint())
        if chosen is None:
            return
        self._adopt_stack_focus(star, int(chosen.data()))

    def step_stack(self, star: TrackStar, step: int) -> None:
        """Wheel on a piled dot walks the name through the songs that share it."""
        ids = self._order_stack([tid for tid in star._stack_ids if tid in self._ranked])
        if len(ids) < 2:
            return
        current = star._stack_focus_id if star._stack_focus_id in ids else star.ranked.track.id
        index = ids.index(current) if current in ids else 0
        self._adopt_stack_focus(star, ids[(index + step) % len(ids)])

    def _sync_pointer_stars(self) -> None:
        if self._user_zoom < LIVE_STARS_ZOOM or self._falling or self._zoom_active:
            return
        self._lod_band = -1
        self._sync_live_stars(force=True)

    def _sync_live_stars(self, *, force: bool = False) -> None:
        """Sky = baked field only. Zoomed in = sparse live stars in the viewport."""
        if self._falling:
            self._present_home_sky()
            return
        zoom = self._user_zoom
        band = int(zoom * 4)
        if not force and band == self._lod_band:
            return
        self._lod_band = band
        self._update_chrome(zoom)

        if zoom < LIVE_STARS_ZOOM or self._zoom_active:
            locked = self._drag_locked_ids()
            for tid, star in self._stars.items():
                if tid in locked:
                    # Keep drag position (may differ from DB after a mid-drag refresh).
                    if tid in self._positions:
                        star.setPos(self._positions[tid])
                    star.set_body_scale(1.0, hot=False)
                    star.show()
                else:
                    star.hide()
            self._field.setOpacity(1.0)
            self._regions.setOpacity(1.0)
            self._lens_color.setOpacity(1.0)
            self._route.setOpacity(1.0)
            self._ensure_regions()
            self._apply_search_veil()
            return

        self._route.setOpacity(0.85)
        self._ensure_regions()
        locked = self._drag_locked_ids()
        groups = self._close_groups(locked)
        body = LIVE_STARS_ZOOM / max(zoom, LIVE_STARS_ZOOM)
        if zoom <= LOD_GLOW_START:
            glow_a = 0.0
        else:
            glow_a = min(1.0, (zoom - LOD_GLOW_START) / 2.4)
        labels_on = zoom >= LOD_LABEL_START
        labeled = 0
        used: list[TrackStar] = []
        for ids in groups:
            rep = self._stack_representative(ids)
            item = self._ranked.get(rep)
            if item is None:
                continue
            star = None
            for tid in ids:
                found = self._stars.get(tid)
                if found is not None:
                    star = found
                    break
            if star is None:
                star = TrackStar(item)
                star._map = self
                self._scene.addItem(star)
            for tid in list(self._stars):
                if self._stars[tid] is star:
                    del self._stars[tid]
            self._stars[rep] = star
            star.apply_style(item, current=rep == self._current_id)
            star.set_stack(ids)
            if rep in locked:
                self._positions[rep] = QPointF(star.pos())
            else:
                star.setPos(self._positions[rep])
            star.set_body_scale(body, hot=star._search_hot)
            star.show()
            show_label = False
            if labels_on and labeled < LABEL_CAP:
                show_label = True
                labeled += 1
            star.set_lod(glow_a if self._keeps_glow(rep, item) else 0.0, show_label)
            used.append(star)
        for tid in list(self._stars):
            if self._stars[tid] not in used:
                self._scene.removeItem(self._stars.pop(tid))
        self._apply_search_veil()
        self._hide_close_smear()

    def _anchor_point(self, view_pos: QPoint | QPointF) -> QPoint:
        return view_pos.toPoint() if isinstance(view_pos, QPointF) else QPoint(view_pos)

    def _cluster_anchor(self, view_pos: QPoint) -> QPoint:
        if not self._star_grid:
            return view_pos
        scene_pt = self.mapToScene(view_pos)
        radius = max(28.0, 110.0 / max(self._user_zoom, 1.0))
        cell = CLUSTER_GRID
        cx0 = int(scene_pt.x() // cell)
        cy0 = int(scene_pt.y() // cell)
        span = max(1, int(radius // cell) + 1)
        nearby: list[QPointF] = []
        best: QPointF | None = None
        best_d = radius * 1.35
        for gx in range(cx0 - span, cx0 + span + 1):
            for gy in range(cy0 - span, cy0 + span + 1):
                for _tid, p in self._star_grid.get((gx, gy), ()):
                    d = hypot(p.x() - scene_pt.x(), p.y() - scene_pt.y())
                    if d <= radius:
                        nearby.append(p)
                    if d < best_d:
                        best_d = d
                        best = p
        if len(nearby) < 2:
            if best is None:
                return view_pos
            nearby = [best]
        cx = sum(p.x() for p in nearby) / len(nearby)
        cy = sum(p.y() for p in nearby) / len(nearby)
        focus = QPointF(
            scene_pt.x() * (1.0 - CLUSTER_PULL) + cx * CLUSTER_PULL,
            scene_pt.y() * (1.0 - CLUSTER_PULL) + cy * CLUSTER_PULL,
        )
        return self.mapFromScene(focus)

    def _set_zoom_target(self, zoom: float, view_pos: QPoint | QPointF) -> None:
        if self._falling:
            self._falling = False
            self._fall_timer.stop()
        self._begin_zoom_interaction()
        self._zoom_target = max(VIEW_ZOOM_MIN, min(VIEW_ZOOM_MAX, zoom))
        anchor = self._anchor_point(view_pos)
        # Cluster pull once per gesture — not every native tick.
        if self._cluster_once and self._zoom_target > self._user_zoom + 1e-4:
            anchor = self._cluster_anchor(anchor)
            self._cluster_once = False
        self._zoom_anchor = anchor
        self._apply_zoom_level(self._zoom_target, self._zoom_anchor)

    def _multiply_zoom(self, factor: float, view_pos: QPoint | QPointF) -> None:
        if factor <= 0:
            return
        self._set_zoom_target(self._zoom_target * factor, view_pos)

    def _apply_zoom_level(self, new_zoom: float, view_pos: QPoint) -> None:
        new_zoom = max(VIEW_ZOOM_MIN, min(VIEW_ZOOM_MAX, new_zoom))
        if abs(new_zoom - self._user_zoom) < 1e-5:
            # Wheel/pinch past the zoom ceiling still begins a gesture that hides
            # live stars — schedule LOD end so they come back.
            self._schedule_zoom_lod()
            return
        if new_zoom <= VIEW_ZOOM_MIN + 1e-3:
            self.reset_view()
            return
        before = self.mapToScene(view_pos)
        actual = new_zoom / self._user_zoom
        self._user_zoom = new_zoom
        self.scale(actual, actual)
        after = self.mapToScene(view_pos)
        delta = after - before
        self.translate(delta.x(), delta.y())
        self._schedule_zoom_lod()

    def _arm_native_zoom_priority(self) -> None:
        self._prefer_native_zoom = True
        if self._native_zoom_clear is not None:
            self._native_zoom_clear.stop()
        clear = QTimer(self)
        clear.setSingleShot(True)
        clear.setInterval(350)
        clear.timeout.connect(self._clear_native_zoom_priority)
        clear.start()
        self._native_zoom_clear = clear

    def _clear_native_zoom_priority(self) -> None:
        self._prefer_native_zoom = False
        self._native_zoom_clear = None

    def wheelEvent(self, event) -> None:
        if self._user_zoom >= LIVE_STARS_ZOOM and not (
            event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            star = self._track_star_from_item(self.itemAt(event.position().toPoint()))
            if star is not None and len(star._stack_ids) > 1:
                delta_y = event.angleDelta().y() or event.pixelDelta().y()
                if delta_y:
                    self.step_stack(star, -1 if delta_y > 0 else 1)
                    event.accept()
                    return
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            if delta == 0:
                delta = event.pixelDelta().y()
            if delta == 0:
                return
            self._cluster_once = True
            factor = VIEW_WHEEL_FACTOR if delta > 0 else 1.0 / VIEW_WHEEL_FACTOR
            self._multiply_zoom(factor, event.position())
            event.accept()
            return
        step = 0.01 if self._radius <= 0.12 else 0.02
        delta_y = event.angleDelta().y()
        if delta_y == 0:
            delta_y = event.pixelDelta().y()
        if delta_y == 0:
            return
        delta = step if delta_y > 0 else -step
        self._radius = max(LENS_RADIUS_MIN, min(LENS_RADIUS_MAX, self._radius + delta))
        self.set_lens(*self.lens_mood())
        self.lens_moved()
        event.accept()

    def event(self, event) -> bool:
        if event.type() == QEvent.Type.NativeGesture:
            return self._native_gesture(event)
        if event.type() == QEvent.Type.Gesture:
            return self._gesture_event(event)
        return super().event(event)

    def _native_gesture(self, event: QNativeGestureEvent) -> bool:
        gtype = event.gestureType()
        if gtype == Qt.NativeGestureType.BeginNativeGesture:
            self._cluster_once = True
            return False
        if gtype == Qt.NativeGestureType.ZoomNativeGesture:
            self._arm_native_zoom_priority()
            raw = float(event.value())
            if abs(raw) > VIEW_NATIVE_OUTLIER:
                event.accept()
                return True
            factor = 1.0 + raw
            pos = self.mapFromGlobal(event.globalPosition().toPoint())
            self._multiply_zoom(factor, pos)
            event.accept()
            return True
        if gtype == Qt.NativeGestureType.SmartZoomNativeGesture:
            self.reset_view()
            event.accept()
            return True
        return False

    def _gesture_event(self, event: QGestureEvent) -> bool:
        pinch = event.gesture(Qt.GestureType.PinchGesture)
        if not isinstance(pinch, QPinchGesture):
            return False
        if self._prefer_native_zoom:
            event.accept()
            return True
        center = pinch.centerPoint()
        if pinch.state() == Qt.GestureState.GestureStarted:
            self._pinch_zoom0 = self._zoom_target
            self._cluster_once = True
            self._set_zoom_target(self._pinch_zoom0, center)
            event.accept()
            return True
        if pinch.state() == Qt.GestureState.GestureUpdated:
            total = float(pinch.totalScaleFactor())
            if total > 0:
                self._set_zoom_target(self._pinch_zoom0 * total, center)
            event.accept()
            return True
        if pinch.state() in (
            Qt.GestureState.GestureFinished,
            Qt.GestureState.GestureCanceled,
        ):
            self._flush_zoom_lod()
            event.accept()
            return True
        return False

    @staticmethod
    def _is_interactive_item(item: QGraphicsItem | None) -> bool:
        while item is not None:
            if isinstance(item, (TrackStar, LensItem)):
                return True
            item = item.parentItem()
        return False

    @staticmethod
    def _track_star_from_item(item: QGraphicsItem | None) -> TrackStar | None:
        while item is not None:
            if isinstance(item, TrackStar):
                return item
            item = item.parentItem()
        return None

    def _flush_pending_snap(self) -> None:
        candidate = self._pending_snap_id
        self._pending_snap_id = None
        if candidate is None or candidate not in self._ranked:
            return
        live = self._ensure_interactive_star(candidate)
        if live is not None:
            self.snap_lens_to_star(live)
            self.lens_moved()
        if self._user_zoom < LIVE_STARS_ZOOM:
            self._lod_band = -1
            self._sync_live_stars(force=True)

    def _cancel_pending_snap(self) -> None:
        self._snap_timer.stop()
        self._pending_snap_id = None

    def _finish_hand_drag(self, event) -> None:
        """Complete a ScrollHandDrag press so QGraphicsView drops sticky pan state."""
        if not self._hand_drag_armed and not self._panning:
            if self.dragMode() == QGraphicsView.DragMode.ScrollHandDrag:
                self.setDragMode(QGraphicsView.DragMode.NoDrag)
            return
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self._panning = False
        if self._hand_drag_armed:
            release = QMouseEvent(
                QEvent.Type.MouseButtonRelease,
                event.position(),
                event.globalPosition(),
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.NoButton,
                event.modifiers(),
            )
            self._hand_drag_armed = False
            super().mouseReleaseEvent(release)

    def _begin_hand_pan(self, event) -> None:
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self._panning = True
        self._hand_drag_armed = True
        super().mousePressEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            item = self.itemAt(event.position().toPoint())
            if not self._is_interactive_item(item):
                scene_pt = self.mapToScene(event.position().toPoint())
                # Zoomed in: baked dots aren't live — materialize the nearest star.
                if self._user_zoom >= LIVE_STARS_ZOOM:
                    tid = self._nearest_track_id(scene_pt, HIT_RADIUS_SKY)
                    if tid is not None and tid in self._ranked:
                        star = self._ensure_interactive_star(tid)
                        if star is not None:
                            super().mousePressEvent(event)
                            return
                    self._begin_hand_pan(event)
                    return
                # Sky mode: remember nearest star for click-snap / tight grab-to-pin.
                tid = self._nearest_track_id(scene_pt, HIT_RADIUS_SKY)
                self._sky_candidate_id = tid if tid in self._ranked else None
                self._sky_press_scene = QPointF(scene_pt)
                self._sky_hold_id = None
                self._sky_dragged = False
                self._sky_defer_pan = False
                self._cancel_pending_snap()
                if self._sky_candidate_id is not None:
                    self.track_hovered.emit(self._ranked[self._sky_candidate_id].track.label)
                    star_pos = self._positions.get(self._sky_candidate_id)
                    grab = (
                        star_pos is not None
                        and hypot(
                            scene_pt.x() - star_pos.x(),
                            scene_pt.y() - star_pos.y(),
                        )
                        <= HIT_RADIUS_SKY_GRAB
                    )
                    if grab:
                        # Wait for move — don't pan first (avoids viewport nudge on pin).
                        self._sky_defer_pan = True
                        event.accept()
                        return
                self._begin_hand_pan(event)
                return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and (
            self._sky_hold_id is not None
            or self._sky_candidate_id is not None
            or self._sky_defer_pan
        ):
            tid = self._sky_hold_id
            star = self._stars.get(tid) if tid is not None else None
            dragged = self._sky_dragged
            candidate = self._sky_candidate_id
            self._sky_hold_id = None
            self._sky_press_scene = None
            self._sky_dragged = False
            self._sky_candidate_id = None
            self._sky_defer_pan = False
            self._finish_hand_drag(event)
            if star is not None and dragged:
                self.star_moved(star)
            elif not dragged and candidate is not None and candidate in self._ranked:
                # Defer snap so a double-click empty can cancel it.
                self._pending_snap_id = candidate
                self._snap_timer.start()
            if self._user_zoom < LIVE_STARS_ZOOM and (star is not None and dragged):
                self._lod_band = -1
                self._sync_live_stars(force=True)
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and (
            self._hand_drag_armed or self._panning
        ):
            self._finish_hand_drag(event)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        self._cancel_pending_snap()
        item = self.itemAt(event.position().toPoint())
        star = self._track_star_from_item(item)
        if star is not None:
            self.track_activated.emit(star.ranked.track.id)
            event.accept()
            return
        if not self._is_interactive_item(item):
            # Tight core only — otherwise empty double-click returns to the full sky.
            tid = self._nearest_track_id(
                self.mapToScene(event.position().toPoint()), HIT_RADIUS_SKY_GRAB
            )
            if tid is not None and tid in self._ranked:
                self.track_activated.emit(tid)
                event.accept()
                return
            self.reset_view()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event) -> None:
        self._pointer_scene = self.mapToScene(event.position().toPoint())
        if (
            self._user_zoom >= LIVE_STARS_ZOOM
            and not self._falling
            and not self._zoom_active
            and not event.buttons()
        ):
            self._pointer_timer.start()
        # Sky: convert a core-star press into drag-to-pin without a prior pan nudge.
        if (
            self._user_zoom < LIVE_STARS_ZOOM
            and self._sky_hold_id is None
            and self._sky_candidate_id is not None
            and self._sky_press_scene is not None
            and event.buttons() & Qt.MouseButton.LeftButton
        ):
            pos = self.mapToScene(event.position().toPoint())
            delta = pos - self._sky_press_scene
            if hypot(delta.x(), delta.y()) > SKY_DRAG_SLOP:
                star_pos = self._positions.get(self._sky_candidate_id)
                grab = (
                    self._sky_defer_pan
                    or (
                        star_pos is not None
                        and hypot(
                            self._sky_press_scene.x() - star_pos.x(),
                            self._sky_press_scene.y() - star_pos.y(),
                        )
                        <= HIT_RADIUS_SKY_GRAB
                    )
                )
                if grab:
                    star = self._ensure_interactive_star(self._sky_candidate_id)
                    if star is not None:
                        if self._hand_drag_armed or self._panning:
                            self._finish_hand_drag(event)
                        self._sky_defer_pan = False
                        self._sky_hold_id = self._sky_candidate_id
                        self._sky_dragged = True
                        star.setPos(
                            QPointF(
                                max(40.0, min(760.0, pos.x())),
                                max(36.0, min(560.0, pos.y())),
                            )
                        )
                        self._update_drag_position(self._sky_hold_id, star.pos())
                        event.accept()
                        return
                # Far from star core → pure pan; drop candidate so release won't snap.
                self._sky_candidate_id = None
                self._sky_defer_pan = False
                if not self._hand_drag_armed and not self._panning:
                    self._begin_hand_pan(event)
                    return
            elif self._sky_defer_pan:
                event.accept()
                return
        if self._sky_hold_id is not None and event.buttons() & Qt.MouseButton.LeftButton:
            star = self._stars.get(self._sky_hold_id)
            if star is not None:
                pos = self.mapToScene(event.position().toPoint())
                pos.setX(max(40.0, min(760.0, pos.x())))
                pos.setY(max(36.0, min(560.0, pos.y())))
                star.setPos(pos)
                self._update_drag_position(self._sky_hold_id, pos)
                self._sky_dragged = True
                self.track_hovered.emit(star.ranked.track.label)
                event.accept()
                return
        item = self.itemAt(event.position().toPoint())
        star = self._track_star_from_item(item)
        if star is not None:
            self.track_hovered.emit(star.ranked.track.label)
        elif self._user_zoom < LIVE_STARS_ZOOM:
            tid = self._nearest_track_id(
                self.mapToScene(event.position().toPoint()), HIT_RADIUS_SKY
            )
            if tid is not None and tid in self._ranked:
                self.track_hovered.emit(self._ranked[tid].track.label)
        elif self._user_zoom >= LIVE_STARS_ZOOM:
            tid = self._nearest_track_id(
                self.mapToScene(event.position().toPoint()), HIT_RADIUS_SKY
            )
            if tid is not None and tid in self._ranked:
                self.track_hovered.emit(self._ranked[tid].track.label)
        super().mouseMoveEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_base_fit()
