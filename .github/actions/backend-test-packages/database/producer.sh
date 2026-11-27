#!/usr/bin/env bash
# Called only on a disposable backend runner, before any application tests.
set -euo pipefail
pg=(docker compose -f docker-compose.test.yml -p futureagi-test exec -T test-db)
tool=(python3 -I .ci/db_dump.py)
fresh="$RUNNER_TEMP/fresh-db"
stage="$RUNNER_TEMP/migrated-db"
mkdir -p "$fresh" "$stage"
if [[ $FOUND == true ]]; then
  bash "$(dirname "$0")/verify-record.sh" "$stage" "$RECIPE"
else
  # A partial or unexpected download is not a clean staging directory.
  [[ -z $(find "$stage" -mindepth 1 -print -quit) ]]
fi
# The control: this checkout migrated from zero, with the overlay on internal
# runs. Observation only: report which backend files it touches
# (.ci/migrate_inputs.py); that never fails the migrate or changes the key.
CI_PRESEED_DIGEST="$fresh/preseed" CI_PRESEED_DUMP="$fresh/fresh.sql.gz" \
  CI_MIGRATE_INPUTS="$RUNNER_TEMP/migrate-inputs" PYTHONPATH=.ci \
  uv run pytest -p migrate_inputs .ci/migrated_db.py --create-db -q
# pg_dump does not print every restored definition as it printed the migrated
# one (CHECK ... = ANY (ARRAY[...]) comes back with each element cast), and a
# second round trip changes nothing. So compare dumps of restored databases:
# the control's, restored once, against the replayed dump.
"${pg[@]}" createdb -U test_user test_ci_control
"${tool[@]}" restore "$fresh/fresh.sql.gz" test_user test_ci_control "${pg[@]}"
"${tool[@]}" dump "$fresh/control.sql.gz" test_user test_ci_control "${pg[@]}"
# Restore a dump and run pytest's migrate on it, as a shard does. That migrate
# applies whatever the dump lacks (the overlay's part on internal runs) and
# records the database again at the post_migrate boundary.
replay() {
  "${pg[@]}" createdb -U test_user test_ci_roundtrip
  "${tool[@]}" restore "$1" test_user test_ci_roundtrip "${pg[@]}"
  CI_PRESEED_DIGEST="$fresh/replayed" CI_PRESEED_DUMP="$fresh/replayed.sql.gz" \
    PG_TEST_DB=test_ci_roundtrip uv run pytest .ci/migrated_db.py -q
}
if [[ $FOUND == true ]]; then
  # This run's shards restored this dump. It must still equal what this
  # checkout migrates, or the run fails: a wrong key is red, never green.
  replay "$stage/dump.sql.gz"
  cmp "$stage/preseed" "$fresh/replayed"
  "${tool[@]}" compare "$fresh/replayed.sql.gz" "$fresh/control.sql.gz"
  exit 0
fi
[[ $PRODUCE == true ]] || exit 0
unqualified() {
  echo "::warning title=Database dump::$1. No dump is saved; shards keep migrating from zero."
  exit 0
}
# Actions artifacts of a public repository are public. Only save SQL that was
# migrated WITHOUT the private checkout, and only when that dump, restored and
# migrated with this checkout, equals the control. Never upload the control.
public="$fresh/fresh.sql.gz"
preseed="$fresh/preseed"
if [[ -e ee/cloud ]]; then
  mv ee/cloud "$RUNNER_TEMP/held-ee-checkout"
  trap 'mv "$RUNNER_TEMP/held-ee-checkout" ee/cloud' EXIT
  CI_PRESEED_DIGEST="$fresh/public-preseed" CI_PRESEED_DUMP="$fresh/public.sql.gz" \
    PG_TEST_DB=test_ci_public uv run pytest .ci/migrated_db.py --create-db -q
  mv "$RUNNER_TEMP/held-ee-checkout" ee/cloud
  trap - EXIT
  public="$fresh/public.sql.gz"
  preseed="$fresh/public-preseed"
fi
replay "$public"
cmp -s "$preseed" "$fresh/replayed" || {
  status=$?
  if [[ $status == 1 ]]; then unqualified 'Migrating the restored public dump changed it'; fi
  echo 'Could not compare seed digests.' >&2
  exit "$status"
}
"${tool[@]}" compare "$fresh/replayed.sql.gz" "$fresh/control.sql.gz" > "$fresh/parity.log" || {
  status=$?
  # db_dump.COMPARE_DIFFERENT; every other nonzero status is a tool failure.
  if [[ $status == 3 ]]; then unqualified 'The public dump plus this checkout differs from a fresh migrate'; fi
  echo 'Could not compare fresh database dumps.' >&2
  exit "$status"
}
cp "$public" "$stage/dump.sql.gz"
cp "$preseed" "$stage/preseed"
printf '%s\n' "$RECIPE" > "$stage/recipe"
(cd "$stage" && sha256sum dump.sql.gz preseed recipe > checksums)
