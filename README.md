> **Experimental playground — not the stable Meridian release line.**
>
> This repository (`dark1ltg/Meridian-lab`) is a scratch space for Meridian ideas.
> Expect breakage, rewrites, and abandoned experiments.
>
> **Open this repo as its own Cursor workspace / Cloud Agent project.** Do not use the Meridian workspace for lab work.
>
> **Stable releases live in [`dark1ltg/Meridian`](https://github.com/dark1ltg/Meridian).**

# Meridian-lab

[![Release](https://img.shields.io/github/v/release/dark1ltg/Meridian?label=release&color=e8b86d)](https://github.com/dark1ltg/Meridian/releases/latest)

**Your library is a night sky. Navigate by feel.**

Meridian-lab tracks the Meridian codebase for experiments. Upstream Meridian is a local, offline Linux music player. Every track becomes a star on a mood map — **Shadow → Glow**, **Still → Kinetic**. Aim a lens where you want to be, and a context queue builds from that neighborhood. No accounts. No streaming. Your files stay on your disk.

![Meridian overview](docs/screenshots/01-overview.png)

<p align="center">
  <img src="docs/screenshots/02-mood-map.png" alt="Mood map with selection lens" width="48%" />
  &nbsp;
  <img src="docs/screenshots/03-matrix-queue.png" alt="Listen matrix and context queue" width="48%" />
</p>

## Features

- **Mood map** — browse by atmosphere instead of folders; drag the lens, scroll to resize, pinch to zoom
- **Listen matrix** — nearby tracks sorted into NOW / DEEP / FILL / SHELF
- **Context queue** — replenishes from the lens, time of day, and listening mode (Focus, Wander, Charge, Dim); **Renew queue** pulls a fresh queue from the current matrix without counting replaced songs as skips
- **Local analysis** — tags plus a short waveform listen (`ffmpeg`: intro/mid/end on long tracks, shifted past silent heads); optional **aubio** for tempo/onset cues; SSD libraries may listen with two workers at once
- **Pins & search** — drag a star to lock its mood; search by title, artist, album, or path
- **Crossfade** — smooth advances while something is already playing

## Get Meridian

### System requirements (AppImage)

**Baseline:** **Ubuntu 24.04 LTS** (or newer) on **x86_64**.

The release AppImage needs **glibc 2.38+** (Ubuntu 24.04 ships **2.39**). Distros with older glibc (for example AlmaLinux 9 / glibc 2.34) will not run it. AlmaLinux 10 (glibc 2.39) matches the baseline.

Also install host **`ffmpeg`** for mood analysis. For H.264 through Qt’s FFmpeg plugin, install system **`x264` / `libx264`** (not bundled). Meridian warns if that library is missing.

On broken GPU/EGL setups (some VMs / missing Mesa), Meridian **does not** use an OpenGL mood-map viewport by default. The map runs on a normal software widget path. To opt into GPU acceleration when your drivers are healthy:

```bash
MERIDIAN_GL=1 ./Meridian-x86_64.AppImage
# or force classic desktop OpenGL:
MERIDIAN_GL=desktop ./Meridian-x86_64.AppImage
```

If a GPU build still fails to start, use:

```bash
LIBGL_ALWAYS_SOFTWARE=1 QT_OPENGL=software ./Meridian-x86_64.AppImage
# or:
MERIDIAN_NO_GL=1 ./Meridian-x86_64.AppImage
```

### AppImage (recommended)

Download **`Meridian-x86_64.AppImage`** from the [latest release](https://github.com/dark1ltg/Meridian/releases/latest).

```bash
chmod +x Meridian-x86_64.AppImage
./Meridian-x86_64.AppImage --install    # menu entry + icons under ~/.local/share
./Meridian-x86_64.AppImage              # or launch from your app menu
```

`--uninstall` removes the menu entry and icons.

### Run from source

Needs system PySide6 (Qt 6) plus a venv for analysis libraries:

```bash
/usr/bin/python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install mutagen numpy aubio
bash scripts/run.sh
```

`aubio` needs the native library (e.g. Arch/CachyOS: `sudo pacman -S aubio`). Without it, Meridian still runs; tempo/onset cues are skipped.

**Add library folder** imports music. `~/Music` is scanned on first launch if it exists. **Rescan** refreshes tags and re-analyzes every track.

### Build the AppImage

For the **supported** release-style binary (Ubuntu 24.04 / glibc ≤ 2.39), prefer:

```bash
bash packaging/build-appimage-ubuntu2404.sh
# → dist/Meridian-x86_64-ubuntu2404.AppImage  (also Meridian-x86_64.AppImage)
```

Uses Docker/Podman with `ubuntu:24.04`, or a local Ubuntu 24.04 rootfs via proot when containers are unavailable.

A plain host build may only run on machines as new as your build PC:

```bash
bash packaging/build-appimage.sh
./dist/Meridian-$(uname -m).AppImage --install   # optional
```

## Quick use

| Action | How |
|---|---|
| Aim the queue | Drag the lens on the mood map |
| Resize neighborhood | Scroll on the map |
| Zoom / pan | Pinch or Ctrl+scroll; drag empty space |
| Pin a mood | Drag a star |
| Renew context queue | **Renew queue** on the context-queue panel (new pass from the current matrix) |
| Play | Double-click a star, matrix row, or queue row; Space plays/pauses |
| Search | Ctrl+F |

Release history: [CHANGELOG.md](CHANGELOG.md) · [GitHub Releases](https://github.com/dark1ltg/Meridian/releases)

## Documentation

How Meridian works under the hood (plain language):

- [How Meridian listens](docs/audio-analysis-pipeline.md) — the short listen of each track  
- [How stars get placed](docs/placement-pipeline.md) — sticker + listen → mood map  
- [Docs index](docs/README.md)

## License

Meridian is free software under the **GNU General Public License v3.0**.  
See [LICENSE](LICENSE) / [COPYING](COPYING).

Third-party components: [THIRD_PARTY_NOTICES.txt](THIRD_PARTY_NOTICES.txt).  
Ubuntu fonts ship under the Ubuntu Font Licence 1.0 in `resources/fonts/`.  
AppImage builds include notices under `usr/share/doc/meridian/` with [SOURCE_OFFER.txt](SOURCE_OFFER.txt).


---

## Lab setup status

This repo was created empty (GitHub does not allow forking your own repo to yourself). Population from `dark1ltg/Meridian` `@ b48f89d` is in progress via Cloud Agent + GitHub MCP.

**If the tree looks incomplete**, finish with a history-preserving mirror from your machine (you own the repo; the Cursor GitHub App installation token cannot push here):

```bash
git clone --bare https://github.com/dark1ltg/Meridian.git /tmp/Meridian.git
cd /tmp/Meridian.git
git push --mirror https://github.com/dark1ltg/Meridian-lab.git
```

After a mirror, re-apply this README banner (mirror overwrites `main`/`master` history).

### Open as a separate Cursor Cloud project

1. [cursor.com](https://cursor.com) → Cloud Agents / new project  
2. Connect repo **`dark1ltg/Meridian-lab`** (not Meridian)  
3. Use a dedicated environment so lab experiments never touch Meridian branches  
