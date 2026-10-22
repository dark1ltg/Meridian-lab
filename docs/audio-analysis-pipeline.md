# How Meridian listens (audio analysis)

This guide explains, in plain language, how Meridian takes a **short listen** of each track on your disk.  
It does not need the internet. It does not stream music.

For how that listen turns into a star on the map, see [How stars get placed](placement-pipeline.md).

## The big idea

Meridian does **not** listen to the whole song every time.

On long enough tracks it takes **three 12-second tastes** — **intro**, **mid**, and **late** (about **36 seconds** total).  
Those tastes are blended with a **mid-heavy** mix (roughly 20% intro / 50% mid / 30% late), not a blind average.  
If the intro head is near-silent (game cues, long pads), windows **shift onto the first audible stretch** so Meridian isn’t tasting only hush.

Shorter tracks get **one or two** windows only — Meridian will not triple-sample the same few seconds of audio.

From that listen it guesses:

- **Shadow ↔ Glow** — darker / softer vs brighter / more open  
- **Still ↔ Kinetic** — calm vs moving / punchy  

That’s the “feel” it will use on the mood map.

## Step by step

### 1. Import (scan)

Meridian walks your music folders and reads the **labels** on the files (title, artist, genre, and so on).

- It does **not** do the deep listen yet.  
- It may park a rough guess on the map from the genre/folder name.  
- It marks the track as “still needs a real listen.”

### 2. A guess from the sticker (tags)

Before listening, it looks at:

- Genre tags  
- Folder names (sometimes more honest than the tag)  
- Year, loudness tags if present  
- Mood-ish words in titles  

That gives a starting “neighborhood” — like “this is probably metal-energy” or “probably chill.”

### 3. The short listen (`ffmpeg`)

It asks your computer’s `ffmpeg` for those section windows. Meridian does **not** ship `ffmpeg` inside the AppImage — it must be on the host `PATH`.

- Seeks stay inside the known duration (unknown length → about **28 seconds** from the start, not a tiny stub).  
- On long tracks, intro / mid / late are compared. A silent or cold-open intro does **not** veto a clear mid+late body. When mid and late **agree closely** *and* the intro is clearly offset (not a mild build-up), Meridian soft-biases that body so a flashy intro can’t yank the pin — normal intros keep more say. When sections disagree, it keeps the **steadier** song-like taste and may inject contrast from a more active window — not a fake middle.  
- If only the intro decoded and mid+late failed, analyze still **places** the track as weak/low-trust (`intro only`) so the map shows the full collection — after trying the same longer (~28s) salvage listen used on short/medium dual plans. Late-only / ends-only are also weak-placed (`end only` / `ends only`).  
- If there is no usable audio **and** no genre/path/keyword evidence, analyze **defers** (`pcm pending`) for a later retry. Tags alone conclude as `seed only` (low-trust).  
- If a later pass can’t hear audio but Meridian **already kept a good pin** (`pcm pending · kept prior`), that row is **concluded as analyzed** so the whole library isn’t re-queued every launch. **Rescan** still clears analyzed on purpose when you ask for a fresh listen.  
- If `ffmpeg` is missing, Meridian still plays music, but analyze leaves stars on genre/folder seeds instead of inventing a waveform placement. Startup shows a warning dialog and a sticky status tip (same idea as the missing-libx264 tip).  
- Stopping analyze kills every in-flight `ffmpeg` process (one or two analyze workers on SSD/NVMe).  
- Large libraries on flash storage may run **two** analyze workers silently (HDD stays at one).

If **aubio** is installed, it also hears tempo / beat-ish clues. Without aubio, Meridian still works; it just has less rhythm info.

### 4. What it measures in that snippet

Still on each already-decoded buffer (no extra “go fetch more audio” beyond the section plan):

- Bright vs dark tone (loudness-normalized so hot masters don’t fake Kinetic)  
- Bass-heavy vs treble-heavy (including a light sub-bass Shadow vote)  
- Steady tone vs noisy / hissy (and optional tonal / noisy timbre hint)  
- How much the sound changes over time  
- How even or punchy the loudness is  
- How regular or jumpy the hits are  
- Spoken non-musical frames dropped when real music remains. A window that is mostly speech is remembered and can dim confidence. A borderline musical frame is not counted as speech.  

Then it squashes all of that into the two map directions: **Glow** and **Kinetic**.  
Multi-window listens may also store a simple **shape** note (steady / build / drop / bookend) and a **steer weight** for the queue — not new map axes.

Simple rules of thumb Meridian tries to follow:

- Quiet hiss should **not** look bright and happy.  
- Brightness belongs on Glow, not “fake energy.” Clear harmonic tone may set Glow from pitch height; muffled listens stay nearer the sticker.  
- Weird / jumpy timing is a **texture**, not automatic “this is energetic.” A muddy pulse skips the BPM→Kinetic step. A beat that does not land on the heard hits is refused. A fast song whose tracker locked to half speed is checked on a double-time grid before that refusal, so a real fast pulse is kept. A refused pulse does not push Kinetic and does not take the BPM bonus. A tag BPM can still set the pace.
- Quiet but even hits are not treated as faint just because the master is quiet. Hit strength is compared inside the song.
- A clear tone may set Glow from pitch height. A melody that wanders an octave can still count as one held pitch. A pitch that will not hold does not get to move Glow. Muffled listens stay nearer the sticker.
- Uneven, punchy loudness feels more Kinetic than flat, even loudness.  
- Blend leans toward windows that are both tonal and onset-solid (a flashy sting can’t become the whole song).

### 5. Save the result

It stores the map position, a confidence note (“how sure are we” — multi-window listens say so), shape/timbre/steer leftovers when known, and a few other clues (brightness, flux, steady rhythm, speech share, whether the pulse was refused) for later tidy-ups — **without** listening again. A stored confidence of exactly zero stays rock-bottom. A missing confidence is the neutral middle, not the same thing as zero.

**Rescan / mtime refresh:** when Meridian clears the “analyzed” flag so a track will be listened to again, it still **keeps the existing PCM mood coords** if leftover brightness/flux clues remain — so Rescan does not flash stars back to genre seeds until a new successful decode lands. Section blend weights are deterministic (no random jitter on merge).

### 6. Tidy the whole library (after many tracks)

When a batch of listens finishes, Meridian does light housekeeping with **no more audio decode**:

- Nudge unpinned songs a little toward their album/artist neighbors  
- Gently unstick songs that landed on top of each other  
- Stretch positions so your collection uses the map more evenly  

If you **pinned** a star, that pin stays. Analyze will not drag it.

## What this is *not*

- Not “pro studio analysis of the full album.”  
- Not surround / 3D spatial audio. “Spatial” here means **where it sits on the mood map**.  
- Not a promise that every song will feel perfectly placed — short listens can still miss weird structures, but three sections catch more than a single mid-track grab.

## Where the code lives (if you care)

| File | Job |
|---|---|
| `meridian/scanner.py` | Import worker + analyze worker |
| `meridian/features.py` | Tags, section decode, glue |
| `meridian/acoustic.py` | Measuring one snippet buffer |
| `meridian/library.py` | Saving moods and tidy-ups |
