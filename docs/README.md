# Documentation

Extra guides for how Meridian listens to your files and puts them on the mood map.

The [main README](../README.md) is the short “what is this / how do I run it” page.  
Release history is in [CHANGELOG.md](../CHANGELOG.md).

## System requirements (release AppImage)

| | |
|---|---|
| **Baseline OS** | Ubuntu 24.04 LTS (x86_64), or equivalent |
| **glibc** | **2.38+** required (Ubuntu 24.04 has **2.39**) |
| **Host tools** | `ffmpeg` for mood analysis; `x264` / `libx264` for H.264 via Qt’s FFmpeg plugin |
| **Display** | Software mood-map viewport by default; set `MERIDIAN_GL=1` (or `desktop`) for OpenGL |

Older glibc (e.g. AlmaLinux 9 / 2.34) will not run the release AppImage. AlmaLinux 10 (glibc 2.39) matches the baseline. Build with `packaging/build-appimage-ubuntu2404.sh` for that target.

OpenGL for the mood map is **opt-in** (`MERIDIAN_GL=1` / `desktop`) because creating a GL viewport can hard-abort on broken EGL/DRI hosts. Default and software paths set `MERIDIAN_NO_GL=1` so the map uses a plain widget:

```bash
MERIDIAN_GL=1 ./Meridian-x86_64.AppImage
LIBGL_ALWAYS_SOFTWARE=1 QT_OPENGL=software ./Meridian-x86_64.AppImage
MERIDIAN_NO_GL=1 ./Meridian-x86_64.AppImage   # skip OpenGL mood-map viewport
```
| Guide | In plain words |
|---|---|
| [How Meridian listens](audio-analysis-pipeline.md) | How it takes a short listen of each song |
| [How stars get placed](placement-pipeline.md) | How that listen (plus tags) becomes a spot on the map |

**Context queue tip:** moving the lens updates the listen matrix, but the playing context queue stays put until it replenishes or you press **Renew queue**. Renew asks for another selection from the *current* matrix — it is not a skip storm.

Screenshots for the README are in [`screenshots/`](screenshots/).
