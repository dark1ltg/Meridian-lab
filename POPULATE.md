# Meridian-lab setup (separate repo + separate Cursor workspace)

`dark1ltg/Meridian-lab` is the experimental fork. Stable product stays in `dark1ltg/Meridian`.

## 1. File tree

Lab `main` was updated 2026-11-08. The lab AppImage from that day ships an LGPL ffmpeg for mood analysis. It started from `dark1ltg/Meridian` at `b48f89d` and has moved on (night-sky experiments, a more honest short listen, the slide and search-line, a sharper patch that looks up only the songs in view, and a dim middle star for every song the listen and the sticker both miss). The time bar marks the stretches that listen used. Songs heard before those marks were saved use the plan their length would have used until the next listen stores the real starts. A pinch that starts up close keeps the stars on screen, and packed stars draw dimmer. When the start, middle, and end agree, the star looks a little more sure. A shaky song on a tight album looks less sure and stays where the listen put it. History was not mirrored; lab commits stay on `main`. See [CHANGELOG.md](CHANGELOG.md).

To replace this repo with a full history mirror later, from a machine logged in as **dark1ltg**:

```bash
git clone --bare https://github.com/dark1ltg/Meridian.git /tmp/Meridian.git
cd /tmp/Meridian.git
git push --mirror https://github.com/dark1ltg/Meridian-lab.git
```

That overwrites lab history. Re-apply the experimental README banner afterward.

## 2. Open a NEW Cursor workspace (required)

Do **not** keep experimenting inside the Meridian workspace. This agent session is bound to Meridian and cannot switch.

- **Desktop:** `git clone https://github.com/dark1ltg/Meridian-lab.git` → Cursor → **Open Folder** on that clone (new window).
- **Cloud Agent:** start a **new** agent with repository `dark1ltg/Meridian-lab`.

Optional: after the mirror, re-add the experimental README banner and commit on Meridian-lab only.
