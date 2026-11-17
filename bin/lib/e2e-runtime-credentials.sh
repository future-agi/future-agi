#!/usr/bin/env bash
# Shared by bin/e2e and direct Compose runners. Source this file; do not execute it.
# Keep generated keys outside the checkout and reuse them for the same project.

# Resolve existing ancestors physically without creating a directory. Missing
# suffixes are allowed, but ambiguous traversal and dangling links fail closed.
_e2e_runtime_physical_path() (
  local path="$1" suffix="" leaf
  [[ "$path" == /* ]] || path="$PWD/$path"
  while [[ "$path" != / && "$path" == */ ]]; do path="${path%/}"; done
  while [[ ! -d "$path" ]]; do
    [[ ! -e "$path" && ! -L "$path" ]] || return 1
    leaf="${path##*/}"
    case "$leaf" in ''|.|..) return 1 ;; esac
    suffix="/$leaf$suffix"
    path="${path%/*}"
    [[ -n "$path" ]] || path=/
  done
  cd -P -- "$path" 2>/dev/null || return 1
  path="${PWD%/}$suffix"
  printf '%s\n' "${path:-/}"
)

_e2e_runtime_private_destination() {
  local cache_root="$1" dir="$2" checkout protected physical candidate boundary
  checkout=$(cd -P -- "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P) || return 1
  # OUT is the direct walk runner's evidence root (not necessarily exported).
  # Additional externally published or bind-mounted roots must be declared as
  # newline-separated paths in E2E_RUNTIME_PUBLIC_ROOTS by direct callers.
  while IFS= read -r protected; do
    [[ -n "$protected" ]] || continue
    physical=$(_e2e_runtime_physical_path "$protected") || return 1
    [[ "$protected" == /* ]] || protected="$PWD/$protected"
    while [[ "$protected" != / && "$protected" == */ ]]; do protected="${protected%/}"; done
    for boundary in "$protected" "$physical"; do
      for candidate in "$cache_root" "$dir"; do
        if [[ "$boundary" == / || "$candidate" == "$boundary" || "$candidate" == "$boundary/"* ]]; then
          printf '%s\n' 'Runtime credential cache must be outside the checkout and public artifact or mount roots' >&2
          return 1
        fi
      done
    done
    # A published subtree within the credential directory is also unsafe.
    if [[ "$physical" == "$dir/"* ]]; then
      printf '%s\n' 'Runtime credential directory must not contain a public artifact or mount root' >&2
      return 1
    fi
  done <<EOF
$checkout
${OUT:-}
${E2E_VALUE_FREE_PUBLISH:-}
${PLAYWRIGHT_HTML_OUTPUT_DIR:-}
${PLAYWRIGHT_JSON_OUTPUT_DIR:-}
${PLAYWRIGHT_BLOB_OUTPUT_DIR:-}
${E2E_RUNTIME_PUBLIC_ROOTS:-}
EOF
}

prepare_runtime_credentials() {
  # Only nonsecret state is expanded before suspending tracing. The inner
  # function owns all secret locals, so they leave scope before tracing resumes.
  local runtime_restore_xtrace=0 runtime_status=0
  case $- in *x*) runtime_restore_xtrace=1; set +x ;; esac
  _e2e_prepare_runtime_credentials "$@" || runtime_status=$?
  if ((runtime_restore_xtrace)); then set -x; fi
  return "$runtime_status"
}

_e2e_prepare_runtime_credentials() {
  local project="${1:-}" cache_root dir
  [[ -n "$project" && "$project" != *[!a-zA-Z0-9_-]* ]] \
    || { printf '%s\n' 'E2E compose project must contain only letters, digits, underscores or hyphens' >&2; return 1; }
  if [[ -n "${XDG_CACHE_HOME:-}" ]]; then
    cache_root="$XDG_CACHE_HOME"
  else
    [[ -n "${HOME:-}" ]] \
      || { printf '%s\n' 'An absolute HOME or XDG_CACHE_HOME is required for runtime credentials' >&2; return 1; }
    cache_root="$HOME/.cache"
  fi
  [[ "$cache_root" == /* ]] \
    || { printf '%s\n' 'Runtime credential cache requires an absolute XDG_CACHE_HOME or HOME' >&2; return 1; }
  dir="$cache_root/futureagi-e2e/$project/credentials"
  # Check both the supplied spelling and its physical destination. A lexical
  # checkout path remains unsafe even when a symlink points outside the tree.
  _e2e_runtime_private_destination "$cache_root" "$dir" || return 1
  cache_root=$(_e2e_runtime_physical_path "$cache_root") || return 1
  [[ ! -L "$dir" ]] \
    || { printf '%s\n' 'Runtime credential directory must not be a symlink' >&2; return 1; }
  dir=$(_e2e_runtime_physical_path "$dir") || return 1
  _e2e_runtime_private_destination "$cache_root" "$dir" || return 1
  local defaults="$dir/fernet-keys" cached_integration_key cached_webhooks_key
  (
    set -o pipefail
    umask 077
    [[ ! -L "$dir" ]] \
      || { printf '%s\n' 'Runtime credential directory must not be a symlink' >&2; exit 1; }
    mkdir -p "$dir" || exit 1
    # Recheck after directory creation, before any key material is generated.
    physical_dir=$(_e2e_runtime_physical_path "$dir") || exit 1
    [[ "$physical_dir" == "$dir" ]] || exit 1
    _e2e_runtime_private_destination "$cache_root" "$physical_dir" || exit 1
    [[ -O "$dir" ]] \
      || { printf '%s\n' 'Runtime credential directory must belong to this user' >&2; exit 1; }
    chmod 0700 "$dir" || exit 1
    if [[ ! -e "$defaults" && ! -L "$defaults" ]]; then
      pending=$(mktemp "$dir/.fernet-keys.XXXXXX") || exit 1
      trap 'rm -f "$pending"' EXIT
      openssl rand -base64 32 | tr '+/' '-_' >"$pending" || exit 1
      openssl rand -base64 32 | tr '+/' '-_' >>"$pending" || exit 1
      # Both keys become visible together; a concurrent invocation may win.
      if ! ln "$pending" "$defaults" 2>/dev/null; then
        [[ -f "$defaults" && ! -L "$defaults" ]] \
          || { printf '%s\n' 'Cannot publish runtime credential defaults' >&2; exit 1; }
      fi
    fi
  ) || return 1
  [[ -f "$defaults" && ! -L "$defaults" && -O "$defaults" ]] \
    || { printf '%s\n' 'Runtime credential defaults must be a regular file owned by this user' >&2; return 1; }
  chmod 0600 "$defaults" || return 1
  {
    IFS= read -r cached_integration_key && IFS= read -r cached_webhooks_key
  } <"$defaults" \
    || { printf '%s\n' 'Cannot read runtime credential defaults' >&2; return 1; }
  [[ "$cached_integration_key" =~ ^[A-Za-z0-9_-]{43}=$ && "$cached_webhooks_key" =~ ^[A-Za-z0-9_-]{43}=$ ]] \
    || { printf '%s\n' 'Invalid runtime credential defaults' >&2; return 1; }
  export INTEGRATION_ENCRYPTION_KEY="${INTEGRATION_ENCRYPTION_KEY:-$cached_integration_key}" || return 1
  # One Fernet key is a valid one-entry webhook key list.
  export WEBHOOKS_ENCRYPTION_KEYS="${WEBHOOKS_ENCRYPTION_KEYS:-$cached_webhooks_key}"
}
