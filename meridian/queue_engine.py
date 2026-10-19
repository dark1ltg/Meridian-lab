from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from math import exp, hypot

from meridian.context import Context, LENS_RADIUS_MIN, Mode, band_bias, mode_bias
from meridian.features import CONFIDENCE_HIGH, CONFIDENCE_LOW, track_is_unplaced
from meridian.library import Track


class Quadrant(str, Enum):
    NOW = "now"  # important + urgent
    DEEP = "deep"  # important + not urgent
    FILL = "fill"  # not important + urgent
    SHELF = "shelf"  # neither


QUADRANT_TITLE = {
    Quadrant.NOW: "NOW",
    Quadrant.DEEP: "DEEP",
    Quadrant.FILL: "FILL",
    Quadrant.SHELF: "SHELF",
}

QUADRANT_SUB = {
    Quadrant.NOW: "Important · Urgent — play this",
    Quadrant.DEEP: "Important · Later — keep close",
    Quadrant.FILL: "Urgent · Light — background pulse",
    Quadrant.SHELF: "Neither — park it",
}

# Soft caps while building a queue so lens loyalty does not become one-artist loops.
_MAX_ARTIST_IN_QUEUE = 2
_MAX_ALBUM_IN_QUEUE = 2

# Renew Queue: soft demotion of the immediately previous context queue (not skips).
_RENEW_PENALTY_BASE = 0.15
_RENEW_PENALTY_SMALL_POOL = 0.10
_RENEW_PENALTY_PER_STREAK = 0.04
_RENEW_PENALTY_CAP = 0.28
_RENEW_STREAK_CAP = 3
_RENEW_POOL_TINY = 12
_RENEW_POOL_SMALL = 25


@dataclass(frozen=True, slots=True)
class RenewalContext:
    """Transient queue-selection hint for Renew Queue (not a listening signal).

    prior_queue_ids: context queue being replaced.
    renew_streak: consecutive renews already applied (0 = first renew).
    exempt_ids: never demote (e.g. currently playing).
    heard_ids: session finishes/plays — not \"presented but unused.\"
    """

    prior_queue_ids: frozenset[int]
    renew_streak: int = 0
    exempt_ids: frozenset[int] = frozenset()
    heard_ids: frozenset[int] = frozenset()


@dataclass(slots=True)
class RankedTrack:
    track: Track
    fit: float
    importance: float
    urgency: float
    quadrant: Quadrant


@dataclass
class QueuePlan:
    ranked: list[RankedTrack]
    order: list[int] = field(default_factory=list)
    by_quadrant: dict[Quadrant, list[RankedTrack]] = field(default_factory=dict)


# Below this, steer is "low" — seed/intro/failed listens stay out of NOW unless
# pinned/loved, or NOW would otherwise be empty (backfill).
STEER_LOW = 0.20


def effective_steer_weight(track: Track) -> float:
    """Persisted steer_weight, or a shared legacy prior when the column is null.

    Used by both scoring and the NOW gate so analyze-failed / no-PCM / end-only
    rows cannot outrank honest seed-only concludes.
    """
    raw = getattr(track, "steer_weight", None)
    if raw is not None:
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = None
        else:
            if value == value:  # not NaN
                return float(value)
    note = (getattr(track, "confidence_note", None) or "").lower()
    if "seed only" in note or "intro only" in note:
        return 0.05
    if "analyze failed" in note:
        return 0.05
    if "end only" in note or "ends only" in note or "mid only" in note:
        return 0.15
    has_pcm = (
        getattr(track, "brightness", None) is not None
        or getattr(track, "acoustic_flux", None) is not None
    )
    if not has_pcm:
        return 0.05
    if bool(getattr(track, "low_trust", False)):
        return 0.15
    return 0.55


def _gauss(distance: float, radius: float) -> float:
    sigma = max(LENS_RADIUS_MIN * 0.85, radius * 0.72)
    return exp(-(distance * distance) / (2 * sigma * sigma))


def _clip01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _artist_key(track: Track) -> str:
    """Prefer real artist over Various Artists so VA catalogs keep diversity caps."""
    aa = (track.albumartist or "").strip().lower()
    ar = (track.artist or "").strip().lower()
    if aa and aa not in {"various artists", "various", "va"}:
        return aa
    return ar or aa


def _album_key(track: Track) -> str:
    """Album anti-repeat key — prefer albumartist so VA comps share one album bucket."""
    album = (track.album or "").strip().lower()
    if not album:
        return ""
    aa = (track.albumartist or "").strip().lower()
    if aa:
        return f"{aa}|{album}"
    ar = (track.artist or "").strip().lower()
    return f"{ar}|{album}" if ar else album


