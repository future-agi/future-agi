#!/usr/bin/env bash
# OCR on the real binary. The mock upstream (agentcc-mockup) serves no
# /v1/ocr, so the OCR provider ("mistral", the OpenAI-compatible adapter,
# which posts to {base}/v1/ocr) points at a stdlib stand-in
# (fixtures/ocr/ocr_standin.py) that answers a recorded Mistral OCR body
# (fixtures/ocr/ocr_response.json: 3 pages processed, markdown and a
# document annotation) and records what it received. The org calls it with
# its own key (BYOK). The request log goes to the mock upstream's /__logs
# (fixtures/ocr/gateway.yaml: base.yaml with bodies captured).
#  1. A range-string page selection ("pages":"0-2") reaches the upstream
#     unchanged, in a body identical to the one sent, under the org's key.
#  2. The client gets the upstream's own body: the document annotation,
#     headers and footers survive.
#  3. The log entry meters the call: metadata.ocr_pages=3, a non-zero BYOK
#     cost estimate (Mistral OCR 4: 3 pages x $0.004, plus the 3 pages the
#     document annotation covers x $0.005 = $0.027), and a response body
#     with pages_processed 3 but no markdown, no annotation and no document
#     URL (only the URL's hash).
#  4. A body that names a field the gateway reads twice, in two cases
#     ("model" and "Model"), is refused with 400 duplicate_field and never
#     reaches the upstream.
#
#   SCN_REDIS=127.0.0.1:16379 SCN_REDIS_DB=<your db> bash scripts/scenarios/05_ocr.sh
#
# set -u only, like the other scenarios: lib.sh's teardown kills processes
# that may already be gone. Every step that can fail checks for it.
set -u
source "$(dirname "$0")/lib.sh"

FIX=$SCN_ROOT/fixtures/ocr
DOC_URL="https://docs.example.com/scn-ocr/invoice-2026-0042.pdf"
BODY="{\"model\":\"mistral-ocr-4-0\",\"document\":{\"type\":\"document_url\",\"document_url\":\"$DOC_URL\"},\"pages\":\"0-2\"}"

# The stand-in and the rendered config live in the run's scratch dir (scn_up
# empties SCN_DIR). The stand-in exits by itself when this shell does.
WORK=$SCN_HOME/scn-ocr
rm -rf "$WORK"
mkdir -p "$WORK" || scn_fail setup "cannot create $WORK"
OCR_PORT=$(_scn_free_port)
python3 -I "$FIX/ocr_standin.py" "$OCR_PORT" "$FIX/ocr_response.json" "$WORK" $$ >"$WORK/ocr-standin.log" 2>&1 &
OCR_PID=$!
deadline=$(( $(date +%s) + 10 ))
until curl -sf -o /dev/null "http://127.0.0.1:$OCR_PORT/__health"; do
  kill -0 "$OCR_PID" 2>/dev/null || scn_fail setup "the OCR stand-in exited: $(tail -5 "$WORK/ocr-standin.log")"
  [ "$(date +%s)" -ge "$deadline" ] && scn_fail setup "the OCR stand-in is not answering on port $OCR_PORT"
  sleep 0.2
done
sed "s#@@OCR_URL@@#http://127.0.0.1:$OCR_PORT#" "$FIX/gateway.yaml" >"$WORK/gateway.yaml" || scn_fail setup "cannot write the config"

export SCN_CONFIG_TEMPLATE=$WORK/gateway.yaml
export SCN_CATALOG_WAIT_SKIP=all # OCR needs no managed catalog
scn_up
trap 'kill "$OCR_PID" 2>/dev/null; scn_down' EXIT

scn_org_config "$SCN_ORG" '{"providers":{"mistral":{"api_key":"scn-test-byok-mistral","models":["mistral-ocr-4-0"],"enabled":true}}}'

