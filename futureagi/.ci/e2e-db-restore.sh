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

# fall_back REASON [LOG]: LOG's last lines go into the warning too.
fall_back() {
  local detail=""
  if [[ -n ${2:-} && -s $2 ]]; then
    echo "Its last try printed (last 20 lines):"
    tail -n 20 "$2"
    # A workflow command is one line: %, CR and LF are escaped.
    detail=$(tail -n 5 "$2" | sed -e 's/%/%25/g' -e 's/\r/%0D/g' | awk '{printf "%s%%0A", $0}')
    detail=" Its last try printed:%0A$detail"
  fi
  echo "::warning title=E2E database reuse::The saved dump was not restored ($1). This leg migrates from zero.$detail"
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
    # Without them every eval-task workflow hangs. The stack refuses that
    # migration's own command outside migrate (model_hub/apps.py allows only
    # listed commands), so Temporal's CLI adds them, as Standalone's Temporal
    # gets them at its start. The names come from the product's own list.
    names=$("${tool[@]}" search-attributes "$here/../tfc/temporal/eval_tasks/search_attributes.py") ||
      fall_back "could not read the eval-task search attributes"
    try_log=$(mktemp)
    start=$SECONDS
    # Temporal on the restored Postgres, as the boot starts it.
    bin/e2e compose up -d --wait temporal > "$try_log" 2>&1 ||
      fall_back "Temporal did not start" "$try_log"
    # The stack's own TEMPORAL_HOST and TEMPORAL_NAMESPACE
    # (docker-compose.distributed.yml; OutsideTest pins them). Only `list`
    # and `create`: an attribute is never removed or changed.
    cli=(bin/e2e compose exec -T temporal temporal operator search-attribute)
    at=(--address temporal:7233 --namespace default)
    # Temporal adds its namespace a little after it starts. Only missing
    # names are created, so a try after a partial one still ends.
    registered=false
    for try in {1..12}; do
      if listed=$("${cli[@]}" list "${at[@]}" 2> "$try_log"); then
        missing=()
        for name in $names; do
          grep -qw -- "$name" <<< "$listed" || missing+=(--name "$name" --type Keyword)
        done
        if ((${#missing[@]} == 0)); then
          registered=true
          break
        fi
        "${cli[@]}" create "${at[@]}" "${missing[@]}" > "$try_log" 2>&1 && continue
      fi
      [[ $try == 12 ]] || sleep 10
    done
    $registered || fall_back "the Temporal search attributes were not registered" "$try_log"
    echo "Registered the Temporal search attributes ($names) in $((SECONDS - start)) s (try $try of 12)."
    ;;
  standalone) ;; # its bootstrap registers them on every start
  *) fall_back "no stack was named" ;;
esac
say restored
