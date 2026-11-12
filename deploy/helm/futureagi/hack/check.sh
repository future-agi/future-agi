#!/usr/bin/env bash
# Static checks of the chart, the same ones .github/workflows/helm-ci.yml runs:
#   * the chart's defaults, and inconsistent values, refuse to render and say
#     what to set
#   * helm lint --strict and helm template for every value set
#   * upgrades that would change a bundled datastore's volume are refused
#     (against a stand-in for the live StatefulSet)
#   * kubeconform -strict on every rendered manifest, per Kubernetes version
#   * invariants of the rendered manifests (hack/rendered_checks.py), and
#     the backend's behaviour settings against docker-compose.distributed.yml
#   * examples/airgap.yaml's mirror commands copy the images the chart pulls
#   * the install notes and hack/support-bundle.sh keep credentials out
#   * hack/support-bundle.sh stops its port-forward, finished or killed
#   * values.yaml, values.schema.json and the README values table agree
#   * the pre-commit Prettier run skips the templates and those generated files
#   * the ClickHouse config files match the ones the Standalone install uses
#   * the UI's security headers match the frontend image's
#   * the UI image's entrypoint writes the Omega flag the chart sets
#
#   deploy/helm/futureagi/hack/check.sh
#
# HELM, KUBECONFORM and PYTHON (with PyYAML) name the binaries (default: from
# PATH), KUBE_VERSIONS
# the Kubernetes versions to validate against, OUT_DIR where the rendered
# manifests go (default: a temporary directory).
set -euo pipefail

chart=$(cd "$(dirname "$0")/.." && pwd)
repo=$(cd "$chart/../../.." && pwd)
helm=${HELM:-helm}
kubeconform=${KUBECONFORM:-kubeconform}
# Python 3 with PyYAML.
python=${PYTHON:-python3}
# The oldest Kubernetes the chart supports (Chart.yaml kubeVersion) and the
# newest GA minor kubeconform has schemas for; the workflows use these too.
kube_versions=${KUBE_VERSIONS:-"1.27.0 1.37.0"}
# JSON schemas of CRD kinds (Gateway API routes, ...), by group and version.
crd_schemas=${CRD_SCHEMAS:-"https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json"}
out=${OUT_DIR:-$(mktemp -d)}
mkdir -p "$out"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

echo "== offline application key generation is refused"
HELM="$helm" "$python" -I "$chart/hack/test_generated_keys.py"

echo "== webhook keys come only from an optional existing Secret reference"
HELM="$helm" "$python" "$chart/hack/test_webhooks_keys.py"

# All other offline renders explicitly opt into disposable CI-generated keys.
# Never copy this opt-out to deployment values or real install commands.
echo "== CAT-2 operator activation is off by default and restart-bound"
HELM="$helm" "$python" "$chart/hack/check_cat2.py"

echo "== collector billing admission and Redis TLS rendering"
HELM="$helm" "$python" -I "$chart/hack/test_collector_billing.py"

echo "== c2.6 chart union: collector billing pin, hosted runtime pins and the Omega validator"
HELM="$helm" "$python" -I "$chart/hack/test_chart_union.py"

echo "== c2.6 chart union: custody, webhook and readiness seams stay intact"
HELM="$helm" "$python" -I "$chart/hack/test_chart_union_preservation.py"

# name|values files (space-separated, relative to the chart)
value_sets=(
  "bundled|examples/bundled.yaml"
  "external|examples/external.yaml"
  "ingress|examples/external.yaml examples/ingress.yaml"
  "bundled-ingress|examples/bundled.yaml examples/ingress.yaml ci/bundled-ingress.yaml"
  "all-components|examples/external.yaml examples/ingress.yaml ci/all-components.yaml"
  "overrides|examples/bundled.yaml ci/overrides.yaml"
  "gitops|examples/external.yaml ci/gitops.yaml"
  "digests|examples/bundled.yaml ci/digests.yaml"
  "digests-unpinned|examples/bundled.yaml ci/digests.yaml ci/digests-unpinned.yaml"
  "worker-queues|examples/bundled.yaml ci/worker-queues.yaml"
  "pooler|examples/external.yaml ci/pooler.yaml"
  "gateway-api|examples/bundled.yaml examples/gateway-api.yaml ci/gateway-api.yaml"
  "ingress-traefik|examples/external.yaml examples/ingress-traefik.yaml"
  "local|examples/local.yaml"
  "size-small|examples/external.yaml examples/sizes/small.yaml"
  "size-medium|examples/external.yaml examples/sizes/medium.yaml"
  "size-large|examples/external.yaml examples/sizes/large.yaml"
  "cloud-gke|examples/cloud/gke.yaml"
  "cloud-eks|examples/cloud/eks.yaml"
  "cloud-aks|examples/cloud/aks.yaml"
  # README "Production", step 2, with a cloud file as my-values.yaml.
  "cloud-aks-medium|examples/cloud/aks.yaml examples/sizes/medium.yaml examples/gateway-api.yaml"
  "cloud-aks-large|examples/cloud/aks.yaml examples/sizes/large.yaml examples/gateway-api.yaml"
  "enterprise|examples/external.yaml examples/enterprise.yaml ci/enterprise.yaml"
  "license-legacy|examples/bundled.yaml ci/license-legacy.yaml"
  "proxy-ca|examples/external.yaml ci/proxy-ca.yaml"
  "airgap|examples/external.yaml examples/airgap.yaml"
  "dockerhub-mirror|examples/bundled.yaml ci/dockerhub-mirror.yaml"
  "external-secrets|examples/external.yaml examples/external-secrets.yaml ci/external-secrets.yaml"
  "openshift|examples/bundled.yaml ci/openshift.yaml"
  "omega|examples/external.yaml examples/ingress.yaml ci/omega.yaml"
  # The Omega Hub (hack/rendered_hub_checks.py): its own Secrets and policies,
  # then the token generated into the chart's Secret.
  "hub|examples/external.yaml examples/hub.yaml ci/hub.yaml"
  "hub-generated|examples/bundled.yaml examples/hub.yaml"
)

echo "== chart defaults: refuse to render, with guidance"
if "$helm" template futureagi "$chart" --set secrets.allowGeneratedKeysWithoutCluster=true >"$out/default.txt" 2>&1; then
  fail "the chart rendered without any datastore configured"
fi
# The hint is the first install command most people copy: it must wait long
# enough for the first bootstrap.
for expected in "postgres.external.host is required" "examples/bundled.yaml" "--timeout 20m"; do
  grep -qF -- "$expected" "$out/default.txt" || {
    cat "$out/default.txt" >&2
    fail "the default render error does not mention: $expected"
  }
done
echo "ok   defaults fail with guidance"

