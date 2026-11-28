#!/usr/bin/env bash
# Before an E2E leg boots its stack: restore the saved database dump into the
# stack's own database, so the boot's migrate has nothing left to apply.
#
# Only Postgres is up while this runs. The boot that follows runs the stack's
# own migrate as always: it applies nothing and sends post_migrate, so the
# seed rows come from the code under test.
#
# It never fails the leg. Anything that goes wrong puts the leg back where a
# leg with no dump starts (`compose down -v`: no container, no volume), and
# the boot migrates from zero. RESULT then says why; a later step turns a
# fall-back into a red leg on a dev push, after the suite has run.
#
# RESULT gets one line:
#   restored             the stack's database is the saved dump
#   zero                 this leg boots from zero by design (ROLE=zero)
#   off                  this run restores nothing for this stack
#   miss: <reason>       the dump is not for this leg; nothing was written
#   fell back: <reason>  a restore was tried and undone
set -uo pipefail
: "${KEY?}" "${STACK?}" "${RESTORE:?}" "${ROLE:?}" "${RECORD:?}" "${RESULT:?}"

here=$(cd "$(dirname "$0")" && pwd)
pg=(bin/e2e compose exec -T postgres)
dump_tool=(python3 -I "$here/db_dump.py")
tool=(python3 -I "$here/e2e_db.py")
user=futureagi database=futureagi

say() {
  printf '%s\n' "$1" > "$RESULT"
  echo "Database for this leg: $1"
}

fall_back() {
  echo "::warning title=E2E database reuse::The saved dump was not restored ($1). This leg migrates from zero."
  bin/e2e compose down -v > /dev/null 2>&1 || true
  say "fell back: $1"
  exit 0
}

if [[ $RESTORE != true ]]; then say off; exit 0; fi
if [[ $ROLE == zero ]]; then say zero; exit 0; fi

[[ -n $KEY ]] || fall_back "no key was handed to this leg"
# The plan job verified it too; this leg checks the copy it downloaded.
saved=$("${tool[@]}" verify "$RECORD" "$KEY") || fall_back "the downloaded dump does not verify"
# Postgres alone, empty: what the boot would start first.
bin/e2e compose up -d --wait postgres > /dev/null 2>&1 || fall_back "Postgres did not start"
version=$("${pg[@]}" psql -X -U "$user" -d postgres -At -c 'SHOW server_version_num') ||
  fall_back "could not read the Postgres version"
recipe=$("${tool[@]}" recipe "$KEY" "$version") || fall_back "could not read the Postgres version"
if [[ $saved != "$recipe" ]]; then
  # A new Postgres release: the dump is not for this server. Nothing written.
  say "miss: Postgres version"
  exit 0
fi
"${dump_tool[@]}" restore "$RECORD/dump.sql.gz" "$user" "$database" "${pg[@]}" > /dev/null ||
  fall_back "the restore failed"
rows=$("${dump_tool[@]}" seed-digest "$user" "$database" "${pg[@]}") ||
  fall_back "could not read the restored rows"
[[ $rows == "$(cat "$RECORD/preseed")" ]] ||
  fall_back "the restored rows are not the ones the dump was made from"

# A migration that writes outside Postgres is in the dump only as applied, so
# the boot's migrate does not run it again. Each such effect is made here,
# before anything that needs it starts. The list, and why the others need
# nothing: OutsideTest in futureagi/.ci/test_e2e_db.py.
case $STACK in
  distributed)
    # tracer 0093 registers the eval-task search attributes in Temporal.
    # Without them every eval-task workflow hangs. The stack's own command;
    # `compose run` starts Temporal (and Redis) on the restored Postgres, as
    # the boot does. Temporal adds its namespace a little after it starts.
    registered=false
    for try in {1..12}; do
      if bin/e2e compose run --rm -T --entrypoint python postgres-schema-bootstrap \
        manage.py register_eval_task_search_attributes > /dev/null 2>&1; then
        registered=true
        break
      fi
      [[ $try == 12 ]] || sleep 10
    done
    $registered || fall_back "the Temporal search attributes were not registered"
    ;;
  standalone) ;; # its bootstrap registers them on every start
  *) fall_back "no stack was named" ;;
esac
say restored
