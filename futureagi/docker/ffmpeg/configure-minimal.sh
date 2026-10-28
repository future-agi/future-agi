#!/bin/sh
# Configure a minimal FFmpeg (ffmpeg + ffprobe) that covers every ffmpeg use in
# the default backend image, instead of Debian's `ffmpeg` package (189 extra
# packages, ~406 MB unpacked / ~150 MB gzip: libllvm15 via mesa, flite, x265,
# SDL, PulseAudio ...). The result links only libc, libm, zlib and libmp3lame
# (an LGPL build: no GPL or nonfree components). FFMPEG_FLAVOR=debian in
# futureagi/Dockerfile.oss brings the Debian package back.
#
# Run from the root of an unpacked FFmpeg source tree:
#   sh configure-minimal.sh /opt/ffmpeg
#
# Call sites this build must satisfy (selftest.py replays each of them):
#   tfc/utils/storage.py detect_audio_format  ffmpeg -i - -f ffmetadata -
#       (format sniff of every _FORMAT_TO_MIME format: mp3, mpeg (MPEG-PS),
#        wav, ogg, flac, aac, m4a, webm, wma, aiff, au)
#   tfc/utils/storage.py convert_to_mp3       ffmpeg -i - -f mp3 -acodec libmp3lame -ab 192k -
#       (an input with cover art maps it to the mp3 muxer's default video
#        codec, PNG, so the png encoder is required)
#   tfc/utils/storage.py video thumbnail      ffmpeg -i f -ss 1 -vframes 1 -vf scale=320:240 -f image2 x.jpg
#   pydub (deepgram_response.py, ee/evals/localizer/error_localizer.py):
#       ffprobe -of json -show_format -show_streams -read_ahead_limit -1 cache:pipe:0
#       ffmpeg -y -read_ahead_limit -1 -i cache:pipe:0 -acodec pcm_{u8,s16le,s24le,s32le} -vn -f wav -
#       ffmpeg -y -f wav -i <tmp> -f mp3 <tmp>      (AudioSegment.export(format="mp3"))
#   ee/voice/services/livekit/recording.py  ffmpeg -i - -filter_complex pan=... -f mp3 -codec:a libmp3lame -q:a 2 -
#   librosa/audioread fallback (EXTRAS=audio only): ffmpeg -i f -f s16le -
#
# FFmpeg >= 7 maps an input of "-" to the fd: protocol, so fd is enabled next
# to pipe. Anything outside this list (e.g. AV1 video, WavPack, ProRes) fails
# with FFmpeg's own "Decoder not found" error.
#
# FFmpeg's configure does not fail on a component name that does not exist: it
# drops it (and warns only when none of a comma-separated list matches). Every
# name below is therefore checked against `./configure --list-<kind>s` first,
# and configure's "not all dependencies are satisfied" warning is fatal too.
set -euf  # -f: no pathname expansion; pcm_* below is expanded by this script
PREFIX=${1:-/opt/ffmpeg}
[ $# -gt 0 ] && shift
LOG=${CONFIGURE_LOG:-/tmp/ffmpeg-configure.log}

PROTOCOLS="pipe fd file cache"
DEMUXERS="aac ac3 aiff amr amrnb amrwb asf au avi caf eac3 flac matroska mov mp3
  mpegps mpegts ogg wav w64 image2 mjpeg"
MUXERS="mp3 wav ffmetadata image2 null pcm_s16le pcm_f32le"
# Every native PCM decoder (pcm_s16le, pcm_mulaw, pcm_f32be, ...) is added
# below from `./configure --list-decoders`, minus the macOS AudioToolbox
# wrappers (pcm_*_at), which configure would only disable again.
DECODERS="aac aac_latm ac3 eac3 alac amrnb amrwb flac mp2 mp2float mp3 mp3float
  opus vorbis wmav1 wmav2 adpcm_ima_wav adpcm_ms gsm_ms
  h264 hevc mpeg1video mpeg2video mpeg4 vp8 vp9 mjpeg png wrapped_avframe"
ENCODERS="libmp3lame pcm_u8 pcm_s16le pcm_s24le pcm_s32le pcm_f32le pcm_mulaw
  pcm_alaw mjpeg png"
PARSERS="aac aac_latm ac3 flac mpegaudio opus vorbis h264 hevc mpegvideo
  mpeg4video vp8 vp9 mjpeg png"
BSFS="aac_adtstoasc h264_mp4toannexb hevc_mp4toannexb vp9_superframe_split"
FILTERS="aresample aformat anull atrim pan amix amerge channelsplit volume scale
  format null trim fps setpts"

list() { ./configure --list-"$1"s | tr -s '[:blank:]' '\n'; }
DECODERS="$DECODERS $(list decoder | grep '^pcm_' | grep -v '_at$' | tr '\n' ' ')"

unknown=""
check() { # kind, names...
    kind=$1
    shift
    available=$(list "$kind")
    for name in "$@"; do
        printf '%s\n' "$available" | grep -qx "$name" || unknown="$unknown $kind:$name"
    done
}
# shellcheck disable=SC2086  # the lists are whitespace-separated on purpose
{
    check protocol $PROTOCOLS
    check demuxer $DEMUXERS
    check muxer $MUXERS
    check decoder $DECODERS
    check encoder $ENCODERS
    check parser $PARSERS
    check bsf $BSFS
    check filter $FILTERS
}
if [ -n "$unknown" ]; then
    echo "configure-minimal.sh: not FFmpeg components in this version:$unknown" >&2
    exit 1
fi

csv() { printf '%s' "$1" | tr -s '[:space:]' ',' | sed 's/^,//; s/,$//'; }

if ! ./configure \
    --prefix="$PREFIX" \
    --disable-everything --disable-autodetect --disable-network \
    --disable-doc --disable-debug --disable-ffplay \
    --disable-shared --enable-static --enable-small \
    --enable-ffmpeg --enable-ffprobe \
    --enable-libmp3lame --enable-zlib \
    --enable-swresample --enable-swscale \
    --enable-protocol="$(csv "$PROTOCOLS")" \
    --enable-demuxer="$(csv "$DEMUXERS")" \
    --enable-muxer="$(csv "$MUXERS")" \
    --enable-decoder="$(csv "$DECODERS")" \
    --enable-encoder="$(csv "$ENCODERS")" \
    --enable-parser="$(csv "$PARSERS")" \
    --enable-bsf="$(csv "$BSFS")" \
    --enable-filter="$(csv "$FILTERS")" \
    "$@" >"$LOG" 2>&1; then
    cat "$LOG"
    echo "configure-minimal.sh: FFmpeg configure failed" >&2
    exit 1
fi
cat "$LOG"
if grep -E "did not match anything|not all dependencies are satisfied" "$LOG" >&2; then
    echo "configure-minimal.sh: configure dropped a requested component (see above)" >&2
    exit 1
fi
