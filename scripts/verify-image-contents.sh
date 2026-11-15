#!/usr/bin/env bash
set -euo pipefail

# Verify the three published backend image boundaries without source mounts.
# The image references may be tags for local checks or immutable digests in CI.
#
# Usage: ./scripts/verify-image-contents.sh [version] [oss|ee|cloud|all]
# Overrides: OSS_IMAGE, EE_IMAGE, CLOUD_IMAGE, CLOUD_DEPLOYMENT_SECRET,
#   OSS_VARIANT: the backend variant OSS_IMAGE was built as
#   (futureagi/Dockerfile.oss IMAGE_VARIANT; https://docs.futureagi.com/docs/self-hosting/images#backend-variants). standard is
#   feature-complete (uv, git, the sandbox SDKs, every NLTK package); slim is
#   the lean base of futureagi/standalone. Default: slim for a tag ending in
#   -slim, else standard. Standalone local builds (./bin/install
#   --from-source, ./bin/dev) are slim: pass OSS_VARIANT=slim;
#   ./bin/install --distributed --from-source builds standard.
#   OSS_FFMPEG_FLAVOR: the FFMPEG_FLAVOR OSS_IMAGE was built with (minimal,
#   the default of both variants; debian; none). Default: the flavor the
#   image recorded at build time (/usr/local/lib/futureagi/ffmpeg-flavor).
#   For an image without that record (published up to v1.58): debian for a
#   standard tag up to v1.58 (LAST_DEBIAN_FFMPEG_RELEASE below: every
#   standard release up to it shipped Debian's ffmpeg), else minimal. A
#   digest reference, or a tag without a version (latest, a local build), is
#   judged by the image's org.opencontainers.image.version label. Images
#   built from this tree carry the record, so the version rule is only for
#   images published before it.

VERSION="${1:-latest}"
FLAVOR="${2:-all}"
case "$FLAVOR" in
  oss|ee|cloud|all) ;;
  *) echo "Usage: $0 [version] [oss|ee|cloud|all]" >&2; exit 2 ;;
esac
OSS_IMAGE="${OSS_IMAGE:-futureagi/future-agi:${VERSION}}"
case "$OSS_IMAGE" in
  *-slim|*-slim@*) OSS_VARIANT="${OSS_VARIANT:-slim}" ;;
  *) OSS_VARIANT="${OSS_VARIANT:-standard}" ;;
esac
case "$OSS_VARIANT" in
  standard|slim) ;;
  *) echo "OSS_VARIANT must be standard or slim, not '${OSS_VARIANT}'" >&2; exit 2 ;;
esac
# The last release whose standard image shipped Debian's ffmpeg package; the
# releases after it ship the minimal build.
LAST_DEBIAN_FFMPEG_RELEASE="1.58"

# The image's org.opencontainers.image.version label (pulled if needed).
image_version_label() {
  local format='{{ index .Config.Labels "org.opencontainers.image.version" }}'
  docker image inspect -f "$format" "$OSS_IMAGE" 2>/dev/null \
    || { docker pull -q "$OSS_IMAGE" >/dev/null 2>&1 \
      && docker image inspect -f "$format" "$OSS_IMAGE" 2>/dev/null; } \
    || true
}

# "MAJOR MINOR" of OSS_IMAGE. A digest pins the image whatever its tag says,
# so a digest reference, or a tag without a version (latest, a local build),
# is judged by the image's version label, then by the tag.
oss_image_version() {
  local ref="${OSS_IMAGE%%@*}" tag="" label="" version
  [[ "${ref##*/}" == *:* ]] && tag="${ref##*:}"
  if [[ "$OSS_IMAGE" == *@* || ! "$tag" =~ ^v?[0-9]+\.[0-9]+ ]]; then
    label="$(image_version_label)"
  fi
  for version in "$label" "$tag"; do
    if [[ "$version" =~ ^v?([0-9]+)\.([0-9]+) ]]; then
      echo "$((10#${BASH_REMATCH[1]})) $((10#${BASH_REMATCH[2]}))"
      return 0
    fi
  done
  return 1
}

# The flavor the image recorded at build time (Dockerfile.oss writes it);
# images published up to v1.58 have no record.
recorded_ffmpeg_flavor() {
  local flavor
  flavor="$(docker run --rm --entrypoint /bin/cat "$OSS_IMAGE" /usr/local/lib/futureagi/ffmpeg-flavor 2>/dev/null)" || return 1
  case "$flavor" in
    minimal|debian|none) echo "$flavor" ;;
    *) return 1 ;;
  esac
}

