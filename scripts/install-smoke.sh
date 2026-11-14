# shellcheck shell=bash
# Checks against an install running on this machine, shared by the install
# smoke test and the upgrade job in .github/workflows/standalone-ci.yml.
# Source it, then call the functions. They read FAGI_ADMIN_EMAIL and
# FAGI_ADMIN_PASSWORD (the account ./bin/install created), SMOKE_API and
# SMOKE_OTLP, and keep their scratch files in RUNNER_TEMP.

SMOKE_API=${SMOKE_API:-http://127.0.0.1:8000}
SMOKE_OTLP=${SMOKE_OTLP:-http://127.0.0.1:4318}

# seconds, command...: rerun the command until it succeeds
eventually() {
  local deadline=$((SECONDS + $1))
  shift
  until "$@"; do
    (( SECONDS < deadline )) || return 1
    sleep 5
  done
}

# Prints an access token for the account (POST /accounts/token/).
smoke_sign_in() {
  curl -fsS --max-time 30 -X POST "$SMOKE_API/accounts/token/" \
      -H 'Content-Type: application/json' \
      -d "$(jq -n --arg email "$FAGI_ADMIN_EMAIL" --arg password "$FAGI_ADMIN_PASSWORD" '{$email, $password}')" \
    | jq -er '.access'
}

# token: prints the account's API key, then its secret key (GET /accounts/keys/).
smoke_keys() {
  curl -fsS --max-time 30 "$SMOKE_API/accounts/keys/" -H "Authorization: Bearer $1" \
    | jq -er '.data | select(.api_key and .secret_key) | .api_key, .secret_key'
}

# project, API key, secret key: sends a two-span OTLP/HTTP trace (an agent run
# with one LLM call answering "Paris") to fi-collector, which must answer 200.
smoke_send_trace() {
  local project=$1 api_key=$2 secret_key=$3 start code
  start=$(date +%s%N)
  jq -n --arg trace "$(openssl rand -hex 16)" --arg root "$(openssl rand -hex 8)" \
    --arg child "$(openssl rand -hex 8)" --arg project "$project" \
    --arg t0 "$start" --arg t1 "$((start + 500000000))" \
    --arg t2 "$((start + 1500000000))" --arg t3 "$((start + 2000000000))" '
    def attr($k; $v): {key: $k, value: {stringValue: $v}};
    {resourceSpans: [{
      resource: {attributes: [attr("service.name"; $project),
        attr("project_name"; $project), attr("project_type"; "observe")]},
      scopeSpans: [{scope: {name: "ci-smoke"}, spans: [
        {traceId: $trace, spanId: $root, name: "agent.run", kind: 1,
         startTimeUnixNano: $t0, endTimeUnixNano: $t3, status: {code: 1},
         attributes: [attr("fi.span.kind"; "AGENT"),
           attr("input.value"; "What is the capital of France?"),
           attr("output.value"; "Paris")]},
        {traceId: $trace, spanId: $child, parentSpanId: $root, name: "llm.call",
         kind: 3, startTimeUnixNano: $t1, endTimeUnixNano: $t2, status: {code: 1},
         attributes: [attr("fi.span.kind"; "LLM"), attr("llm.model_name"; "gpt-4o-mini"),
           attr("input.value"; "capital of France?"), attr("output.value"; "Paris"),
           {key: "llm.token_count.prompt", value: {intValue: "12"}},
           {key: "llm.token_count.completion", value: {intValue: "3"}}]}
      ]}]
    }]}' > "$RUNNER_TEMP/otlp.json"
  code=$(curl -sS --max-time 30 -o "$RUNNER_TEMP/otlp-response.txt" -w '%{http_code}' \
    -X POST "$SMOKE_OTLP/v1/traces" -H 'Content-Type: application/json' \
    -H "X-Api-Key: $api_key" -H "X-Secret-Key: $secret_key" \
    --data @"$RUNNER_TEMP/otlp.json") || true
  if [ "$code" != 200 ]; then
    echo "OTLP/HTTP ingest on $SMOKE_OTLP answered ${code:-nothing}: $(head -c 300 "$RUNNER_TEMP/otlp-response.txt" || true)" >&2
    return 1
  fi
}

# token, project: the trace smoke_send_trace sent reads back through the
# Observe API.
smoke_trace_readable() {
  local token=$1 project=$2 project_id
  project_id=$(curl -fsS --max-time 30 -G "$SMOKE_API/tracer/project/" \
      -H "Authorization: Bearer $token" --data-urlencode "name=$project" \
    | jq -er --arg name "$project" '[.result.projects[] | select(.name == $name)][0].id') \
    || return 1
  curl -fsS --max-time 30 -G "$SMOKE_API/tracer/observation-span/list_spans_observe/" \
      -H "Authorization: Bearer $token" --data-urlencode "project_id=$project_id" \
      --data-urlencode page_number=0 --data-urlencode page_size=10 \
    > "$RUNNER_TEMP/spans.json" || return 1
  grep -q '"llm.call"' "$RUNNER_TEMP/spans.json" && grep -q 'Paris' "$RUNNER_TEMP/spans.json"
}

# tag, service...: every running container of each service (docker compose ps
# in the install's directory) was created from an image with that tag. Names
# the first one that was not on stderr.
smoke_services_run_tag() {
  local tag=$1 service ids id image
  shift
  for service in "$@"; do
    ids=$(docker compose ps -q "$service") || return 1
    if [ -z "$ids" ]; then
      echo "$service has no running container" >&2
      return 1
    fi
    for id in $ids; do
      image=$(docker inspect --format '{{.Config.Image}}' "$id") || return 1
      if [ "${image##*:}" != "$tag" ]; then
        echo "$service runs $image, not :$tag" >&2
        return 1
      fi
    done
  done
}
