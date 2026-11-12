#!/bin/sh
# Try public source mirrors in order. A successful HTTP response is not enough:
# every candidate must match the one pinned release checksum before extraction.
set -eu
version=${1:?FFmpeg version required}
checksum=${2:?Pinned SHA-256 required}
destination=${3:?Archive destination required}
case "$version" in ''|*[!0-9.]*) exit 2 ;; esac
case "$checksum" in ''|*[!0-9a-f]*) exit 2 ;; esac
[ "${#checksum}" -eq 64 ] || exit 2
candidate=$(mktemp "${destination}.XXXXXX")
trap 'rm -f "$candidate"' EXIT HUP INT TERM
for mirror in \
    https://ffmpeg.org/releases \
    https://www.ffmpeg.org/releases \
    https://ffmpeg.mirror.garr.it/releases \
    https://www.mirrorservice.org/sites/ffmpeg.org/releases
do
    # Truncate even after an incomplete transfer; never reuse a partial archive.
    : > "$candidate"
    if curl --fail --silent --show-error --location \
        --proto '=https' --proto-redir '=https' \
        --connect-timeout 20 --max-time 180 \
        --output "$candidate" "$mirror/ffmpeg-$version.tar.xz" \
        && printf '%s  %s\n' "$checksum" "$candidate" | sha256sum --check --status -
    then
        mv "$candidate" "$destination"
        exit 0
    fi
    printf 'FFmpeg source mirror failed download or checksum verification: %s\n' "$mirror" >&2
done
printf 'No FFmpeg source mirror supplied the pinned archive.\n' >&2
exit 1
