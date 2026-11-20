#!/usr/bin/env bash
# Runs only on the disposable Debian 12 worker. The controller writes the source
# identity into instance metadata. Its credentials and Git config stay off-VM.
set -Eeu -o pipefail
op=bootstrap
trap 'rc=$?; printf "failed %s %d %d\n" "$op" "$rc" "$LINENO" >> "$HOME/worker.log"; exit "$rc"' ERR
stage() { op=$1; printf "stage %s\n" "$op" >> "$HOME/worker.log"; echo "$1" > "$HOME/stage"; printf '%s %s\n' "$(date -u +%FT%TZ)" "$1" >> "$HOME/stages.log"; }
meta() { curl -fsS --max-time 5 --noproxy '*' -H 'Metadata-Flavor: Google' "http://metadata.google.internal/computeMetadata/v1/$1"; }
project=$(meta project/project-id)
[[ $project == fagi-omega-staging && $(uname -sm) == 'Linux x86_64' ]]
SKILLS_GCP_CASE=$(meta instance/attributes/skills-case)
SKILLS_GCP_HEAD=$(git rev-parse HEAD)
export SKILLS_GCP_CASE SKILLS_GCP_HEAD
[[ $SKILLS_GCP_HEAD == "$(meta instance/attributes/skills-head)" && $(git rev-parse 'HEAD^{tree}') == "$(meta instance/attributes/skills-tree)" ]]

stage bootstrap
op=apt
sudo apt-get install -y --no-install-recommends acl ca-certificates curl jq python3-venv
sudo install -d -m 0755 /etc/apt/keyrings
op=docker-repository
sudo curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
echo 'deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian bookworm stable' | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt-get update -qq
op=docker-install
sudo apt-get install -y --no-install-recommends docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
op=docker-start
sudo systemctl start docker
# OS Login principals come from NSS rather than /etc/passwd. Grant access on
# this throwaway VM without rewriting a local account or running tests as root.
sudo setfacl -m "u:$(id -u):rw" /var/run/docker.sock
stage packages
private=$HOME/private
install -d -m 0700 "$private" "$private/bin"
receipt=$HOME/receipt.json
sdk=$(jq -er '.sdk' "$receipt")
pair_digest=$(jq -er '.pair_sha256' "$receipt")
op=verify-pair
wheels=$(python3 -I e2e/stack/verify-skill-packages.py "$HOME/pair" "$pair_digest" "$SKILLS_GCP_HEAD" "$sdk" "$(jq -er '.traceai' "$receipt")" --require-sums)
sdk_wheel="$HOME/pair/${wheels%%$'\n'*}"
# Match the ordinary E2E job's Node and lockfile rather than Debian's Node 18.
op=node-install
node_container=$(docker create node:22.18.0-bookworm-slim)
docker cp "$node_container:/usr/local/bin/node" "$private/bin/node"
docker cp "$node_container:/usr/local/lib/node_modules/npm" "$private/npm"
docker rm "$node_container" >/dev/null
ln -s "$private/npm/bin/npm-cli.js" "$private/bin/npm"
export PATH="$private/bin:$PATH" CI=1
npm install --prefix "$private/tools" --ignore-scripts yarn@1.22.22
ln -s "$private/tools/node_modules/yarn/bin/yarn.js" "$private/bin/yarn"
op=harness-install
yarn --cwd e2e install --frozen-lockfile
op=browser-install
(cd e2e && yarn playwright install --with-deps chromium)

