#!/usr/bin/env bash

set -euo pipefail

usage() {
    cat <<'USAGE'
Usage: verify-simulation-runner-deployment.sh [options]

Validate the root Compose file and the disabled-by-default GCP simulation-runner
chart scaffolding without changing Docker, Helm, or Kubernetes state.

Options:
  --repo-root PATH         Future-AGI repository root
  --deployment-root PATH   Deployment repository root
  --compose-file PATH      Compose file to validate; repeat for an explicit set
  --release NAME           Helm release name used for rendering
  --namespace NAME         Helm namespace used for rendering
  --runner-tag TAG         Override the runner image tag for rendering (latest is rejected)
  --runner-env-file PATH   KEY=VALUE file holding the runner worker's EFFECTIVE
                           environment, e.g. `docker exec <runner> env > file` or
                           the rendered Secret. Not a shared .env: other services
                           may legitimately carry keys the runner must not see.
                           Repeat for layered files; later files win. Names are
                           checked, values are never printed.
  --voice-transports LIST  Comma-separated hosted voice transports the deployment
                           must serve: sip_outbound (we dial the agent),
                           sip_inbound_retell (the Retell agent dials our leased
                           number). Adds that transport's required settings.
  --env-only               Run only the runner environment contract check; skips
                           the Compose and Helm renders (no docker/helm needed).
  -h, --help               Show this help

Environment overrides use the SIMULATION_RUNNER_* prefix with the same names.

Runner environment contract (what the hosted voice runner refuses without).
Every problem is reported together; a value of only whitespace counts as missing.
  always            LIVEKIT_URL LIVEKIT_API_KEY LIVEKIT_API_SECRET INTERNAL_API_SECRET
                    and ALK_RUNNER_API_URL (or FI_BASE_URL) for result ingestion
  sip_outbound      LIVEKIT_OUTBOUND_TRUNK_ID PSTN_CALLER_NUMBER (E.164)
  sip_inbound_retell ALK_SIM_SLOT_LEASE_SCRIPT SIM_SLOT_LEASE_STORE
  must be unset     FI_API_KEY FI_SECRET_KEY (the SDK submitter prefers them over
                    the internal bearer and ingestion then authenticates the wrong
                    org), HOSTED_RUNNER_MAX_DURATION_SECONDS (retired)
  optional          HOSTED_RUNNER_PARENT_SLACK_SECONDS (integer, default 600),
                    HOSTED_RUNNER_LEASED_ROOM_REUSE (default false; turn on only
                    once a reuse-capable runner kit is deployed). Assigned-but-
                    empty is refused: int("") kills the worker at import.
  glued lines       a value containing one of these names followed by "=" is two
                    assignments joined by an append with no trailing newline.
USAGE
}

die() {
    echo "ERROR: $*" >&2
    exit 1
}

script_dir=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=${SIMULATION_RUNNER_REPO_ROOT:-$(CDPATH= cd -- "$script_dir/.." && pwd)}
deployment_root=${SIMULATION_RUNNER_DEPLOYMENT_ROOT:-}
compose_files=()
explicit_compose_set=false
if [[ -n "${SIMULATION_RUNNER_COMPOSE_FILE:-}" ]]; then
    compose_files=("$SIMULATION_RUNNER_COMPOSE_FILE")
    explicit_compose_set=true
fi
helm_release=${SIMULATION_RUNNER_HELM_RELEASE:-simulation-runner-preflight}
helm_namespace=${SIMULATION_RUNNER_HELM_NAMESPACE:-default}
runner_tag=${SIMULATION_RUNNER_IMAGE_TAG:-preflight-only}
runner_env_files=()
if [[ -n "${SIMULATION_RUNNER_RUNNER_ENV_FILE:-}" ]]; then
    runner_env_files=("$SIMULATION_RUNNER_RUNNER_ENV_FILE")
fi
voice_transports=${SIMULATION_RUNNER_VOICE_TRANSPORTS:-}
env_only=false

