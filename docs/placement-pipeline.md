# How stars get placed (placement)

This guide explains, in plain language, how Meridian decides **where a song’s star sits** on the mood map.

For how it takes the short listen, see [How Meridian listens](audio-analysis-pipeline.md).

## The map in one sentence

- Left ↔ right: **Shadow ↔ Glow** (darker/softer vs brighter/more open)  
- Down ↔ up: **Still ↔ Kinetic** (calm vs moving)

Every track ends up with two numbers that mean those directions. That’s placement.

## The big idea

Placement is a **compromise**:

1. What the **sticker / folder** suggests (genre, path, words)  
2. What the **short listen** actually sounded like  

Meridian decides **how much to trust each side**.  
Good labels keep songs in a sensible neighborhood. A clear, steady listen can still pull a wrong label toward the truth. A muddy listen does **not** get to boss the map around.

## Step by step

### 1. Start with the sticker guess

From tags and folders, Meridian picks a starting spot — the “genre cloud.”

### 2. Compare to the short listen

After analysis, it has a second opinion from the waveform (on long tracks: a mid-heavy blend of intro / mid / late 12s tastes — see [How Meridian listens](audio-analysis-pipeline.md)).

It asks: *How far apart are the sticker guess and the listen?*  
And: *Did the listen sound steady and trustworthy, or shaky?*

### 3. Blend the two (the important part)

Depending on the file, Meridian uses different rules:

**Well-tagged song with a real BPM**  
Stay near the sticker. Only allow a **small** nudge from the listen.  
If the listen is clear, steady, and *obviously* elsewhere, allow a **bigger** nudge on Glow (brightness). Kinetic stays a **fine-tune** when tags are good and the rhythm evidence is truly solid (not mid-shaky) — pace often matches genre/BPM already. Tag↔path genre conflict can open that Kinetic room further and must not claw it back.

**Genre known, but BPM missing / weak / conflicting / container-only**  
Mix sticker and listen inside a slightly looser genre neighborhood. With steady onset evidence, Kinetic may pull harder from the waveform (aubio/PCM) than the soft-lock path allows.

**Messy / empty tags (common on raw dumps)**  
Trust the listen much more.

**Tag says one genre, folder says another** (example: tag “jazz”, folder “DrumAndBass”)  
That means the **labels are arguing**, not that the listen is automatically right.  
Meridian **trusts the labels less**. The listen only gets as much of that freed trust as it earned (steady listen → more; shaky listen → little).  
It does **not** mean “ignore everything and max out the waveform.”

### 4. Tiny tempo tweak

BPM can give Kinetic a small push — but only when the pulse sounds clear enough.  
A refused pulse skips that push and skips the BPM bonus. A tag BPM can still set the pace. A fast song is not refused just because the tracker locked to half speed.  
If the tag tempo and the heard tempo disagree, Meridian picks carefully (steady rhythm → heard tempo can win the nudge; otherwise the tag stays in charge).  
On the “stay near the sticker” path, tempo still can’t kick the star outside that soft neighborhood.

Title/artist mood words that clearly fight a **steady** listen are undone; **genre / folder seed stays**.

### 5. How sure are we?

Meridian stores a confidence note (tag? path? listen? conflict?).  
Low confidence means “take this with a grain of salt” in the UI — it doesn’t invent a third map axis.  
A confidence of exactly zero draws as a weak pewter star. If the number is missing, the star uses the neutral middle. Those two are not the same.  
Focus / NOW ranking gives a **light** lift to higher-confidence listens over dim scrapes at similar distance — not enough to bury a clearly closer neighbor.  
Full multi-window listens **steer** the queue more; seed-only / intro-only listens stay out of NOW unless the neighborhood has nothing better (pins and loved tracks still qualify). Shape/timbre leftovers gently bias Dim / Charge / Focus — still the same two map axes.

### 6. Save (pins stay put)

The star’s position is written to the library.  
If you dragged and **pinned** a star, analyze will not overwrite that pin.

**Rescan does not wipe a real listen:** clearing `analyzed` to re-queue decode still preserves valence/energy (and related PCM fields) when prior brightness/flux clues are still on the row. Stars stay where the last successful listen put them until a new decode replaces them.

### 7. Light tidy after a big analyze

Without listening again:

- Soft pull toward album/artist neighbors (unpinned only)  
- Unstick clones that stacked on the same spot  
- Spread the sky a bit relative to *your* collection  

Rough / partial listens (seed-only, intro-only, end-only) can get nudged toward album mates, but tidy keeps them in the **unsure** confidence band so they still look dim.  

### Partial listens and “seed only”

- Heard *some* audio but not a solid middle → star still placed, marked low-trust.  
- Heard nothing but tags/folders help → rough `seed only` place (better than a missing star).  
- Heard nothing *and* no tags → deferred for a later retry.  
- A later failed listen does **not** overwrite a prior good waveform pin — and that “kept prior” case is marked analyzed so every launch doesn’t re-queue thousands of finished tracks (Rescan still retries on purpose).

## How listening feeds placement

| From the short listen | What placement does with it |
|---|---|
| “Sounds bright / dark” | Pushes Glow |
| “Sounds calm / punchy” | Pushes Kinetic |
| “Listen was steady” | Allowed to move the star more |
| “Listen was shaky / intro≠drop” | Keep the sticker closer; don’t over-trust the waveform |
| Leftover brightness / change / steady-beat clues | Later tidy-ups and Focus ranking — not a second map |

**One line:** listening *feels* the file; placement decides *how far that feeling may move the star* away from the label.

## What placement is not

- Not “always matches your personal taste.” Feel is human and subjective.  
- Not a full-song deep dive (only that short listen).  
- Not the lens chasing whatever is playing — you (or search) move the lens.  
- Not permission for bad tags to be ignored forever, or for noisy audio to rewrite the whole sky.

## After the star is placed

While you listen, finishes and skips can gently nudge **unpinned** stars relative to where your lens is.  
That nudge is drawn as a short slide. A pewter ghost stays on the old spot for about a second. Clicks follow the moving star. A press during the slide keeps the star on the sky. Constellation lines, neighborhood tiles, and the queue path aim at the new spot when the slide starts. A **pin does not slide**.  
Choosing a search result moves the lens, which was already allowed. A short cool line shows the trip, then fades. That line is not the queue path. If the lens is already on that song, no line is drawn.  
Zooming in a little, once the pinch has stopped, draws a sharper picture of the patch on screen. A bit closer, a tighter picture of that same patch replaces it. Only one detail picture is kept, and it uses the same places as the wide sky. Neighborhood lines get finer in that middle zoom. In close, the picture steps aside and the clickable stars take over. A sliding star is left out of the detail picture.  
The matrix and context queue use those positions to pick “what’s near the lens” — that’s ranking, not a brand-new analyze.

## Where the code lives (if you care)

| File | Job |
|---|---|
| `meridian/features.py` | Sticker seed + blend rules |
| `meridian/acoustic.py` | Short-listen measurements |
| `meridian/library.py` | Save, tidy, listen nudges |
| `meridian/ui/mood_map.py` | Draw the stars |
| `meridian/queue_engine.py` | Queue from lens distance (uses placement; doesn’t create it) |