def _base_score(item: RankedTrack) -> float:
    return item.fit * 0.7 + item.importance * 0.3


def preferred_pool_size(ranked: list[RankedTrack]) -> int:
    """NOW + DEEP + FILL count — usable renew alternatives near the lens."""
    return sum(1 for r in ranked if r.quadrant != Quadrant.SHELF)


def renewal_penalty_strength(pool_size: int, renew_streak: int) -> float:
    """Soft demotion amount; 0 in tiny pools. Streak adds a bounded nudge."""
    if pool_size < _RENEW_POOL_TINY:
        return 0.0
    base = _RENEW_PENALTY_SMALL_POOL if pool_size < _RENEW_POOL_SMALL else _RENEW_PENALTY_BASE
    streak = max(0, min(_RENEW_STREAK_CAP, int(renew_streak)))
    return min(_RENEW_PENALTY_CAP, base + _RENEW_PENALTY_PER_STREAK * streak)


def renewal_penalty_ids(
    renewal: RenewalContext,
    tracks: list[Track],
    explicit_ids: set[int],
) -> set[int]:
    """Prior-queue leftovers to soft-demote — never skips, never hard bans."""
    loved = {t.id for t in tracks if t.loved}
    pinned = {t.id for t in tracks if t.pinned}
    return (
        set(renewal.prior_queue_ids)
        - set(renewal.exempt_ids)
        - set(renewal.heard_ids)
        - loved
        - pinned
        - set(explicit_ids)
    )


def mix_counts(ctx: Context) -> tuple[int, int, int, int, int]:
    """Return (explicit, now, deep, fill, shelf) take sizes for the context queue.

    Modes reshape the Eisenhower mix; skip pressure steals a little from NOW into FILL
    so a skip streak explores the lens neighborhood instead of doubling down.
    """
    if ctx.mode == Mode.FOCUS:
        explicit, now, deep, fill, shelf = 3, 8, 6, 3, 1
    elif ctx.mode == Mode.CHARGE:
        explicit, now, deep, fill, shelf = 3, 9, 3, 4, 2
    elif ctx.mode == Mode.DIM:
        explicit, now, deep, fill, shelf = 2, 5, 7, 3, 3
    else:  # WANDER
        explicit, now, deep, fill, shelf = 3, 7, 5, 4, 2

    pressure = _clip01(ctx.skip_pressure)
    steal = int(round(2.0 * pressure))
    if steal:
        now = max(3, now - steal)
        fill = fill + steal
    return explicit, now, deep, fill, shelf


