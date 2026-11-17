#!/usr/bin/env bash
# HR-JRN-01: a value-free walk job, and the publication boundary's ONE final check (through the
# publication choke point, lib/value-free-boundary.mjs).
# e2e/walks/launch/launch.sh runs this on the VM in place of the exit-code one-liner, from
# the checkout, which is the pull root. Usage:
#   value-free-job.sh <exit-file> <pull>... -- <command>...
# <pull>: every pattern the launch passes to --pull, exactly; <exit-file> is one of them (it
# is pulled, so it is checked like the rest).
#   1. A fresh 0700 registries root for the command, E2E_VALUE_FREE_REGISTRIES: run-walks.sh
#      gives each profile's value-free run its own registry below it, and the run leaves it
#      in place (lib/value-free.ts: whoever creates a registry removes it).
#   2. The command runs with its stdout and stderr held in a file beside <exit-file> on this
#      machine (never pulled, never printed as it runs).
#   3. Its true exit code goes into <exit-file>.
#   4. Last, after every rename and write: the publication choke point's final check (
#      node e2e/lib/value-free-boundary.mjs publish) over exactly the pulls and the held output,
#      against every value the runs registered (it removes the registries once read). Clean: it
#      prints exactly the bytes it scanned (the held output, a newline if it lacks one, and its
#      STATUS constant), and every pull stays for the launch. Anything else, registries it
#      cannot read whole included, refuses the whole export: every pull's entry, <exit-file>
#      included, is moved aside on this machine (never deleted) and only one STATUS constant is
#      printed, so the launch lands nothing. A check that does not reach its verdict is treated
#      the same way here, silently: from the command's end on, nothing but the check prints
#      (this script's own errors go to a file beside <exit-file>), and its output is never
#      captured or echoed (harness/value-free-consumers.spec.ts).
#   5. On any refusal, whatever a pull still reaches is withheld
#      here too, never deleted, never pulled: a pull of one exact entry (`<dir>/**` or a
#      relative path) moves to a sibling `.<name>-withheld-*`; then everything ANY pull reaches,
#      read the way the walk rig reads a pull (cloud vm/exec/exec-collect.sh: prefixed with the
#      pull root unless absolute, split on blanks, globbed with globstar, nullglob and dotglob,
#      read lexically), moves into one quarantine, value-free-withheld-* on this machine's
#      private side (step 7), by its path below the root; a pull reaching the root itself (an
#      empty pattern) moves every entry below the root. Up to three passes, until no pull
#      reaches anything the rig would take.
#   6. The rig transfers a regular file whose lexical path lies below
#      /Users or /Volumes, inside the pull root or not, so a pull declared OUTSIDE the root (an
#      absolute path, an escaping one, the root's parent) is withheld too: what it reaches there
#      moves into the quarantine below `outside/` by its absolute path. What the rig never
#      transfers is never moved. A directory holding the root, the registries or the quarantine,
#      or one the rig transfers from, never moves itself (what it holds does: `..` reaches the
#      root's siblings); the registries, the quarantine and this home (unless the rig could
#      collect from it, step 7) are never read. The walk launcher refuses such a declaration before dispatch
#      (e2e/walks/launch/launch.sh); this holds if one is dispatched.
#   7. Every path is RESOLVED before it is classified
#      (realpath: links, `.`, `..` and relative spellings), and compared in that canonical form.
#      This machine's private side, where the registries root and the quarantine go, is the first
#      of TMPDIR and /tmp whose fresh directory, made there and then resolved, lies neither at or
#      below the pull root nor at, below or above /Users or /Volumes (a candidate that fails is
#      removed: it is empty); none, and nothing runs: one constant (VFE5), no exit file, exit 1.
#      This home is left unread only when its canonical path (and its spelling) is not one the rig
#      could collect from; else what a pull reaches below it is withheld like any other entry (it
#      never moves itself). Whether the rig would take an entry is read both ways, its lexical
#      path (the rig's own reading) and where it canonically lies; an entry reached THROUGH a link
#      (one that is not the pull root's own spelling, nor /Users or /Volumes) is withheld by moving
#      that link, never what it points to (the rig follows a link a pattern names).
# Its own exit is 0 (the command and the check passed) or 1, never another.
set -uo pipefail
usage() { echo 'usage: value-free-job.sh <exit-file> <pull>... -- <command>...'; exit 1; }
(($# >= 4)) || usage
exit_file=$1
shift
pulls=()
while (($#)) && [[ $1 != -- ]]; do pulls+=("$1"); shift; done
(($# >= 2)) || usage
shift
listed=0
for p in "${pulls[@]}"; do [[ $p == "$exit_file" ]] && listed=1; done
((listed)) || usage
dir=$(dirname "$exit_file")
mkdir -p "$dir" || exit 1
root=$PWD
rootc=$(pwd -P) || exit 1
within() { [[ $1 == "$2" || $1 == "$2"/* || $2 == / ]]; } # <path> is <dir> or below it
canon() { # <path>: its canonical path (every link, `.`, `..` and relative spelling resolved; it exists) in `cpath`
  cpath=$(realpath -e -- "$1" 2> /dev/null && printf x) || return 1
  cpath=${cpath%$'\nx'}
}
# The directories the walk rig transfers from (exec-collect.sh add()), as spelled and resolved where that differs.
transporting=(/Users /Volumes)
for t in /Users /Volumes; do canon "$t" && [[ $cpath != "$t" ]] && transporting+=("$cpath"); done
collectable() { # <a directory>: at or below the pull root, or at, below or above a directory the rig transfers from
  local t
  for t in "$root" "$rootc" "${transporting[@]}"; do within "$1" "$t" || within "$t" "$1" && return 0; done
  return 1
}
# The private side (step 7): a fresh directory made in each candidate, resolved, then classified.
registries=
for base in "${TMPDIR:-}" /tmp; do
  [[ -n $base ]] || continue
  made=$(mktemp -d "${base%/}/e2e-vf-registries-XXXXXX" 2> /dev/null) || continue
  if canon "$made" && ! collectable "$cpath"; then registries=$cpath; break; fi
  rmdir -- "$made" 2> /dev/null # empty, and this script's own
done
[[ -n $registries ]] || { printf 'VFE5\n'; exit 1; }
chmod 700 "$registries" || exit 1
private=${registries%/*}
E2E_VALUE_FREE_REGISTRIES=$registries "$@" > "$dir/job-output.log" 2>&1
rc=$?
exec 2>> "$dir/job-errors.log"
echo "$rc" > "$exit_file"
check=0
node e2e/lib/value-free-boundary.mjs publish --registries "$registries" --print "$dir/job-output.log" -- "${pulls[@]}" || check=$?
if ((check != 0)); then
  # The check withholds what every pull reaches when it refuses; whatever a pull still reaches here (a check that did not
  # reach its verdict) is withheld too (steps 5 and 6 above).
  IFS=$' \t\n'
  shopt -s globstar nullglob dotglob
  exact() { # a pull of one exact entry: `<dir>/**` or a relative path with no glob, blank, `.`, `..` or empty component
    local at=${1%/\*\*}
    [[ -n $at && $at != /* && $at != *[][*?{}\\[:space:]]* && /$at/ != *//* && /$at/ != */./* && /$at/ != */../* ]]
  }
  for p in "${pulls[@]}"; do
    exact "$p" || continue
    at=${p%/\*\*}
    if [[ -e $at || -L $at ]]; then mv -- "$at" "$(mktemp -u "$(dirname "$at")/.$(basename "$at")-withheld-XXXXXX")"; fi
  done
  quarantine=
  sealed=("$registries")
  # This home (step 7): resolved; left unread only when neither it nor its spelling is a directory the rig collects from.
  home=
  if [[ -n ${HOME:-} ]] && canon "$HOME"; then
    home=$cpath
    collectable "$home" || collectable "${HOME%/}" || sealed+=("$home")
  fi
  transferable() { # <a path>: at or below the pull root (either spelling), or below a directory the rig transfers from
    local t
    within "$1" "$root" || within "$1" "$rootc" && return 0
    for t in "${transporting[@]}"; do [[ $1 == "$t"/* ]] && return 0; done
    return 1
  }
  door() { # <a lexical path>: the first link on its way down (never the path itself) that is neither the pull root's own
    # spelling (the root or an ancestor) nor /Users or /Volumes, in `dpath`: the rig follows it, so it is what is withheld
    local rest=${1#/} p=
    while [[ $rest == */* ]]; do
      p=$p/${rest%%/*}
      rest=${rest#*/}
      [[ -L $p ]] || continue
      within "$root" "$p" && continue
      [[ $p == /Users || $p == /Volumes ]] && continue
      dpath=$p
      return 0
    done
    return 1
  }
  entry() { # <a lexical path>: where its entry canonically lies (its directory resolved, the entry never followed), in `epath`
    local d=${1%/*}
    [[ $1 == / ]] && { epath=/; return 0; }
    canon "${d:-/}" || return 1
    epath=${cpath%/}/${1##*/}
  }
  aside() { # <an entry>: into the quarantine, by its path below the root (outside it: outside/<its path>); 0 when it moved
    local rel to
    if within "$1" "$root"; then rel=${1#"$root"/}; elif within "$1" "$rootc"; then rel=${1#"$rootc"/}; else rel=outside$1; fi
    [[ -e $1 || -L $1 ]] || return 1
    if [[ -z $quarantine ]]; then
      made=$(mktemp -d "$private/value-free-withheld-XXXXXX") || return 1
      if ! canon "$made" || collectable "$cpath"; then rmdir -- "$made"; return 1; fi
      quarantine=$cpath
      sealed+=("$quarantine")
    fi
    to=$quarantine/$rel
    if [[ -e $to || -L $to ]]; then to=$(mktemp -d "$quarantine/again-XXXXXX")/$rel || return 1; fi
    mkdir -p -- "$(dirname "$to")" && mv -T -- "$1" "$to"
  }
  visit() { # <a lexical path a pull reaches>: withhold what the rig would take of it
    local at=$1 a x dpath epath
    # Reached through a link: the rig follows it, so the link moves (where the rig would take it), never what it points to.
    if door "$at"; then
      transferable "$dpath" && aside "$dpath" && moved=1
      return 0
    fi
    entry "$at" || return 0
    # The registries, the quarantine and an unread home are never read (compared canonically).
    for a in "${sealed[@]}"; do within "$epath" "$a" && return 0; done
    for a in "$root" "$rootc" "${sealed[@]}" ${home:+"$home"} "${transporting[@]}"; do
      within "$a" "$at" || within "$a" "$epath" || continue
      # It holds the root, the registries, the quarantine or the home, or the rig transfers from it: what it holds, never it.
      [[ -d $at && ! -L $at ]] || return 0
      for x in "${at%/}"/*; do visit "$x"; done
      return 0
    done
    if transferable "$at" || transferable "$epath"; then aside "$at" && moved=1; fi
    return 0
  }
  for pass in 1 2 3; do
    moved=0
    for p in "${pulls[@]}"; do
      [[ $p == /* ]] || p=$root/$p
      for m in $p; do # unquoted: split on blanks and globbed, as the rig reads it
        [[ -e $m || -L $m ]] || continue
        m=$(realpath -s -- "$m" && printf x) || continue
        visit "${m%$'\nx'}"
      done
    done
    ((moved)) || break
  done
fi
((rc == 0 && check == 0)) && exit 0 || exit 1
