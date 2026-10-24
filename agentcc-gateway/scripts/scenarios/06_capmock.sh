#!/usr/bin/env bash
# The capability mock (cmd/agentcc-capmock) and lib_cap.sh, on the built
# binary.
#  0. It refuses to start beside a variable named like a vendor key, naming
#     the variable and never its value.
#  1. Every fake answers its golden (fixtures/capmock/goldens.json: one exchange
#     per vendor, the echo fakes included, each asserting the vendor's own
#     response fields), and /__fakes lists exactly those fakes.
#  2. status:429 answers 429 with Retry-After, from a header and, through
#     scn_cap_arm, for the next call only (a gateway forwards no test header).
#  3. Async jobs: AssemblyAI goes upload -> queued -> completed with
#     job_delay; job_fail_at:failed ends AssemblyAI in error, Azure DI and
#     fal in their failure states, armed or by header.
#  4. /__received echoes the last body each echo fake saw, with credential
#     headers kept as a hash only.
#  5. The other directives: fail_after, report_cost, url_expiry and
#     drop_response.
#
#   SCN_REDIS=127.0.0.1:16379 SCN_REDIS_DB=<your db> bash scripts/scenarios/06_capmock.sh
#
# The gateway is not started here: 07_capability_core.sh drives the gateway
# against this mock.
set -u
source "$(dirname "$0")/lib.sh"
source "$(dirname "$0")/lib_cap.sh"
mkdir -p "$SCN_DIR"

json() { scn_json "$1" "$SCN_CAP_DIR/last.body"; }

scn_check "0. the mock refuses a vendor-key environment"
scn_cap_build
env -i PATH="$PATH" HOME="$HOME" EXA_API_KEY=scn-not-a-real-key "$SCN_CAPMOCK_BIN" --addr 127.0.0.1:0 \
  >"$SCN_CAP_DIR/refused.log" 2>&1 &
pid=$!
for _ in $(seq 50); do kill -0 "$pid" 2>/dev/null || break; sleep 0.1; done
refused=false
if kill -0 "$pid" 2>/dev/null; then
  kill -9 "$pid" # it started: the guard is broken
else
  wait "$pid" || refused=true
fi
scn_assert_eq 0.refused true "$refused"
scn_assert_eq 0.names_the_variable 1 "$(grep -c 'EXA_API_KEY' "$SCN_CAP_DIR/refused.log")"
scn_assert_eq 0.never_the_value 0 "$(grep -c 'scn-not-a-real-key' "$SCN_CAP_DIR/refused.log")"

scn_cap_up

scn_check "1. every fake answers its golden"
result=$(scn_cap_golden "$SCN_ROOT/fixtures/capmock/goldens.json")
echo "$result"
scn_assert_eq 1.exchanges 23 "$(echo "$result" | jq .exchanges)"
scn_assert_eq 1.no_failures "[]" "$(echo "$result" | jq -c .failures)"
code=$(scn_cap_call GET /__fakes)
scn_assert_eq 1.fakes_listed 23 "$(json 'len(d["fakes"])')"
scn_assert_eq 1.echo_fakes '["echo-a", "echo-b"]' "$(json '[f["name"] for f in d["fakes"] if f["name"].startswith("echo")]')"

scn_check "2. status:429 by header, then armed for one call"
code=$(scn_cap_call POST /tavily/search '{"query":"q"}' -H 'x-capmock-behavior: status:429')
scn_assert_eq 2.header_429 429 "$code"
scn_assert_eq 2.retry_after 1 "$(scn_cap_hdr Retry-After)"
scn_assert_eq 2.vendor_error_body 429 "$(json 'd["error"]["code"]')"
scn_cap_arm brave status:429 1
code=$(scn_cap_call GET '/brave/res/v1/web/search?q=x')
scn_assert_eq 2.armed_429 429 "$code"
code=$(scn_cap_call GET '/brave/res/v1/web/search?q=x')
scn_assert_eq 2.arming_used_up 200 "$code"

scn_check "3. async jobs, and job_fail_at:failed"
code=$(scn_cap_call POST /assemblyai/v2/upload 'RIFF audio')
upload=$(json 'd["upload_url"]')
code=$(scn_cap_call POST /assemblyai/v2/transcript "{\"audio_url\":\"$upload\"}" -H 'x-capmock-behavior: job_delay:600')
transcript=$(json 'd["id"]')
scn_assert_eq 3.assemblyai_queued queued "$(json 'd["status"]')"
code=$(scn_cap_call GET "/assemblyai/v2/transcript/$transcript")
scn_assert_eq 3.assemblyai_still_queued queued "$(json 'd["status"]')"
sleep 0.7
code=$(scn_cap_call GET "/assemblyai/v2/transcript/$transcript")
scn_assert_eq 3.assemblyai_completed completed "$(json 'd["status"]')"
scn_assert_eq 3.assemblyai_text "hello from the capability mock" "$(json 'd["text"]')"
scn_cap_arm assemblyai job_fail_at:failed 1
code=$(scn_cap_call POST /assemblyai/v2/transcript "{\"audio_url\":\"$upload\"}")
code=$(scn_cap_call GET "/assemblyai/v2/transcript/$(json 'd["id"]')")
scn_assert_eq 3.assemblyai_error error "$(json 'd["status"]')"
code=$(scn_cap_call POST '/azure-di/documentintelligence/documentModels/prebuilt-read:analyze?api-version=2024-11-30' \
  '{"urlSource":"https://example.com/a.pdf"}' -H 'x-capmock-behavior: job_fail_at:failed')
