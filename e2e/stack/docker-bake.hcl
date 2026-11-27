# The images an E2E CI leg builds from its checkout, in one parallel build
# (.github/workflows/e2e-ci.yml, "Build the stack images from PR code").
# Run from the repository root; contexts are relative to it. Each target loads
# into the local Docker store under the tag the stack runs. The registry layer
# cache is read only here; e2e-cache.yml writes it on pushes.

variable "IMAGE_TAG" {
  default = "e2e-ci"
}

# distributed or standalone. Standalone assembles its app image on the slim
# backend variant, as a release does.
variable "E2E_STACK" {
  default = "distributed"
}

variable "CACHE_REPO" {
  default = "ghcr.io/future-agi/future-agi/e2e-cache"
}

# The release builds' layer cache (the slim backend's).
variable "BUILD_CACHE" {
  default = "ghcr.io/future-agi/future-agi/build-cache"
}

group "default" {
  targets = ["backend", "code-executor", "fi-collector", "frontend", "gateway"]
}

target "backend" {
  # Contexts mirror release-images.yml: Dockerfile.oss expects the futureagi/
  # dir as context, not the repo root.
  context    = "futureagi"
  dockerfile = "Dockerfile.oss"
  args = {
    IMAGE_VARIANT = E2E_STACK == "standalone" ? "slim" : null
  }
  tags = ["futureagi/future-agi:${IMAGE_TAG}"]
  cache-from = concat(
    ["type=registry,ref=${CACHE_REPO}:backend"],
    E2E_STACK == "standalone" ? [
      "type=registry,ref=${CACHE_REPO}:backend-slim",
      "type=registry,ref=${BUILD_CACHE}:backend-slim-amd64",
    ] : [],
  )
  output = ["type=docker"]
}

# Both topologies exercise the mandatory executor's policy propagation.
target "code-executor" {
  context    = "futureagi/code-executor"
  dockerfile = "Dockerfile"
  tags       = ["futureagi/code-executor:${IMAGE_TAG}"]
  cache-from = ["type=registry,ref=${CACHE_REPO}:code-executor"]
  output     = ["type=docker"]
}

target "fi-collector" {
  context    = "fi-collector"
  dockerfile = "Dockerfile"
  tags       = ["futureagi/fi-collector:${IMAGE_TAG}"]
  cache-from = ["type=registry,ref=${CACHE_REPO}:fi-collector"]
  output     = ["type=docker"]
}

target "frontend" {
  context    = "frontend"
  dockerfile = "Dockerfile"
  tags       = ["futureagi/frontend:${IMAGE_TAG}"]
  cache-from = ["type=registry,ref=${CACHE_REPO}:frontend"]
  output     = ["type=docker"]
}

# The eval flow routes the mock LLM through the gateway, so gateway changes
# have to run as built code, not as released :latest.
target "gateway" {
  context    = "agentcc-gateway"
  dockerfile = "Dockerfile"
  tags       = ["futureagi/agentcc-gateway:${IMAGE_TAG}"]
  cache-from = ["type=registry,ref=${CACHE_REPO}:gateway"]
  output     = ["type=docker"]
}
