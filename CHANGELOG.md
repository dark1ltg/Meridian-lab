# Changelog

All notable Meridian releases are listed here. Download AppImages from [Releases](https://github.com/dark1ltg/Meridian/releases).

## [1.3.6](https://github.com/dark1ltg/Meridian/releases/tag/v1.3.6) — 2026-09-10 (AppImage refreshed 2026-10-18)

Version stays **1.3.6**. This refresh makes the short listen more honest and the queue more careful — analysis upgrades, placement honesty, and a mass re-queue bug fix — on top of earlier path-to-9 / dual-worker / map-completeness work.

### Analysis & honesty (this AppImage)

**Smarter short listen**
- Loudness is normalized before Kinetic/Glow cues so a hot master doesn’t look “more energetic” just because it’s louder.
- Silent game-cue intros shift the listen onto the first audible stretch instead of tasting only hush.
- Multi-window listens can store a simple **shape** note (steady / build / drop / bookend) and a **timbre** hint (tonal vs noisy) for the queue — still the same two map axes.
- Full listens steer the queue more; seed-only / intro-only listens stay out of NOW unless nothing better is nearby (or the track is pinned / loved).

**More honest placement**
- Spoken non-musical frames are dropped when real music remains in the listen.
- Blend leans toward windows that are both tonal and onset-solid (with a floor so a flashy sting can’t become the whole song).
- Clear harmonic tone may set Glow from pitch height; muffled listens stay nearer the sticker; heavy sub-bass can vote Shadow once.
- Muddy pulse skips the BPM→Kinetic step; title/artist mood words that clearly fight a steady listen are undone (genre/folder seed stays).

**Bug fixes**
- **Kept-prior PCM miss no longer re-queues the whole library:** when a later pass can’t hear audio but Meridian already kept a good pin (`pcm pending · kept prior`), that row is marked analyzed so every launch doesn’t re-listen thousands of finished tracks. Rescan still retries on purpose.
- NOW fills with the closest neighbors when everything under the lens is low-steer (no empty NOW from seed-only neighborhoods).
- Finding audio after a silent head is a normal listen, not a “weak listen” tax; partial multi-window listens can’t claim full steer weight.
- Skimpy re-analyze keeps a prior shape note; seed-only rows don’t get fake high steer; near-floor hiss stays quiet on Glow.
- Dialogue / slope / pitch-Glow / keyword-undo / dual-window honesty gates fixed so the honesty pack actually fires on real audio.
- Mood map uses an OpenGL viewport by default. A helper process checks the driver first; if that check fails, the map stays on the software widget. `MERIDIAN_NO_GL=1`, `MERIDIAN_GL=off`, `QT_OPENGL=software`, or `LIBGL_ALWAYS_SOFTWARE=1` keep software.
- Night-sky stars stay large enough to see at fit zoom, including a big library. A weak guess leans pewter instead of a dimmer neon, and a loved star keeps a warm ring.
- Lines between nearby songs are worked out once and reused when only the queue colors change, so moving the lens does not stall a large library. A crowded patch still connects the closest songs.
- The middle of the night sky is easier to see: a soft cross marks Shadow against Glow and Still against Kinetic.
- Stars stay white, or pewter when the guess is weak, until the lens covers them. Inside the lens they take the matrix color. The committed queue is a path on the sky, brightest on the song that is playing.
- The song that is playing pulses. Starting it sends a short ripple through the nearby stars. A search blacks out the rest of the sky and flares the match. Zooming back to the whole sky eases out like falling backward. Night stars stay crisp and daytime stars look hazy. A long song is a larger star and a short one is a speck.
- Picking a search result restores the sky. A filename match flares too. The pulse only runs while sound is playing, and only that small spot redraws. The first sky draw does not ripple. Zooming home shows the whole sky during the fall, and a second double-click finishes the trip.
- The context queue rows are stars in play order. The four matrix lists are plain names again, with the colored top edge and subtitle.
- Matrix song rows have a little more line height (two pixels above and below). The queue list is unchanged.
- Unheard songs with no genre sticker stay off the middle of the sky. A clear listen may leave a well-tagged sticker by the evidence widen. Stored brightness and flux finish the spot for trusted songs too. A clean listen lets strong brightness or motion count more. A clear tone draws as a hard point and a noisy one stays softer.
- A played song still gets the listen nudge, and the acoustic finish no longer skips it. Soundtrack and game labels stay off the sky until a listen. A filename keyword still shows. A muddy flux no longer counts as agreement. A noisy star keeps the same size. A strong clean brightness or motion still moves the point after the genre clamp. The song that is playing stays on the sky even when it has no mood yet.
- The quarter grid is gone. The four corners carry colder or warmer light, and diving in fades the constellations and grows a name plus a role chip.

### What’s new (plain English)

**Faster listening on SSDs**
- On flash storage (SSD/NVMe), Meridian quietly uses **two** waveform listeners at once so large libraries finish sooner.
- On hard drives (or mixed/unknown disks) it stays at **one** listener so the drive isn’t thrashed.
- No new settings or switches — it just happens. Advanced override: `MERIDIAN_ANALYZE_WORKERS=1` or `2`.

**Smarter short listen (intro / middle / end)**
- Long songs get three short listens (~12s each at the start, middle, and end) instead of one mid-track peek.
- Short songs still get one or two listens — Meridian doesn’t triple-sample the same bit of audio.
- A silent cold open no longer “vetoes” a clear middle and end.

**Your whole collection on the map**
- If Meridian heard *any* real audio (even just the start or the fade), the track gets a star — marked unsure when the listen was partial.
- If it heard nothing but tags/folder names exist, it still places a rough star from those (`seed only`) so songs aren’t stuck invisible as “deferred.”
- It only leaves a track pending when there’s **no audio and no useful tags**.

### Bug fixes

**First-run analyze never started (critical)**
- On newer PySide/Qt, the “scan finished → start listening” handoff was silently dropped, so the map stayed on tag guesses with no waveform pass.
- **Fixed:** that handoff now uses real Qt slots so analyze starts after indexing.

**Good listens no longer get wiped by a bad retry**
- If a song already had a real listen and a later pass can’t hear it, Meridian keeps the good pin and tries again later — it doesn’t replace it with a genre sticky-note.

**Rough placements stay honestly dim**
- Start-only / end-only / ends-only listens stay low-confidence so they look unsure on the map.
- Album tidy can still nudge those stars toward album mates, but it won’t promote them into “looks trusted.”
- A longer salvage listen of a pad/intro head still stays marked intro-only / unsure.

**Honest status messages**
- If a pass placed everything from tags with no waveform listen, the status says so — it no longer implies “updated from local audio.”

**Scan vs analyze**
- Adding folders / scanning waits while waveform analyze (or tidy) is still running, so two jobs don’t fight over the library database.
- Stopping analyze with two listeners uses one shared time budget instead of stacking long waits.

**Other analyze polish**
- Mid-only body listens get full credit (not a confusing “weak + multi-window” label).
- Seed-only retries after a missing-ffmpeg day only re-run when ffmpeg was actually missing before — not every launch.
- Post-analyze tidy (album clustering) runs in the background so the window doesn’t freeze.
- Decode abort kills every in-flight ffmpeg when you stop.

### Earlier 1.3.6 refreshes (still included)

**2026-10-03 — path-to-9 blend**
- Soft genre+BPM residual and normal genre clamp slightly looser; good tags only fine-tune Kinetic; mid+late body trust when the intro is clearly offset; Focus lightly prefers sure pins.
- **Bug fixes:** mid-shaky listens don’t get a wider Kinetic shove; tag↔path conflict keeps evidence-opened envelopes; normal build-ups keep more say; confidence `0.0` counts as unsure.

**2026-10-02 — queue / Rescan / search / reliability**
- Dead next / sole-track next no longer hard-restarts or strands playback; deferred analyze status is honest.
- Rescan keeps prior waveform placement until a new decode lands; matrix re-click restarts in place; zoom LOD recovers; missing host ffmpeg warns at startup.
- Search: double-click or Enter injects play + queue; single-click only highlights.
- High-severity reliability: ffmpeg pipe closes, scan/analyze generation guards, loved tracks stay in NOW/DEEP when they fit, scaled ffmpeg timeouts.

**2026-09-25 — mood-map import UX**
- Same-genre tracks form clouds after scan; denser starfield for large libraries; throttled map refresh during analyze; sticky job status; 1 song = 1 star.

**2026-09-23 and earlier in 1.3.6**
- Mid-fade credit, Various Artists album buckets, BPM 0 ignored at scan, Renew Queue first-run tip, pin lock rings on the map.
- **Renew Queue:** fresh recommendation pass from the current listen matrix without treating replaced songs as skips.

### Packaging

- AppImage baseline remains Ubuntu 24.04 / glibc 2.38+.
- Host **ffmpeg** required for mood analysis; system **libx264** for H.264 playback through Qt’s FFmpeg plugin (not bundled).
- Release AppImage refreshed **2026-10-18** with analysis upgrades, placement honesty, and kept-prior defer fix (plus earlier path-to-9 / dual-worker / map-completeness work).
- **Bug fix:** AppImage AppStream stamp now updates only the newest release row (older history like 1.3.5 stays intact).
- **Bug fix:** GitHub “Refresh AppImage” workflow no longer fails with Permission denied when writing the SHA256 checksum after a Docker/proot build.


## [1.3.5](https://github.com/dark1ltg/Meridian/releases/tag/v1.3.5) — 2026-09-06 (AppImage refreshed 2026-09-09)

Richer acoustic mood cues from the existing PCM decode budget, smarter local recommendations, more honest initial placement (soft-PCM and conflict handling), desktop integration, and reliability passes for scan, matrix/context queue, crossfade accounting, quit/analyze teardown, mood map, OpenGL/EGL startup, and BPM/signal trust.

### Acoustic profile
- Multi-band frequency energy and spectral flux from the current FFT/PCM path
- RMS dynamics (mean, variation, peak, range, trend, consistency) and onset density/burstiness/consistency
- Nonlinear brightness mapping; confidence reflects spectral/rhythm stability
- Local-window aggregation inside the decode; map remains Shadow↔Glow / Still↔Kinetic
- Longer tracks use two ~14s windows (early + mid) within the same ~28s mono @ 11025 Hz budget; short tracks keep a single window
- Disagree-aware dual-window merge: strong intro/drop mismatch keeps the stabler window instead of a false middle
- Richer 2D blend from band/mid balance, flux, and energy trend; tonal vs noisy weighting (flatness pulls Glow down)
- Energy uses RMS consistency and peak; burstiness textures Kinetic only when onset structure is trustworthy
- Evidence-gated soft-PCM: stable waveform that clearly disagrees with a genre+BPM seed may move farther (Glow freer than Kinetic)
- Container/catalog labels (OST, soundtrack, game, VGM, …) stay neighborhood priors but do not soft-lock PCM like acoustic genres
- Kinetic leans on motion/onset/flux over raw loudness; steady loops dampen loudness-as-energy; lighter ZCR weight
- Dual-window strong disagree keeps the stabler base and injects Kinetic contrast from the active window
- Tag vs path genre conflict reduces metadata authority; freed weight goes to PCM only in proportion to PCM trust (not maxed)
- Structure-aware soft/genre clamps: steady rhythm + genre disagreement trusts PCM more; unstable material hugs the seed
- When tag and detected BPM conflict, energy nudge trusts detected if rhythm is steady, otherwise the tag
- Persists onset consistency, spectral flux, and brightness for Focus ranking and album spread (no re-decode)
- After album smooth, within-album acoustic spread unsticks clones using those cues (pins untouched; default max shift ~0.07)
- Expanded local genre seeds (phonk, hyperpop, drill, vaporwave, amapiano, city pop, and related aliases)
- Pins, metadata, and total decode budget unchanged

### Recommendations (local)
- Skip pressure (recent skips) widens the lens neighborhood, softens NOW stickiness, and steals queue slots into FILL
- Context queue soft-caps artist/album repeats (2) while alternatives exist, then relaxes to fill gaps
- Gap-fill protects the matrix mix: NOW/DEEP/FILL first (diversity on, then off); SHELF only after, with a small cap
- Mode-aware matrix mix: Focus steadier NOW/DEEP; Charge more NOW; Dim more DEEP; Wander balanced
- Finish vs skip history weights importance more strongly; listen nudges on unpinned stars are slightly stronger
- Focus mode lightly prefers tracks with steady persisted onset consistency

### Acoustic reliability
- Spurious aubio BPM on silence / near-zero onsets is rejected (needs real onset evidence)
- Tag BPM `0` / NaN is ignored — no soft-PCM shortcut, false BPM conflict, or confidence “BPM” claim
- Prefer a real tag BPM; otherwise use detected BPM (never treat `0` as present)
- Non-finite PCM (Inf/NaN) fails the signal check; mood values are sanitized before write
- Pinned tracks keep BPM through analyze (same protection as valence/energy)
- Upsert that resets `analyzed=0` clears the in-process analyze denylist so Rescan can retry

### Placement fixes
- Soft genre+BPM: tagged BPM no longer undoes the soft PCM energy envelope
- Quiet near-floor hiss no longer maps as Glow; bass darkness counted once via band glow
- Brightness contributes to Shadow↔Glow only (not Still↔Kinetic)
- Relative spectral flux with log mapping — steady / evolving / noisy stay distinct

### Library scan safety
- Empty or missing folders no longer wipe the library (`delete_missing` only after a successful walk finds audio)
- Multi-root scans: an empty sibling root (unmounted drive, empty mount) no longer prunes that root’s DB tracks just because another root still has audio
- Sparse/wrong mounts: a root is not pruned when this walk finds less than half the tracks the DB already knows under it
- Partial / unreadable trees are not pruned — only fully walked roots are eligible for cleanup
- File symlinks that resolve outside a library root are ignored; symlink directories are not descended into
- Failed scans no longer look like success (UI does not start analyze on failure)

### Queue & playback reliability
- Playback errors (corrupt/unsupported media) auto-skip and advance like missing files instead of stalling
- Outgoing-deck errors during a crossfade no longer skip the incoming track
- Corrupt/unsupported files are denylisted for the session (cleared on rescan) so they do not re-enter the context queue
- Hard-cut play credit is undone if media errors before ~500ms of progress
- Crossfade auto-advance ignores provisional short durations and never arms in the first 10s (VBR false ends)
- Spurious EndOfMedia at the start of a longer incoming track no longer ejects it when the fade settles
- Quit/rescan disconnects worker UI slots, kills in-flight ffmpeg, and keeps timed-out threads alive until they exit (no UAF)
- Context queue gap-fill no longer raids SHELF while NOW/DEEP/FILL still have unused tracks (anti-repeat yields first)
- Worker finished/progress slots are queued to the UI thread; QThread.wait() is never called from the worker itself (fixes abort: “Thread tried to wait on itself”)
- AppImage/startup no longer forces desktop OpenGL by default (broken EGL/DRI hosts); `MERIDIAN_GL=desktop` restores the old preference; `LIBGL_ALWAYS_SOFTWARE=1` / `QT_OPENGL=software` prefer software + non-GL map viewport
- Lens drag and star pin refresh the map **without** rebuilding the context queue mid-listen
- Scan / rescan / analyze refresh the matrix and map without replacing the live context queue
- Map star play syncs `queue_index` (or inserts like a matrix pull) so Next/Prev stay aligned
- Search snaps the lens and refreshes the Eisenhower matrix before pulling the hit
- Plan refresh and replenish both skip missing files; rebuilds keep ephemeral “play once” rows still in order
- Listen-nudge plan refreshes deferred under the play lock are flushed after unlock
- Tiny libraries no longer hard-cut restart the track that just finished (hard-exclude on replenish)
- Short tracks that end during a crossfade still advance when the fade settles
- Empty replenish after nearly-finished no longer leaves playback permanently stuck
- When the playing track is absent from a rebuilt queue, Next lands on the first row (not the second)
- Play credit is deferred until a hard cut or a settled fade; aborting a fade commits the armed incoming play
- Next during a natural end-of-track fade finish-nudges the outgoing track (never skip-credits it), then leaves the incoming under the 8s rule
- Map / matrix / search jumps use the same 8s skip-vs-finish rule as Next (not always “finished”)
- Prev during crossfade restores the outgoing track without double `play_count` or spurious finish on the next song
- Pause / seek / stop mid end-of-track fade still finish-nudges the outgoing track and commits the incoming play
- Failed analyze is denylisted in-process (with DB retries) so a poison file cannot loop the worker

### Mood map interaction
- Lens sits under live stars so tracks inside the lens stay clickable and draggable
- Starfield rebakes only when the bake fingerprint changes (fewer hitches on large libraries)
- Sky press candidates survive mid-press refreshes; core-star pin no longer pans the viewport first
- Zoomed-in clicks materialize the nearest baked star; title double-click plays the track
- Deferred lens snap so double-click empty can reset without first snapping the lens
- In-progress pins stay visible during pinch/zoom; star grid updates while dragging

### Desktop / AppImage
- Freedesktop `.desktop`, hicolor icons (16–512 + SVG), AppStream metainfo
- `Meridian-*.AppImage --install` / `--uninstall` registers a normal menu entry under `~/.local/share`
- AppImage does not ship `libx264` (GPL-2.0-only); host `x264`/`libx264` is used for H.264 via Qt’s FFmpeg plugin
- Sticky startup / status tip when host `libx264` is missing under the FFmpeg media backend
- Safer quit: longer waits for scan/analyze; do not `deleteLater` live threads; do not close SQLite under workers that outlive the wait
- Licence compliance pack: full LGPL-3/GPL texts for Qt, `BUILD_LIBRARIES.txt` inventory, expanded `SOURCE_OFFER` for bundled GPL libs
- **Baseline support:** release AppImage targets **Ubuntu 24.04 LTS** (x86_64); requires **glibc 2.38+** (24.04 provides 2.39)
- Portable build helpers: `packaging/build-appimage-ubuntu2404.sh`, `Dockerfile.ubuntu2404`, `audit-appimage-glibc.py`

## [1.3.4](https://github.com/dark1ltg/Meridian/releases/tag/v1.3.4) — 2026-09-06

Bugfix — queue reliability and mood-map correctness.

### Queue & analyze
- Missing / deleted tracks no longer freeze the queue (depth-capped skip; replenish ignores gone files)
- Failed analyze marks poison files done so the worker cannot loop forever
- Analyze abort / restart races guarded with generation + closing flags
- Skip during crossfade credits the outgoing track, not the incoming one

### Mood map
- Pin drag survives mid-drag map refreshes (zoomed and sky hold)
- Lens ring matches mood-space selection (ellipse + mode radius scale)
- Trackpad lens scroll uses `pixelDelta` when `angleDelta` is 0
- Live hit targets sized to the drawn star
- Sticky pan after sky pin/pan cleaned up
- Double-click empty returns to the full sky; play only on a tight star core
- Backdrop chrome no longer leaks if redrawn

## [1.3.3](https://github.com/dark1ltg/Meridian/releases/tag/v1.3.3) — 2026-09-06

Bugfix — library safety, map interaction, scan/analyze reliability.

- Empty / missing folders no longer wipe the library on scan
- Sky pan vs pin: empty-space pans stay pans; drag-to-pin only from a tight press on the star
- Safer quit during analyze
- Pin confidence / notes survive rescan upserts
- Analyze restarts if new unanalyzed tracks appear mid-run
- Listen nudge credits finish after natural crossfade
- Remain time, `added_at`, search disambiguation, and worker `deleteLater` fixes

## [1.3.2](https://github.com/dark1ltg/Meridian/releases/tag/v1.3.2) — 2026-09-06

- Pytest suite and smoke scripts
- AppImage excludes pytest / numpy tests from the bundle

## [1.3.1](https://github.com/dark1ltg/Meridian/releases/tag/v1.3.1) — 2026-09-06

- Sky-mode star drag-to-pin fix

## [1.3.0](https://github.com/dark1ltg/Meridian/releases/tag/v1.3.0) — 2026-09-06

- Confidence polish and library/genre percentile rescale (pins stay put)