echo "== inconsistent values: refuse to render"
# name|expected message|helm arguments (over examples/bundled.yaml)
# An expected message starting with re: is an extended regular expression:
# values.schema.json rejects some values before validate.yaml runs, and Helm
# 3.18+ and Helm 4 word schema errors differently from older Helm 3.
refused=(
  "recaptcha without its key|config.recaptcha needs secrets.extra.RECAPTCHA_SECRET_KEY|--set config.recaptcha=true"
  "gateway replicas without Redis|agentccGateway runs more than one replica without Redis|--set agentccGateway.replicas=2 --set agentccGateway.redis.enabled=false"
  "gateway Redis over TLS|the gateway has no Redis TLS|--set redis.mode=external --set redis.external.host=r --set redis.external.tls=true --set agentccGateway.redis.enabled=true"
  "a ClickHouse user the bundled server lacks|clickhouse.user must be default with clickhouse.mode=bundled|--set clickhouse.user=fa"
  "an autoscaled gateway with Redis over TLS|to a Redis without TLS, or run one replica with agentccGateway.redis.enabled=false|--set redis.mode=external --set redis.external.host=r --set redis.external.tls=true --set agentccGateway.autoscaling.enabled=true"
  "Enterprise without a license|edition=ee needs a license|--set edition=ee"
  "an unknown edition|re:edition.*must be one of|--set edition=enterprise"
  "two license sources|set license.existingSecret or license.key, not both|--set license.key=k --set license.existingSecret=s"
  "noProxy without a proxy|global.proxy.noProxy is set without|--set global.proxy.noProxy=.corp"
  "a proxy that is not a URL|re:httpsProxy.*oes not match pattern|--set global.proxy.httpsProxy=proxy:3128"
  "two CA bundle sources|global.caBundle.configMap or global.caBundle.secret, not both|--set global.caBundle.configMap=a --set global.caBundle.secret=b"
  "air-gapped serving without its volume|global.airgap with serving.enabled needs serving.persistence.enabled|--set global.airgap=true --set serving.enabled=true"
  "half an OAuth client|auth.google needs both clientId and clientSecret|--set auth.google.clientId=x"
  "an ExternalSecret the chart would not read|externalSecrets.secrets.app needs secrets.existingSecret|--set externalSecrets.enabled=true --set externalSecrets.secretStoreRef.name=vault --set externalSecrets.secrets.app.dataFrom[0].extract.key=a"
  "an unknown OpenShift mode|re:adaptSecurityContext.*must be one of|--set global.compatibility.openshift.adaptSecurityContext=on"
  "an unknown ExternalSecret group|re:[Pp]roperty ?[Nn]ame.*vault|--set externalSecrets.secrets.vault.dataFrom[0].extract.key=a"
  "an LLM gateway host shared with the API|gatewayApi.llmGateway.host must differ from the app and API hosts|--set gatewayApi.enabled=true --set gatewayApi.parentRefs[0].name=gw --set gatewayApi.app.host=app.example.com --set gatewayApi.api.host=api.example.com --set gatewayApi.llmGateway.enabled=true --set gatewayApi.llmGateway.host=api.example.com"
  "an LLM gateway host shared with the app|gatewayApi.llmGateway.host must differ from the app and API hosts|--set gatewayApi.enabled=true --set gatewayApi.parentRefs[0].name=gw --set gatewayApi.app.host=app.example.com --set gatewayApi.api.host=api.example.com --set gatewayApi.llmGateway.enabled=true --set gatewayApi.llmGateway.host=app.example.com"
  "Omega without its settings|omega.issuer.url is required with omega.enabled|--set omega.enabled=true"
  "Omega without an ingress|omega.enabled needs ingress.enabled or gatewayApi.enabled|--set omega.enabled=true"
  "Omega EE over plain HTTP|re:omega[./]gateway[./]ee[./]url|--set omega.enabled=true --set ingress.enabled=true --set ingress.app.host=app.example.com --set ingress.api.host=api.example.com --set ingress.tls[0].secretName=tls --set ingress.tls[0].hosts[0]=app.example.com --set ingress.tls[0].hosts[1]=api.example.com --set omega.issuer.url=https://app.example.com/omega --set omega.issuer.keyId=k1 --set omega.issuer.privateKey.existingSecret=key --set omega.gateway.image.tag=t --set omega.gateway.ee.url=http://omega-os.omega.svc:8787 --set omega.gateway.jwks.existingConfigMap=jwks --set omega.gateway.rateLimitRedis.existingSecret=redis --set omega.internalTls.existingSecret=tls"
  "Omega EE on loopback|omega.gateway.ee.url must name a cluster address|--set omega.enabled=true --set ingress.enabled=true --set ingress.app.host=app.example.com --set ingress.api.host=api.example.com --set ingress.tls[0].secretName=tls --set ingress.tls[0].hosts[0]=app.example.com --set ingress.tls[0].hosts[1]=api.example.com --set omega.issuer.url=https://app.example.com/omega --set omega.issuer.keyId=k1 --set omega.issuer.privateKey.existingSecret=key --set omega.gateway.image.tag=t --set omega.gateway.ee.url=https://127.0.0.1:8443 --set omega.gateway.jwks.existingConfigMap=jwks --set omega.gateway.rateLimitRedis.existingSecret=redis --set omega.internalTls.existingSecret=tls"
  "Omega JWKS from two sources|omega.gateway.jwks needs existingConfigMap or existingSecret (one of them)|--set omega.enabled=true --set ingress.enabled=true --set ingress.app.host=app.example.com --set ingress.api.host=api.example.com --set ingress.tls[0].secretName=tls --set ingress.tls[0].hosts[0]=app.example.com --set ingress.tls[0].hosts[1]=api.example.com --set omega.issuer.url=https://app.example.com/omega --set omega.issuer.keyId=k1 --set omega.issuer.privateKey.existingSecret=key --set omega.gateway.image.tag=t --set omega.gateway.ee.url=https://omega-os.omega.svc:8443 --set omega.gateway.jwks.existingConfigMap=jwks --set omega.gateway.rateLimitRedis.existingSecret=redis --set omega.internalTls.existingSecret=tls --set omega.gateway.jwks.existingSecret=jwks"
  "an Omega grace period shorter than the drain|omega.gateway.terminationGracePeriodSeconds must be at least 20|--set omega.enabled=true --set ingress.enabled=true --set ingress.app.host=app.example.com --set ingress.api.host=api.example.com --set ingress.tls[0].secretName=tls --set ingress.tls[0].hosts[0]=app.example.com --set ingress.tls[0].hosts[1]=api.example.com --set omega.issuer.url=https://app.example.com/omega --set omega.issuer.keyId=k1 --set omega.issuer.privateKey.existingSecret=key --set omega.gateway.image.tag=t --set omega.gateway.ee.url=https://omega-os.omega.svc:8443 --set omega.gateway.jwks.existingConfigMap=jwks --set omega.gateway.rateLimitRedis.existingSecret=redis --set omega.internalTls.existingSecret=tls --set omega.gateway.terminationGracePeriodSeconds=10"
  "an Omega browser origin over plain HTTP|re:omega[./]gateway[./]allowedOrigins|--set omega.enabled=true --set ingress.enabled=true --set ingress.app.host=app.example.com --set ingress.api.host=api.example.com --set ingress.tls[0].secretName=tls --set ingress.tls[0].hosts[0]=app.example.com --set ingress.tls[0].hosts[1]=api.example.com --set omega.issuer.url=https://app.example.com/omega --set omega.issuer.keyId=k1 --set omega.issuer.privateKey.existingSecret=key --set omega.gateway.image.tag=t --set omega.gateway.ee.url=https://omega-os.omega.svc:8443 --set omega.gateway.jwks.existingConfigMap=jwks --set omega.gateway.rateLimitRedis.existingSecret=redis --set omega.internalTls.existingSecret=tls --set omega.gateway.allowedOrigins[0]=http://app.example.com"
  "an Omega Console on the UI host|omega.gateway.console.host must differ from the UI and API hosts|--set omega.enabled=true --set ingress.enabled=true --set ingress.app.host=app.example.com --set ingress.api.host=api.example.com --set ingress.tls[0].secretName=tls --set ingress.tls[0].hosts[0]=app.example.com --set ingress.tls[0].hosts[1]=api.example.com --set omega.issuer.url=https://app.example.com/omega --set omega.issuer.keyId=k1 --set omega.issuer.privateKey.existingSecret=key --set omega.gateway.image.tag=t --set omega.gateway.ee.url=https://omega-os.omega.svc:8443 --set omega.gateway.jwks.existingConfigMap=jwks --set omega.gateway.rateLimitRedis.existingSecret=redis --set omega.internalTls.existingSecret=tls --set omega.gateway.console.enabled=true --set omega.gateway.console.host=app.example.com"
  "an Omega exchange with no URL|omega.gateway.sessionExchangeUrl is required when omega.internalTls.enabled is false|--set omega.enabled=true --set ingress.enabled=true --set ingress.app.host=app.example.com --set ingress.api.host=api.example.com --set ingress.tls[0].secretName=tls --set ingress.tls[0].hosts[0]=app.example.com --set ingress.tls[0].hosts[1]=api.example.com --set omega.issuer.url=https://app.example.com/omega --set omega.issuer.keyId=k1 --set omega.issuer.privateKey.existingSecret=key --set omega.gateway.image.tag=t --set omega.gateway.ee.url=https://omega-os.omega.svc:8443 --set omega.gateway.jwks.existingConfigMap=jwks --set omega.gateway.rateLimitRedis.existingSecret=redis --set omega.internalTls.existingSecret=tls --set omega.internalTls.enabled=false"
  "Omega without its revocation channel|omega.hostEvents.existingSecret is required with omega.enabled|--set omega.enabled=true --set ingress.enabled=true --set ingress.app.host=app.example.com --set ingress.api.host=api.example.com --set ingress.tls[0].secretName=tls --set ingress.tls[0].hosts[0]=app.example.com --set ingress.tls[0].hosts[1]=api.example.com --set omega.issuer.url=https://app.example.com/omega --set omega.issuer.keyId=k1 --set omega.issuer.privateKey.existingSecret=key --set omega.gateway.image.tag=t --set omega.gateway.ee.url=https://omega-os.omega.svc:8443 --set omega.gateway.jwks.existingConfigMap=jwks --set omega.gateway.rateLimitRedis.existingSecret=redis --set omega.internalTls.existingSecret=tls"
  "an Omega revocation channel over plain HTTP|omega.hostEvents.url must be https|--set omega.enabled=true --set ingress.enabled=true --set ingress.app.host=app.example.com --set ingress.api.host=api.example.com --set ingress.tls[0].secretName=tls --set ingress.tls[0].hosts[0]=app.example.com --set ingress.tls[0].hosts[1]=api.example.com --set omega.issuer.url=https://app.example.com/omega --set omega.issuer.keyId=k1 --set omega.issuer.privateKey.existingSecret=key --set omega.gateway.image.tag=t --set omega.gateway.ee.url=https://omega-os.omega.svc:8443 --set omega.gateway.jwks.existingConfigMap=jwks --set omega.gateway.rateLimitRedis.existingSecret=redis --set omega.internalTls.existingSecret=tls --set omega.hostEvents.url=http://omega-os.omega.svc:8787/host/events --set omega.hostEvents.keyId=fa-1 --set omega.hostEvents.existingSecret=omega-host-events"
  "an Omega revocation key id the sender refuses|omega.hostEvents.keyId must be|--set omega.enabled=true --set ingress.enabled=true --set ingress.app.host=app.example.com --set ingress.api.host=api.example.com --set ingress.tls[0].secretName=tls --set ingress.tls[0].hosts[0]=app.example.com --set ingress.tls[0].hosts[1]=api.example.com --set omega.issuer.url=https://app.example.com/omega --set omega.issuer.keyId=k1 --set omega.issuer.privateKey.existingSecret=key --set omega.gateway.image.tag=t --set omega.gateway.ee.url=https://omega-os.omega.svc:8443 --set omega.gateway.jwks.existingConfigMap=jwks --set omega.gateway.rateLimitRedis.existingSecret=redis --set omega.internalTls.existingSecret=tls --set omega.hostEvents.url=https://omega-os.omega.svc:8443/host/events --set omega.hostEvents.keyId=-k1 --set omega.hostEvents.existingSecret=omega-host-events"
  "an Omega revocation URL on a port the sender refuses|omega.hostEvents.url must be on a port from 1 to 65535|--set omega.enabled=true --set ingress.enabled=true --set ingress.app.host=app.example.com --set ingress.api.host=api.example.com --set ingress.tls[0].secretName=tls --set ingress.tls[0].hosts[0]=app.example.com --set ingress.tls[0].hosts[1]=api.example.com --set omega.issuer.url=https://app.example.com/omega --set omega.issuer.keyId=k1 --set omega.issuer.privateKey.existingSecret=key --set omega.gateway.image.tag=t --set omega.gateway.ee.url=https://omega-os.omega.svc:8443 --set omega.gateway.jwks.existingConfigMap=jwks --set omega.gateway.rateLimitRedis.existingSecret=redis --set omega.internalTls.existingSecret=tls --set omega.hostEvents.url=https://omega-os.omega.svc:65536/host/events --set omega.hostEvents.keyId=fa-1 --set omega.hostEvents.existingSecret=omega-host-events"
  "a worker override of the Omega revocation channel|worker.extraEnv.OMEGA_HOST_EVENTS_URL: the chart sets it from omega.hostEvents.url|--set omega.enabled=true --set ingress.enabled=true --set ingress.app.host=app.example.com --set ingress.api.host=api.example.com --set ingress.tls[0].secretName=tls --set ingress.tls[0].hosts[0]=app.example.com --set ingress.tls[0].hosts[1]=api.example.com --set omega.issuer.url=https://app.example.com/omega --set omega.issuer.keyId=k1 --set omega.issuer.privateKey.existingSecret=key --set omega.gateway.image.tag=t --set omega.gateway.ee.url=https://omega-os.omega.svc:8443 --set omega.gateway.jwks.existingConfigMap=jwks --set omega.gateway.rateLimitRedis.existingSecret=redis --set omega.internalTls.existingSecret=tls --set omega.hostEvents.url=https://omega-os.omega.svc:8443/host/events --set omega.hostEvents.keyId=fa-1 --set omega.hostEvents.existingSecret=omega-host-events --set worker.extraEnv.OMEGA_HOST_EVENTS_URL=x"
  "Omega delegation through extraEnv without omega.enabled|config.extraEnv.OMEGA_SQUAD_DELEGATION_ISSUER configures Omega delegation without omega.enabled|--set config.extraEnv.OMEGA_SQUAD_DELEGATION_ISSUER=https://app.example.com/omega"
  "Omega delegation through a disguised extraEnv name|config.extraEnv[\"OMEGA_SQUAD_DELEGATION_ISSUER\t\"] configures Omega delegation without omega.enabled|--set-json config.extraEnv={\"OMEGA_SQUAD_DELEGATION_ISSUER\t\":\"https://app.example.com/omega\"}"
  "an env name with whitespace around it|config.extraEnv[\"LOG_FORMAT\t\"]: a variable name with whitespace around it|--set-json config.extraEnv={\"LOG_FORMAT\t\":\"json\"}"
  "Omega EE's host-events sender label on every resource|omega: commonLabels holds omega.futureagi.ai/host-events-sender|--set-string commonLabels.omega\.futureagi\.ai/host-events-sender=true"
  "Omega EE's host-events sender label on the code executor|omega: codeExecutor.podLabels holds omega.futureagi.ai/host-events-sender|--set-string codeExecutor.podLabels.omega\.futureagi\.ai/host-events-sender=true"
  "Omega EE's host-events sender label set by hand on a worker|omega: worker.podLabels holds omega.futureagi.ai/host-events-sender|--set-string worker.podLabels.omega\.futureagi\.ai/host-events-sender=true"
  "the Hub without a checker image tag|hub.checker.image.tag (or .digest) is required|--set hub.enabled=true --set hub.checker.runner.image=r@sha256:0000000000000000000000000000000000000000000000000000000000000000 --set hub.checker.runner.dockerHost=tcp://runner:2376 --set hub.checker.runner.tlsSecret=t"
  "the Hub without its runner image|hub.checker.runner.image is required|--set hub.enabled=true --set hub.checker.image.tag=1 --set hub.checker.runner.dockerHost=tcp://runner:2376 --set hub.checker.runner.tlsSecret=t"
  "the Hub without a runner host|hub.checker.runner.dockerHost is required, as tcp://<runner host>:2376|--set hub.enabled=true --set hub.checker.image.tag=1 --set hub.checker.runner.image=r --set hub.checker.runner.tlsSecret=t"
  "the Hub on a node's Docker socket|re:dockerHost.*oes not match pattern|--set hub.enabled=true --set hub.checker.image.tag=1 --set hub.checker.runner.image=r --set hub.checker.runner.tlsSecret=t --set hub.checker.runner.dockerHost=unix:///var/run/docker.sock"
  "the Hub without the runner's client certificate|hub.checker.runner.tlsSecret is required|--set hub.enabled=true --set hub.checker.image.tag=1 --set hub.checker.runner.image=r --set hub.checker.runner.dockerHost=tcp://runner:2376"
  "a Hub maintainer organisation that is not an id|re:maintainerOrgId.*oes not match pattern|--set hub.maintainerOrgId=acme"
  "an OTLP/gRPC host shared with the LLM gateway|gatewayApi.otlpGrpc.host is required and must differ from the HTTP route hosts|--set gatewayApi.enabled=true --set gatewayApi.parentRefs[0].name=gw --set gatewayApi.app.host=app.example.com --set gatewayApi.api.host=api.example.com --set gatewayApi.llmGateway.enabled=true --set gatewayApi.llmGateway.host=llm.example.com --set gatewayApi.otlpGrpc.enabled=true --set gatewayApi.otlpGrpc.host=llm.example.com"
)
for case in "${refused[@]}"; do
  IFS='|' read -r name expected args <<<"$case"
  # shellcheck disable=SC2086 # args is a word list
  if "$helm" template futureagi "$chart" --set secrets.allowGeneratedKeysWithoutCluster=true -f "$chart/examples/bundled.yaml" $args >"$out/refused.txt" 2>&1; then
    fail "rendered despite: $name"
  fi
  if [[ $expected == re:* ]]; then
    expected=${expected#re:}
    grep_mode=-qE
  else
    grep_mode=-qF
  fi
  grep "$grep_mode" -- "$expected" "$out/refused.txt" || {
    cat "$out/refused.txt" >&2
    fail "the error for \"$name\" does not mention: $expected"
  }
  echo "ok   $name"
done
rm -f "$out/refused.txt"

echo "== platform provider credentials reach only the gateway (agentccGateway.providerCredentials)"
HELM="$helm" "$python" "$chart/hack/test_provider_credentials.py" || fail "provider credentials reach a workload other than the gateway, or a refusal is missing"

echo "== install-time settings: a change to a live StatefulSet's volume is refused"
# `lookup` finds nothing under helm template: a copy of the chart reads the
# live StatefulSet from live.yaml instead.
live_chart="$out/live-chart"
rm -rf "$live_chart"
cp -R "$chart" "$live_chart"
"$python" - "$live_chart/templates/validate.yaml" <<'PY' || fail "validate.yaml no longer looks up the StatefulSet: update this check"
import sys
path = sys.argv[1]
lookup = 'lookup "apps/v1" "StatefulSet" $.Release.Namespace $name'
text = open(path).read()
assert text.count(lookup) == 1
open(path, "w").write(text.replace(lookup, '(get ($.Files.Get "live.yaml" | fromYaml) $name | default dict)'))
PY
# name|storageClassName of the live claim (none: absent)|its size|helm
# arguments (over examples/bundled.yaml)|expected message (empty: renders)
live_cases=(
  "the same volume|none|20Gi||"
  "the same size in other units|none|20Gi|--set postgres.bundled.persistence.size=20480Mi|"
  "a larger volume|none|20Gi|--set postgres.bundled.persistence.size=30Gi|postgres.bundled.persistence.size is 30Gi, but StatefulSet futureagi-postgres was created with 20Gi"
  "the same StorageClass|fast|20Gi|--set global.storageClass=fast|"
  "a StorageClass after the cluster default|none|20Gi|--set global.storageClass=fast|gives storageClassName \"fast\", but StatefulSet futureagi-postgres was created with no storageClassName"
  "no StorageClass (-) after the cluster default|none|20Gi|--set postgres.bundled.persistence.storageClass=-|gives storageClassName \"\" (\"-\"), but StatefulSet futureagi-postgres was created with no storageClassName"
  "the cluster default after no StorageClass (-)|\"\"|20Gi||gives no storageClassName (the cluster default), but StatefulSet futureagi-postgres was created with storageClassName \"\" (\"-\")"
  "no StorageClass (-) kept|\"\"|20Gi|--set global.storageClass=-|"
)
for case in "${live_cases[@]}"; do
  IFS='|' read -r name class size args expected <<<"$case"
  class_line=""
  [ "$class" = none ] || class_line="          storageClassName: $class"$'\n'
  printf 'futureagi-postgres:\n  spec:\n    volumeClaimTemplates:\n      - metadata: {name: data}\n        spec:\n%s          resources: {requests: {storage: %s}}\n' \
    "$class_line" "$size" >"$live_chart/live.yaml"
  # shellcheck disable=SC2086 # args is a word list
  if "$helm" template futureagi "$live_chart" --set secrets.allowGeneratedKeysWithoutCluster=true -f "$chart/examples/bundled.yaml" $args >"$out/live.txt" 2>&1; then
    [ -z "$expected" ] || fail "rendered despite: $name"
  elif [ -z "$expected" ] || ! grep -qF -- "$expected" "$out/live.txt"; then
    cat "$out/live.txt" >&2
    fail "the error for \"$name\" does not mention: ${expected:-(it should render)}"
  fi
  echo "ok   $name"
done
rm -rf "$out/live.txt" "$live_chart"

for set in "${value_sets[@]}"; do
  name=${set%%|*}
  args=()
  for file in ${set#*|}; do
    args+=(-f "$chart/$file")
  done
  echo "== $name"
  "$helm" lint "$chart" --strict --set secrets.allowGeneratedKeysWithoutCluster=true "${args[@]}" >"$out/$name.lint.txt" 2>&1 || {
    cat "$out/$name.lint.txt" >&2
    fail "helm lint ($name)"
  }
  "$helm" template futureagi "$chart" --set secrets.allowGeneratedKeysWithoutCluster=true --namespace futureagi "${args[@]}" >"$out/$name.yaml"
  # Rendered for each Kubernetes version: some fields depend on it (the
  # kubelet's preStop sleep action needs 1.30).
  mkdir -p "$out/kube"
  for version in $kube_versions; do
    "$helm" template futureagi "$chart" --set secrets.allowGeneratedKeysWithoutCluster=true --namespace futureagi --kube-version "$version" "${args[@]}" >"$out/kube/$name-$version.yaml"
    "$kubeconform" -strict -summary -kubernetes-version "$version" \
      -schema-location default -schema-location "$crd_schemas" "$out/kube/$name-$version.yaml" ||
      fail "kubeconform ($name, Kubernetes $version)"
  done
done

echo "== GitOps: rendering again changes nothing"
# Argo CD renders on every sync, where `lookup` finds nothing: with the
# keys in secrets.existingSecret, nothing may be generated anew.
"$helm" template futureagi "$chart" --set secrets.allowGeneratedKeysWithoutCluster=true --namespace futureagi \
  -f "$chart/examples/external.yaml" -f "$chart/ci/gitops.yaml" >"$out/gitops.again.txt"
diff -u "$out/gitops.yaml" "$out/gitops.again.txt" || fail "a second render of gitops differs"
rm -f "$out/gitops.again.txt"
echo "ok   identical"

echo "== openshift-auto (the security.openshift.io/v1 API present)"
"$helm" template futureagi "$chart" --set secrets.allowGeneratedKeysWithoutCluster=true --namespace futureagi -f "$chart/examples/bundled.yaml" \
  --api-versions security.openshift.io/v1 >"$out/openshift-auto.yaml"
echo "ok   rendered"

echo "== the install notes print the proxy's host, never its login"
# helm template leaves NOTES.txt out; a client-side dry run renders it. It
# never talks to the caller's cluster (no kubeconfig): Helm 3 wants one for a
# dry run, so it is skipped there.
if KUBECONFIG="$out/no-kubeconfig" "$helm" install futureagi "$chart" --set secrets.allowGeneratedKeysWithoutCluster=true --dry-run=client --namespace futureagi \
  -f "$chart/examples/bundled.yaml" --set global.proxy.httpsProxy=http://corp:notes-123@proxy.corp.example:3128 \
  --set objectStorage.bundled.service.downloadPort=9100 >"$out/notes.txt" 2>&1; then
  sed -n '/^NOTES:/,$p' "$out/notes.txt" >"$out/notes-only.txt"
  if grep -q 'notes-123' "$out/notes-only.txt" || ! grep -qF 'through the proxy proxy.corp.example:3128;' "$out/notes-only.txt"; then
    cat "$out/notes-only.txt" >&2
    fail "the install notes print the proxy URL with its login"
  fi
  # MINIO_URL is http://localhost:<downloadPort>: the port-forward matches it.
  grep -qF 'port-forward svc/futureagi-minio 9100:9000' "$out/notes-only.txt" || {
    cat "$out/notes-only.txt" >&2
    fail "the install notes' MinIO port-forward ignores objectStorage.bundled.service.downloadPort"
  }
  # A login urlParse cannot parse ('#', a stray '%') still renders.
  KUBECONFIG="$out/no-kubeconfig" "$helm" install futureagi "$chart" --set secrets.allowGeneratedKeysWithoutCluster=true --dry-run=client --namespace futureagi \
    -f "$chart/examples/bundled.yaml" --set-string 'global.proxy.httpsProxy=http://corp:n#o%zz@proxy.corp.example:3128' \
    >"$out/notes-odd.txt" 2>&1 || { cat "$out/notes-odd.txt" >&2; fail "install notes with an odd proxy login"; }
  grep -qF 'through the proxy proxy.corp.example:3128;' "$out/notes-odd.txt" || {
    sed -n '/^NOTES:/,$p' "$out/notes-odd.txt" >&2
    fail "the install notes misprint a proxy with an odd login"
  }
  echo "ok   host:port only"
elif grep -q 'cluster unreachable' "$out/notes.txt"; then
  echo "skip (this Helm needs a cluster for a client-side dry run)"
else
  cat "$out/notes.txt" >&2
  fail "helm install --dry-run=client"
fi

echo "== GitOps examples (not Helm values): YAML and CRD schemas"
for file in "$chart"/examples/gitops/*.yaml; do
  "$python" -c 'import sys, yaml; docs = [d for d in yaml.safe_load_all(open(sys.argv[1])) if d]; assert docs and all("kind" in d for d in docs), sys.argv[1]' "$file" ||
    fail "invalid YAML: $file"
  "$kubeconform" -strict -summary -schema-location default -schema-location "$crd_schemas" "$file" ||
    fail "kubeconform ($file)"
done

echo "== default image inventory still renders offline"
HELM="$helm" "$chart/hack/list-images.sh" "$chart" >"$out/inventory.txt" || fail "default image inventory"
[ -s "$out/inventory.txt" ] || fail "default image inventory is empty"
if grep -qE '^(apiVersion:|kind:|data:)' "$out/inventory.txt"; then
  fail "image inventory emitted manifests"
fi
rm -f "$out/inventory.txt"
echo "ok   default image inventory"

echo "== examples/airgap.yaml: its mirror commands copy each image the chart then pulls"
# Its list-images.sh arguments and crane copy destination, run on this chart:
# the destinations must be exactly the images external.yaml and airgap.yaml
# pull. A list made with the mirror already set names the mirror as the
# source, and the copy nests it a second time.
airgap=$chart/examples/airgap.yaml
list_args=$(sed -n 's/^#//; /list-images\.sh/,/> images\.txt/p' "$airgap" | tr -d '\\\n' |
  sed -nE 's/.*list-images\.sh [^ ]+ -- +(.*) > images\.txt.*/\1/p')
# shellcheck disable=SC2016 # the example's own $ref, not this script's
mirror_prefix=$(sed -nE 's/.*crane copy "\$ref" "(.*)\$\{ref#\*\/\}".*/\1/p' "$airgap")
if [ -z "$list_args" ] || [ -z "$mirror_prefix" ]; then
  fail "examples/airgap.yaml no longer lists and copies the images the way this check reads them: update it"
fi
list_words=()
# shellcheck disable=SC2086 # list_args is a word list
for word in $list_args; do
  case $word in *.yaml) list_words+=("$chart/examples/$word") ;; *) list_words+=("$word") ;; esac