def classify(tracks: list[Track], ctx: Context, explicit_ids: set[int]) -> list[RankedTrack]:
    """Map tracks onto the Eisenhower grid from the mood-lens neighborhood.

    Importance and urgency used to be the same `fit` number, so anything
    important was also urgent and DEEP (important, not urgent) stayed empty.
    Urgency is closeness to the lens. Importance is catalog weight plus being
    in the wider neighborhood. Nearby tracks are split into inner NOW, ring
    DEEP, and outer FILL so moving the lens always refills DEEP.
    """
    bx, by = band_bias(ctx.band)
    mv, me, _ = mode_bias(ctx.mode)
    # Tiny clock/mode nudge; the lens the user dragged is the real target.
    target_x = _clip01(0.88 * ctx.lens_x + 0.12 * (bx + mv))
    target_y = _clip01(0.88 * ctx.lens_y + 0.12 * (by + me))
    pressure = _clip01(ctx.skip_pressure)
    # Skip streak slightly widens the neighborhood so FILL can surface alternatives.
    radius = max(LENS_RADIUS_MIN, ctx.lens_radius * (1.0 + 0.22 * pressure))

    ranked: list[RankedTrack] = []
    distances: list[float] = []
    for track in tracks:
        # No real mood yet: not a star, and not a shelf row pretending to be one.
        if track_is_unplaced(track):
            continue
        dist = hypot(track.valence - target_x, track.energy - target_y)
        fit = _gauss(dist, radius)
        plays = int(track.play_count or 0)
        skips = int(track.skip_count or 0)
        total = max(1, plays + skips)
        skip_ratio = skips / total
        importance = 0.28 * fit
        if track.loved:
            importance += 0.42
        importance += 0.22 * min(plays, 10) / 10
        if track.pinned:
            importance += 0.08
        # Finishes vs skips: net listen quality after a few observations.
        if plays + skips >= 3:
            finish_ratio = plays / total
            importance += 0.14 * (finish_ratio - 0.5)
        # Focus prefers steady rhythm when we have a persisted onset cue.
        if ctx.mode == Mode.FOCUS and track.onset_consistency is not None:
            oc = float(track.onset_consistency)
            if oc > 0.70:
                importance += 0.06
            elif oc < 0.35:
                importance -= 0.04
        # Focus / Dim: tonal loops over noisy stings (timbre is not a map axis).
        timbre = getattr(track, "timbre_flag", None)
        if timbre == "tonal" and ctx.mode in (Mode.FOCUS, Mode.DIM):
            importance += 0.03
        elif timbre == "noisy" and ctx.mode == Mode.FOCUS:
            importance -= 0.04
        elif timbre == "noisy" and ctx.mode == Mode.DIM and track.energy > 0.55:
            importance -= 0.05
        # Prefer sure pins over dim scrapes at similar distance (Focus / NOW neighborhood).
        # Keep the nudge light so it doesn't stack harshly with Focus onset_consistency;
        # a closer dim neighbor must still be able to compete on combined score.
        raw_conf = getattr(track, "mood_confidence", None)
        conf = float(raw_conf) if raw_conf is not None else 0.5
        if track.low_trust or conf <= 0.0:
            # Exact 0.0 is rock-bottom / unsure — not medium (falsy `or 0.5` trap).
            importance -= 0.035
            fit *= 0.97
        else:
            # Subtle lift for fuller listens; stronger when confidence is clearly high.
            span = max(1e-6, 1.0 - CONFIDENCE_LOW)
            conf_lift = 0.025 * max(0.0, min(1.0, (conf - CONFIDENCE_LOW) / span))
            importance += conf_lift
            if conf >= CONFIDENCE_HIGH:
                fit = min(1.0, fit + 0.01)
            if ctx.mode == Mode.FOCUS and conf >= CONFIDENCE_HIGH:
                importance += 0.01
        # Steer weight: seed-only / intro-only are barely safe to steer — cut
        # importance lightly here; NOW promotion is gated separately below.
        steer = effective_steer_weight(track)
        if steer < STEER_LOW and not track.pinned and not track.loved:
            importance *= 0.82
            fit *= 0.94
        importance = min(1.0, max(0.0, importance)) * (1.0 - 0.45 * skip_ratio)
        # High skip pressure softens NOW stickiness (favor exploring the ring).
        urgency = fit * (1.0 - 0.18 * pressure)
        if track.id in explicit_ids:
            urgency = min(1.0, urgency + 0.45)
        if ctx.mode.value == "charge" and track.energy > 0.62:
            urgency = min(1.0, urgency + 0.08)
        if ctx.mode.value == "dim" and track.energy < 0.4:
            urgency = min(1.0, urgency + 0.08)
        # Shape note: Dim avoids drop-shaped tracks; Charge prefers them.
        # Pins / loves skip shape modifiers (same spirit as steer exceptions).
        shape = getattr(track, "shape_note", None)
        if shape and not track.pinned and not track.loved:
            if shape == "drop":
                if ctx.mode == Mode.CHARGE:
                    urgency = min(1.0, urgency + 0.10)
                    importance = min(1.0, importance + 0.04)
                elif ctx.mode == Mode.DIM:
                    urgency *= 0.72
                    importance *= 0.85
            elif shape == "build" and ctx.mode == Mode.CHARGE:
                urgency = min(1.0, urgency + 0.04)
            elif shape == "steady" and ctx.mode == Mode.DIM:
                urgency = min(1.0, urgency + 0.03)
        # Low steer: further soften urgency so they lose NOW races.
        if steer < STEER_LOW and not track.pinned and not track.loved and track.id not in explicit_ids:
            urgency *= 0.35
        ranked.append(
            RankedTrack(
                track=track,
                fit=fit,
                importance=importance,
                urgency=urgency,
                quadrant=Quadrant.SHELF,
            )
        )
        distances.append(dist)

    nearby_scale = 2.4 + 0.7 * pressure
    in_range = [i for i, dist in enumerate(distances) if dist <= radius * nearby_scale]
    nearby_idx = list(in_range)
    if len(nearby_idx) < 6:
        # Still surface nearest neighbors for ranking, but do not promote far tracks
        # into NOW/DEEP/FILL — they stay SHELF unless loved/importance lifts them.
        nearby_idx = sorted(range(len(distances)), key=distances.__getitem__)[: min(16, len(distances))]
    nearby_idx.sort(key=lambda i: distances[i])
    in_range_set = set(in_range)

    n = len([i for i in nearby_idx if i in in_range_set])
    if n == 0:
        n_now, n_deep = 0, 0
    elif n == 1:
        n_now, n_deep = 1, 0
    elif n == 2:
        n_now, n_deep = 1, 1
    else:
        # Under skip pressure, shrink the NOW core and grow the FILL ring.
        now_frac = 0.38 - 0.10 * pressure
        deep_frac = 0.34
        n_now = max(1, round(n * now_frac))
        n_deep = max(1, round(n * deep_frac))
        if n_now + n_deep >= n:
            n_deep = max(1, n - n_now - 1) if n >= 3 else n - n_now

    range_order = 0
    deferred_low_steer: list[int] = []
    for i in nearby_idx:
        item = ranked[i]
        if item.track.id in explicit_ids:
            item.quadrant = Quadrant.NOW
            continue
        if i not in in_range_set:
            continue
        # Push seed-only / intro-only / failed out of NOW unless pinned or loved.
        t = item.track
        low_steer = (
            effective_steer_weight(t) < STEER_LOW
            and not t.pinned
            and not t.loved
        )
        if range_order < n_now:
            if low_steer:
                deferred_low_steer.append(i)
                continue
            item.quadrant = Quadrant.NOW
        elif range_order < n_now + n_deep:
            item.quadrant = Quadrant.DEEP
        else:
            item.quadrant = Quadrant.FILL
        range_order += 1

    # If NOW would be empty (all-nearby low-steer), backfill closest deferred —
    # prefer pinned/loved/higher steer, then original distance order.
    now_count = sum(1 for r in ranked if r.quadrant == Quadrant.NOW)
    need_now = max(0, n_now - now_count)
    if need_now > 0 and deferred_low_steer:
        ordered = sorted(
            enumerate(deferred_low_steer),
            key=lambda ei: (
                0 if ranked[ei[1]].track.pinned else 1,
                0 if ranked[ei[1]].track.loved else 1,
                -effective_steer_weight(ranked[ei[1]].track),
                ei[0],
            ),
        )
        backfilled: set[int] = set()
        for _ord, i in ordered:
            if need_now <= 0:
                break
            ranked[i].quadrant = Quadrant.NOW
            backfilled.add(i)
            need_now -= 1
            range_order += 1
        deferred_low_steer = [i for i in deferred_low_steer if i not in backfilled]

    # Remaining low-steer leftovers land in DEEP/FILL.
    for i in deferred_low_steer:
        item = ranked[i]
        if item.quadrant != Quadrant.SHELF:
            continue
        if range_order < n_now + n_deep:
            item.quadrant = Quadrant.DEEP
        else:
            item.quadrant = Quadrant.FILL
        range_order += 1

    # Loved / often-played tracks near the lens belong in DEEP, not SHELF or FILL.
    # FILL→DEEP matters when band bias pushes a lens-center loved track into the
    # outer ring (e.g. DAY) so short NOW+DEEP plans still keep it (H4).
    for i, item in enumerate(ranked):
        if item.quadrant in (Quadrant.NOW, Quadrant.DEEP):
            continue
        if item.importance >= 0.42 and distances[i] <= radius * 3.2:
            item.quadrant = Quadrant.DEEP

    ranked.sort(key=lambda r: (r.fit * 0.7 + r.importance * 0.3), reverse=True)
    return ranked


