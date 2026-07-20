#!/usr/bin/env bash
#
# Branch creation helper script.
# Creates a properly named branch following the project's naming convention.
#
# Usage:
#   ./scripts/create-branch.sh <type> <description> [<ticket-id>]
#
# Examples:
#   ./scripts/create-branch.sh feat user-authentication AUTH-123
#   ./scripts/create-branch.sh fix login-error BUG-456
#   ./scripts/create-branch.sh docs update-readme
#
# The script will:
#   - Validate the branch type and description
#   - Check if the branch already exists
#   - Create and checkout the new branch
#   - Provide next steps guidance

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ── Valid branch types (Conventional Commits prefixes) ────────────────────────
VALID_TYPES="feat fix chore docs refactor test perf"

# ── Regex from .husky/pre-push and BRANCH_NAMING_CONVENTION.md ───────────────
BRANCH_REGEX="^(feat|fix|chore|docs|refactor|test|perf)\/[a-zA-Z0-9]+([a-zA-Z0-9\-]*[a-zA-Z0-9])?$"

# ── Helpers ───────────────────────────────────────────────────────────────────
red()   { printf "\033[31m%s\033[0m\n" "$1"; }
green() { printf "\033[32m%s\033[0m\n" "$1"; }
yellow() { printf "\033[33m%s\033[0m\n" "$1"; }

usage() {
    cat <<HELP
Usage: ./scripts/create-branch.sh <type> <description> [<ticket-id>]

Valid types: feat, fix, chore, docs, refactor, test, perf

Examples:
  ./scripts/create-branch.sh feat user-authentication AUTH-123
  ./scripts/create-branch.sh fix login-error BUG-456
  ./scripts/create-branch.sh docs update-readme
HELP
    exit 1
}

# ── Argument parsing ──────────────────────────────────────────────────────────
if [[ $# -lt 2 ]]; then
    red "Error: Missing required arguments."
    usage
fi

BRANCH_TYPE="$1"
DESCRIPTION="$2"
TICKET_ID="${3:-}"  # optional

# ── Validate type ─────────────────────────────────────────────────────────────
if ! echo "$VALID_TYPES" | grep -qw "$BRANCH_TYPE"; then
    red "Error: Invalid branch type '${BRANCH_TYPE}'."
    red "Valid types: feat, fix, chore, docs, refactor, test, perf"
    exit 1
fi

# ── Build branch name ─────────────────────────────────────────────────────────
if [[ -n "$TICKET_ID" ]]; then
    BRANCH_NAME="${BRANCH_TYPE}/${TICKET_ID}-${DESCRIPTION}"
else
    BRANCH_NAME="${BRANCH_TYPE}/${DESCRIPTION}"
fi

# ── Validate full branch name against regex ───────────────────────────────────
if [[ ! "$BRANCH_NAME" =~ $BRANCH_REGEX ]]; then
    red "Error: Branch name '${BRANCH_NAME}' does not match naming convention."
    red "Format: <type>/<ticket-id>-<description> or <type>/<description>"
    red "Description must use kebab-case (lowercase letters, numbers, hyphens)."
    exit 1
fi

# ── Check if branch already exists ────────────────────────────────────────────
if git show-ref --verify --quiet "refs/heads/${BRANCH_NAME}" 2>/dev/null; then
    red "Error: Branch '${BRANCH_NAME}' already exists locally."
    echo "  Switch to it:  git checkout ${BRANCH_NAME}"
    echo "  Or delete it:  git branch -D ${BRANCH_NAME}"
    exit 1
fi

if git show-ref --verify --quiet "refs/remotes/origin/${BRANCH_NAME}" 2>/dev/null; then
    yellow "Warning: Branch '${BRANCH_NAME}' exists on origin."
    echo "  Track it:      git checkout ${BRANCH_NAME}"
    echo "  Or delete it:  git push origin --delete ${BRANCH_NAME}"
    exit 1
fi

# ── Create and checkout ──────────────────────────────────────────────────────
git checkout -b "$BRANCH_NAME"

green "Branch '${BRANCH_NAME}' created and checked out."

echo ""
echo "Next steps:"
echo "  1. Make your changes"
echo "  2. Commit:     git add . && git commit"
echo "  3. Push:       git push origin -u ${BRANCH_NAME}"
echo "  4. Open a PR against 'main'"
echo ""
echo "See BRANCH_NAMING_CONVENTION.md for full guidelines."