done
sources=$(HELM="$helm" "$chart/hack/list-images.sh" "$chart" -- --set secrets.allowGeneratedKeysWithoutCluster=true "${list_words[@]}")
pulled=$(HELM="$helm" "$chart/hack/list-images.sh" "$chart" -- --set secrets.allowGeneratedKeysWithoutCluster=true -f "$chart/examples/external.yaml" -f "$airgap")
copied=$(while read -r ref; do echo "$mirror_prefix${ref#*/}"; done <<<"$sources" | sort)
if [ "$copied" != "$pulled" ]; then
  echo "sources:" >&2
  echo "$sources" >&2
  diff -u <(echo "$pulled") <(echo "$copied") >&2 || true
  fail "examples/airgap.yaml copies its images to other references than the chart pulls"
fi
echo "ok   $(wc -l <<<"$sources" | tr -d ' ') images"

echo "== hack/support-bundle.sh parses and redacts"
bash -n "$chart/hack/support-bundle.sh" || fail "support-bundle.sh does not parse"
if command -v shellcheck >/dev/null; then
  shellcheck "$chart/hack/support-bundle.sh" || fail "shellcheck support-bundle.sh"
fi
# Its redaction functions, on values, log lines, describe output and the
# install notes with secrets in them: by name, and PEM blocks and credentials
# in URLs (a proxy login, a DATABASE_URL in extraEnv) under any name, the
# values included. Each value goes whole (spaces, commas, quotes, a long
# value Helm or pprint folds, the continuation lines of a describe env value,
# a PEM block) and what follows it stays. Each sample is its own call, as
# each file of the bundle is. Synthetic secrets end in -123; generated key
# payloads are checked separately, before any redacted output leaves the child.
redactors=$(sed -n '/^sensitive=/,/^run() {/p' "$chart/hack/support-bundle.sh" | sed '$d')
IFS= read -r -d '' samples <<'SAMPLES' || true
set +x
set -o pipefail
fixture_fail() {
  printf 'FAIL: %s\n' "$1" >&2
  exit 1
}
# Generate private fixtures only at runtime, with cleanup in this shell scope.
umask 077
fixture_dir=$(mktemp -d) || fixture_fail "cannot create redaction fixture directory"
trap 'rm -rf "$fixture_dir"' EXIT
openssl ecparam -name prime256v1 -genkey -noout -out "$fixture_dir/ec" 2>/dev/null ||
  fixture_fail "cannot generate EC redaction fixture"
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out "$fixture_dir/rsa" 2>/dev/null ||
  fixture_fail "cannot generate RSA redaction fixture"
