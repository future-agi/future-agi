# shellcheck shell=bash
# The Future AGI images built from this checkout, tagged `local` for
# ./bin/install --from-source and ./bin/dev, and with the E2E stack's tag for
# bin/e2e (bin/lib/build-local.ps1 is the installer's PowerShell copy).
# Sourced, with the repository root as the working directory; needs say, ok,
# die and log_only, and DIM and RESET set.

# FUTURE_AGI_REVISION overrides the checkout revision; use a full lowercase
# commit SHA. Archives without either keep the Dockerfile's default.
build_local_image() { # tag, then `docker build` arguments
  local tag="$1" rev="${FUTURE_AGI_REVISION:-}"
  shift
  if [[ -n "$rev" ]]; then
    [[ "$rev" =~ ^[0-9a-f]{40}$ ]] || die "FUTURE_AGI_REVISION must be a full 40-character lowercase hexadecimal commit SHA."
  elif ! rev="$(git rev-parse --verify -q HEAD 2>/dev/null)"; then
    rev=
  fi
  if [[ -n "$rev" ]]; then
    set -- --build-arg "REVISION=$rev" --label "org.opencontainers.image.revision=$rev" "$@"
  fi
  say "  ${DIM}building ${tag}${RESET}"
  log_only "running: docker build -t $tag $*"
  docker build -t "$tag" "$@" || die "Building $tag failed; the build output above has the reason."
  ok "Built $tag"
}

# Builds WHAT: all (the default), backend, frontend, collector or gateway,
# tagged TAG (default `local`). Standalone's app image is assembled from the
# other four on the slim backend variant, as the published futureagi/standalone
# is, and again after any of them; Distributed runs the default variant.
build_local_images() { # standalone|distributed, [what], [tag]
  local setup="$1" what="${2:-all}" tag="${3:-local}"
  local -a backend_variant=()
  case "$what" in
    all|backend|frontend|collector|gateway) ;;
    *) die "unknown rebuild target: $what (all, backend, frontend, collector, gateway)" ;;
  esac
  if [[ "$setup" == standalone ]]; then
    backend_variant=(--build-arg IMAGE_VARIANT=slim)
  fi
  if [[ "$what" == all || "$what" == backend ]]; then
    build_local_image "futureagi/future-agi:$tag" -f futureagi/Dockerfile.oss \
      ${backend_variant[@]+"${backend_variant[@]}"} futureagi
  fi
  if [[ "$what" == all || "$what" == frontend ]]; then
    build_local_image "futureagi/frontend:$tag" frontend
  fi
  if [[ "$what" == all || "$what" == collector ]]; then
    build_local_image "futureagi/fi-collector:$tag" fi-collector
  fi
  if [[ "$what" == all || "$what" == gateway ]]; then
    build_local_image "futureagi/agentcc-gateway:$tag" agentcc-gateway
  fi
  if [[ "$setup" == standalone ]]; then
    build_local_standalone_app "$tag"
  fi
}

# The code-evaluation sandbox, tagged TAG (default `local`). The stack runs it
# as a non-root user on a read-only root filesystem, settings a published
# executor of another release may not support. Its Dockerfile builds FROM the
# base image it pins (ARG CODE_EXECUTOR_BASE); a pin that is neither on this
# host nor published yet is built first, under that tag, from Dockerfile.base.
build_local_code_executor() { # [tag]
  local base
  [[ -f futureagi/code-executor/Dockerfile ]] ||
    die "futureagi/code-executor/Dockerfile is missing, so the code executor cannot be built from this checkout."
  base="$(sed -n 's/^ARG CODE_EXECUTOR_BASE=//p' futureagi/code-executor/Dockerfile | head -n 1)"
  [[ -n "$base" ]] || die "futureagi/code-executor/Dockerfile pins no CODE_EXECUTOR_BASE, so the code executor cannot be built from this checkout."
  if ! docker image inspect "$base" >/dev/null 2>&1 && ! docker manifest inspect "$base" >/dev/null 2>&1; then
    build_local_image "$base" -f futureagi/code-executor/Dockerfile.base futureagi/code-executor
  fi
  build_local_image "futureagi/code-executor:${1:-local}" futureagi/code-executor
}

# Standalone's app image alone, over the four component images already built
# with TAG.
build_local_standalone_app() { # [tag]
  local tag="${1:-local}"
  build_local_image "futureagi/standalone:$tag" -f deploy/standalone/Dockerfile \
    --build-arg BACKEND_IMAGE="futureagi/future-agi:$tag" \
    --build-arg FRONTEND_IMAGE="futureagi/frontend:$tag" \
    --build-arg FI_COLLECTOR_IMAGE="futureagi/fi-collector:$tag" \
    --build-arg AGENTCC_GATEWAY_IMAGE="futureagi/agentcc-gateway:$tag" \
    deploy/standalone
}
