# Documentation

Extra guides for how Meridian listens to your files and puts them on the mood map. These notes match lab `main` as of 2026-11-11.

The [main README](../README.md) is the short “what is this / how do I run it” page.  
Release history is in [CHANGELOG.md](../CHANGELOG.md).

## System requirements (release AppImage)

| | |
|---|---|
| **Baseline OS** | Ubuntu 24.04 LTS (x86_64), or equivalent |
| **glibc** | **2.38+** required (Ubuntu 24.04 has **2.39**) |
| **Host tools** | `x264` / `libx264` for H.264 via Qt’s FFmpeg plugin. Mood analysis uses the LGPL `ffmpeg` shipped in the AppImage. Aubio in the image is built without libavcodec, so tempo does not need host `libx264.so.164` (Kubuntu 26.04 ships `.165` only). A source checkout still needs `ffmpeg` on `PATH`. |
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

The time bar marks each stretch the short listen actually used. Click a mark and the song jumps there. The star stays put. Songs heard before these marks were saved use the plan their length would have used until the next listen stores the real starts. The listen can refuse a fake pulse, remember speech, and keep a real fast tempo. A song with no listen and no sticker still gets a dim star in the middle. Three tastes that agree make a star a little more sure. A shaky song on a tight album looks less sure and is not dragged toward its neighbors. On the sky, a confidence of zero stays a weak star, a finish or skip slides an unpinned star, and search draws a short trip line that is not the queue. Stars do not move to unstack, and a shared exact spot stays one dot. Under 1.6× the wide picture shows a pile as one dot and a count, with coarse neighborhood lines and no names. From 1.6× to 2.4× the sharper patch stays, the coarse lines fade out, and finer lines are not drawn. The dot under the pointer can show a short name, and clicking a pile zooms closer. Stars cannot be grabbed until the close view has settled. At 2.4× the lines are gone, the count fades, and the songs become real stars. Names are dim at 2.4× and readable by 2.6×. Each close star shows one short line: how sure the spot is, or “no listen” when that is why it sits in the middle. A pin shows that it is pinned. The playing song shows that it is playing. There is no card on the star. The zoom stops at 5×. From 2.6× to 5× the same stars stay put, so a loose cluster opens up. The 220 live stars cover the cluster under the lens. The wide picture is cut out of that cluster, and the edges of the view stay the picture. A pinch that starts up close keeps those stars on the sky. While you pinch, the pile fades, and a drag still slides the map. The star under the pointer stays bright, including on the wide sky. A pin or the song that starts playing updates that one star. Stars packed together draw dimmer. The song playing, a pin, and the star under the pointer stay bright.

**Context queue tip:** moving the lens updates the listen matrix, but the playing context queue stays put until it replenishes or you press **Renew queue**. Renew asks for another selection from the *current* matrix — it is not a skip storm.

Screenshots for the README are in [`screenshots/`](screenshots/).