openssl rsa -in "$fixture_dir/rsa" -traditional -out "$fixture_dir/rsa-traditional" 2>/dev/null ||
  fixture_fail "cannot format RSA redaction fixture"
ec_private_key=$(sed 's/^/      /' "$fixture_dir/ec") || fixture_fail "cannot read EC redaction fixture"
ec_payload=$(sed '/^-/d' "$fixture_dir/ec") || fixture_fail "cannot read EC redaction payload"
rsa_payload=$(sed '/^-/d' "$fixture_dir/rsa-traditional" | tr -d '\n') ||
  fixture_fail "cannot read RSA redaction payload"
[ -n "$ec_payload" ] && [ -n "$rsa_payload" ] || fixture_fail "empty generated redaction payload"
# Boundaries and the masked non-secret labels are assembled at runtime too.
pem_begin='-----BEGIN'
pem_end='-----END'
pem_key=$(printf '%s %s%s' PRIVATE KEY '-----')
pem_certificate=$(printf '%s%s%s' CERTI FICATE '-----')
postgres_secret=$(printf '%s-%s' futureagi postgres)
checksum_annotation=$(printf '%s/%s' checksum secrets)
api_keys="${rsa_payload}-123"
redacted=$(
printf "license:\n  key: lic-123\n  url: https://licenses\npostgres:\n  password: pg-123\nsecrets:\n  extra:\n    SENTRY_DSN: dsn-123\n" | redact_yaml
printf "global:\n  proxy:\n    httpsProxy: http://corp:proxy-123@proxy.corp:3128\n    noProxy: .corp\nconfig:\n  extraEnv:\n    DATABASE_URL: postgres://app:url-123@db.corp:5432/app\n" | redact_yaml
printf "PG_PASSWORD:  env-123\napi_key=\"log-123\" user=bob\n" | redact_text
printf "      HTTPS_PROXY:   http://corp:describe-123@proxy.corp:3128\n      NO_PROXY:      .corp\n      DATABASE_URL:  postgres://app:dburl-123@db.corp:5432/app\n" | redact_text
printf "NOTE     Outbound traffic goes through http://corp:status-123@proxy.corp:3128; NO_PROXY covers\n" | redact_text
printf "config:\n  extraEnv:\n    DB_URL: postgres://app:p@ss-123@db.corp:5432/app\n" | redact_yaml
redact_yaml <<EOF
postgres:
  password: correct horse battery staple and more words past eighty columns fold-123
    folded tail-123
  host: db.corp
bootstrap:
  users:
  - password: item-123
    name: kept-name
config:
  extraEnv:
    GOOGLE_SA_JSON: '{"type":"service_account","project":"p","private_key":"${pem_begin}
      ${pem_key}\nsa-pem-123\n${pem_end} ${pem_key}\n","client_email":"x@y"}'
    JWT_SIGNING_PEM: |
${ec_private_key}
    LOG_LEVEL: info
    SA_JSON: '{"type":"service_account","private_key":"${pem_begin} ${pem_key}\nfolded-pem-123\n${pem_end}
      ${pem_key}\n","client_email":"x@y"}'
EOF
redact_text <<EOF
    Environment:
      SERVICE_PASSWORD:  correct-123 horse, battery staple-123
      PG_PASSWORD:       <set to the key 'password' in secret '${postgres_secret}'>  Optional: false
      TLS_PRIVATE_KEY:   ${pem_begin} RSA ${pem_key}
                         ${rsa_payload}
                         ${pem_end} RSA ${pem_key}
      SIGNING_SECRET:    first-line-123
                         second-line-123

      QUOTED_TOKEN:      "open-123
                         close-123"
      QUOTED_PASSWORD:   "quoted-123 horse" battery-123
      SIGNING_KEYS:      "k1-123"
                         k2-123
      API_KEYS:
                         ${api_keys}
      LOG_LEVEL:         info
    Mounts:
Volumes:
  gcp-credentials:
    Type:        Secret (a volume populated by a Secret)
    SecretName:  futureagi-gcp
    Optional:    false
EOF
redact_text <<EOF
2026-09-29T10:00:00.000000000Z loaded ${pem_begin} ${pem_key}
2026-09-29T10:00:00.000000000Z log-pem-123
2026-09-29T10:00:00.000000000Z ${pem_end} ${pem_key}
2026-09-29T10:00:01.000000000Z worker ready
EOF
redact_text <<EOF
{"event": "login", "password": "first,second two-123", "user": "bob"}
{"msg": "{\"token\": \"nested value-123\", \"n\": 1}", "level": "info"}
{"max_tokens": 1024, "model": "m1"}
{"credentials": {"id": "a", "key": "obj-123"}, "level": "info"}
{'secret': 'py value-123', 'n': 1}
{'client_secret': 'pprint wraps a long string into parts-123 on the lines '
                  'under its name-123',
 'user': 'bob'}
{"cert": "${pem_begin} ${pem_certificate}\nMIIC-123\n${pem_end} ${pem_certificate}\n", "x": 1}
"GET /api/traces/?page=2&api_key=query-123&q=x HTTP/1.1" 200
Annotations:  ${checksum_annotation}: 0f1e2d
              kubectl.kubernetes.io/restartedAt: 2026-09-29T10:00:00Z
EOF
) || fixture_fail "support-bundle.sh redactor samples failed"
# Newline-separated EC payload lines are separate fixed-string patterns: even
# one surviving line must fail, before any output can reach the caller.
for generated_payload in "$ec_payload" "$rsa_payload"; do
  if grep -qF -- "$generated_payload" <<<"$redacted" 2>/dev/null; then
    fixture_fail "support-bundle.sh redaction leaves generated key material"
  else
    [ "$?" -eq 1 ] || fixture_fail "cannot check generated redaction payload"
  fi
