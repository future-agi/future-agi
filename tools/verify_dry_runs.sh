#!/usr/bin/env bash
# Runs every dry run the examples/experiments/r4 READMEs print, exactly as
# written, and checks what each one claims. Exit 0 only when all pass.
#   bash tools/verify_dry_runs.sh
# stdlib Python 3 only; no network, no socket (the dry runs never bind one).
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
R4="$ROOT/examples/experiments/r4"
fails=0
pass() { echo "PASS $*"; }
fail() { echo "FAIL $*"; fails=$((fails + 1)); }

# Every folder the r4 README lists must exist, and every folder must be listed.
listed=$(sed -n 's/^| `\([a-z0-9-]*\)\/` .*/\1/p' "$R4/README.md" | sort)
present=$(cd "$R4" && find . -mindepth 1 -maxdepth 1 -type d | sed 's|^\./||' | sort)
if [ "$listed" = "$present" ]; then pass "r4 README lists exactly the shipped folders ($present)"; else
  fail "r4 README lists [$listed] but the folders are [$present]"; fi

# endpoint-http: the two dry-run lines of its README.
EH="$R4/endpoint-http"
for f in server.py body_template.json sample_rows.jsonl README.md; do
  [ -f "$EH/$f" ] && pass "endpoint-http/$f exists" || fail "endpoint-http/$f missing"
done
for cmd in "python3 -I server.py --dry-run" "python3 -I server.py --dry-run --verify-hmac s3cret"; do
  grep -qF "$cmd" "$EH/README.md" || fail "endpoint-http README no longer prints: $cmd"
  out=$(cd "$EH" && $cmd 2>&1); rc=$?
  if [ $rc -ne 0 ]; then fail "$cmd (rc=$rc): $out"; continue; fi
  # One {"reply": "Thanks, I'm on it: …", "row_id": <the row's id>} per sample row, in order.
  check=$(cd "$EH" && printf '%s\n' "$out" | python3 -I -c '
import json, sys
rows = [json.loads(l) for l in open("sample_rows.jsonl", encoding="utf-8") if l.strip()]
outs = [json.loads(l) for l in sys.stdin if l.strip()]
assert len(outs) == len(rows), f"{len(outs)} replies for {len(rows)} rows"
for r, o in zip(rows, outs):
    assert o.get("row_id") == r["id"], (o, r["id"])
    assert o["reply"] == "Thanks, I'"'"'m on it: " + r["instruction"].strip()[:80], o
print(len(outs))
' 2>&1) && pass "$cmd: $check replies, one per sample row, row ids in order" || fail "$cmd: $check"
done
# The body template is the one the README and server.py describe.
tmpl=$(python3 -I -c 'import json,sys; print(json.dumps(json.load(open(sys.argv[1]))))' "$EH/body_template.json")
[ "$tmpl" = '{"message": "{{instruction}}"}' ] && pass "body_template.json is {\"message\": \"{{instruction}}\"}" || fail "body_template.json is $tmpl"

echo "verify_dry_runs: $fails failure(s)"
[ $fails -eq 0 ]
