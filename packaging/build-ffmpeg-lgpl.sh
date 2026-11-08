#!/usr/bin/env bash
# Build a small static LGPL ffmpeg for Meridian mood analysis.
#
# Decode-only: common audio to raw f32le PCM (the analyze command in
# meridian/features.py). No --enable-gpl, no nonfree, no libx264, no libx265.
# The binary is static so PyInstaller's LD_LIBRARY_PATH cannot redirect it
# onto Qt's bundled libavcodec.
#
# Output: .appimage-tools/ffmpeg-lgpl/bin/ffmpeg
#   bash packaging/build-ffmpeg-lgpl.sh          # build if needed, then check
#   bash packaging/build-ffmpeg-lgpl.sh --check BIN
#   bash packaging/build-ffmpeg-lgpl.sh --force  # rebuild even if cached
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FFMPEG_VERSION="9.0.2"
FFMPEG_SHA256="8c3850283eb25fa026482078a04051e0be17347b09ef81a0849bec15a96e002e"
FFMPEG_URL="https://ffmpeg.org/releases/ffmpeg-${FFMPEG_VERSION}.tar.xz"
# Bump when the configure recipe changes so a cached binary is rebuilt.
RECIPE="lgpl-audio-pcm-2"
PREFIX="${ROOT}/.appimage-tools/ffmpeg-lgpl"
SRC_ROOT="${ROOT}/.appimage-tools/src"
TARBALL="${SRC_ROOT}/ffmpeg-${FFMPEG_VERSION}.tar.xz"
BIN="${PREFIX}/bin/ffmpeg"
STAMP="${PREFIX}/FFMPEG_LGPL_BUILD.txt"
MAX_BYTES=$((30 * 1024 * 1024))

usage() {
  echo "usage: $0 [--check BIN] [--force]" >&2
  exit 2
}

CHECK_ONLY=""
FORCE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --check)
      [[ $# -ge 2 ]] || usage
      CHECK_ONLY="$2"
      shift 2
      ;;
    --force) FORCE=1; shift ;;
    -h|--help) usage ;;
    *) usage ;;
  esac
done