def build_plan(
    tracks: list[Track],
    ctx: Context,
    explicit_ids: list[int],
    length: int = 18,
    exclude_ids: set[int] | None = None,
    hard_exclude_ids: set[int] | None = None,
    renewal: RenewalContext | None = None,
) -> QueuePlan:
    """Build a context queue from lens fit, time-of-day, and the four matrix lists.

    When ``renewal`` is set (Renew Queue), the current matrix still drives
    candidates; prior-queue leftovers get a soft score demotion only — never
    skip_count / skip_pressure contamination.
    """
    explicit_set = set(explicit_ids)
    skip = set(exclude_ids or ())
    hard_exclude = set(hard_exclude_ids or ())
    skip |= hard_exclude
    ranked = classify(tracks, ctx, explicit_set)
    buckets: dict[Quadrant, list[RankedTrack]] = {q: [] for q in Quadrant}
    for item in ranked:
        buckets[item.quadrant].append(item)

    penalty_ids: set[int] = set()
    penalty_strength = 0.0
    if renewal is not None and renewal.prior_queue_ids:
        penalty_ids = renewal_penalty_ids(renewal, tracks, explicit_set)
        penalty_strength = renewal_penalty_strength(
            preferred_pool_size(ranked), renewal.renew_streak
        )

    def matrix_key(item: RankedTrack) -> tuple[float, float]:
        return (-_base_score(item), item.track.last_played or 0.0)

    def queue_key(item: RankedTrack) -> tuple[float, float]:
        score = _base_score(item)
        if penalty_strength > 0.0 and item.track.id in penalty_ids:
            score -= penalty_strength
        return (-score, item.track.last_played or 0.0)

    for bucket in buckets.values():
        bucket.sort(key=matrix_key)

    def queue_order(items: list[RankedTrack]) -> list[RankedTrack]:
        if penalty_strength <= 0.0 or not penalty_ids:
            return items
        return sorted(items, key=queue_key)

    order: list[int] = []
    used: set[int] = set()
    artist_n: dict[str, int] = {}
    album_n: dict[str, int] = {}
    n_explicit, n_now, n_deep, n_fill, n_shelf = mix_counts(ctx)
    # Soft ceiling on SHELF during gap-fill so anti-repeat cannot empty NOW/DEEP/FILL
    # into a SHELF-heavy queue while those matrix lists still have unused tracks.
    shelf_gap_cap = max(n_shelf, 3)

    def take(items: list[RankedTrack], n: int, *, allow_recent: bool, diversity: bool) -> None:
        grabbed = 0
        for item in queue_order(items):
            if grabbed >= n:
                break
            tid = item.track.id
            if tid in used:
                continue
            # Recently played are soft-skipped first; hard-excluded ids stay out even
            # when allow_recent fills gaps (tiny libraries must not loop the same song).
            if tid in hard_exclude:
                continue
            if not allow_recent and tid in skip:
                continue
            artist = _artist_key(item.track)
            album = _album_key(item.track)
            if diversity:
                if artist and artist_n.get(artist, 0) >= _MAX_ARTIST_IN_QUEUE:
                    continue
                if album and album_n.get(album, 0) >= _MAX_ALBUM_IN_QUEUE:
                    continue
            order.append(tid)
            used.add(tid)
            if artist:
                artist_n[artist] = artist_n.get(artist, 0) + 1
            if album:
                album_n[album] = album_n.get(album, 0) + 1
            grabbed += 1

    def shelf_count() -> int:
        shelf_ids = {r.track.id for r in buckets[Quadrant.SHELF]}
        return sum(1 for tid in order if tid in shelf_ids)

    def take_preferred(*, allow_recent: bool, diversity: bool) -> None:
        need = length - len(order)
        if need <= 0:
            return
        take(buckets[Quadrant.NOW], need, allow_recent=allow_recent, diversity=diversity)
        need = length - len(order)
        if need <= 0:
            return
        take(buckets[Quadrant.DEEP], need, allow_recent=allow_recent, diversity=diversity)
        need = length - len(order)
        if need <= 0:
            return
        take(buckets[Quadrant.FILL], need, allow_recent=allow_recent, diversity=diversity)

    # Mix from all four playlists; mode + skip pressure set the ratios.
    take(
        [r for r in ranked if r.track.id in explicit_set],
        min(n_explicit, len(explicit_set)),
        allow_recent=True,
        diversity=False,
    )
    take(buckets[Quadrant.NOW], n_now, allow_recent=False, diversity=True)
    take(buckets[Quadrant.DEEP], n_deep, allow_recent=False, diversity=True)
    take(buckets[Quadrant.FILL], n_fill, allow_recent=False, diversity=True)
    take(buckets[Quadrant.SHELF], n_shelf, allow_recent=False, diversity=True)

    # Gap-fill: protect the matrix mix. Diversity yields before SHELF does.
    if len(order) < length:
        # Pass A — preferred buckets, keep anti-repeat.
        take_preferred(allow_recent=True, diversity=True)
    if len(order) < length:
        # Pass B — same buckets, relax artist/album caps so a dense cluster can still feed.
        take_preferred(allow_recent=True, diversity=False)
    if len(order) < length:
        # Pass C — limited SHELF only after NOW/DEEP/FILL are exhausted under both rules.
        room = max(0, shelf_gap_cap - shelf_count())
        take(
            buckets[Quadrant.SHELF],
            min(length - len(order), room),
            allow_recent=True,
            diversity=True,
        )
    if len(order) < length:
        room = max(0, shelf_gap_cap - shelf_count())
        take(
            buckets[Quadrant.SHELF],
            min(length - len(order), room),
            allow_recent=True,
            diversity=False,
        )
    if len(order) < length:
        # Last resort (tiny / exhausted library): any remaining ranked track.
        take(ranked, length - len(order), allow_recent=True, diversity=False)
    return QueuePlan(ranked=ranked, order=order[:length], by_quadrant=buckets)
