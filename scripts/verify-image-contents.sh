#!/usr/bin/env bash
set -euo pipefail

# Verify the three published backend image boundaries without source mounts.
# The image references may be tags for local checks or immutable digests in CI.
#
# Usage: ./scripts/verify-image-contents.sh [version] [oss|ee|cloud|all]
# Overrides: OSS_IMAGE, EE_IMAGE, CLOUD_IMAGE, CLOUD_DEPLOYMENT_SECRET

VERSION="${1:-latest}"
FLAVOR="${2:-all}"
case "$FLAVOR" in
  oss|ee|cloud|all) ;;
  *) echo "Usage: $0 [version] [oss|ee|cloud|all]" >&2; exit 2 ;;
esac
OSS_IMAGE="${OSS_IMAGE:-futureagi/future-agi:${VERSION}}"
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

image_test() {
  local image="$1"
  shift
  docker run --rm --entrypoint /bin/sh "$image" -c "$*"
}

image_python() {
  local image="$1"
  shift
  docker run --rm \
    --entrypoint python \
    -e DJANGO_SETTINGS_MODULE=tfc.settings.settings \
    "$image" "$@"
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
  check "OSS image is available" docker image inspect "$OSS_IMAGE"
fi
if [[ "$FLAVOR" == ee || "$FLAVOR" == all ]]; then
  check "Self-hosted EE image is available" docker image inspect "$EE_IMAGE"
fi
if [[ "$FLAVOR" == cloud || "$FLAVOR" == all ]]; then
  check "Cloud image is available" docker image inspect "$CLOUD_IMAGE"
fi
echo ""

if [[ "$FLAVOR" == oss || "$FLAVOR" == all ]]; then
echo "=== OSS Image: ${OSS_IMAGE} ==="
check "No ee path or symlink" image_test "$OSS_IMAGE" \
  "test ! -e /app/backend/ee && test ! -L /app/backend/ee"
check_absent "No cloud control-plane package" image_test "$OSS_IMAGE" test -e /app/backend/ee/cloud/control_plane
check "Django boots without EE" image_python "$OSS_IMAGE" -c "import django; django.setup()"
check "No cloud routes" check_routes "$OSS_IMAGE" absent \
  /v1/internal/licenses /v1/self-hosted/activations /v1/enterprise/heartbeats
check "OSS remains the OSS image flavor with cloud env set" docker run --rm \
  --entrypoint python \
  -e DJANGO_SETTINGS_MODULE=tfc.settings.settings \
  -e CLOUD_DEPLOYMENT=DEV \
  -e CLOUD_DEPLOYMENT_SECRET="$CLOUD_DEPLOYMENT_SECRET" \
  "$OSS_IMAGE" -c "import django; django.setup(); from tfc.capabilities import service; assert service.get_deployment_flavor().value == 'oss_image'"
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