default_ffmpeg_flavor() {
  recorded_ffmpeg_flavor && return 0
  local major minor last_major="${LAST_DEBIAN_FFMPEG_RELEASE%%.*}" last_minor="${LAST_DEBIAN_FFMPEG_RELEASE#*.}"
  if [[ "$OSS_VARIANT" == standard ]] && read -r major minor < <(oss_image_version) \
    && (( major < last_major || (major == last_major && minor <= last_minor) )); then
    echo debian
  else
    echo minimal
  fi
}

if [[ -z "${OSS_FFMPEG_FLAVOR:-}" && ( "$FLAVOR" == oss || "$FLAVOR" == all ) ]]; then
  OSS_FFMPEG_FLAVOR="$(default_ffmpeg_flavor)"
fi
OSS_FFMPEG_FLAVOR="${OSS_FFMPEG_FLAVOR:-minimal}"
case "$OSS_FFMPEG_FLAVOR" in
  minimal|debian|none) ;;
  *) echo "OSS_FFMPEG_FLAVOR must be minimal, debian or none, not '${OSS_FFMPEG_FLAVOR}'" >&2; exit 2 ;;
esac
EE_IMAGE="${EE_IMAGE:-futureagi/future-agi-ee:${VERSION}}"
CLOUD_IMAGE="${CLOUD_IMAGE:-futureagi/future-agi-cloud:${VERSION}}"
CLOUD_DEPLOYMENT_SECRET="${CLOUD_DEPLOYMENT_SECRET:-}"

PASS=0
FAIL=0

check() {
  local desc="$1"
  shift
  if "$@" >/dev/null 2>&1; then
    echo "  PASS: ${desc}"
    PASS=$((PASS + 1))
  else
    echo "  FAIL: ${desc}"
    FAIL=$((FAIL + 1))
  fi
}

check_absent() {
  local desc="$1"
  shift
  if ! "$@" >/dev/null 2>&1; then
    echo "  PASS: ${desc}"
    PASS=$((PASS + 1))
  else
    echo "  FAIL: ${desc}"
    FAIL=$((FAIL + 1))
  fi
}

# Runs its arguments as given (`sh -c "a | b"` stays one script); "$*" would
# re-split them, so `sh -c "ffmpeg -encoders | grep ..."` ran a bare `ffmpeg`.
image_test() {
  local image="$1"
  shift
  docker run --rm --entrypoint /bin/sh "$image" -c '"$@"' sh "$@"
}

image_python() {
  local image="$1"
  shift
  docker run --rm \
    --entrypoint python \
    -e DJANGO_SETTINGS_MODULE=tfc.settings.settings \
    "$image" "$@"
}

image_available() {
  local image="$1"
  docker image inspect "$image" >/dev/null 2>&1 ||
    docker manifest inspect "$image" >/dev/null 2>&1
}

check_routes() {
  local image="$1"
  local expected="$2"
  shift 2
  docker run --rm \
    --entrypoint python \
    -e DJANGO_SETTINGS_MODULE=tfc.settings.settings \
    -e CLOUD_DEPLOYMENT=DEV \
    -e CLOUD_DEPLOYMENT_SECRET="$CLOUD_DEPLOYMENT_SECRET" \
    "$image" -c '
import django
import sys

django.setup()
from django.urls import resolve
from django.urls.exceptions import Resolver404

expected = sys.argv[1]
paths = sys.argv[2:]
for path in paths:
    try:
        match = resolve(path)
    except Resolver404:
        if expected == "present":
            raise SystemExit(f"missing route: {path}")
    else:
        if expected == "absent":
            raise SystemExit(f"unexpected route: {path}")
' "$expected" "$@"
}

echo "=== Image availability ==="
if [[ "$FLAVOR" == oss || "$FLAVOR" == all ]]; then
  check "OSS image is available" image_available "$OSS_IMAGE"
fi
if [[ "$FLAVOR" == ee || "$FLAVOR" == all ]]; then
  check "Self-hosted EE image is available" image_available "$EE_IMAGE"
fi
if [[ "$FLAVOR" == cloud || "$FLAVOR" == all ]]; then
  check "Cloud image is available" image_available "$CLOUD_IMAGE"
fi
echo ""