scn_check "1. a range-string page selection reaches the upstream unchanged"
code=$(scn_post /v1/ocr "$BODY")
scn_assert_eq 1.status 200 "$code"
REQ_ID=$(scn_hdr x-agentcc-request-id)
[ -n "$REQ_ID" ] || scn_fail 1.request_id "no x-agentcc-request-id header"
[ -s "$WORK/received.jsonl" ] || scn_fail 1.received "the stand-in received nothing: $(cat "$SCN_DIR/last.body")"
scn_assert_eq 1.upstream_calls 1 "$(wc -l <"$WORK/received.jsonl" | tr -d ' ')"
tail -1 "$WORK/received.jsonl" | jq -r .body >"$SCN_DIR/upstream-body.json"
scn_assert_eq 1.body_verbatim "$BODY" "$(cat "$SCN_DIR/upstream-body.json")"
scn_assert_eq 1.pages_raw '"0-2"' "$(jq -c .pages <"$SCN_DIR/upstream-body.json")"
scn_assert_eq 1.org_key "Bearer scn-test-byok-mistral" "$(tail -1 "$WORK/received.jsonl" | jq -r .authorization)"

scn_check "2. the client gets the upstream's own body"
# Byte for byte, whitespace included: the recorded body is pretty-printed, so
# a decode and re-encode anywhere on the way would show.
scn_assert_eq 2.raw_body same "$(python3 -I -c 'import sys
want = open(sys.argv[1], "rb").read().strip()
got = open(sys.argv[2], "rb").read()
print("same" if got == want else "differs: %d bytes, want %d" % (len(got), len(want)))' "$FIX/ocr_response.json" "$SCN_DIR/last.body")"
scn_assert_jq 2.annotation '.document_annotation | fromjson | .invoice_number' "2026-0042"
scn_assert_jq 2.footer '.pages[2].footer' "Page 3 of 3"

scn_check "3. the log entry meters 3 pages at a BYOK estimate and keeps no document text"
deadline=$(( $(date +%s) + 30 ))
while :; do
  curl -sS "$SCN_MOCK/__logs" | jq --arg id "$REQ_ID" '[.logs[] | select(.request_id == $id)] | first // empty' >"$SCN_DIR/entry.json"
  [ -s "$SCN_DIR/entry.json" ] && break
  [ "$(date +%s)" -ge "$deadline" ] && scn_fail 3.entry "no request log entry for $REQ_ID"
  sleep 0.5
done
scn_assert_jq 3.ocr_pages '.metadata.ocr_pages' "3" "$SCN_DIR/entry.json"
scn_assert_jq 3.byok_estimate_nonzero '.cost > 0' "true" "$SCN_DIR/entry.json"
scn_assert_jq 3.byok_estimate '.metadata.cost' "0.027000" "$SCN_DIR/entry.json"
scn_assert_jq 3.billing_mode '.metadata.billing_mode' "byok" "$SCN_DIR/entry.json"
scn_assert_jq 3.status '.status_code' "200" "$SCN_DIR/entry.json"
scn_assert_jq 3.pages_processed '.response_body.pages_processed' "3" "$SCN_DIR/entry.json"
scn_assert_jq 3.no_pages_listed '.response_body | has("pages")' "false" "$SCN_DIR/entry.json"
scn_assert_eq 3.no_markdown 0 "$(grep -c 'INVOICE 2026-0042\|Total due\|Widgets' "$SCN_DIR/entry.json")"
scn_assert_eq 3.no_annotation 0 "$(grep -c 'invoice_number' "$SCN_DIR/entry.json")"
scn_assert_eq 3.no_document_url 0 "$(grep -c 'docs.example.com' "$SCN_DIR/entry.json")"
scn_assert_jq 3.document_hash '.request_body.document.ref_sha256 | length' "64" "$SCN_DIR/entry.json"

scn_check "4. a field spelled in two cases is refused before the upstream"
SMUGGLED="{\"model\":\"mistral-ocr-premium\",\"document\":{\"type\":\"document_url\",\"document_url\":\"$DOC_URL\"},\"Model\":\"mistral-ocr-4-0\"}"
code=$(scn_post /v1/ocr "$SMUGGLED")
scn_assert_eq 4.status 400 "$code"
scn_assert_jq 4.code '.error.code' "duplicate_field"
scn_assert_jq 4.param '.error.param' "Model"
scn_assert_eq 4.upstream_calls 1 "$(wc -l <"$WORK/received.jsonl" | tr -d ' ')"

scn_pass