scn_assert_eq 3.azure_202 202 "$code"
operation=$(scn_cap_hdr Operation-Location)
code=$(curl -sS -o "$SCN_CAP_DIR/last.body" -w '%{http_code}' "$operation")
scn_assert_eq 3.azure_failed failed "$(json 'd["status"]')"
code=$(scn_cap_call POST /fal/fal-ai/flux/dev '{"prompt":"p"}' -H 'x-capmock-behavior: job_fail_at:failed')
response_url=$(json 'd["response_url"]')
code=$(curl -sS -o "$SCN_CAP_DIR/last.body" -w '%{http_code}' "$response_url")
scn_assert_eq 3.fal_failed 500 "$code"
code=$(scn_cap_call POST /fal/fal-ai/flux/dev '{"prompt":"p"}' -H 'x-capmock-behavior: job_fail_at:running')
scn_assert_eq 3.bad_failure_status_refused 400 "$code"

scn_check "4. /__received echoes the last body"
code=$(scn_cap_call POST /echo-a/echo '{"text":"first"}')
code=$(scn_cap_call POST /echo-a/echo '{"text":"last-body-marker","n":7}' -H 'Authorization: Bearer scn-not-a-real-token')
code=$(scn_cap_call POST /echo-b/echo '{"text":"echo-b-body"}')
scn_cap_received echo-a 1 >"$SCN_CAP_DIR/last.body"
scn_assert_eq 4.one_entry 1 "$(json 'len(d["received"])')"
scn_assert_eq 4.echo_a_last_body '{"n": 7, "text": "last-body-marker"}' "$(json 'json.dumps(d["received"][0]["body"], sort_keys=True)')"
scn_assert_eq 4.credential_hashed true "$(json 'd["received"][0]["headers"]["authorization"].startswith("sha256:")')"
scn_assert_eq 4.no_clear_credential 0 "$(grep -c 'scn-not-a-real-token' "$SCN_CAP_DIR/last.body")"
scn_cap_received echo-b 1 >"$SCN_CAP_DIR/last.body"
scn_assert_eq 4.echo_b_last_body echo-b-body "$(json 'd["received"][0]["body"]["text"]')"

scn_check "5. fail_after, report_cost, url_expiry and drop_response"
scn_cap_arm echo-b fail_after:1,status:503
code=$(scn_cap_call POST /echo-b/echo '{}')
scn_assert_eq 5.fail_after_first_ok 200 "$code"
code=$(scn_cap_call POST /echo-b/echo '{}')
scn_assert_eq 5.fail_after_then_503 503 "$code"
scn_cap_clear echo-b
code=$(scn_cap_call POST /echo-a/echo '{}' -H 'x-capmock-behavior: report_cost:0.0015')
scn_assert_eq 5.report_cost 0.0015 "$(json 'd["usage"]["cost_usd"]')"
code=$(scn_cap_call POST /exa/search '{}' -H 'x-capmock-behavior: report_cost:0.02')
scn_assert_eq 5.report_cost_vendor_field 0.02 "$(json 'd["costDollars"]["total"]')"
code=$(scn_cap_call GET '/brave/res/v1/web/search?q=x' '' -H 'x-capmock-behavior: report_cost:0.02')
scn_assert_eq 5.report_cost_refused_without_vendor_field 400 "$code"
code=$(scn_cap_call POST /assemblyai/v2/upload 'RIFF' -H 'x-capmock-behavior: url_expiry:1')
expiring=$(json 'd["upload_url"]')
scn_assert_eq 5.url_before_expiry 200 "$(curl -sS -o /dev/null -w '%{http_code}' "$expiring")"
sleep 1.2
scn_assert_eq 5.url_after_expiry 403 "$(curl -sS -o /dev/null -w '%{http_code}' "$expiring")"
code=$(scn_cap_call POST /echo-a/echo '{}' -H 'x-capmock-behavior: drop_response' 2>/dev/null)
scn_assert_eq 5.drop_response 000 "$code"

scn_pass
