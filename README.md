# Meridian-lab

This folder is the workshop for [Meridian](https://github.com/dark1ltg/Meridian).

Meridian is a music player that lives on your own computer. It does not stream, and it does not ask you to sign in. Your songs stay in your folders. This lab is where new ideas for that player get tried. The everyday app, the one with real releases, stays in the other repository.

Ideas here can break, change shape, or get thrown away. That is what the workshop is for.

![A look at the player](docs/screenshots/01-overview.png)

<p align="center">
  <img src="docs/screenshots/02-mood-map.png" alt="The night sky and the lens" width="48%" />
  &nbsp;
  <img src="docs/screenshots/03-matrix-queue.png" alt="The four lists and the queue" width="48%" />
</p>

## What you do with it

Your library is drawn as a night sky. Each song is a star.

- Left to right is mood, from shadow to glow.
- Bottom to top is energy, from still to moving.

Drag the lens to the part of the sky you feel like. The four lists beside the map fill with the songs under that lens:

- **NOW** — play these
- **DEEP** — stay with this mood
- **FILL** — songs that fit in between
- **SHELF** — keep these nearby, not yet

When that neighborhood feels right, press **Renew**. That commits the queue. The songs already playing stay put. Replaced songs are not counted as skips.

Double-click a star to play it. Drag a star to lock its mood in place. Search finds a title, a person, an album, or a file name.

## What the lab is trying right now

The sky is the experiment. The way you drive it stays the same: move the lens, read the four lists, press Renew when you want a new queue.

Stars stay quiet until the lens covers them. Inside the lens they take the color of their list. The committed queue is a path drawn on the sky. The song that is playing pulses. Starting a song sends a short ripple through the songs around it. A search darkens the sky and flares the match. Zooming back out to the whole sky eases away. Night stars look crisp, and daytime stars look a little hazy. A long song is a bigger star. A short one is a speck.

The four lists are plain names. None of this is a promise that the everyday app will look the same.

## Keep this workshop separate

Open this folder as its own project. A cloud agent for an experiment should use the **Meridian-lab** repository. Lab branches stay here. The stable Meridian project is a different repo.

How this copy was filled in is written down in [POPULATE.md](POPULATE.md).

## Try it

On a recent Ubuntu-style Linux, the packaged app is `dist/Meridian-x86_64.AppImage` after a build. Older systems may not run that file.

```bash
chmod +x Meridian-x86_64.AppImage
./Meridian-x86_64.AppImage
```

To run from the source folder, use the system Python with Qt, then:

```bash
/usr/bin/python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install mutagen numpy aubio
bash scripts/run.sh
```

The player can still start if the tempo library is missing. It just skips that part of the listen. The first launch looks in your Music folder if you have one.

Building the package is `bash packaging/build-appimage.sh` on this machine, or `bash packaging/build-appimage-ubuntu2404.sh` when you want a build that matches Ubuntu 24.04.

## The everyday app

Downloads and release notes for the stable player: [Meridian releases](https://github.com/dark1ltg/Meridian/releases).

Deeper notes on this lab, in plain language:

- [How a song gets a short listen](docs/audio-analysis-pipeline.md)
- [How that listen becomes a place on the sky](docs/placement-pipeline.md)
- [Doc index](docs/README.md)
- [What changed](CHANGELOG.md)

Meridian is free software under the GNU General Public License v3.0. See [LICENSE](LICENSE).