op=client-install
case "$SKILLS_GCP_CASE" in
  056|057|058|059)
    export E2E_STACK=standalone E2E_SKILLS_CAMPAIGNS_ENABLED=true
    spec=flows/skills/j5-registered-measurements.spec.ts
    grep="SKILLS-E2E-$SKILLS_GCP_CASE:"
    python3 -m venv "$private/sdk"
    export CTXHUB_J5_PYTHON="$private/sdk/bin/python"
    "$CTXHUB_J5_PYTHON" -m pip install "$sdk_wheel" httpx
    "$CTXHUB_J5_PYTHON" -I -m fi.context --help >/dev/null
    npm install --prefix "$private/codex" --ignore-scripts --include=optional @openai/codex@0.167.1
    export J5_CODEX_BINARY="$private/codex/node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl/bin/codex"
    J5_CODEX_SHA256=$(node --input-type=module -e "import { CODEX_BINARY_SHA256 } from './e2e/lib/skills-r3/j5-native-fixture.mjs'; console.log(CODEX_BINARY_SHA256)")
    export J5_CODEX_SHA256 J5_EVIDENCE_DIR="$private/j5" J5_PROVENANCE_JSON="$private/provenance.json"
    echo "$J5_CODEX_SHA256  $J5_CODEX_BINARY" | sha256sum -c -
    [[ $("$J5_CODEX_BINARY" --version) == 'codex-cli 0.167.1' ]]
    ;;
  063)
    export E2E_STACK=standalone J6_WHEELS="$private/wheels" J6_APP_PYTHON="$private/sdk/bin/python" J6_PROVENANCE="$private/provenance.json"
    spec=flows/skills/j6-incident-recovery.spec.ts; grep='SKILLS-E2E-063:'
    python3 -m venv "$private/sdk"
    mv "$HOME/application" "$J6_WHEELS"
    files=("$J6_WHEELS"/*); app=("$J6_WHEELS"/ctxhub_support_application-*.whl)
    [[ ${#files[@]} == 2 && ${#app[@]} == 1 && -f ${app[0]} ]]
    cmp "$sdk_wheel" "$J6_WHEELS/${sdk_wheel##*/}"
    printf '%s  %s\n' "$(jq -er '.application_sha256' "$receipt")" "${app[0]}" | sha256sum -c -
    "$J6_APP_PYTHON" -m pip install "$J6_WHEELS"/*.whl
    "$J6_APP_PYTHON" -m pip check
    ;;
  j3|j4)
    export E2E_STACK=distributed
    if [[ $SKILLS_GCP_CASE == j3 ]]; then
      python3 -m venv "$private/sdk"
      export J3_PYTHON="$private/sdk/bin/python"
      "$J3_PYTHON" -m pip install "${sdk_wheel}[context-deepagents]" deepagents==0.4.11 langgraph-checkpoint-sqlite
      "$J3_PYTHON" -m pip check
      export J3_DRIVER_FIXTURE_DIR="$private/j3" J3_PROVENANCE_PATH="$private/provenance.json" J3_COHORT=configured
      spec=harness/skills-r3/j3-driver-fixture.spec.ts; grep='J3 driver initial condition: three v1 parents over a v7 shared policy'
    else
      export J4_MEASUREMENT_DIR="$private/j4" J4_PROVENANCE="$private/provenance.json" J4_COHORTS=cold,configured
      spec=harness/skills-r3/j4-driver.spec.ts; grep='J4 registered driver retains honest cold and configured reports'
    fi
    ;;
  *) exit 2 ;;
esac
stage build
if [[ $E2E_STACK == standalone ]]; then
  op=build-standalone
  bin/e2e build standalone
  E2E_GOOGLE_IMAGE_TAG=$(sed -n 's/^FUTURE_AGI_VERSION=//p' e2e/stack/standalone-e2e.env)
  export E2E_GOOGLE_IMAGE_TAG
  service=app
else
  op=build-distributed
  bin/e2e build all
  op=build-gateway
  docker build -t futureagi/agentcc-gateway:e2e-local agentcc-gateway
  export FUTURE_AGI_VERSION=e2e-local FRONTEND_VERSION=e2e-local AGENTCC_GATEWAY_VERSION=e2e-local
  export CODE_EXECUTOR_VERSION=e2e-local E2E_FI_COLLECTOR_VERSION=e2e-local E2E_GOOGLE_IMAGE_TAG=e2e-local
  service=backend
fi
stage boot
bin/e2e up
if [[ ${E2E_SKILLS_CAMPAIGNS_ENABLED:-false} == true ]]; then
  [[ $(bin/e2e compose exec -T app printenv SKILLS_CONTEXT_CAMPAIGNS_ENABLED) == true ]]
fi
stage provenance
umask 077
base=$(meta instance/attributes/skills-base)
git cat-file -e "${base}^{commit}"
image=$(docker inspect --type container --format '{{.Image}}' "$(bin/e2e compose ps -q "$service")")
[[ $image =~ ^sha256:[0-9a-f]{64}$ ]]
dirty=false
if [[ -n $(git status --porcelain --untracked-files=normal) ]]; then dirty=true; fi
# J3/J4/J6 complete their own fixture hashes after provisioning; J5's
# synthetic 40-repository input is deterministic and fully known before boot.
jq -n --arg project "$project" --arg base "$base" --arg head "$SKILLS_GCP_HEAD" --arg tree "$(git rev-parse 'HEAD^{tree}')" \
  --arg sdk "$sdk" --argjson dirty "$dirty" --arg image "${image#sha256:}" --arg wheel "$(sha256sum "$sdk_wheel" | cut -d' ' -f1)" \
  --arg lock "$(sha256sum e2e/yarn.lock | cut -d' ' -f1)" \
  '{environment:"gcp",project:$project,product_base:$base,product_head:$head,product_tree:$tree,sdk_commit:$sdk,dirty:$dirty,runtime_digest:$image,package_hashes:[$wheel],lockfile_hashes:[$lock]}' > "$private/provenance.json"
if [[ -n ${J5_PROVENANCE_JSON:-} ]]; then
  fixture=$(node --input-type=module -e "import {fixtureRepositories} from './e2e/benchmarks/context-hub/drivers/j5.mjs'; import {digest} from './e2e/lib/skills-r3/canonical.mjs'; console.log(digest(fixtureRepositories()))")
  jq --arg hash "$fixture" '. + {fixture_hash:$hash}' "$private/provenance.json" > "$private/j5-provenance.json"
  export J5_PROVENANCE_JSON="$private/j5-provenance.json"
fi
stage playwright
export SKILLS_GCP_PAIR_SHA256="$pair_digest"
export SKILLS_GCP_SPEC="$spec" SKILLS_GCP_TITLE="$grep"
bin/e2e test "$spec" -g "$grep" --workers=1 --retries=0 --output="$private/pw" --reporter=./scripts/skills-gcp-reporter.mjs
jq -e '.passed == true and .count == 1' "$HOME/result.json" >/dev/null
stage completed