if [[ "$FLAVOR" == oss || "$FLAVOR" == all ]]; then
echo "=== OSS Image: ${OSS_IMAGE} (${OSS_VARIANT} variant) ==="
echo "ffmpeg flavor: ${OSS_FFMPEG_FLAVOR}"
check "Has shared EE package tree" image_test "$OSS_IMAGE" test -d /app/backend/ee
check_absent "No cloud-private package" image_test "$OSS_IMAGE" test -e /app/backend/ee/cloud
check "Has required NLTK runtime corpora" image_python "$OSS_IMAGE" -c \
  "import nltk; [nltk.data.find(p) for p in ('corpora/stopwords/', 'tokenizers/punkt_tab/english/', 'taggers/averaged_perceptron_tagger_eng/', 'corpora/wordnet/')]; assert nltk.word_tokenize('Future AGI') == ['Future', 'AGI']; assert nltk.pos_tag(['Future'])[0][1]"
case "$OSS_FFMPEG_FLAVOR" in
  none)
    check_absent "No ffmpeg (FFMPEG_FLAVOR=none)" image_test "$OSS_IMAGE" sh -c "command -v ffmpeg || command -v ffprobe"
    ;;
  *)
    check "ffmpeg can encode mp3 and png" image_test "$OSS_IMAGE" sh -c "ffmpeg -hide_banner -encoders | grep -q libmp3lame && ffmpeg -hide_banner -encoders | grep -qw png"
    check "Has ffprobe" image_test "$OSS_IMAGE" sh -c "ffprobe -hide_banner -version"
    ;;
esac
if [[ "$OSS_FFMPEG_FLAVOR" == minimal ]]; then
  check "Ships the ffmpeg LGPL notice" image_test "$OSS_IMAGE" test -f /usr/local/share/doc/ffmpeg/COPYING.LGPLv2.1
  check_absent "No Debian ffmpeg package tree" image_test "$OSS_IMAGE" sh -c "dpkg -s ffmpeg || dpkg-query -W -f '\${Package}\n' | grep -E '^libav(codec|format|filter|device)'"
fi
if [[ "$OSS_FFMPEG_FLAVOR" == debian ]]; then
  check "Has Debian's ffmpeg package" image_test "$OSS_IMAGE" dpkg -s ffmpeg
fi
if [[ "$OSS_VARIANT" == standard ]]; then
  # A drop-in for what builds on it: the EE and cloud images run
  # `uv pip install` in it, and the simulation runner's hosted-harness jobs
  # need the sandbox SDKs and git.
  check "Has uv (EE and cloud images install on top)" image_test "$OSS_IMAGE" sh -c "command -v uv"
  check "Has git (hosted-harness GitHub sources)" image_test "$OSS_IMAGE" sh -c "git --version"
  check "Has the optional groups of the base dependencies" image_python "$OSS_IMAGE" -c \
    "import daytona, e2b, httpx_ws, stripe, flower, vertexai, langchain_community, channels_rabbitmq, claude_agent_sdk"
  check "Has every NLTK package" image_python "$OSS_IMAGE" -c \
    "import nltk; [nltk.data.find(p) for p in ('corpora/omw-1.4/', 'tokenizers/punkt/', 'taggers/averaged_perceptron_tagger/')]"
  check "Keeps every googleapiclient discovery document" image_python "$OSS_IMAGE" -c \
    "from googleapiclient.discovery_cache import get_static_doc; assert get_static_doc('drive', 'v3')"
else
  check_absent "No uv in the runtime image" image_test "$OSS_IMAGE" sh -c "command -v uv"
  check_absent "No git in the runtime image" image_test "$OSS_IMAGE" sh -c "command -v git"
fi
check "Django boots without cloud-private code" image_python "$OSS_IMAGE" -c "import django; django.setup()"
check "Django boots as uid 1000 and can write where the app writes" docker run --rm \
  --user 1000:1000 \
  --entrypoint python \
  -e DJANGO_SETTINGS_MODULE=tfc.settings.settings \
  "$OSS_IMAGE" -c "import django, tempfile; django.setup(); [tempfile.NamedTemporaryFile(dir=d).close() for d in ('/app/backend/logs', '/app/backend/media', '/app/backend/static', '/app/backend/tfc/logs', '/app/backend/tfc/saml_logs', '/app/backend/tfc/metadata', '/app/backend/tfc/compare')]"
check "No cloud routes" check_routes "$OSS_IMAGE" absent \
  /v1/internal/licenses /v1/self-hosted/activations /v1/enterprise/heartbeats
check "CE base remains self-hosted with an invalid cloud secret" docker run --rm \
  --entrypoint python \
  -e DJANGO_SETTINGS_MODULE=tfc.settings.settings \
  -e CLOUD_DEPLOYMENT=DEV \
  -e CLOUD_DEPLOYMENT_SECRET="$CLOUD_DEPLOYMENT_SECRET" \
  "$OSS_IMAGE" -c "import django; django.setup(); from tfc.capabilities import service; assert service.get_deployment_flavor().value == 'self_hosted_ee_image'"