done
printf '%s\n' "$redacted"
SAMPLES
redacted=$(bash -c "$redactors"$'\n'"$samples")
if grep -qF -- '-123' <<<"$redacted"; then
  fail "support-bundle.sh redaction leaves a secret"
fi
kept_after_redaction=(
  'https://licenses' 'noProxy: .corp' 'NO_PROXY:      .corp' 'postgres://<redacted>@db.corp:5432/app' 'http://<redacted>@proxy.corp:3128;'
  'api_key="<redacted>" user=bob'
  '  host: db.corp' '    name: kept-name'
  '"private_key":"<redacted>' '    JWT_SIGNING_PEM: |' '    LOG_LEVEL: info'
  "      PG_PASSWORD:       <set to the key 'password' in secret 'futureagi-postgres'>  Optional: false"
  '      SERVICE_PASSWORD:  <redacted>' '      TLS_PRIVATE_KEY:   <redacted>' '      LOG_LEVEL:         info' '    Mounts:'
  '    Type:        Secret (a volume populated by a Secret)' '    Optional:    false'
  'loaded <redacted>' '2026-09-29T10:00:01.000000000Z worker ready'
  '"password": "<redacted>", "user": "bob"}' '\"token\": \"<redacted>\", \"n\": 1}", "level": "info"}'
  '{"max_tokens": <redacted>, "model": "m1"}' '{"credentials": <redacted>' "{'secret': '<redacted>', 'n': 1}"
  "{'client_secret': '<redacted>'" " 'user': 'bob'}"
  '{"cert": "<redacted>\n", "x": 1}' '&api_key=<redacted>&q=x HTTP/1.1" 200'
  '              kubectl.kubernetes.io/restartedAt: 2026-09-29T10:00:00Z'
)
for kept in "${kept_after_redaction[@]}"; do
  grep -qF -- "$kept" <<<"$redacted" || {
    fail "support-bundle.sh redaction removes: $kept"
  }