while (($#)); do
    case "$1" in
        --repo-root)
            (($# >= 2)) || die "--repo-root requires a path"
            repo_root=$2
            shift 2
            ;;
        --deployment-root)
            (($# >= 2)) || die "--deployment-root requires a path"
            deployment_root=$2
            shift 2
            ;;
        --compose-file)
            (($# >= 2)) || die "--compose-file requires a path"
            [[ -n "$2" ]] || die "--compose-file requires a non-empty path"
            compose_files+=("$2")
            explicit_compose_set=true
            shift 2
            ;;
        --release)
            (($# >= 2)) || die "--release requires a name"
            helm_release=$2
            shift 2
            ;;
        --namespace)
            (($# >= 2)) || die "--namespace requires a name"
            helm_namespace=$2
            shift 2
            ;;
        --runner-tag)
            (($# >= 2)) || die "--runner-tag requires a tag"
            [[ -n "$2" ]] || die "--runner-tag requires a non-empty tag"
            runner_tag=$2
            shift 2
            ;;
        --runner-env-file)
            (($# >= 2)) || die "--runner-env-file requires a path"
            [[ -n "$2" ]] || die "--runner-env-file requires a non-empty path"
            runner_env_files+=("$2")
            shift 2
            ;;
        --voice-transports)
            (($# >= 2)) || die "--voice-transports requires a list"
            voice_transports=$2
            shift 2
            ;;
        --env-only)
            env_only=true
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            die "unknown argument: $1"
            ;;
    esac
done

[[ "$runner_tag" != "latest" ]] || die "--runner-tag latest is not allowed"

if [[ -z "$deployment_root" ]]; then
    deployment_root="$repo_root/../deployment"
fi
if ((${#compose_files[@]} == 0)); then
    compose_files=("$repo_root/docker-compose.yml")
    if [[ -f "$repo_root/docker-compose.dev.yml" ]]; then
        compose_files+=("$repo_root/docker-compose.dev.yml")
    fi
fi

[[ -d "$repo_root" ]] || die "repository root does not exist: $repo_root"
if ((${#runner_env_files[@]} == 0)); then
    [[ "$env_only" != true ]] || die "--env-only requires at least one --runner-env-file"
    [[ -z "$voice_transports" ]] || \
        die "--voice-transports names a contract to check but no --runner-env-file" \
            "was given"
fi
if [[ "$env_only" != true ]]; then
    [[ -d "$deployment_root" ]] || die "deployment root does not exist: $deployment_root"
    for compose_file in "${compose_files[@]}"; do
        [[ -f "$compose_file" ]] || die "Compose file does not exist: $compose_file"
    done
fi

# ---- Runner environment contract -------------------------------------------
# The hosted voice runner reads these from its process environment and refuses
# one run per missing name. Check them here, once, before anything is rolled
# out, and report every problem together. Only names are ever printed; values
# stay in this process. Plain file scans, no bash-4 features: this must run
# from a stock macOS bash 3.2 too.

contract_failures=()

contract_fail() {
    contract_failures+=("$*")
}

runner_env_file_exists() {
    local file
    for file in "${runner_env_files[@]}"; do
        [[ -f "$file" ]] || die "runner env file does not exist: $file"
    done
}

strip_ws() {
    local value=$1
    value=${value#"${value%%[![:space:]]*}"}
    value=${value%"${value##*[![:space:]]}"}
    printf '%s' "$value"
}

# Last assignment across the files wins, matching dotenv/Compose semantics.
# Whitespace around the value and then surrounding quotes are dropped the way
# the builder's strip does, so a value of spaces counts as missing here as it
# does there. The raw form is the value verbatim: the input is the runner's
# effective environment, which the builder hands to the kit untouched, and the
# kit's E.164 rule must hold on exactly that.
runner_env_value() {
    local name=$1
    local raw=${2:-}
    local file line value found=""
    for file in "${runner_env_files[@]}"; do
        while IFS= read -r line || [[ -n "$line" ]]; do
            line=${line%$'\r'}
            [[ "$line" == "$name="* ]] || continue
            value=${line#*=}
            if [[ -z "$raw" ]]; then
                value=$(strip_ws "$value")
                if [[ "$value" =~ ^\"(.*)\"$ || "$value" =~ ^\'(.*)\'$ ]]; then
                    value=$(strip_ws "${BASH_REMATCH[1]}")
                fi
            fi
            found=$value
        done <"$file"
    done
    printf '%s' "$found"
}

runner_env_set() {
    [[ -n "$(runner_env_value "$1")" ]]
}

# True when any file carries a NAME= line, even an empty one. An optional
# setting that is assigned but empty is not "absent": int("") kills the worker
# at import and an empty boolean silently reads as false.
runner_env_assigned() {
    local name=$1
    local file
    for file in "${runner_env_files[@]}"; do
        grep -q "^$name=" "$file" && return 0
    done
    return 1
}

# A value that contains one of this contract's exact names followed by "=" is
# two lines glued together by an append onto a file with no trailing newline;
# both settings are then wrong and the failure surfaces one run later. Exact
# names only: a prefix pattern would refuse a URL query like ?LIVEKIT_URL_HINT=.
contract_names='LIVEKIT_URL|LIVEKIT_API_KEY|LIVEKIT_API_SECRET|LIVEKIT_OUTBOUND_TRUNK_ID'
contract_names+='|INTERNAL_API_SECRET|ALK_RUNNER_API_URL|FI_BASE_URL|PSTN_CALLER_NUMBER'
contract_names+='|ALK_SIM_SLOT_LEASE_SCRIPT|SIM_SLOT_LEASE_STORE'
contract_names+='|HOSTED_RUNNER_PARENT_SLACK_SECONDS|HOSTED_RUNNER_LEASED_ROOM_REUSE'
contract_names+='|HOSTED_RUNNER_MAX_DURATION_SECONDS|FI_API_KEY|FI_SECRET_KEY'
glued_pattern="($contract_names)="

check_runner_env_not_glued() {
    local file line value lineno
    for file in "${runner_env_files[@]}"; do
        lineno=0
        while IFS= read -r line || [[ -n "$line" ]]; do
            lineno=$((lineno + 1))
            line=${line%$'\r'}
            [[ "$line" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]] || continue
            value=${line#*=}
            if [[ "$value" =~ $glued_pattern ]]; then
                contract_fail "$file line $lineno looks like two assignments glued on one line" \
                    "(missing newline before the next KEY=)"
            fi
        done <"$file"
    done
}

check_runner_env_present() {
    local scope=$1
    shift
    local missing=()
    local name
    for name in "$@"; do
        runner_env_set "$name" || missing+=("$name")
    done
    ((${#missing[@]} == 0)) || \
        contract_fail "missing for $scope: ${missing[*]}"
}

check_runner_env_absent() {
    local reason=$1
    shift
    local present=()
    local name
    for name in "$@"; do
        runner_env_set "$name" && present+=("$name")
    done
    ((${#present[@]} == 0)) || \
        contract_fail "must be unset: ${present[*]} ($reason)"
}

verify_runner_env() {
    local transport value
    runner_env_file_exists
    check_runner_env_not_glued
    check_runner_env_present "hosted voice runs" \
        LIVEKIT_URL LIVEKIT_API_KEY LIVEKIT_API_SECRET INTERNAL_API_SECRET
    # The child reports results to one of these; without either, nothing lands.
    runner_env_set ALK_RUNNER_API_URL || runner_env_set FI_BASE_URL || \
        contract_fail "missing for result ingestion: ALK_RUNNER_API_URL (or FI_BASE_URL)"
    local transports=()
    IFS=', ' read -r -a transports <<<"$voice_transports"
    for transport in "${transports[@]+"${transports[@]}"}"; do
        transport=${transport// /}
        case "$transport" in
            "")
                ;;
            sip_outbound)
                check_runner_env_present "sip_outbound (we dial the agent)" \
                    LIVEKIT_OUTBOUND_TRUNK_ID PSTN_CALLER_NUMBER
                value=$(runner_env_value PSTN_CALLER_NUMBER raw)
                # The kit's transport model rejects anything but E.164 at hydration,
                # on the value as handed over: stray whitespace fails it there too.
                if [[ -n "$value" && ! "$value" =~ ^\+[1-9][0-9]{6,14}$ ]]; then
                    contract_fail "PSTN_CALLER_NUMBER is not E.164 (+ then 7-15 digits;" \
                        "no spaces or quotes, exactly as the runner env holds it)"
                fi
                ;;
            sip_inbound_retell)
                check_runner_env_present \
                    "sip_inbound_retell (the Retell agent dials our leased number)" \
                    ALK_SIM_SLOT_LEASE_SCRIPT SIM_SLOT_LEASE_STORE
                ;;
            *)
                contract_fail "unknown voice transport: $transport" \
                    "(expected sip_outbound, sip_inbound_retell)"
                ;;
        esac
    done
    local key_pair_reason="the SDK submitter prefers the org key pair over the"
    key_pair_reason+=" internal bearer, so ingestion authenticates the wrong org"
    check_runner_env_absent "$key_pair_reason" FI_API_KEY FI_SECRET_KEY
    check_runner_env_absent \
        "retired: the run budget is the child's own deadline" \
        HOSTED_RUNNER_MAX_DURATION_SECONDS
    if runner_env_assigned HOSTED_RUNNER_PARENT_SLACK_SECONDS; then
        value=$(runner_env_value HOSTED_RUNNER_PARENT_SLACK_SECONDS)
        [[ "$value" =~ ^\+?[0-9]+$ ]] || \
            contract_fail "HOSTED_RUNNER_PARENT_SLACK_SECONDS must be a non-negative integer" \
                "(an empty assignment kills the worker at import)"
    fi
    if runner_env_assigned HOSTED_RUNNER_LEASED_ROOM_REUSE; then
        value=$(runner_env_value HOSTED_RUNNER_LEASED_ROOM_REUSE)
        # The code lowercases and treats true/1/yes as on and anything else as
        # off, so a typo silently disables reuse; only the explicit spellings pass.
        case "$(printf '%s' "$value" | tr '[:upper:]' '[:lower:]')" in
            true|1|yes|false|0|no) ;;
            *)
                contract_fail "HOSTED_RUNNER_LEASED_ROOM_REUSE must be true/1/yes or false/0/no" \
                    "(anything else reads as false; empty is refused)"
                ;;
        esac
    fi
    if ((${#contract_failures[@]} > 0)); then
        local failure
        for failure in "${contract_failures[@]}"; do
            echo "ERROR: runner env contract: $failure" >&2
        done
        exit 1
    fi
    echo "Runner environment contract (${#runner_env_files[@]} file(s);" \
        "transports: ${voice_transports:-none}): OK"
}

if ((${#runner_env_files[@]} > 0)); then
    verify_runner_env
fi
if [[ "$env_only" == true ]]; then
    echo "Simulation-runner deployment preflight (env only): PASS"
    exit 0
fi

command -v helm >/dev/null 2>&1 || die "helm is required"
helm_command=(helm)

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    compose_command=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
    compose_command=(docker-compose)
else
    die "Docker Compose is required"
fi

umask 077
temp_dir=$(mktemp -d "${TMPDIR:-/tmp}/simulation-runner-preflight.XXXXXX")
cleanup() {
    rm -rf -- "$temp_dir"
}
trap cleanup EXIT

run_quiet() {
    local description=$1
    local log_file=$2
    shift 2

    if ! "$@" >"$log_file" 2>&1; then
        die "$description failed; command output was withheld to keep this preflight secret-safe"
    fi
}

run_to_file() {
    local description=$1
    local output_file=$2
    local log_file=$3
    shift 3

    if ! "$@" >"$output_file" 2>"$log_file"; then
        die "$description failed; command output was withheld to keep this preflight secret-safe"
    fi
}

assert_contains() {
    local file=$1
    local needle=$2
    local description=$3

    grep -Fq -- "$needle" "$file" || die "$description"
}

assert_not_contains() {
    local file=$1
    local needle=$2
    local description=$3

    if grep -Fq -- "$needle" "$file"; then
        die "$description"
    fi
}

rendered_env_value() {
    local env_name=$1
    local file=$2

    awk -v env_name="$env_name" '
        $0 ~ "^[[:space:]]+- name: " env_name "$" {
            if (getline <= 0) {
                exit
            }
            sub(/^[[:space:]]*value:[[:space:]]*/, "")
            gsub(/"/, "")
            print
            exit
        }
    ' "$file"
}

assert_rendered_env_value() {
    local file=$1
    local env_name=$2
    local expected=$3
    local description=$4
    local actual

    actual=$(rendered_env_value "$env_name" "$file")
    [[ "$actual" == "$expected" ]] || die "$description"
}

extract_compose_runner() {
    local compose_render=$1
    local runner_render=$2

    awk '
        $0 == "  worker-simulation-runner:" {
            in_runner=1
            print
            next
        }
        in_runner && /^  [^[:space:]][^:]*:/ {
            exit
        }
        in_runner {
            print
        }
    ' "$compose_render" >"$runner_render"
}

compose_field_value() {
    local field_name=$1
    local file=$2

    awk -v field_name="$field_name" '
        $1 == field_name ":" {
            value=$0
            sub(/^[[:space:]]*[^:]+:[[:space:]]*/, "", value)
            gsub(/"/, "", value)
            print value
            exit
        }
    ' "$file"
}

assert_compose_field_value() {
    local file=$1
    local field_name=$2
    local expected=$3
    local description=$4
    local actual

    actual=$(compose_field_value "$field_name" "$file")
    [[ "$actual" == "$expected" ]] || die "$description"
}

runner_value() {
    local field_name=$1
    local values_file=$2

    awk -v field_name="$field_name" '
        $0 == "temporal_worker_simulation_runner:" {
            in_runner=1
            next
        }
        in_runner && /^[^[:space:]]/ {
            exit
        }
        in_runner && $1 == field_name ":" {
            value=$2
            gsub(/"/, "", value)
            print value
            exit
        }
    ' "$values_file"
}

# Compose renders durations as 5m30s / 1h10m / 45s; the stop grace must be
# compared in seconds against the worker's drain timeout.
compose_duration_seconds() {
    local text=$1 total=0 num unit
    while [[ "$text" =~ ^([0-9]+)([hms]) ]]; do
        num=${BASH_REMATCH[1]}
        unit=${BASH_REMATCH[2]}
        case "$unit" in
            h) total=$((total + num * 3600)) ;;
            m) total=$((total + num * 60)) ;;
            s) total=$((total + num)) ;;
        esac
        text=${text#"${BASH_REMATCH[0]}"}
    done
    [[ -z "$text" ]] || return 1
    printf '%s' "$total"
}

assert_integer_at_least() {
    local value=$1
    local minimum=$2
    local description=$3

    [[ "$value" =~ ^[0-9]+$ ]] || die "$description is not an integer"
    ((value >= minimum)) || die "$description is below the required safety margin"
}

compose_args=()
for compose_file in "${compose_files[@]}"; do
    compose_args+=( -f "$compose_file" )
done
compose_render="$temp_dir/compose.yaml"
run_to_file \
    "Compose configuration validation" \
    "$compose_render" \
    "$temp_dir/compose.log" \
    env \
    SIMULATION_RUNNER_VERSION=preflight-only \
    "${compose_command[@]}" \
    --profile workers \
    "${compose_args[@]}" \
    config
compose_runner_render="$temp_dir/compose-runner.yaml"
extract_compose_runner "$compose_render" "$compose_runner_render"
[[ -s "$compose_runner_render" ]] || die "Compose render omitted worker-simulation-runner"
assert_compose_field_value \
    "$compose_runner_render" \
    image \
    "futureagi/future-agi-simulation-runner:preflight-only" \
    "Compose runner image is not the synthetic preflight tag"
assert_not_contains \
    "$compose_runner_render" \
    'futureagi/future-agi-simulation-runner:latest' \
    "Compose runner accepts a mutable latest image"
assert_compose_field_value \
    "$compose_runner_render" \
    TEMPORAL_TASK_QUEUE \
    simulation_runner \
    "Compose runner queue is not simulation_runner"
assert_compose_field_value \
    "$compose_runner_render" \
    TEMPORAL_ALL_QUEUES \
    false \
    "Compose runner does not set TEMPORAL_ALL_QUEUES=false"
assert_compose_field_value \
    "$compose_runner_render" \
    ALK_RUNNER_PYTHON \
    /opt/alk-venv/bin/python \
    "Compose runner does not use the SDK venv Python"
compose_child_concurrency=$(compose_field_value ALK_RUNNER_MAX_CONCURRENCY "$compose_runner_render")
compose_activity_concurrency=$(compose_field_value TEMPORAL_MAX_CONCURRENT_ACTIVITIES "$compose_runner_render")
compose_workflow_concurrency=$(compose_field_value TEMPORAL_MAX_CONCURRENT_WORKFLOW_TASKS "$compose_runner_render")
[[ "$compose_child_concurrency" == 4 ]] || die "Compose runner child concurrency is not 4"
[[ "$compose_activity_concurrency" == 4 ]] || die "Compose runner activity concurrency is not 4"
[[ "$compose_child_concurrency" == "$compose_activity_concurrency" ]] || \
    die "Compose runner concurrency caps differ"
[[ "$compose_workflow_concurrency" == 8 ]] || die "Compose runner workflow-task concurrency default is not 8"
# A stop that lands during a phone call must outlast the worker's drain, or
# Docker force-kills the child mid-call and the leased number stays taken.
compose_drain_seconds=$(compose_field_value TEMPORAL_GRACEFUL_SHUTDOWN_TIMEOUT "$compose_runner_render")
compose_stop_grace=$(compose_field_value stop_grace_period "$compose_runner_render")
[[ "$compose_drain_seconds" =~ ^[0-9]+$ ]] || \
    die "Compose runner does not set an integer TEMPORAL_GRACEFUL_SHUTDOWN_TIMEOUT"
[[ -n "$compose_stop_grace" ]] || die "Compose runner does not set stop_grace_period"
compose_stop_grace_seconds=$(compose_duration_seconds "$compose_stop_grace") || \
    die "Compose runner stop_grace_period is not a duration: $compose_stop_grace"
((compose_stop_grace_seconds > compose_drain_seconds)) || \
    die "Compose runner stop_grace_period must exceed TEMPORAL_GRACEFUL_SHUTDOWN_TIMEOUT"
if [[ "$explicit_compose_set" == true ]]; then
    echo "Compose configuration (caller-specified set): OK"
else
    echo "Compose configuration (root plus dev override when present): OK"
fi

helm_values=(
    --set temporal_worker_simulation_runner.enabled=true
    --set-string "temporal_worker_simulation_runner.image.tag=$runner_tag"
)

if [[ "$helm_release" == *application* ]]; then
    helm_fullname=$helm_release
else
    helm_fullname="${helm_release}-application"
fi
helm_fullname=${helm_fullname:0:63}
helm_fullname=${helm_fullname%-}
expected_backend_url="http://${helm_fullname}-backend-service"

for region in eu us; do
    chart_dir="$deployment_root/$region/gcp/deployment"
    [[ -f "$chart_dir/Chart.yaml" ]] || die "$region GCP chart does not exist: $chart_dir"

    default_enabled=$(awk '
        $0 == "temporal_worker_simulation_runner:" {
            in_runner=1
            next
        }
        in_runner && /^[^[:space:]]/ {
            exit
        }
        in_runner && $1 == "enabled:" {
            print $2
            exit
        }
    ' "$chart_dir/values.yaml")
    [[ "$default_enabled" == "false" ]] || die "$region runner is not disabled by default"
    echo "$region runner disabled by default: OK"

    run_quiet \
        "$region Helm lint" \
        "$temp_dir/$region-lint.log" \
        "${helm_command[@]}" \
        lint \
        "$chart_dir" \
        "${helm_values[@]}"
    echo "$region Helm lint: OK"

    rendered_worker="$temp_dir/$region-runner.yaml"
    run_to_file \
        "$region simulation-runner render" \
        "$rendered_worker" \
        "$temp_dir/$region-render.log" \
        "${helm_command[@]}" \
        template \
        "$helm_release" \
        "$chart_dir" \
        --namespace "$helm_namespace" \
        "${helm_values[@]}" \
        --show-only templates/temporal-worker-simulation-runner.yaml

    deployment_count=$(grep -Fc 'kind: Deployment' "$rendered_worker" || true)
    [[ "$deployment_count" -eq 1 ]] || die "$region render did not produce exactly one runner Deployment"
    assert_not_contains \
        "$rendered_worker" \
        'kind: Service' \
        "$region runner render unexpectedly contains a Service"
    assert_not_contains \
        "$rendered_worker" \
        'kind: Secret' \
        "$region runner render contains a Secret manifest"

    assert_contains \
        "$rendered_worker" \
        "image: \"futureagi/future-agi-simulation-runner:$runner_tag\"" \
        "$region runner does not use the expected dedicated SDK-bearing image/tag"
    assert_not_contains \
        "$rendered_worker" \
        'futureagi/future-agi-simulation-runner:latest' \
        "$region runner accepts a mutable latest image"
    assert_rendered_env_value \
        "$rendered_worker" \
        SERVICE_TYPE \
        temporal-worker \
        "$region runner does not use SERVICE_TYPE=temporal-worker"
    assert_rendered_env_value \
        "$rendered_worker" \
        TEMPORAL_TASK_QUEUE \
        simulation_runner \
        "$region runner does not poll simulation_runner"
    assert_rendered_env_value \
        "$rendered_worker" \
        TEMPORAL_RELOAD_DISPATCHER_ON_START \
        false \
        "$region runner enables dispatcher reload on startup"
    assert_rendered_env_value \
        "$rendered_worker" \
        TEMPORAL_ALL_QUEUES \
        false \
        "$region runner does not disable all-queues polling"
    assert_rendered_env_value \
        "$rendered_worker" \
        TEMPORAL_EXCLUDED_QUEUES \
        "" \
        "$region runner inherits a queue exclusion"
    assert_rendered_env_value \
        "$rendered_worker" \
        ALK_RUNNER_PYTHON \
        /opt/alk-venv/bin/python \
        "$region runner does not use the SDK venv Python"
    assert_contains \
        "$rendered_worker" \
        'envFrom:' \
        "$region runner omits envFrom"
    assert_contains \
        "$rendered_worker" \
        'secretRef:' \
        "$region runner does not inherit a Secret through secretRef"
    secret_ref_count=$(grep -Fc 'secretRef:' "$rendered_worker" || true)
    [[ "$secret_ref_count" -eq 1 ]] || die "$region runner has more than one Secret source"
    assert_contains \
        "$rendered_worker" \
        'name: core-backend-worker-l' \
        "$region runner does not reference the existing core-backend-worker-l Secret"
    assert_not_contains \
        "$rendered_worker" \
        'INTERNAL_API_SECRET:' \
        "$region runner renders INTERNAL_API_SECRET directly instead of using the existing secretRef"
    assert_not_contains \
        "$rendered_worker" \
        'FI_API_KEY' \
        "$region runner introduces an FI_API_KEY requirement"
    assert_not_contains \
        "$rendered_worker" \
        'FI_SECRET_KEY' \
        "$region runner introduces an FI_SECRET_KEY requirement"
    assert_rendered_env_value \
        "$rendered_worker" \
        ALK_RUNNER_API_URL \
        "$expected_backend_url" \
        "$region runner API URL is not the release-local backend Service"
    assert_not_contains \
        "$rendered_worker" \
        'futureagi.com' \
        "$region runner API URL contains a public host"

    child_concurrency=$(rendered_env_value ALK_RUNNER_MAX_CONCURRENCY "$rendered_worker")
    activity_concurrency=$(rendered_env_value TEMPORAL_MAX_CONCURRENT_ACTIVITIES "$rendered_worker")
    assert_rendered_env_value \
        "$rendered_worker" \
        ALK_RUNNER_MAX_CONCURRENCY \
        4 \
        "$region runner child concurrency default is not 4"
    assert_rendered_env_value \
        "$rendered_worker" \
        TEMPORAL_MAX_CONCURRENT_ACTIVITIES \
        4 \
        "$region runner activity concurrency default is not 4"
    [[ "$child_concurrency" == "$activity_concurrency" ]] || die "$region runner concurrency caps differ"

    assert_rendered_env_value "$rendered_worker" TEMPORAL_MAX_CONCURRENT_WORKFLOW_TASKS 4 \
        "$region runner workflow-task concurrency is not 4"
    assert_rendered_env_value "$rendered_worker" TEMPORAL_GRACEFUL_SHUTDOWN_TIMEOUT 4200 \
        "$region runner graceful shutdown timeout is not 4200"
    assert_rendered_env_value "$rendered_worker" HOSTED_RUNNER_VOICE_ENABLED false \
        "$region runner enables hosted voice by default"
    assert_rendered_env_value "$rendered_worker" REGISTER_TEMPORAL_SCHEDULES false \
        "$region runner enables schedule registration"
    assert_rendered_env_value "$rendered_worker" TEMPORAL_TARGET_MEMORY_USAGE "" \
        "$region runner enables dynamic memory tuning"
    assert_rendered_env_value "$rendered_worker" TEMPORAL_TARGET_CPU_USAGE "" \
        "$region runner enables dynamic CPU tuning"
    # The run budget now comes from each job's own deadline; a global ceiling
    # in the worker env is a leftover that no code reads.
    assert_rendered_env_value "$rendered_worker" HOSTED_RUNNER_MAX_DURATION_SECONDS "" \
        "$region runner still sets the retired HOSTED_RUNNER_MAX_DURATION_SECONDS ceiling"

    assert_contains \
        "$rendered_worker" \
        'maxUnavailable: 0' \
        "$region runner rollout does not set maxUnavailable=0"
    assert_contains \
        "$rendered_worker" \
        'maxSurge: 1' \
        "$region runner rollout does not set maxSurge=1"
    termination_grace=$(awk '/^[[:space:]]+terminationGracePeriodSeconds:/ {print $2; exit}' "$rendered_worker")
    temporal_shutdown=$(rendered_env_value TEMPORAL_GRACEFUL_SHUTDOWN_TIMEOUT "$rendered_worker")
    pre_stop_seconds=$(runner_value preStopSeconds "$chart_dir/values.yaml")
    shutdown_margin_seconds=$(runner_value shutdownMarginSeconds "$chart_dir/values.yaml")
    assert_integer_at_least "$termination_grace" 1 "$region terminationGracePeriodSeconds"
    assert_integer_at_least "$temporal_shutdown" 1 "$region Temporal graceful shutdown timeout"
    assert_integer_at_least "$pre_stop_seconds" 0 "$region preStopSeconds"
    assert_integer_at_least "$shutdown_margin_seconds" 0 "$region shutdownMarginSeconds"
    required_termination_grace=$((pre_stop_seconds + temporal_shutdown + shutdown_margin_seconds))
    ((termination_grace >= required_termination_grace)) || \
        die "$region termination grace does not cover preStop + Temporal shutdown + margin"
    kill_line=$(awk '/kill -TERM 1/ {print NR; exit}' "$rendered_worker")
    sleep_line=$(awk -v sleep_command="sleep $pre_stop_seconds" 'index($0, sleep_command) {print NR; exit}' "$rendered_worker")
    [[ "$kill_line" =~ ^[0-9]+$ && "$sleep_line" =~ ^[0-9]+$ && "$kill_line" -lt "$sleep_line" ]] || \
        die "$region preStop hook does not signal PID1 before sleeping"

    echo "$region runner render and invariants: OK"
done

echo "Simulation-runner deployment preflight: PASS"
