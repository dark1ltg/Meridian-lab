# Documentation

Extra guides for how Meridian listens to your files and puts them on the mood map. These notes match lab `main` as of 2026-10-22.

The [main README](../README.md) is the short “what is this / how do I run it” page.  
Release history is in [CHANGELOG.md](../CHANGELOG.md).

## System requirements (release AppImage)

| | |
|---|---|
| **Baseline OS** | Ubuntu 24.04 LTS (x86_64), or equivalent |
| **glibc** | **2.38+** required (Ubuntu 24.04 has **2.39**) |
| **Host tools** | `ffmpeg` for mood analysis; `x264` / `libx264` for H.264 via Qt’s FFmpeg plugin |
| **Display** | OpenGL mood-map viewport when the driver probe succeeds; software widget if it does not |

Older glibc (e.g. AlmaLinux 9 / 2.34) will not run the release AppImage. AlmaLinux 10 (glibc 2.39) matches the baseline. Build with `packaging/build-appimage-ubuntu2404.sh` for that target.

The mood map uses desktop OpenGL by default. Startup probes the driver in a helper process, because creating a GL viewport can hard-abort on broken EGL/DRI hosts and `try/except` cannot catch that. A failed probe sets `MERIDIAN_NO_GL=1` and the map uses a plain widget. These also keep the software map:

```bash
MERIDIAN_NO_GL=1 ./Meridian-x86_64.AppImage
MERIDIAN_GL=off ./Meridian-x86_64.AppImage
LIBGL_ALWAYS_SOFTWARE=1 QT_OPENGL=software ./Meridian-x86_64.AppImage
```

| Guide | In plain words |
|---|---|
| [How Meridian listens](audio-analysis-pipeline.md) | How it takes a short listen of each song |
| [How stars get placed](placement-pipeline.md) | How that listen (plus tags) becomes a spot on the map |

The listen can refuse a fake pulse, remember speech, and keep a real fast tempo. Three tastes that agree make a star a little more sure. A shaky song on a tight album looks less sure and is not dragged toward its neighbors. On the sky, a confidence of zero stays a weak star, a finish or skip slides an unpinned star, and search draws a short trip line that is not the queue. A medium zoom, after the pinch stops, draws one sharper picture of the patch on screen from the songs in that patch, then a tighter one a bit closer. Only one is kept. Neighborhood lines get finer there. In close, the clickable stars take over. A pinch that starts up close keeps those stars on the sky. While you pinch, the pile fades, and a drag still slides the map. When you let go before the close view, the picture is whole again. The star under the pointer stays bright, including on the wide sky. A pin or the song that starts playing updates that one star. Stars packed together draw dimmer. The song playing, a pin, and the star under the pointer stay bright.

**Context queue tip:** moving the lens updates the listen matrix, but the playing context queue stays put until it replenishes or you press **Renew queue**. Renew asks for another selection from the *current* matrix — it is not a skip storm.

Screenshots for the README are in [`screenshots/`](screenshots/).