done
echo "ok   redacts"

echo "== hack/support-bundle.sh leaves no port-forward behind"
# Against a fake kubectl, helm and curl: kubectl's port-forward is one
# long-lived process, as the real one is, that records its PID. The script
# must stop it, and remove its working directory, when it finishes and when
# it is killed.
fake="$out/support-bundle"
rm -rf "$fake"
mkdir -p "$fake/bin" "$fake/tmp"
cat >"$fake/bin/kubectl" <<'SH'
#!/bin/sh
case " $* " in
  *" port-forward "*) echo $$ >"$FAKE_STATE/port-forward.pid"; exec sleep 300 ;;
  *" get svc -l "*) printf futureagi-backend ;;
  *" get svc futureagi-backend "*) printf 8000 ;;
esac
SH
printf '#!/bin/sh\n' >"$fake/bin/helm"
cat >"$fake/bin/curl" <<'SH'
#!/bin/sh
# /health/ answers once FAKE_STATE/healthy exists and the port-forward, which
# it goes through, is up (has recorded its PID). Until then it fails slowly,
# as a starting backend does, so the script is still waiting (about 50 s of
# retries) when a busy machine gets round to signalling it.
case " $* " in
  *"/health/"*)
    [ -e "$FAKE_STATE/healthy" ] && [ -s "$FAKE_STATE/port-forward.pid" ] && exit 0
    sleep 2
    exit 1 ;;
  *) echo '{}' ;;
esac
SH
chmod +x "$fake/bin/kubectl" "$fake/bin/helm" "$fake/bin/curl"
# env execs the script: the process run or signalled is the script itself.
support_bundle=(env FAKE_STATE="$fake" KUBECTL="$fake/bin/kubectl" HELM="$fake/bin/helm" PATH="$fake/bin:$PATH"
  TMPDIR="$fake/tmp" bash "$chart/hack/support-bundle.sh" -o "$fake/out")
port_forward_stopped() {
  local pid
  pid=$(cat "$fake/port-forward.pid" 2>/dev/null) || fail "support-bundle.sh ($1) did not start its port-forward"
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid"
    fail "support-bundle.sh ($1) left its port-forward running (PID $pid)"
  fi
  [ -z "$(ls -A "$fake/tmp")" ] || fail "support-bundle.sh ($1) left its working directory"
  rm -f "$fake/port-forward.pid"
}
# Before any other failure: a port-forward the script left must not outlive
# check.sh.
kill_port_forward() {
  kill "$(cat "$fake/port-forward.pid" 2>/dev/null)" 2>/dev/null || true
}
touch "$fake/healthy"
"${support_bundle[@]}" >"$fake/run.txt" 2>&1 || { cat "$fake/run.txt" >&2; kill_port_forward; fail "support-bundle.sh against a fake cluster"; }
ls "$fake/out"/futureagi-support-futureagi-*.tar.gz >/dev/null || { kill_port_forward; fail "support-bundle.sh wrote no bundle"; }
port_forward_stopped "finished"
# Killed while it waits for the backend, with SIGTERM and with Ctrl-C (SIGINT
# to its whole process group): after cleaning up it must die of the signal,
# so that a caller (a loop stopped with Ctrl-C) knows. A shell's $? reads
# 128+n either way, hence Python.
cat >"$fake/kill.py" <<'PY'
# kill.py FAKE SIGNAL CMD...: runs CMD, sends it SIGNAL once its port-forward
# is up (INT to the process group, as Ctrl-C does) and says how it ended.
import os
import signal
import subprocess
import sys
import time

fake, sig, cmd = sys.argv[1], signal.Signals["SIG" + sys.argv[2]], sys.argv[3:]
pid_file = os.path.join(fake, "port-forward.pid")
with open(os.path.join(fake, "run.txt"), "w") as log:
    # A shell running check.sh in the background (`&`) ignores SIGINT, and an
    # ignored signal is inherited, so give the script Ctrl-C's default back.
    bundle = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True,
                              preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_DFL))
for _ in range(100):
    if os.path.exists(pid_file) and os.path.getsize(pid_file):
        break
    time.sleep(0.1)
try:
    if sig == signal.SIGINT:
        os.killpg(bundle.pid, sig)
    else:
        bundle.send_signal(sig)
except ProcessLookupError:
    pass
code = bundle.wait()
print(f"killed by {sig.name}" if code == -sig else f"exited {code}")
PY
rm -f "$fake/healthy"
for sig in TERM INT; do
  how=$("$python" "$fake/kill.py" "$fake" "$sig" "${support_bundle[@]}") || true
  [ "$how" = "killed by SIG$sig" ] || { cat "$fake/run.txt" >&2; kill_port_forward; fail "support-bundle.sh ${how:-failed} on SIG$sig, not killed by it"; }
  port_forward_stopped "killed with SIG$sig"
done
rm -rf "$fake"
echo "ok   stopped"

