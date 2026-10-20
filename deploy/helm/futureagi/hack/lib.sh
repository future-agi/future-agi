# shellcheck shell=bash
# Shared by the hack scripts (sourced, not run).

# resolve_digest REF: the sha256 digest of REF's manifest (list), from its
# registry through docker buildx imagetools, crane or oras, the first
# installed one that answers. Fails without a sha256 digest.
resolve_digest() {
  local ref=$1 digest=""
  if command -v docker >/dev/null 2>&1 && docker buildx version >/dev/null 2>&1; then
    digest=$(docker buildx imagetools inspect "$ref" --format '{{json .Manifest}}' 2>/dev/null | jq -er '.digest' 2>/dev/null) || digest=""
  fi
  if [ -z "$digest" ] && command -v crane >/dev/null 2>&1; then
    digest=$(crane digest "$ref" 2>/dev/null) || digest=""
  fi
  if [ -z "$digest" ] && command -v oras >/dev/null 2>&1; then
    digest=$(oras resolve "$ref" 2>/dev/null) || digest=""
  fi
  [[ "$digest" =~ ^sha256:[a-f0-9]{64}$ ]] || return 1
  printf '%s\n' "$digest"
}
