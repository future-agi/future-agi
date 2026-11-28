#!/usr/bin/env bash
# The scratch control for an E2E stack's saved database dump.
#
# It runs on a leg whose stack (E2E_STACK) is up, beside the stack's own
# database and never in it: the stack's own image migrates a scratch database
# from zero. With a saved dump (FOUND=true) the two are compared. On a dev
# push (PRODUCE=true) with no usable saved dump, one is written to OUT.
#
# A saved dump made by another Postgres version is not compared: a new minor
# release may print a dump differently. A dev push then saves a fresh one.
#
# Both sides of every compare have been through one restore and one dump:
# pg_dump prints a restored definition differently from a migrated one.
#
# Exit 3: the saved dump differs from a fresh migrate. Any other failure
# means the control could not run. Callers tell the two apart.
set -euo pipefail
: "${KEY:?}" "${FOUND:?}" "${PRODUCE:?}" "${RECORD:?}" "${OUT:?}" "${RUNNER_TEMP:?}"
stack=${E2E_STACK:-distributed}
[[ $FOUND == true || $PRODUCE == true ]] || exit 0

here=$(cd "$(dirname "$0")" && pwd)
pg=(bin/e2e compose exec -T postgres)
dump_tool=(python3 -I "$here/db_dump.py")
tool=(python3 -I "$here/e2e_db.py")
user=futureagi
work="$RUNNER_TEMP/e2e-db-control"
rm -rf "$work"
mkdir -p "$work" "$OUT"
[[ -z $(find "$OUT" -mindepth 1 -print -quit) ]]

scratch() {
  "${pg[@]}" dropdb -U "$user" --if-exists "$1"
  "${pg[@]}" createdb -U "$user" "$1"
}

# The stack's own `manage.py migrate --noinput`, stopped before post_migrate
# (e2e_db_migrate.py says why). Passed as text: .ci/ is not in the image.
boundary=$(< "$here/e2e_db_migrate.py")

# The stack's own migrate, pointed at another database. Same image, same
# settings; what its own migrate step sets before it migrates is set here too.
case $stack in
  # The bootstrap service's definition carries SERVICE_TYPE=bootstrap and
  # NO_STARTUP_DB_MUTATIONS=false. Its entrypoint adds nothing migrate reads.
  distributed) run_migrate=(--entrypoint python postgres-schema-bootstrap) ;;
  # deploy/standalone/bin/bootstrap.py sets these two in its own process
  # before it migrates. A one-off command in the app image skips the start
  # script, whose exports are for the servers.
  standalone)
    run_migrate=(-e NO_STARTUP_DB_MUTATIONS=false -e SERVICE_TYPE=bootstrap
      -w /app/backend app python)
    ;;
  *)
    echo "::error title=E2E database dump::No scratch migrate is defined for the stack '$stack'."
    exit 1
    ;;
esac

# migrate DATABASE DUMP: a scratch database migrated from zero, and its dump.
# `migrate` alone: no cache table, no seed command.
migrate() {
  scratch "$1"
  if ! bin/e2e compose run --rm --no-deps -T -e "PG_DB=$1" "${run_migrate[@]}" \
    -c "$boundary" > "$work/$1.log" 2>&1; then
    tail -n 40 "$work/$1.log"
    echo "::error title=E2E database dump::The scratch migrate failed."
    return 1
  fi
  "${dump_tool[@]}" dump "$2" "$user" "$1" "${pg[@]}"
}

# replay DUMP OUT: DUMP restored into an empty scratch database, dumped again.
replay() {
  scratch e2e_scratch_replay
  "${dump_tool[@]}" restore "$1" "$user" e2e_scratch_replay "${pg[@]}"
  "${dump_tool[@]}" dump "$2" "$user" e2e_scratch_replay "${pg[@]}"
}

replayed_rows() {
  "${dump_tool[@]}" seed-digest "$user" e2e_scratch_replay "${pg[@]}"
}

# differ FIRST SECOND: 0 when equal, 3 when they differ (the tables are then
# in $where), anything else when they could not be compared.
differ() {
  local status=0
  "${dump_tool[@]}" compare "$1" "$2" > "$work/compare.log" || status=$?
  if [[ $status == 3 ]]; then
    where=$("${tool[@]}" differing "$1" "$2" | paste -sd, - | sed 's/,/, /g')
  elif [[ $status != 0 ]]; then
    cat "$work/compare.log"
  fi
  return "$status"
}

version=$("${pg[@]}" psql -X -U "$user" -d postgres -At -c 'SHOW server_version_num')
# The tool prints its reason on stdout; a failed capture would swallow it.
recipe=$("${tool[@]}" recipe "$KEY" "$version") || { printf '%s\n' "$recipe"; exit 1; }

if [[ $FOUND == true ]]; then
  saved=$("${tool[@]}" verify "$RECORD" "$KEY") || { printf '%s\n' "$saved"; exit 1; }
  if [[ $saved != "$recipe" ]]; then
    echo "::notice title=E2E database dump::The saved dump was made by another Postgres version ($saved, this server gives $recipe). It is not compared."
    [[ $PRODUCE == true ]] || exit 0
    FOUND=false
  fi
fi

migrate e2e_scratch_fresh "$work/fresh.sql.gz"
fresh_rows=$("${dump_tool[@]}" seed-digest "$user" e2e_scratch_fresh "${pg[@]}")
replay "$work/fresh.sql.gz" "$work/control.sql.gz"
fresh_replayed_rows=$(replayed_rows)

if [[ $FOUND == true ]]; then
  replay "$RECORD/dump.sql.gz" "$work/saved.sql.gz"
  # What a leg would hold after restoring: the rows the producer dumped.
  [[ $(replayed_rows) == "$(cat "$RECORD/preseed")" ]] || {
    echo "::error title=E2E database dump::Restoring the saved dump does not give back the rows it was made from."
    exit 1
  }
  status=0
  differ "$work/saved.sql.gz" "$work/control.sql.gz" || status=$?
  if [[ $status == 3 ]]; then
    echo "::error title=Saved E2E database dump differs from a fresh migrate::Differs in: $where. A file outside the key's inputs changed what migrate writes, or a migration writes a value that changes from run to run. Add the file to the key in futureagi/.ci/e2e_db.py, or raise VERSION there."
  fi
  [[ $status == 0 ]] || exit "$status"
  echo "The saved E2E dump ($saved) equals a fresh migrate: same schema, same rows up to generated ids and times."
  exit 0
fi
[[ $PRODUCE == true ]] || exit 0

unqualified() {
  echo "::warning title=E2E database dump::$1. No dump is saved; every leg keeps migrating from zero."
  exit 0
}

[[ $fresh_rows == "$fresh_replayed_rows" ]] ||
  unqualified "Restoring the fresh dump does not give back its rows"
# Only a dump this commit can reproduce is saved: a second migrate from zero
# must give the same dump. One that differs would fail every later run.
migrate e2e_scratch_second "$work/second.sql.gz"
replay "$work/second.sql.gz" "$work/second-control.sql.gz"
status=0
differ "$work/second-control.sql.gz" "$work/control.sql.gz" || status=$?
if [[ $status == 3 ]]; then unqualified "Two migrates of this commit differ in: $where"; fi
[[ $status == 0 ]] || exit "$status"

"${tool[@]}" record "$OUT" "$work/fresh.sql.gz" "$fresh_rows" "$recipe"
echo "Saved the E2E database dump as $recipe."