echo "== rendered invariants"
# No manifest may carry an unexpanded template or an empty image.
if grep -nE '<no value>|image:( *| *"")$' "$out"/*.yaml; then
  fail "a rendered manifest has an unset value"
fi
# Inside the repository, the Python services also default their behaviour
# settings as docker-compose.distributed.yml does.
compose=()
if [ -f "$repo/docker-compose.distributed.yml" ]; then
  compose=(--compose "$repo/docker-compose.distributed.yml")
fi
"$python" "$chart/hack/rendered_checks.py" "$out" ${compose[@]+"${compose[@]}"}
# The Omega Hub's objects render only with hub.enabled, and hold their rules
# (hack/rendered_hub_checks.py) ...
"$python" "$chart/hack/rendered_hub_checks.py" "$out"
# ... and it notices a checker on a node's Docker socket, or one handed the
# backend's secrets.
mkdir -p "$out/broken-hub"
for mutant in socket secrets; do
  "$python" - "$out/hub.yaml" "$mutant" >"$out/broken-hub/hub.yaml" <<'EOF'
import sys

import yaml

docs = [d for d in yaml.safe_load_all(open(sys.argv[1])) if d]
for doc in docs:
    if doc["kind"] == "Deployment" and doc["metadata"]["name"].endswith("-hub-checker"):
        spec = doc["spec"]["template"]["spec"]
        container = spec["containers"][0]
        if sys.argv[2] == "socket":
            spec["volumes"].append({"name": "docker", "hostPath": {"path": "/var/run/docker.sock"}})
            for entry in container["env"]:
                if entry["name"] == "DOCKER_HOST":
                    entry["value"] = "unix:///var/run/docker.sock"
        else:
            container["envFrom"] = [{"secretRef": {"name": "futureagi-secrets"}}]
            container["env"].append({"name": "PG_PASSWORD", "value": "x"})
yaml.safe_dump_all(docs, sys.stdout)
EOF
  if "$python" "$chart/hack/rendered_hub_checks.py" "$out/broken-hub" >"$out/broken-hub.txt" 2>&1; then
    fail "rendered_hub_checks.py passes a checker with the $mutant mutant"
  fi
  case $mutant in
    socket) expected="mounts the host path /var/run/docker.sock" ;;
    *) expected="envFrom would hand the checker more than its own settings" ;;
  esac
  grep -qF -- "$expected" "$out/broken-hub.txt" || {
    cat "$out/broken-hub.txt" >&2
    fail "rendered_hub_checks.py does not name the $mutant mutant"
  }
  echo "ok   the $mutant mutant is caught"
done
rm -rf "$out/broken-hub" "$out/broken-hub.txt"
# ... and they notice pods that read AGENTCC_WEBHOOK_SECRET from
# secrets.existingSecret while the chart's Secret holds the one given inline.
mkdir -p "$out/broken"
"$python" - "$out/all-components.yaml" >"$out/broken/all-components.yaml" <<'EOF'
import sys

import yaml


def point(node):
    if isinstance(node, dict):
        ref = node.get("valueFrom", {}).get("secretKeyRef")
        if node.get("name") == "AGENTCC_WEBHOOK_SECRET" and ref:
            ref["name"] = "futureagi-app"
        for value in node.values():
            point(value)
    elif isinstance(node, list):
        for value in node:
            point(value)


docs = [d for d in yaml.safe_load_all(open(sys.argv[1])) if d]
point(docs)
yaml.safe_dump_all(docs, sys.stdout)
EOF
if "$python" "$chart/hack/rendered_checks.py" "$out/broken" >"$out/broken.txt" 2>&1; then
  fail "rendered_checks.py passes pods that read the webhook secret from the wrong Secret"
fi
grep -qF "no Secret holds AGENTCC_WEBHOOK_SECRET ('futureagi-app'" "$out/broken.txt" || {
  cat "$out/broken.txt" >&2
  fail "rendered_checks.py does not name the missing webhook secret"
}
rm -rf "$out/broken" "$out/broken.txt"
echo "ok   a webhook secret no Secret holds is caught"
# ... and a bootstrap job Argo CD would run by its Helm hooks alone: in
# PreSync, before the bundled datastores it waits for exist.
mkdir -p "$out/broken"
"$python" - "$out/bundled.yaml" >"$out/broken/bundled.yaml" <<'EOF'
import sys

import yaml

docs = [d for d in yaml.safe_load_all(open(sys.argv[1])) if d]
for doc in docs:
    annotations = doc["metadata"].get("annotations") or {}
    for key in [k for k in annotations if k.startswith("argocd.argoproj.io/")]:
        del annotations[key]
yaml.safe_dump_all(docs, sys.stdout)
EOF
if "$python" "$chart/hack/rendered_checks.py" "$out/broken" >"$out/broken.txt" 2>&1; then
  fail "rendered_checks.py passes a bootstrap job Argo CD runs before the bundled datastores"
fi
grep -qF "Argo CD runs it in PreSync wave 0, not after ConfigMap futureagi-clickhouse (Sync wave 0)" "$out/broken.txt" || {
  cat "$out/broken.txt" >&2
  fail "rendered_checks.py does not name the datastores the bootstrap job runs before"
}
rm -rf "$out/broken" "$out/broken.txt"
echo "ok   a bootstrap job Argo CD runs before the bundled datastores is caught"
# ... and Omega objects that would not hold: an issuer key others can read, a
# TLS sidecar forwarding beyond loopback, a gateway in plain-HTTP mode, a
# gateway running as the API's ServiceAccount with its token mounted, a UI
# with its Omega tabs off, a host-events secret others can read, a worker
# that would check EE's certificate without the CA the gateway trusts, a
# bootstrap Job that registers the revocation drain hourly, and the gateway
# left in a render with omega.enabled false.
mkdir -p "$out/broken"
"$python" - "$out/omega.yaml" "$out/bundled.yaml" "$out/broken" <<'EOF'
import sys
from pathlib import Path

import yaml

omega = [d for d in yaml.safe_load_all(open(sys.argv[1])) if d]
for doc in omega:
    spec = doc.get("spec", {}).get("template", {}).get("spec", {})
    for volume in spec.get("volumes", []):
        if volume["name"] in ("omega-issuer", "omega-host-events"):
            volume["secret"]["defaultMode"] = 0o644
    if doc["kind"] == "Job" and doc["metadata"]["name"].endswith("-bootstrap"):
        for container in spec["containers"]:
            container["env"] = [e for e in container.get("env", []) if e["name"] != "OMEGA_SQUAD_DELEGATION_ISSUER"]
    if doc["kind"] == "Deployment" and doc["metadata"]["name"].endswith("-worker"):
        for container in spec["containers"]:
            container["env"] = [e for e in container.get("env", []) if e["name"] != "OMEGA_HOST_EVENTS_CA_FILE"]
    for container in spec.get("containers", []):
        if container["name"] == "omega-tls":
            container["args"] = [a.replace("127.0.0.1", "0.0.0.0") for a in container["args"]]
        for env in container.get("env", []):
            if env["name"] == "OMEGA_REMOTE_BASE_URL":
                env["value"] = "http://omega-os.omega.svc:8787"
    if doc["kind"] == "Deployment" and doc["metadata"]["name"].endswith("omega-gateway"):
        spec["serviceAccountName"] = "futureagi"
        spec["automountServiceAccountToken"] = True
    if doc["kind"] == "Deployment" and doc["metadata"]["name"].endswith("-frontend"):
        for container in spec["containers"]:
            container["env"] = [e for e in container.get("env", []) if e["name"] != "VITE_OMEGA_CONSOLE"]
yaml.safe_dump_all(omega, open(Path(sys.argv[3]) / "omega.yaml", "w"))
bundled = [d for d in yaml.safe_load_all(open(sys.argv[2])) if d]
bundled += [d for d in omega if d["kind"] == "Deployment" and d["metadata"]["name"].endswith("omega-gateway")]
yaml.safe_dump_all(bundled, open(Path(sys.argv[3]) / "bundled.yaml", "w"))
EOF
if "$python" "$chart/hack/rendered_checks.py" "$out/broken" >"$out/broken.txt" 2>&1; then
  fail "rendered_checks.py passes broken Omega objects"
fi
for expected in "issuer key volume mode 420, not 0440" "the TLS sidecar forwards beyond loopback" \
  "gateway OMEGA_REMOTE_BASE_URL='http://omega-os.omega.svc:8787' is not an https remote URL" \
  "bundled: Deployment futureagi-omega-gateway renders with omega.enabled false" \
  "the gateway runs as the API's ServiceAccount 'futureagi'" "the gateway pod mounts a ServiceAccount token" \
  "the UI's Omega tabs are off (VITE_OMEGA_CONSOLE is not 1)" \
  "omega: futureagi-worker host-events secret volume mode 420, not 0440" \
  "omega: futureagi-worker does not check EE's certificate with the CA the gateway trusts (OMEGA_HOST_EVENTS_CA_FILE=None)" \
  "the bootstrap Job does not see the issuer"; do
  grep -qF -- "$expected" "$out/broken.txt" || {
    cat "$out/broken.txt" >&2
    fail "rendered_checks.py does not report: $expected"
  }
done
rm -rf "$out/broken" "$out/broken.txt"
echo "ok   broken Omega objects are caught"
# ... and the pod label Omega EE's NetworkPolicy admits host-event senders by
# (omega.futureagi.ai/host-events-sender): on pods that send nothing (the UI,
# the bootstrap Job or code-executor), missing from a worker that sends, in a
# selector, or in a render with omega.enabled false, is caught.
mkdir -p "$out/broken"
"$python" - "$out/omega.yaml" "$out/bundled.yaml" "$out/broken" "$out/all-components.yaml" <<'EOF'
import sys
from pathlib import Path

import yaml

label = "omega.futureagi.ai/host-events-sender"
omega = [d for d in yaml.safe_load_all(open(sys.argv[1])) if d]
hits = 0
for doc in omega:
    kind, name = doc["kind"], doc["metadata"]["name"]
    if (kind, name) in (("Deployment", "futureagi-frontend"), ("Job", "futureagi-bootstrap")):
        doc["spec"]["template"]["metadata"]["labels"][label] = "true"
        hits += 1
    if (kind, name) == ("Deployment", "futureagi-worker-exact-aggregation"):
        del doc["spec"]["template"]["metadata"]["labels"][label]
        hits += 1
    if (kind, name) == ("Deployment", "futureagi-backend"):
        doc["spec"]["selector"]["matchLabels"][label] = "true"
        hits += 1
assert hits == 4, hits
yaml.safe_dump_all(omega, open(Path(sys.argv[3]) / "omega.yaml", "w"))
bundled = [d for d in yaml.safe_load_all(open(sys.argv[2])) if d]
for doc in bundled:
    if (doc["kind"], doc["metadata"]["name"]) == ("Deployment", "futureagi-backend"):
        doc["spec"]["template"]["metadata"]["labels"][label] = "true"
yaml.safe_dump_all(bundled, open(Path(sys.argv[3]) / "bundled.yaml", "w"))
all_components = [d for d in yaml.safe_load_all(open(sys.argv[4])) if d]
executor = next(d for d in all_components if (d["kind"], d["metadata"]["name"]) == ("Deployment", "futureagi-code-executor"))
# What scalar YAML injection used to do: both pod labels and immutable selectors
# contain the trusted label, while app.kubernetes.io/instance remains futureagi.
executor["spec"]["template"]["metadata"]["labels"][label] = "true"
executor["spec"]["selector"]["matchLabels"][label] = "true"
yaml.safe_dump_all(all_components, open(Path(sys.argv[3]) / "all-components.yaml", "w"))
EOF
if "$python" "$chart/hack/rendered_checks.py" "$out/broken" >"$out/broken.txt" 2>&1; then
  fail "rendered_checks.py passes a misplaced host-events sender label"
fi
for expected in "omega: Deployment futureagi-frontend: its pods carry omega.futureagi.ai/host-events-sender but send no host events" \
  "omega: Job futureagi-bootstrap: its pods carry omega.futureagi.ai/host-events-sender but send no host events" \
  "omega: Deployment futureagi-worker-exact-aggregation: its pods send host events (OMEGA_HOST_EVENTS_URL) but lack omega.futureagi.ai/host-events-sender" \
  "omega: Deployment futureagi-backend has omega.futureagi.ai/host-events-sender in its selector" \
  "all-components: Deployment futureagi-code-executor: its pods carry omega.futureagi.ai/host-events-sender but send no host events" \
  "all-components: Deployment futureagi-code-executor has omega.futureagi.ai/host-events-sender in its selector" \
  "bundled: Deployment futureagi-backend: its pods carry omega.futureagi.ai/host-events-sender but send no host events"; do
  grep -qF -- "$expected" "$out/broken.txt" || {
    cat "$out/broken.txt" >&2
    fail "rendered_checks.py does not report: $expected"
  }
done
rm -rf "$out/broken" "$out/broken.txt"
echo "ok   a misplaced host-events sender label is caught"
# ... and the Omega gateway and the Hub share only the UI's switch for its
# Omega products (VITE_OMEGA_CONSOLE): the switch in a render with neither, a
# gateway setting in the Hub's render, or a Hub setting in the gateway's
# render is still caught.
mkdir -p "$out/broken"
"$python" - "$out" "$out/broken" <<'EOF'
import sys
from pathlib import Path

import yaml

src, dst = Path(sys.argv[1]), Path(sys.argv[2])
for render, deployment, container, entry in (
    ("bundled", "futureagi-frontend", "frontend", {"name": "VITE_OMEGA_CONSOLE", "value": "1"}),
    ("hub", "futureagi-backend", "backend", {"name": "OMEGA_REMOTE_BASE_URL", "value": "https://omega.example.com"}),
    ("omega", "futureagi-backend", "backend", {"name": "OMEGA_HUB_URL", "value": "https://hub.example.com"}),
):
    docs = [d for d in yaml.safe_load_all((src / f"{render}.yaml").read_text()) if d]
    hits = 0
    for doc in docs:
        if doc["kind"] == "Deployment" and doc["metadata"]["name"] == deployment:
            for c in doc["spec"]["template"]["spec"]["containers"]:
                if c["name"] == container:
                    c.setdefault("env", []).append(entry)
                    hits += 1
    assert hits == 1, (render, deployment, container, hits)
    yaml.safe_dump_all(docs, open(dst / f"{render}.yaml", "w"))
EOF
if "$python" "$chart/hack/rendered_checks.py" "$out/broken" >"$out/broken.txt" 2>&1; then
  fail "rendered_checks.py passes an Omega setting where the gateway is off"
fi
if "$python" "$chart/hack/rendered_hub_checks.py" "$out/broken" >>"$out/broken.txt" 2>&1; then
  fail "rendered_hub_checks.py passes a Hub setting where the Hub is off"
fi
for expected in "bundled: Deployment futureagi-frontend/frontend gets ['VITE_OMEGA_CONSOLE'] with omega off" \
  "bundled: futureagi-frontend/frontend sets ['VITE_OMEGA_CONSOLE'] with hub.enabled off" \
  "hub: Deployment futureagi-backend/backend gets ['OMEGA_REMOTE_BASE_URL'] with omega off" \
  "omega: futureagi-backend/backend sets ['OMEGA_HUB_URL'] with hub.enabled off"; do
  grep -qF -- "$expected" "$out/broken.txt" || {
    cat "$out/broken.txt" >&2
    fail "the Omega or Hub checks do not report: $expected"
  }
done
rm -rf "$out/broken" "$out/broken.txt"
echo "ok   the gateway and the Hub share only the UI switch"

echo "== provider defaults and operator overrides"
HELM="$helm" "$python" "$chart/hack/test_provider_concurrency.py" -v

echo "== the Omega revocation channel (omega.hostEvents) on every sender pod"
HELM="$helm" "$python" "$chart/hack/test_omega_host_events_chart.py" -v

echo "== values, schema and README"
"$python" "$chart/hack/values_docs.py" --check

echo "== the pre-commit formatter leaves the templates and generated files alone"
# scripts/lint-staged-root-format.mjs runs Prettier on staged YAML, JSON and
# Markdown: it cannot parse Go templates, and it would reformat what
# values_docs.py writes, which the check above compares byte for byte.
if [ -f "$repo/scripts/lint-staged-root-format.mjs" ]; then
  for path in "deploy/helm/*/templates/" deploy/helm/futureagi/values.schema.json deploy/helm/futureagi/README.md; do
    grep -qxF -- "$path" "$repo/.prettierignore" 2>/dev/null || fail ".prettierignore does not list $path"
  done
  echo "ok   ignored"