ensure_build_tools() {
  local missing=()
  local cmd
  for cmd in gcc make curl tar xz strip; do
    command -v "$cmd" >/dev/null 2>&1 || missing+=("$cmd")
  done
  if ((${#missing[@]} == 0)); then
    return 0
  fi
  if [[ "$(id -u)" -eq 0 ]] && command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y --no-install-recommends \
      build-essential curl ca-certificates xz-utils binutils pkg-config
    return 0
  fi
  echo "ERROR: missing build tools: ${missing[*]}" >&2
  exit 1
}

# Configure-name lists (space-separated) filtered to names this source knows.
pick_csv() {
  local kind="$1"
  shift
  local have picked=() name
  have="$(./configure --disable-x86asm --list-"${kind}" 2>/dev/null | tr ' ' '\n' | sed '/^$/d')"
  for name in "$@"; do
    if grep -qx "$name" <<<"$have"; then
      picked+=("$name")
    fi
  done
  local IFS=,
  echo "${picked[*]}"
}

require_csv() {
  local csv="$1" needle="$2" label="$3"
  if [[ ",${csv}," != *",${needle},"* ]]; then
    echo "ERROR: required ${label} '${needle}' is not in this FFmpeg source" >&2
    exit 1
  fi
}

verify_ffmpeg_lgpl() {
  local bin="$1"
  local dump ldd_out size
  if [[ ! -x "$bin" ]]; then
    echo "ERROR: ffmpeg is not executable: $bin" >&2
    exit 1
  fi
  dump="$("$bin" -hide_banner -version 2>&1 || true)"
  dump+=$'\n'
  dump+="$("$bin" -hide_banner -buildconf 2>&1 || true)"
  if ! grep -q "ffmpeg version" <<<"$dump"; then
    echo "ERROR: ${bin} -version did not report an ffmpeg version" >&2
    printf '%s\n' "$dump" >&2
    exit 1
  fi
  if grep -F -e '--enable-gpl' -e '--enable-nonfree' \
      -e '--enable-libx264' -e '--enable-libx265' \
      -e 'libx264' -e 'libx265' <<<"$dump"; then
    echo "ERROR: ${bin} reports a GPL / x264 / x265 / nonfree build" >&2
    printf '%s\n' "$dump" >&2
    exit 1
  fi
  if grep -E -e '(^|[^A-Za-z0-9])x264([^A-Za-z0-9]|$)' \
      -e '(^|[^A-Za-z0-9])x265([^A-Za-z0-9]|$)' <<<"$dump"; then
    echo "ERROR: ${bin} version/buildconf lists x264 or x265" >&2
    printf '%s\n' "$dump" >&2
    exit 1
  fi
  ldd_out="$(ldd "$bin" 2>&1 || true)"
  if ! grep -E -q 'not a dynamic executable|statically linked' <<<"$ldd_out"; then
    echo "ERROR: bundled ffmpeg must be static (got dynamic linkage)" >&2
    printf '%s\n' "$ldd_out" >&2
    exit 1
  fi
  if grep -E -e 'libx264' -e 'libx265' -e 'libavcodec' -e 'libavformat' <<<"$ldd_out"; then
    echo "ERROR: bundled ffmpeg links GPL/shared ffmpeg libraries" >&2
    printf '%s\n' "$ldd_out" >&2
    exit 1
  fi
  size="$(stat -c%s "$bin")"
  if (( size > MAX_BYTES )); then
    echo "ERROR: ffmpeg binary is ${size} bytes (cap ${MAX_BYTES}); refusing a full static build" >&2
    exit 1
  fi
  if (( size < 500000 )); then
    echo "ERROR: ffmpeg binary is unexpectedly tiny (${size} bytes)" >&2
    exit 1
  fi
}

self_test_decode() {
  local bin="$1"
  local wav bytes
  wav="$(mktemp --suffix=.wav)"
  python3 - "$wav" <<'PY'
import math, struct, sys, wave
path = sys.argv[1]
rate = 44100
n = rate // 2
with wave.open(path, "w") as handle:
    handle.setnchannels(2)
    handle.setsampwidth(2)
    handle.setframerate(rate)
    frames = bytearray()
    for i in range(n):
        sample = int(8000 * math.sin(2 * math.pi * 440 * i / rate))
        frames += struct.pack("<hh", sample, sample)
    handle.writeframes(frames)
PY
  # Same shape as meridian.features._decode_pcm: seek, duration, mono, 11025, f32le on stdout.
  # -nostdin so a tty cannot turn the overwrite prompt into a hang.
  if ! bytes="$("$bin" -nostdin -y -v error -ss 0.000 -t 0.500 -i "$wav" -ac 1 -ar 11025 -f f32le pipe:1 | wc -c | tr -d ' ')"; then
    echo "ERROR: bundled ffmpeg could not decode a wav to f32le" >&2
    rm -f "$wav"
    exit 1
  fi
  rm -f "$wav"
  # 0.5s * 11025 Hz * 4 bytes. Allow a little slack.
  if (( bytes < 20000 || bytes > 30000 )); then
    echo "ERROR: f32le self-test wrote ${bytes} bytes (expected ~22050)" >&2
    exit 1
  fi
}

write_stamp() {
  local bin="$1"
  {
    echo "Meridian bundled ffmpeg (LGPL mood-analysis decode)"
    echo "version: ${FFMPEG_VERSION}"
    echo "recipe: ${RECIPE}"
    echo "source: ${FFMPEG_URL}"
    echo "sha256: ${FFMPEG_SHA256}"
    echo "licence: LGPL-2.1-or-later"
    echo "purpose: common audio to raw f32le PCM (mono, 11025 Hz)"
    echo "not included: --enable-gpl, nonfree codecs, libx264, libx265"
    echo "binary bytes: $(stat -c%s "$bin")"
    echo
    echo "version string:"
    "$bin" -hide_banner -version
    echo
    echo "buildconf:"
    "$bin" -hide_banner -buildconf
  } > "$STAMP"
}

build_ffmpeg() {
  local src decoders demuxers parsers muxers encoders filters protocols
  local -a args
  ensure_build_tools
  mkdir -p "$SRC_ROOT"
  rm -rf "$PREFIX"
  mkdir -p "$PREFIX"
  if [[ ! -f "$TARBALL" ]]; then
    echo "Downloading FFmpeg ${FFMPEG_VERSION}…"
    curl -fL --retry 3 -o "${TARBALL}.partial" "$FFMPEG_URL"
    mv "${TARBALL}.partial" "$TARBALL"
  fi
  local got
  got="$(sha256sum "$TARBALL" | awk '{print $1}')"
  if [[ "$got" != "$FFMPEG_SHA256" ]]; then
    echo "ERROR: FFmpeg tarball sha256 mismatch" >&2
    echo "  expected ${FFMPEG_SHA256}" >&2
    echo "  got      ${got}" >&2
    exit 1
  fi
  rm -rf "${SRC_ROOT}/ffmpeg-${FFMPEG_VERSION}"
  tar -xJf "$TARBALL" -C "$SRC_ROOT"
  src="${SRC_ROOT}/ffmpeg-${FFMPEG_VERSION}"
  if [[ ! -f "${src}/COPYING.LGPLv2.1" ]]; then
    echo "ERROR: upstream tree has no COPYING.LGPLv2.1" >&2
    exit 1
  fi
  (
    cd "$src"
    decoders="$(pick_csv decoders \
      aac aac_latm ac3 alac ape dsd_lsbf dsd_msbf eac3 flac mp1 mp2 mp3 mp3float \
      mpc7 mpc8 opus tta vorbis wavpack wmav1 wmav2 \
      pcm_alaw pcm_f32be pcm_f32le pcm_f64le pcm_mulaw \
      pcm_s16be pcm_s16le pcm_s24be pcm_s24le pcm_s32be pcm_s32le pcm_s8 pcm_u8)"
    demuxers="$(pick_csv demuxers \
      aac ac3 aiff ape asf au caf dsf flac matroska mov mp3 mpc mpc8 ogg tta wav wv)"
    parsers="$(pick_csv parsers aac aac_latm ac3 flac mpegaudio opus vorbis)"
    muxers="$(pick_csv muxers pcm_f32le)"
    encoders="$(pick_csv encoders pcm_f32le)"
    filters="$(pick_csv filters aresample aformat)"
    protocols="$(pick_csv protocols file pipe)"
    require_csv "$decoders" "aac" "decoder"
    require_csv "$decoders" "flac" "decoder"
    require_csv "$decoders" "vorbis" "decoder"
    require_csv "$decoders" "opus" "decoder"
    require_csv "$decoders" "pcm_s16le" "decoder"
    require_csv "$decoders" "pcm_f32le" "decoder"
    require_csv "$decoders" "alac" "decoder"
    if [[ ",${decoders}," != *",mp3,"* && ",${decoders}," != *",mp3float,"* ]]; then
      echo "ERROR: required decoder mp3/mp3float missing" >&2
      exit 1
    fi
    require_csv "$demuxers" "mp3" "demuxer"
    require_csv "$demuxers" "mov" "demuxer"
    require_csv "$demuxers" "flac" "demuxer"
    require_csv "$demuxers" "ogg" "demuxer"
    require_csv "$demuxers" "wav" "demuxer"
    require_csv "$demuxers" "aiff" "demuxer"
    require_csv "$demuxers" "matroska" "demuxer"
    require_csv "$muxers" "pcm_f32le" "muxer"
    require_csv "$encoders" "pcm_f32le" "encoder"
    require_csv "$filters" "aresample" "filter"
    require_csv "$protocols" "file" "protocol"
    require_csv "$protocols" "pipe" "protocol"
    # pcm_f32le muxer registers the f32le format analyze asks for (-f f32le).
    # Prefix is the string baked into `ffmpeg -version`, not the copy we ship.
    args=(
      --prefix=/opt/meridian-lgpl-ffmpeg
      --disable-all
      --disable-autodetect
      --disable-gpl
      --disable-nonfree
      --disable-network
      --disable-debug
      --disable-doc
      --disable-x86asm
      --enable-small
      --enable-static
      --disable-shared
      --enable-ffmpeg
      --enable-avutil
      --enable-avcodec
      --enable-avformat
      --enable-avfilter
      --enable-swresample
      --enable-protocol="${protocols}"
      --enable-filter="${filters}"
      --enable-demuxer="${demuxers}"
      --enable-decoder="${decoders}"
      --enable-parser="${parsers}"
      --enable-muxer="${muxers}"
      --enable-encoder="${encoders}"
      --extra-cflags=-Os
      --extra-ldexeflags=-static
    )
    echo "Configuring LGPL ffmpeg ${FFMPEG_VERSION} (${RECIPE})…"
    ./configure "${args[@]}"
    make -j"$(nproc)"
    make install DESTDIR="${src}/destdir"
  )
  mkdir -p "${PREFIX}/bin"
  install -m 0755 "${src}/destdir/opt/meridian-lgpl-ffmpeg/bin/ffmpeg" "$BIN"
  install -m 0644 "${src}/COPYING.LGPLv2.1" "${PREFIX}/COPYING.LGPLv2.1"
  if find "$PREFIX" \( -name '*.so' -o -name '*.so.*' \) | grep -q .; then
    echo "ERROR: LGPL ffmpeg prefix contains shared libraries" >&2
    find "$PREFIX" \( -name '*.so' -o -name '*.so.*' \) >&2
    exit 1
  fi
  strip --strip-unneeded "$BIN"
  rm -rf "$src"
}

if [[ -n "$CHECK_ONLY" ]]; then
  verify_ffmpeg_lgpl "$CHECK_ONLY"
  echo "OK: $CHECK_ONLY is a static LGPL ffmpeg ($(stat -c%s "$CHECK_ONLY") bytes)"
  exit 0
fi

if [[ "$FORCE" -eq 0 && -x "$BIN" && -f "$STAMP" ]] \
  && grep -q "version: ${FFMPEG_VERSION}" "$STAMP" \
  && grep -q "recipe: ${RECIPE}" "$STAMP"; then
  verify_ffmpeg_lgpl "$BIN"
  echo "Using cached LGPL ffmpeg ${FFMPEG_VERSION} ($(stat -c%s "$BIN") bytes)"
  exit 0
fi

build_ffmpeg
verify_ffmpeg_lgpl "$BIN"
self_test_decode "$BIN"
write_stamp "$BIN"
echo "Built $BIN ($(stat -c%s "$BIN") bytes)"
"$BIN" -hide_banner -version