echo ""
fi

if [[ "$FLAVOR" == ee || "$FLAVOR" == all ]]; then
echo "=== Self-hosted EE Image: ${EE_IMAGE} ==="
check "Has ee/ directory" image_test "$EE_IMAGE" test -d /app/backend/ee
check "Has ee/licensing/" image_test "$EE_IMAGE" test -d /app/backend/ee/licensing
check "Has ee/falcon_ai/" image_test "$EE_IMAGE" test -d /app/backend/ee/falcon_ai
check "Has ee/voice/" image_test "$EE_IMAGE" test -d /app/backend/ee/voice
check "Has ee/turing/" image_test "$EE_IMAGE" test -d /app/backend/ee/turing
check "Has ee/protect/" image_test "$EE_IMAGE" test -d /app/backend/ee/protect
check "Has ee/evals/" image_test "$EE_IMAGE" test -d /app/backend/ee/evals
check_absent "No ee/cloud/ directory" image_test "$EE_IMAGE" test -e /app/backend/ee/cloud
check_absent "No cloud license generator" image_test "$EE_IMAGE" test -e /app/backend/ee/cloud/control_plane/license_generator.py
check_absent "No cloud billing internals" image_test "$EE_IMAGE" test -e /app/backend/ee/cloud/billing
check "Django boots with missing license" image_python "$EE_IMAGE" -c "import django; django.setup()"
check "Django boots with invalid license" docker run --rm \
  --entrypoint python \
  -e DJANGO_SETTINGS_MODULE=tfc.settings.settings \
  -e EE_LICENSE_KEY=invalid "$EE_IMAGE" -c "import django; django.setup()"
check "Self-hosted EE has no cloud routes" check_routes "$EE_IMAGE" absent \
  /v1/internal/licenses /v1/self-hosted/activations /v1/enterprise/heartbeats
check "EE image registers ee.licensing app" image_python "$EE_IMAGE" -c \
  "import django; django.setup(); from django.conf import settings; assert 'ee.licensing' in settings.INSTALLED_APPS or any(a.endswith('.LicensingConfig') for a in settings.INSTALLED_APPS), settings.INSTALLED_APPS"
check "EE image wires EE feature middleware" image_python "$EE_IMAGE" -c \
  "import django; django.setup(); from django.conf import settings; assert any('ee.usage.middleware' in m or 'ee.middleware' in m for m in settings.MIDDLEWARE), settings.MIDDLEWARE"
echo ""
fi

if [[ "$FLAVOR" == cloud || "$FLAVOR" == all ]]; then
echo "=== Cloud Image: ${CLOUD_IMAGE} ==="
check "Has ee/ directory" image_test "$CLOUD_IMAGE" test -d /app/backend/ee
check "Has ee/cloud/ directory" image_test "$CLOUD_IMAGE" test -d /app/backend/ee/cloud
check "Has cloud billing package" image_test "$CLOUD_IMAGE" test -d /app/backend/ee/cloud/billing
check "Has cloud control plane" image_test "$CLOUD_IMAGE" test -d /app/backend/ee/cloud/control_plane
check "Has cloud telemetry package" image_test "$CLOUD_IMAGE" test -d /app/backend/ee/cloud/telemetry
check "Has license generator" image_test "$CLOUD_IMAGE" test -f /app/backend/ee/cloud/control_plane/license_generator.py
check "Has stripe service" image_test "$CLOUD_IMAGE" test -f /app/backend/ee/cloud/billing/stripe_service.py
check "Cloud deployment secret is configured" test -n "$CLOUD_DEPLOYMENT_SECRET"
check "Django boots as cloud" docker run --rm \
  --entrypoint python \
  -e DJANGO_SETTINGS_MODULE=tfc.settings.settings \
  -e CLOUD_DEPLOYMENT=DEV \
  -e CLOUD_DEPLOYMENT_SECRET="$CLOUD_DEPLOYMENT_SECRET" \
  "$CLOUD_IMAGE" -c "import django; django.setup(); from tfc.capabilities import service; assert service.get_deployment_flavor().value == 'cloud_image'"
check "Cloud control-plane routes are mounted" check_routes "$CLOUD_IMAGE" present \
  /v1/internal/licenses /v1/self-hosted/activations /v1/enterprise/heartbeats
echo ""
fi

echo "=== Results ==="
echo "  Passed: ${PASS}"
echo "  Failed: ${FAIL}"
if [ "$FAIL" -gt 0 ]; then
  echo "  STATUS: FAILED"
  exit 1
fi
echo "  STATUS: ALL PASSED"