else
  echo "skip (not in the repository)"
fi

echo "== ClickHouse config files match the Standalone install"
if [ -d "$repo/deploy/standalone/clickhouse" ]; then
  diff -u "$repo/deploy/standalone/clickhouse/config.d/zz-small-host.xml" "$chart/files/clickhouse/config.d/zz-small-host.xml"
  diff -u "$repo/deploy/standalone/clickhouse/users.d/zz-small-host.xml" "$chart/files/clickhouse/users.d/zz-small-host.xml"
  diff -u "$repo/futureagi/.ci/clickhouse-storage-policy.xml" "$chart/files/clickhouse/config.d/storage-policy.xml"
  echo "ok   identical"
else
  echo "skip (not in the repository)"
fi

echo "== the UI's security headers match the frontend image's"
if [ -f "$repo/frontend/security-headers.conf" ]; then
  diff -u "$repo/frontend/security-headers.conf" "$chart/files/frontend/security-headers.conf"
  echo "ok   identical"
else
  echo "skip (not in the repository)"
fi

echo "== the UI image writes the Omega flag the chart sets into its runtime config"
if [ -f "$repo/frontend/docker-entrypoint.sh" ]; then
  # The variables the chart gives the frontend container in the omega set, through the image's entrypoint.
  "$python" - "$out/omega.yaml" >"$out/frontend-env.sh" <<'PYEOF'
import shlex
import sys

import yaml

for doc in yaml.safe_load_all(open(sys.argv[1])):
    if doc and doc["kind"] == "Deployment" and doc["metadata"]["name"] == "futureagi-frontend":
        for container in doc["spec"]["template"]["spec"]["containers"]:
            for env in container.get("env", []):
                if env["name"].startswith("VITE_") and "value" in env:
                    print(f"export {env['name']}={shlex.quote(env['value'])}")
PYEOF
  (. "$out/frontend-env.sh" && CONFIG_PATH="$out/config.js" sh "$repo/frontend/docker-entrypoint.sh" true >/dev/null)
  grep -qF 'VITE_OMEGA_CONSOLE: "1",' "$out/config.js" || { cat "$out/config.js" >&2; fail "config.js lacks VITE_OMEGA_CONSOLE"; }
  rm -f "$out/frontend-env.sh" "$out/config.js"
  echo "ok   VITE_OMEGA_CONSOLE reaches /config.js"
else
  echo "skip (not in the repository)"
fi

echo "all checks passed (manifests in $out)"
