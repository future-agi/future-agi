"""Gateway-only custody of platform provider credentials (ONE KEY WP-D1..D3).

Offline `helm template` controls with the chart's own fixtures and synthetic
--set-json values only. Nothing here prints a rendered manifest or Helm's
output: every failure carries fixed text (Helm's error, when quoted, has the
synthetic sentinel masked). Requires PyYAML like the other rendered checks.

    HELM=helm python3 deploy/helm/futureagi/hack/test_provider_credentials.py
"""

import hashlib
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

CHART = Path(__file__).resolve().parent.parent
SENTINEL = "synthetic-provider-sentinel-0451"
SECRET = "synthetic-gateway-provider-credentials"
GATEWAY = "futureagi-agentcc-gateway"
GATEWAY_CONTAINER = "agentcc-gateway"
BUNDLED = ("examples/bundled.yaml",)
ALL_COMPONENTS = ("examples/external.yaml", "examples/ingress.yaml", "ci/all-components.yaml")
WORKLOADS = {"Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob", "Pod"}
LLM_FIELDS = ("openaiApiKey", "anthropicApiKey", "googleApiKey", "awsAccessKeyId", "awsSecretAccessKey")
# The provider names the chart refuses on non-gateway paths with gatewayOnly
# (futureagi.providerCredentials.fixedNames, templates/_provider_credentials.tpl),
# plus every name in keys.
FIXED_NAMES = frozenset(
    {
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "OPENROUTER_API_KEY",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AZURE_OPENAI_KEY",
        "AZURE_OPENAI_API_KEY",
        "AZURE_AD_TOKEN",
        "GOOGLE_ACCESS_TOKEN",
        "XAI_API_KEY",
        "COHERE_API_KEY",
        "MISTRAL_API_KEY",
        "GROQ_API_KEY",
        "TOGETHER_API_KEY",
        "FIREWORKS_API_KEY",
        "DEEPINFRA_API_KEY",
        "PERPLEXITY_API_KEY",
        "CEREBRAS_API_KEY",
        "DEEPGRAM_API_KEY",
        "CARTESIA_API_KEY",
        "ELEVEN_API_KEY",
        "ELEVENLABS_API_KEY",
        "AZURE_CLIENT_ID",
        "AZURE_CLIENT_SECRET",
        "AZURE_TENANT_ID",
    }
)
# The chart's default gateway config references ${OPENAI_API_KEY}.
KEYS = [
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENROUTER_API_KEY",
    "BEDROCK_AWS_ACCESS_KEY_ID",
    "BEDROCK_AWS_SECRET_ACCESS_KEY",
    "CUSTOM_PROVIDER_TOKEN",
]

SWITCH = "agentccGateway.providerCredentials.gatewayOnly"
REFUSED_INLINE = (
    "secrets.llm.{}: inline provider keys are refused with agentccGateway.providerCredentials.gatewayOnly; "
    "put them in the Secret agentccGateway.providerCredentials.existingSecret names"
)
REFUSED_LLM_SECRET = (
    "secrets.llm.existingSecret: refused with agentccGateway.providerCredentials.gatewayOnly "
    "(it reaches the Python pods and its references are optional); "
    "use agentccGateway.providerCredentials.existingSecret"
)
REFUSED_GATEWAY_ENV = (
    "agentccGateway.extraEnv.{}: a provider credential may not be a plain pod-spec value; "
    "list it in agentccGateway.providerCredentials.keys"
)
REFUSED_NON_GATEWAY = "{}: provider credentials reach only the gateway with agentccGateway.providerCredentials.gatewayOnly"
REFUSED_ENV_FROM = (
    "config.extraEnvFrom: it reads agentccGateway.providerCredentials.existingSecret, "
    "which only the gateway may read"
)
REFUSED_VOLUME = (
    "{}.extraVolumes: it mounts agentccGateway.providerCredentials.existingSecret, "
    "which only the gateway may read"
)
REFUSED_SHARED = (
    "agentccGateway.providerCredentials.existingSecret must name a Secret no other workload reads "
    "(it equals {})"
)
REFUSED_NO_SECRET = (
    "agentccGateway.providerCredentials.existingSecret is required with "
    "agentccGateway.providerCredentials.gatewayOnly: the Secret holding the gateway's provider credentials"
)
REFUSED_NO_KEYS = (
    "agentccGateway.providerCredentials.keys is required with "
    "agentccGateway.providerCredentials.gatewayOnly: the variable names the gateway reads from that Secret"
)
REFUSED_KEYS_WITHOUT_SECRET = (
    "agentccGateway.providerCredentials.keys needs agentccGateway.providerCredentials.existingSecret"
)
REFUSED_SECRET_WITHOUT_KEYS = (
    "agentccGateway.providerCredentials.existingSecret needs agentccGateway.providerCredentials.keys"
)
REFUSED_LITERAL_API_KEY = (
    "agentccGateway.config.providers.{}.api_key must be a ${{VAR}} reference or a secret URI "
    "(vault://, aws-sm://, gcp-sm://, azure-kv://), never a literal"
)
# The gateway resolves secret URIs in providers.<id>.api_key only
# (agentcc-gateway/internal/secrets/config.go): anywhere else a URI is the literal credential.
REFUSED_LITERAL_FIELD = (
    "agentccGateway.config.providers.{}.{} must be a ${{VAR}} reference, never a literal "
    "(the gateway resolves secret URIs in api_key only)"
)


def refused_literal(provider, field):
    if field == "api_key":
        return REFUSED_LITERAL_API_KEY.format(provider)
    return REFUSED_LITERAL_FIELD.format(provider, field)


REFUSED_UNLISTED = (
    "agentccGateway.config.providers.{}.{} references a variable that is not in "
    "agentccGateway.providerCredentials.keys"
)
REFUSED_STDIO_MCP = (
    "agentccGateway.config.mcp.servers.{}: a stdio MCP server inherits the gateway's environment, "
    "provider credentials included; refused with agentccGateway.providerCredentials.gatewayOnly"
)
REFUSED_ESO_NAME = (
    "externalSecrets.secrets.providerCredentials needs agentccGateway.providerCredentials.existingSecret"
)
REFUSED_SA_NAME = (
    "agentccGateway.serviceAccount.name must differ from serviceAccount.name and "
    "bootstrap.serviceAccount.name: the gateway's identity is its own"
)
REFUSED_DEFAULT_SA = (
    "agentccGateway.serviceAccount.name: set a dedicated non-default ServiceAccount with "
    "agentccGateway.providerCredentials.gatewayOnly; the namespace default account is shared by other workloads"
)


def helm_template(values, fixtures=BUNDLED):
    command = [
        os.environ.get("HELM", "helm"),
        "template",
        "futureagi",
        str(CHART),
        "--namespace",
        "futureagi",
        # c2.5 refuses offline key generation except for disposable CI renders.
        "--set",
        "secrets.allowGeneratedKeysWithoutCluster=true",
    ]
    for fixture in fixtures:
        command.extend(["-f", str(CHART / fixture)])
    for path, value in values.items():
        command.extend(["--set-json", f"{path}={json.dumps(value)}"])
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        env=dict(os.environ, KUBECONFIG=os.devnull),
    )


def masked(text):
    """Helm's error text with the synthetic sentinel masked, for diagnostics."""
    return text.replace(SENTINEL, "<sentinel>").strip()[-700:]


def gateway_only(extra=None):
    values = {
        SWITCH: True,
        "agentccGateway.serviceAccount.create": True,
        "agentccGateway.providerCredentials.existingSecret": SECRET,
        "agentccGateway.providerCredentials.keys": KEYS,
        "secrets.llm.existingSecret": "",
        # all-components exercises these opaque sources for self-host. The
        # gatewayOnly positive baseline must not enable sources it now refuses.
        # Negative controls override these below through extra.
        "config.extraEnvFrom": [],
        "agentccGateway.extraEnvFrom": [],
    }
    for field in LLM_FIELDS:
        values[f"secrets.llm.{field}"] = ""
    values.update(extra or {})
    return values


def pod_specs(docs):
    for doc in docs:
        kind = doc.get("kind")
        if kind not in WORKLOADS:
            continue
        if kind == "Pod":
            spec = doc.get("spec") or {}
        elif kind == "CronJob":
            spec = doc["spec"]["jobTemplate"]["spec"]["template"]["spec"]
        else:
            spec = doc["spec"]["template"]["spec"]
        yield f"{kind}/{doc['metadata']['name']}", spec


def effective_service_account(spec):
    # Kubernetes uses the namespace default account for an omitted/empty field.
    return spec.get("serviceAccountName") or "default"


def gateway_account_values(create, name):
    return {
        "agentccGateway.serviceAccount": {"create": create, "name": name},
        "serviceAccount": {"create": True, "name": "synthetic-app"},
        "bootstrap.serviceAccount": {"create": True, "name": "synthetic-bootstrap"},
        "agentccGateway.config.providers.openai.api_key": "gcp-sm://" + SENTINEL,
    }


def containers(spec):
    return list(spec.get("initContainers") or []) + list(spec.get("containers") or [])


def env_of(spec, container_name):
    for container in containers(spec):
        if container.get("name") == container_name:
            return list(container.get("env") or [])
    return []


def secret_ref(entry):
    return ((entry or {}).get("valueFrom") or {}).get("secretKeyRef") or {}


def exposures(spec, names):
    """Names (never values) of the ways a pod spec could see provider credentials."""
    found = []
    for container in containers(spec):
        for entry in container.get("env") or []:
            if entry.get("name") in names or secret_ref(entry).get("name") == SECRET:
                found.append(f"{container.get('name')}:{entry.get('name')}")
        for source in container.get("envFrom") or []:
            if (source.get("secretRef") or {}).get("name") == SECRET:
                found.append(f"{container.get('name')}:envFrom")
    for volume in spec.get("volumes") or []:
        sources = [volume.get("secret") or {}]
        sources += [item.get("secret") or {} for item in (volume.get("projected") or {}).get("sources") or []]
        if any(SECRET in (source.get("secretName"), source.get("name")) for source in sources):
            found.append(f"volume:{volume.get('name')}")
    return found


def comparable(docs, dropped=()):
    """Every rendered document but the Secrets (generated keys differ per render)."""
    return {
        (doc["kind"], doc["metadata"]["name"]): doc
        for doc in docs
        if doc["kind"] != "Secret" and (doc["kind"], doc["metadata"]["name"]) not in dropped
    }


# Census derived from the pod templates and their environment/volume helpers.
ENV_PATHS = (
    "config.extraEnv", "backend.extraEnv", "worker.extraEnv", "worker.queues[].extraEnv",
    "frontend.extraEnv", "serving.extraEnv", "codeExecutor.extraEnv", "fiCollector.extraEnv",
    "secrets.extra", "agentccGateway.extraEnv", "hub.checker.extraEnv", "omega.gateway.extraEnv",
)
OPTIONAL_READERS = (
    "omega.hostEvents.existingSecret", "omega.hostEvents.ca.existingSecret",
    "omega.issuer.privateKey.existingSecret", "omega.internalTls.existingSecret",
    "omega.gateway.jwks.existingSecret", "omega.gateway.upstreamCa.existingSecret",
    "omega.gateway.otlp.credentials.existingSecret", "omega.gateway.rateLimitRedis.existingSecret",
    "hub.checker.tokenSecret", "hub.checker.runner.tlsSecret",
)
SHARED_READERS = (
    "secrets.existingSecret", "license.existingSecret", "config.email.existingSecret",
    "auth.google.existingSecret", "auth.github.existingSecret", "auth.microsoft.existingSecret",
    "bootstrap.admin.existingSecret", "postgres.existingSecret", "clickhouse.existingSecret",
    "redis.existingSecret", "objectStorage.existingSecret", "global.caBundle.secret",
)
HOSTED_NAMES = (
    "AGENTCC_DEPLOYMENT_PROFILE", "AGENTCC_HOSTED_ONEKEY_ACTIVATION",
    "AGENTCC_HOSTED_RELEASE_REVISION", "AGENTCC_ALLOW_INSECURE_ADMIN_TOKEN",
    "AGENTCC_ALLOW_PRIVATE_PROVIDER_URLS", "AGENTCC_AUTH_ENABLED", "AGENTCC_SYNC_ON_STARTUP",
    "AGENTCC_SYNC_INTERVAL", "AGENTCC_CONTROL_PLANE_URL", "AGENTCC_CONTROL_PLANE_TOKEN",
    "AGENTCC_MCP_EGRESS_PRIVATE_ALLOW", "AGENTCC_INTERNAL_API_KEY", "AGENTCC_ADMIN_TOKEN",
    "AGENTCC_WEBHOOK_SECRET", "INTEGRATION_ENCRYPTION_KEY", "NO_STARTUP_DB_MUTATIONS",
    "STARTUP_DB_MUTATION_MODE", "REGISTER_TEMPORAL_SCHEDULES", "DJANGO_CACHE_BACKEND",
    "AGENTCC_REDIS_ADDRESS", "AGENTCC_REDIS_DB", "AGENTCC_REDIS_PASSWORD",
    "AGENTCC_TEST_SYNTHETIC_ENDPOINT", "INTEGRATIONS_TEST_SYNTHETIC_ENDPOINT",
)


def optional_products():
    return {
        "omega.enabled": True,
        "omega.issuer.url": "https://issuer.example.com",
        "omega.issuer.keyId": "synthetic-issuer",
        "omega.issuer.privateKey.existingSecret": "synthetic-issuer",
        "omega.hostEvents.url": "https://omega.example.com/host/events",
        "omega.hostEvents.keyId": "synthetic-events",
        "omega.hostEvents.existingSecret": "synthetic-events",
        "omega.hostEvents.secretKey": "OPENAI_API_KEY",
        "omega.hostEvents.ca.existingSecret": "synthetic-events-ca",
        "omega.internalTls.enabled": True,
        "omega.internalTls.existingSecret": "synthetic-tls",
        "omega.gateway.image.tag": "synthetic",
        "omega.gateway.ee.url": "https://omega.example.com",
        "omega.gateway.jwks.existingSecret": "synthetic-jwks",
        "omega.gateway.jwks.existingConfigMap": "",
        "omega.gateway.rateLimitRedis.existingSecret": "synthetic-omega-redis",
        "omega.gateway.upstreamCa.existingSecret": "synthetic-upstream-ca",
        "omega.gateway.upstreamCa.existingConfigMap": "",
        "omega.gateway.otlp.tracesUrl": "https://collector.example.com/v1/traces",
        "omega.gateway.otlp.credentials.existingSecret": "synthetic-otlp",
        "omega.gateway.serviceAccount.create": True,
        "ingress.enabled": True,
        "ingress.app.host": "app.example.com",
        "ingress.api.host": "api.example.com",
        "omega.gateway.allowedOrigins": ["https://app.example.com"],
        "hub.enabled": True,
        "hub.checker.image.tag": "synthetic",
        "hub.checker.tokenSecret": "synthetic-hub",
        "hub.checker.runner.image": "synthetic-runner",
        "hub.checker.runner.dockerHost": "tcp://runner.example.com:2376",
        "hub.checker.runner.tlsSecret": "synthetic-runner-tls",
        "serving.enabled": True,
        "codeExecutor.enabled": True,
        "backend.collectStatic": True,
        "worker.exactAggregation.enabled": True,
    }


def hosted_values():
    return gateway_only({
        "hosted.enabled": True,
        "billing.enforcement": "true",
        "secrets.existingSecret": "synthetic-app-keys",
        "hosted.releaseRevision": "1" * 40,
        "hosted.singleReplica": True,
        "agentccGateway.replicas": 1,
        "agentccGateway.redis.enabled": "true",
        "agentccGateway.controlPlaneSync": True,
        "agentccGateway.allowPrivateProviderURLs": False,
        "networkPolicy.enabled": True,
        "networkPolicy.ingressFrom": [{"namespaceSelector": {"matchLabels": {"synthetic": "ingress"}}}],
        "backend.image.digest": "sha256:" + "1" * 64,
        "agentccGateway.image.digest": "sha256:" + "2" * 64,
        "agentccGateway.config.auth.enabled": True,
        "agentccGateway.config.guardrails.enabled": True,
        "agentccGateway.config.server.trusted_proxies": ["10.0.0.0/8"],
        "agentccGateway.config.server.trusted_hops": 0,
    })


def env_input(path, name):
    if path == "worker.queues[].extraEnv":
        return {"worker.queues": [{"name": "tasks_s", "extraEnv": {name: SENTINEL}}, {"name": "tasks_l"}]}
    return {path: {name: SENTINEL}}


def source_census(sources):
    # Fingerprint pod producers and helpers, not values, fixtures or Secret objects.
    # Quoting scalar emitters is intentionally irrelevant to the input census.
    return {
        name: hashlib.sha256(text.replace(" | quote", "").encode()).hexdigest()
        for name, text in sources.items()
        if re.search(r"^kind: (?:Deployment|StatefulSet|DaemonSet|Job|CronJob|Pod)\s*$", text, re.M)
        or (name.endswith(".tpl") and name not in {
            "_provider_credentials.tpl", "_provider_config.tpl", "omega/_validate.tpl",
        })
    }


POD_SOURCE_CENSUS = {
    "_billing.tpl": "51c44b0f9917c9f0a66d7d26e5279d45fab10436bb221fd88270c24370e4afd8",
    "_enterprise.tpl": "07444b2bb27529c1515bec79daf6bf22fba0e7ca4352107cdc3388bf281171a6",
    "_env.tpl": "882f0d29aa43c7bab2d71e56b95815714bf1e0bc01f2ca3bbb652ad6c02f4c0f",
    "_helpers.tpl": "7e70db11712704468d50cc5fac34d30130737e0fe4108a5ec7e2b380f252ad60",
    "_scaling.tpl": "5db2c96350fe3385431f4d443fda099630d208770343791b6e5a4f6a63762350",
    "agentcc-connectors/_helpers.tpl": "390fc05df788e06d058b0c661206929c636d306bdda06d2ddd76f52f102c5b3d",
    "agentcc-connectors/deployment.yaml": "9d1be4de30fce0139925e2cf491ca114e3fdf58d96e0ae8811ac77b8531878ca",
    "agentcc-gateway/deployment.yaml": "c2c70502daffe56d11b70d5cde332a61ca8ecff0413b4f662c7f33769800f860",
    "backend/deployment.yaml": "92ae5261da62ec3aa9ab52a6cf53e10bd5f7be74f3b856ec018452506ae2c670",
    "bootstrap/job.yaml": "e9668762aac85aebc448fa617f26195383d61bbae31c495e73563ee3dea3a2ca",
    "code-executor/deployment.yaml": "c47f7b187dfe2f342832e4d26efb5ede0abac1846c9833e4edff34b64264c43c",
    "datastores/clickhouse.yaml": "2c48abbe525666b5291388c7f9028bde16cd3a653ac2fee5e23499ecd7d91a8c",
    "datastores/minio.yaml": "b60b26f3a2786bc062b3ad7eb8c5b72f43a80f7bf307972799ca8f06cefd7aa3",
    "datastores/postgres.yaml": "0cb727c244b6bcd72370d0a838b4a2828d26cc44f1e51b4eb5f2bc59bc1450fe",
    "datastores/redis.yaml": "82fb939c2d67e8e571b3f4660200b3f4f72bdbd6e6fe69bb45437e5063ccb50b",
    "datastores/temporal.yaml": "39cdeb9a48375f44a5883d8806c1daab7cfaab89bb435c59be239177407a9540",
    "fi-collector/deployment.yaml": "f142d2e708e9d7085667bb8222857ab522bac97d4bd2f7c88b7bd92c5ff33635",
    "frontend/deployment.yaml": "effd833986bc2db16484bc932116f0f5a0a2ca648da4c1797c35123644f0d4a0",
    "hosted-migration-job.yaml": "b2bcb34997ca4ea3bb37eb7554068de82f0221e50b57627f6b2576d2d0172be9",
    "hub/_hub.tpl": "cd2ae7732d7df5f2a35effb3ff4a2523cb1e492bba38bfa113057b2949d8429b",
    "hub/checker.yaml": "fa168b33105e2bdc8bd99b029c72c58b479fa2409d1f77766b3aae091831946c",
    "omega/_omega.tpl": "5aea28be2351d1a2544ac0fb070ea70580226ef3badb58dd9910ac42939288eb",
    "omega/gateway.yaml": "0c4f28452464aaa18c91d3663469648acdf873aedd69ddc9bcea5b44701b7ff3",
    "serving/deployment.yaml": "391f6c10f4b35ed423888f857c47fb818571e72b0a6347fa1a1e7bf9bf997c5c",
    "tests/test-connection.yaml": "73cebf0013009148907f375ff0a099a1db28f6b7e8a181719a54c057887d4305",
    "worker/deployment.yaml": "78bbc3d15b0c8a13e859696edf57a3e4ecc63ec14f4aae7f1dccb078fcd0052b",
}


class ProviderCredentialRenderTests(unittest.TestCase):
    def render(self, values, fixtures=BUNDLED):
        run = helm_template(values, fixtures)
        self.assertTrue(run.returncode == 0, "the chart refused a render this test expects: " + masked(run.stderr))
        return [doc for doc in yaml.safe_load_all(run.stdout) if doc]

    def assert_refused(self, values, *messages, fixtures=BUNDLED):
        run = helm_template(values, fixtures)
        self.assertFalse(run.stdout.strip(), "refusal emitted a partial manifest")
        self.assertTrue(run.returncode != 0, "the chart rendered despite: " + "; ".join(messages))
        self.assertTrue(SENTINEL not in run.stdout + run.stderr, "Helm's output carries the synthetic value")
        for message in messages:
            self.assertTrue(message in run.stderr, "missing refusal: " + message)

    def specs(self, docs):
        return dict(pod_specs(docs))

    def find(self, specs, suffix):
        matches = [spec for where, spec in specs.items() if where.endswith(suffix)]
        self.assertTrue(len(matches) == 1, f"expected exactly one {suffix}")
        return matches[0]

    # ---- WP-D1: the gateway-only path ----

    def test_self_host_default_unchanged(self):
        # Without gatewayOnly, secrets.llm still reaches the gateway and the API (built-in evals).
        docs = self.render({"secrets.llm.openaiApiKey": SENTINEL, "secrets.llm.anthropicApiKey": SENTINEL})
        specs = self.specs(docs)
        backend = {entry["name"] for entry in env_of(self.find(specs, "Deployment/futureagi-backend"), "backend")}
        gateway = {entry["name"] for entry in env_of(self.find(specs, "Deployment/" + GATEWAY), GATEWAY_CONTAINER)}
        for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
            self.assertTrue(name in backend, "self-host: the API no longer reads " + name)
            self.assertTrue(name in gateway, "self-host: the gateway no longer reads " + name)

    def test_gateway_only_names_reach_only_gateway(self):
        names = FIXED_NAMES | set(KEYS)
        for fixtures in (BUNDLED, ALL_COMPONENTS):
            with self.subTest(fixtures=fixtures[-1]):
                docs = self.render(gateway_only({"backend.collectStatic": True}), fixtures)
                seen_gateway = False
                for where, spec in pod_specs(docs):
                    if where == "Deployment/" + GATEWAY:
                        seen_gateway = True
                        env = env_of(spec, GATEWAY_CONTAINER)
                        entries = {entry["name"]: entry for entry in env}
                        self.assertTrue(len(entries) == len(env), "the gateway has duplicate env names")
                        for key in KEYS:
                            ref = secret_ref(entries.get(key))
                            self.assertTrue(
                                ref.get("name") == SECRET and ref.get("key") == key and not ref.get("optional"),
                                f"the gateway does not read {key} from the provider Secret by a required reference",
                            )
                        for name in names & set(entries):
                            self.assertTrue(
                                secret_ref(entries[name]).get("name") == SECRET,
                                f"the gateway reads {name} from somewhere other than the provider Secret",
                            )
                        # Only the gateway container itself: no init container or sidecar.
                        others = dict(spec, volumes=[])
                        others["containers"] = [c for c in spec.get("containers") or [] if c.get("name") != GATEWAY_CONTAINER]
                        leaked = exposures(others, names)
                        self.assertTrue(not leaked, f"{where} has another container that can see provider credentials")
                        continue
                    leaked = exposures(spec, names)
                    self.assertTrue(not leaked, f"{where} can see provider credentials: {', '.join(leaked)}")
                self.assertTrue(seen_gateway, "no gateway Deployment was rendered")

    def test_new_path_wins_over_secrets_llm_without_gateway_only(self):
        keys = ["OPENAI_API_KEY", "OPENROUTER_API_KEY", "GEMINI_API_KEY"]
        docs = self.render(
            {
                "agentccGateway.providerCredentials.existingSecret": SECRET,
                "agentccGateway.providerCredentials.keys": keys,
                "secrets.llm.openaiApiKey": SENTINEL,
                # secrets.llm.googleApiKey reaches the gateway as GEMINI_API_KEY.
                "secrets.llm.googleApiKey": SENTINEL,
            }
        )
        specs = self.specs(docs)
        gateway = env_of(self.find(specs, "Deployment/" + GATEWAY), GATEWAY_CONTAINER)
        for key in keys:
            refs = [secret_ref(entry) for entry in gateway if entry["name"] == key]
            self.assertTrue(len(refs) == 1, f"the gateway has {len(refs)} {key} entries, expected one")
            self.assertTrue(refs[0].get("name") == SECRET and not refs[0].get("optional"), f"{key}: not the provider Secret")
        backend = {entry["name"]: entry for entry in env_of(self.find(specs, "Deployment/futureagi-backend"), "backend")}
        for key in ("OPENROUTER_API_KEY", "GEMINI_API_KEY"):
            self.assertTrue(key not in backend, "the API reads a gateway-only key: " + key)
        self.assertTrue(secret_ref(backend.get("OPENAI_API_KEY")).get("name") != SECRET, "the API reads the provider Secret")

    def test_keys_beside_gateway_extra_env_render_once(self):
        # Without gatewayOnly a name in both keys and agentccGateway.extraEnv
        # renders once, the extraEnv value winning as every extraEnv does.
        docs = self.render(
            {
                "agentccGateway.providerCredentials.existingSecret": SECRET,
                "agentccGateway.providerCredentials.keys": ["OPENAI_API_KEY", "CUSTOM_PROVIDER_TOKEN"],
                "agentccGateway.extraEnv": {"CUSTOM_PROVIDER_TOKEN": "synthetic-plain"},
            }
        )
        gateway = env_of(self.find(self.specs(docs), "Deployment/" + GATEWAY), GATEWAY_CONTAINER)
        names = [entry["name"] for entry in gateway]
        self.assertTrue(len(names) == len(set(names)), "the gateway has duplicate env names")
        entries = {entry["name"]: entry for entry in gateway}
        self.assertTrue(
            entries.get("CUSTOM_PROVIDER_TOKEN", {}).get("value") == "synthetic-plain",
            "agentccGateway.extraEnv no longer wins over agentccGateway.providerCredentials.keys",
        )
        self.assertTrue(secret_ref(entries.get("OPENAI_API_KEY")).get("name") == SECRET, "OPENAI_API_KEY: not the provider Secret")

    # ---- WP-D2: refusals ----

    def test_refuses_inline_secrets_llm(self):
        for field in LLM_FIELDS:
            with self.subTest(field=field):
                self.assert_refused(gateway_only({f"secrets.llm.{field}": SENTINEL}), REFUSED_INLINE.format(field))

    def test_refuses_secrets_llm_existing_secret(self):
        self.assert_refused(gateway_only({"secrets.llm.existingSecret": "synthetic-llm"}), REFUSED_LLM_SECRET)

    def test_refuses_provider_names_in_gateway_extra_env(self):
        for name in ("OPENAI_API_KEY", "CUSTOM_PROVIDER_TOKEN"):
            with self.subTest(name=name):
                self.assert_refused(
                    gateway_only({"agentccGateway.extraEnv": {name: SENTINEL}}), REFUSED_GATEWAY_ENV.format(name)
                )

    def test_refuses_provider_names_on_non_gateway_paths(self):
        queues = [{"name": "tasks_s"}, {"name": "tasks_l", "extraEnv": {"OPENROUTER_API_KEY": SENTINEL}}]
        cases = [
            ({"config.extraEnv": {"ANTHROPIC_API_KEY": SENTINEL}}, "config.extraEnv.ANTHROPIC_API_KEY"),
            ({"backend.extraEnv": {"OPENAI_API_KEY": SENTINEL}}, "backend.extraEnv.OPENAI_API_KEY"),
            ({"worker.extraEnv": {"GEMINI_API_KEY": SENTINEL}}, "worker.extraEnv.GEMINI_API_KEY"),
            ({"worker.queues": queues}, "worker.queues[1].extraEnv.OPENROUTER_API_KEY"),
            ({"frontend.extraEnv": {"XAI_API_KEY": SENTINEL}}, "frontend.extraEnv.XAI_API_KEY"),
            ({"serving.extraEnv": {"GROQ_API_KEY": SENTINEL}}, "serving.extraEnv.GROQ_API_KEY"),
            ({"codeExecutor.extraEnv": {"AWS_SECRET_ACCESS_KEY": SENTINEL}}, "codeExecutor.extraEnv.AWS_SECRET_ACCESS_KEY"),
            ({"fiCollector.extraEnv": {"DEEPGRAM_API_KEY": SENTINEL}}, "fiCollector.extraEnv.DEEPGRAM_API_KEY"),
            ({"secrets.extra": {"COHERE_API_KEY": SENTINEL}}, "secrets.extra.COHERE_API_KEY"),
            ({"config.extraEnv": {"CUSTOM_PROVIDER_TOKEN": SENTINEL}}, "config.extraEnv.CUSTOM_PROVIDER_TOKEN"),
        ]
        for extra, path in cases:
            with self.subTest(path=path):
                self.assert_refused(gateway_only(extra), REFUSED_NON_GATEWAY.format(path))

    def test_refuses_extra_env_from_and_volumes_on_provider_secret(self):
        secret_volume = [{"name": "provider", "secret": {"secretName": SECRET}}]
        projected_volume = [{"name": "provider", "projected": {"sources": [{"secret": {"name": SECRET}}]}}]
        cases = [
            ({"config.extraEnvFrom": [{"secretRef": {"name": SECRET}}]}, REFUSED_ENV_FROM),
            ({"backend.extraVolumes": secret_volume}, REFUSED_VOLUME.format("backend")),
            ({"worker.extraVolumes": projected_volume}, REFUSED_VOLUME.format("worker")),
            ({"fiCollector.extraVolumes": secret_volume}, REFUSED_VOLUME.format("fiCollector")),
            ({"bootstrap.extraVolumes": secret_volume}, REFUSED_VOLUME.format("bootstrap")),
        ]
        for extra, message in cases:
            with self.subTest(message=message.split(":")[0]):
                self.assert_refused(gateway_only(extra), message)

    def test_refuses_shared_provider_secret(self):
        self.assert_refused(
            gateway_only({"secrets.existingSecret": SECRET}), REFUSED_SHARED.format("secrets.existingSecret")
        )
        self.assert_refused(
            gateway_only({"agentccGateway.providerCredentials.existingSecret": "futureagi-secrets"}),
            REFUSED_SHARED.format("the chart's own Secret"),
        )
        # The CA bundle Secret is mounted into every pod.
        self.assert_refused(
            gateway_only({"global.caBundle.secret": SECRET}), REFUSED_SHARED.format("global.caBundle.secret")
        )

    def test_refuses_literal_provider_credentials(self):
        cases = [
            ({"agentccGateway.config.providers.openai.api_key": SENTINEL}, ("openai", "api_key")),
            (
                {"agentccGateway.config.providers.openai.api_key": SENTINEL + "${OPENAI_API_KEY}"},
                ("openai", "api_key"),
            ),
            (
                {
                    "agentccGateway.config.providers.bedrock": {
                        "aws_access_key_id": "${BEDROCK_AWS_ACCESS_KEY_ID}",
                        "aws_secret_access_key": SENTINEL,
                    }
                },
                ("bedrock", "aws_secret_access_key"),
            ),
            ({"agentccGateway.config.providers.bedrock": {"aws_session_token": SENTINEL}}, ("bedrock", "aws_session_token")),
            ({"agentccGateway.config.providers.vertex": {"service_account_json": SENTINEL}}, ("vertex", "service_account_json")),
        ]
        for extra, (provider, field) in cases:
            with self.subTest(provider=provider, field=field):
                self.assert_refused(gateway_only(extra), refused_literal(provider, field))

    def test_refuses_secret_uri_outside_api_key(self):
        cases = [
            ({"agentccGateway.config.providers.bedrock": {"aws_secret_access_key": "vault://secret/data/synthetic#k"}}, ("bedrock", "aws_secret_access_key")),
            ({"agentccGateway.config.providers.vertex": {"service_account_json": "gcp-sm://synthetic-sa"}}, ("vertex", "service_account_json")),
        ]
        for extra, (provider, field) in cases:
            with self.subTest(provider=provider, field=field):
                self.assert_refused(gateway_only(extra), refused_literal(provider, field))

    def test_accepts_references_and_secret_uris(self):
        docs = self.render(
            gateway_only(
                {
                    "agentccGateway.config.providers.anthropic": {"api_key": "$ANTHROPIC_API_KEY"},
                    "agentccGateway.config.providers.openrouter": {"api_key": "vault://secret/data/synthetic#api_key"},
                    "agentccGateway.serviceAccount.create": True,
                    "agentccGateway.config.providers.bedrock": {
                        "aws_access_key_id": "${BEDROCK_AWS_ACCESS_KEY_ID}",
                        "aws_secret_access_key": "${BEDROCK_AWS_SECRET_ACCESS_KEY}",
                    },
                }
            )
        )
        self.find(self.specs(docs), "Deployment/" + GATEWAY)

    def test_refuses_unlisted_reference(self):
        for reference in ("${UNLISTED_PROVIDER_KEY}", "$UNLISTED_PROVIDER_KEY"):
            with self.subTest(form=reference[:2]):
                self.assert_refused(
                    gateway_only({"agentccGateway.config.providers.openai.api_key": reference}),
                    REFUSED_UNLISTED.format("openai", "api_key"),
                )

    def test_refuses_missing_secret_reference(self):
        self.assert_refused(gateway_only({"agentccGateway.providerCredentials.existingSecret": ""}), REFUSED_NO_SECRET)
        self.assert_refused(gateway_only({"agentccGateway.providerCredentials.keys": []}), REFUSED_NO_KEYS)
        self.assert_refused({"agentccGateway.providerCredentials.keys": ["OPENAI_API_KEY"]}, REFUSED_KEYS_WITHOUT_SECRET)
        self.assert_refused({"agentccGateway.providerCredentials.existingSecret": SECRET}, REFUSED_SECRET_WITHOUT_KEYS)

    def test_refuses_stdio_mcp_server(self):
        servers = {
            "local": {"transport": "stdio", "command": "/bin/synthetic-mcp"},
            "tools": {"command": "/bin/synthetic-tools"},
        }
        self.assert_refused(
            gateway_only({"agentccGateway.config.mcp": {"enabled": True, "servers": servers}}),
            REFUSED_STDIO_MCP.format("local"),
            REFUSED_STDIO_MCP.format("tools"),
        )
        remote = {"remote": {"transport": "http", "url": "https://mcp.example.com/mcp"}}
        self.render(gateway_only({"agentccGateway.config.mcp": {"enabled": True, "servers": remote}}))

    def test_external_secret_group(self):
        group = {"data": {"OPENAI_API_KEY": {"key": "synthetic/provider", "property": "openai"}}}
        eso = {
            "externalSecrets.enabled": True,
            "externalSecrets.secretStoreRef.name": "synthetic-store",
            "externalSecrets.secrets.providerCredentials": group,
        }
        docs = self.render(gateway_only(eso))
        targets = {
            doc["metadata"]["name"]: doc["spec"]["target"]["name"] for doc in docs if doc["kind"] == "ExternalSecret"
        }
        self.assertTrue(
            targets.get("futureagi-provider-credentials") == SECRET,
            "no ExternalSecret creates agentccGateway.providerCredentials.existingSecret",
        )
        self.assert_refused(eso, REFUSED_ESO_NAME)

    # ---- WP-D3: the gateway's own ServiceAccount ----

    def test_default_service_account_unchanged(self):
        specs = self.specs(self.render({}))
        gateway = self.find(specs, "Deployment/" + GATEWAY)
        backend = self.find(specs, "Deployment/futureagi-backend")
        self.assertTrue(
            effective_service_account(gateway) == effective_service_account(backend),
            "without agentccGateway.serviceAccount the gateway leaves the chart-wide ServiceAccount",
        )

    def test_gateway_service_account(self):
        annotation = {"iam.gke.io/gcp-service-account": "synthetic-gateway@example.iam.gserviceaccount.com"}
        base = self.render({})
        own = self.render(
            {"agentccGateway.serviceAccount.create": True, "agentccGateway.serviceAccount.annotations": annotation}
        )
        accounts = {doc["metadata"]["name"]: doc for doc in own if doc["kind"] == "ServiceAccount"}
        self.assertTrue(GATEWAY in accounts, "no gateway ServiceAccount was rendered")
        self.assertTrue(
            (accounts[GATEWAY]["metadata"].get("annotations") or {}) == annotation,
            "the gateway ServiceAccount does not carry agentccGateway.serviceAccount.annotations",
        )
        # Its token mount follows serviceAccount.automountServiceAccountToken, like the chart-wide one.
        for mounted in (False, True):
            docs = self.render(
                {
                    "agentccGateway.serviceAccount.create": True,
                    "serviceAccount.automountServiceAccountToken": mounted,
                }
            )
            account = next(d for d in docs if d["kind"] == "ServiceAccount" and d["metadata"]["name"] == GATEWAY)
            pod = self.find(self.specs(docs), "Deployment/" + GATEWAY)
            self.assertTrue(
                account.get("automountServiceAccountToken") is mounted and pod.get("automountServiceAccountToken") is mounted,
                f"the gateway ServiceAccount's token mount is not {mounted}",
            )
        own_gateway = self.find(self.specs(own), "Deployment/" + GATEWAY)
        self.assertTrue(own_gateway.get("serviceAccountName") == GATEWAY, "the gateway does not run as its own account")
        dropped = {("Deployment", GATEWAY), ("ServiceAccount", GATEWAY)}
        before, after = comparable(base, dropped), comparable(own, dropped)
        changed = sorted(f"{kind}/{name}" for kind, name in set(before) | set(after) if before.get((kind, name)) != after.get((kind, name)))
        self.assertTrue(not changed, "other documents changed: " + ", ".join(changed))
        base_gateway = next(doc for doc in base if doc["kind"] == "Deployment" and doc["metadata"]["name"] == GATEWAY)
        own_doc = next(doc for doc in own if doc["kind"] == "Deployment" and doc["metadata"]["name"] == GATEWAY)
        own_doc["spec"]["template"]["spec"]["serviceAccountName"] = base_gateway["spec"]["template"]["spec"]["serviceAccountName"]
        self.assertTrue(own_doc == base_gateway, "the gateway Deployment changed beyond its serviceAccountName")

    def test_gateway_existing_service_account(self):
        docs = self.render({"agentccGateway.serviceAccount.name": "synthetic-gateway-sa"})
        gateway = self.find(self.specs(docs), "Deployment/" + GATEWAY)
        self.assertTrue(gateway.get("serviceAccountName") == "synthetic-gateway-sa", "the gateway ignores its account name")
        names = {doc["metadata"]["name"] for doc in docs if doc["kind"] == "ServiceAccount"}
        self.assertTrue("synthetic-gateway-sa" not in names, "an existing account was created by the chart")

    def test_refuses_gateway_service_account_name_collision(self):
        specs = self.specs(self.render({}))
        chart_account = self.find(specs, "Deployment/futureagi-backend").get("serviceAccountName")
        bootstrap_account = self.find(specs, "Job/futureagi-bootstrap").get("serviceAccountName")
        for name in (chart_account, bootstrap_account):
            with self.subTest(name=name):
                self.assert_refused(
                    {"agentccGateway.serviceAccount.create": True, "agentccGateway.serviceAccount.name": name},
                    REFUSED_SA_NAME,
                )
        # The created account's default name, taken by the chart-wide one.
        self.assert_refused(
            {"agentccGateway.serviceAccount.create": True, "serviceAccount.name": GATEWAY}, REFUSED_SA_NAME
        )

    # ---- R2: opaque sources, Vertex, identity and the whole-config expansion ----

    def test_refuses_opaque_python_env_from(self):
        for source in ("secretRef", "configMapRef"):
            with self.subTest(source=source):
                self.assert_refused(
                    gateway_only({"config.extraEnvFrom": [{source: {"name": "synthetic-other"}}]}),
                    "config.extraEnvFrom: opaque sources are refused with agentccGateway.providerCredentials.gatewayOnly",
                )

    def test_refuses_opaque_gateway_sources(self):
        for source in ("secretRef", "configMapRef"):
            with self.subTest(source=source):
                self.assert_refused(
                    gateway_only({"agentccGateway.extraEnvFrom": [{source: {"name": "synthetic-other"}}]}),
                    "agentccGateway.extraEnvFrom: refused with agentccGateway.providerCredentials.gatewayOnly",
                )
        self.assert_refused(
            gateway_only({
                "agentccGateway.existingConfigMap": "synthetic-config",
                "agentccGateway.config.providers.openai.api_key": SENTINEL,
                "agentccGateway.config.mcp.servers": {"local": {"command": "/bin/synthetic"}},
            }),
            "agentccGateway.existingConfigMap: refused with agentccGateway.providerCredentials.gatewayOnly",
        )

    def test_vertex_secret_has_provider_secret_protections(self):
        vertex = "synthetic-vertex"
        base = {"agentccGateway.gcpCredentials.existingSecret": vertex}
        for component in ("backend", "worker", "fiCollector", "bootstrap"):
            for volume in (
                {"name": "vertex", "secret": {"secretName": vertex}},
                {"name": "vertex", "projected": {"sources": [{"secret": {"name": vertex}}]}},
            ):
                with self.subTest(component=component, projected="projected" in volume):
                    self.assert_refused(
                        gateway_only(dict(base, **{component + ".extraVolumes": [volume]})),
                        component + ".extraVolumes: it mounts agentccGateway.gcpCredentials.existingSecret, which only the gateway may read",
                    )
        for path in ("secrets.existingSecret", "redis.existingSecret", "global.caBundle.secret"):
            with self.subTest(shared=path):
                self.assert_refused(
                    gateway_only(dict(base, **{path: vertex})),
                    "agentccGateway.gcpCredentials.existingSecret must name a Secret no other workload reads",
                )
        self.assert_refused(
            gateway_only({"agentccGateway.gcpCredentials.existingSecret": "futureagi-secrets"}),
            "agentccGateway.gcpCredentials.existingSecret must name a Secret no other workload reads",
        )
        self.assert_refused(
            gateway_only(dict(base, **{"config.extraEnvFrom": [{"secretRef": {"name": vertex}}]})),
            "config.extraEnvFrom: it reads agentccGateway.gcpCredentials.existingSecret, which only the gateway may read",
        )

    def test_vertex_mount_remains_gateway_only(self):
        for name in ("synthetic-vertex", SECRET):
            with self.subTest(shared_with_provider_secret=name == SECRET):
                docs = self.render(gateway_only({"agentccGateway.gcpCredentials.existingSecret": name}), ALL_COMPONENTS)
                found = []
                for where, spec in pod_specs(docs):
                    for volume in spec.get("volumes") or []:
                        if (volume.get("secret") or {}).get("secretName") == name:
                            found.append(where)
                self.assertEqual(found, ["Deployment/" + GATEWAY], "Vertex mount escaped the gateway")

    def test_existing_gateway_account_cannot_be_shared(self):
        for path in ("serviceAccount.name", "bootstrap.serviceAccount.name"):
            with self.subTest(path=path):
                self.assert_refused(
                    gateway_only({"agentccGateway.serviceAccount.name": "synthetic-sa", path: "synthetic-sa"}),
                    REFUSED_SA_NAME,
                )
        self.assert_refused(gateway_only({"agentccGateway.serviceAccount.name": "futureagi"}), REFUSED_SA_NAME)
        self.assert_refused(
            gateway_only({"agentccGateway.serviceAccount.name": "default", "serviceAccount.create": False}),
            REFUSED_SA_NAME,
        )

    # R3 / R2-SA-01: implicit-default consumers also share the gateway identity.
    def test_gateway_only_refuses_namespace_default_account(self):
        for fixtures in (BUNDLED, ALL_COMPONENTS):
            for create in (False, True):
                for secret_uri in (False, True):
                    with self.subTest(fixtures=fixtures[-1], create=create, secret_uri=secret_uri):
                        extra = gateway_account_values(create, "default")
                        if not secret_uri:
                            extra["agentccGateway.config.providers.openai.api_key"] = "${OPENAI_API_KEY}"
                        self.assert_refused(gateway_only(extra), REFUSED_DEFAULT_SA, fixtures=fixtures)

    def test_gateway_only_refuses_inherited_default_account(self):
        for fixtures in (BUNDLED, ALL_COMPONENTS):
            with self.subTest(fixtures=fixtures[-1]):
                extra = gateway_account_values(False, "")
                extra["serviceAccount"] = {"create": False, "name": ""}
                extra["agentccGateway.config.providers.openai.api_key"] = "${OPENAI_API_KEY}"
                self.assert_refused(gateway_only(extra), REFUSED_DEFAULT_SA, fixtures=fixtures)

    def test_gateway_only_distinct_account_is_exclusive(self):
        for fixtures in (BUNDLED, ALL_COMPONENTS):
            for create, name in ((False, "synthetic-gateway-sa"), (True, "synthetic-gateway-sa"), (True, "")):
                with self.subTest(fixtures=fixtures[-1], create=create, generated=not name):
                    docs = self.render(gateway_only(gateway_account_values(create, name)), fixtures)
                    specs = self.specs(docs)
                    gateway = self.find(specs, "Deployment/" + GATEWAY)
                    account = effective_service_account(gateway)
                    self.assertTrue(account == (name or GATEWAY), "the gateway lost its dedicated account")
                    self.assertTrue(
                        any(not spec.get("serviceAccountName") for spec in specs.values()),
                        "the fixture no longer exercises implicit-default workloads",
                    )
                    for where, spec in specs.items():
                        if where != "Deployment/" + GATEWAY:
                            self.assertTrue(
                                effective_service_account(spec) != account,
                                "another workload shares the effective gateway ServiceAccount",
                            )

    def test_self_host_namespace_default_account_unchanged(self):
        for fixtures in (BUNDLED, ALL_COMPONENTS):
            for create in (False, True):
                with self.subTest(fixtures=fixtures[-1], create=create):
                    docs = self.render(gateway_account_values(create, "default"), fixtures)
                    gateway = self.find(self.specs(docs), "Deployment/" + GATEWAY)
                    self.assertTrue(
                        effective_service_account(gateway) == "default",
                        "self-host no longer permits the namespace default account",
                    )

    def test_secret_uri_requires_dedicated_gateway_identity(self):
        for scheme in ("vault", "aws-sm", "gcp-sm", "azure-kv"):
            extra = {
                "agentccGateway.config.providers.openai.api_key": scheme + "://synthetic/provider",
                "agentccGateway.serviceAccount.create": False,
            }
            with self.subTest(scheme=scheme):
                self.assert_refused(
                    gateway_only(extra),
                    "agentccGateway.serviceAccount: a dedicated account is required for secret URIs with agentccGateway.providerCredentials.gatewayOnly",
                )
                for account in ({"create": True}, {"name": "synthetic-gateway-sa"}):
                    self.render(gateway_only(dict(extra, **{"agentccGateway.serviceAccount": account})))

    def test_refuses_shared_cloud_identity_annotations(self):
        for key in ("iam.gke.io/gcp-service-account", "eks.amazonaws.com/role-arn", "azure.workload.identity/client-id"):
            for other in ("serviceAccount", "bootstrap.serviceAccount"):
                with self.subTest(key=key, other=other):
                    extra = {
                        "agentccGateway.serviceAccount": {"name": "synthetic-gw", "annotations": {key: SENTINEL}},
                        other + ".annotations": {key: SENTINEL},
                    }
                    self.assert_refused(
                        gateway_only(extra),
                        "agentccGateway.serviceAccount.annotations: cloud identity must not be shared with other workloads",
                    )
        self.render(gateway_only({
            "agentccGateway.serviceAccount": {"create": True, "annotations": {"iam.gke.io/gcp-service-account": "synthetic-gw"}},
            "serviceAccount.annotations": {"iam.gke.io/gcp-service-account": "synthetic-app"},
        }))

    def test_refuses_secret_manager_env_names_on_plain_paths(self):
        # These fallbacks are read in internal/secrets/{aws,azure}.go.
        for name in ("AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET", "AZURE_TENANT_ID", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
            for path in ("backend.extraEnv", "agentccGateway.extraEnv"):
                with self.subTest(name=name, path=path):
                    message = REFUSED_GATEWAY_ENV.format(name) if path.startswith("agentccGateway") else REFUSED_NON_GATEWAY.format(path + "." + name)
                    self.assert_refused(gateway_only({path: {name: SENTINEL}}), message)

    def test_inline_credential_field_census(self):
        # Names from the gateway config structs, including non-provider credentials
        # and dynamic guardrail config. Paths, public keys and URLs are separate policies.
        cases = [
            ("secrets", "vault", "token"), ("secrets", "vault", "app_role_id"),
            ("secrets", "vault", "app_role_secret"), ("secrets", "aws", "access_key_id"),
            ("secrets", "aws", "secret_access_key"), ("secrets", "azure", "tenant_id"),
            ("secrets", "azure", "client_id"), ("secrets", "azure", "client_secret"),
            ("cache", "s3", "access_key_id"), ("cache", "s3", "secret_access_key"),
            ("cache", "azure_blob", "connection_string"), ("cache", "azure_blob", "sas_token"),
            ("cache", "redis", "password"), ("redis", "password"),
            ("cache", "semantic", "qdrant", "api_key"), ("cache", "semantic", "weaviate", "api_key"),
            ("cache", "semantic", "pinecone", "api_key"), ("admin", "token"),
            ("control_plane", "admin_token"), ("control_plane", "webhook_secret"),
            ("mcp", "servers", "remote", "auth", "token"), ("mcp", "servers", "remote", "auth", "key"),
            ("a2a", "agents", "remote", "auth", "token"), ("a2a", "agents", "remote", "auth", "key"),
            ("auth", "keys", 0, "key"), ("guardrails", "rules", 0, "config", "api_key"),
            ("guardrails", "rules", 0, "config", "secret_key"),
            ("guardrails", "rules", 0, "config", "access_key"),
            ("guardrails", "rules", 0, "config", "token"),
            ("guardrails", "rules", 0, "config", "client_id"),
            ("guardrails", "rules", 0, "config", "client_secret"),
        ]
        for parts in cases:
            with self.subTest(path=".".join(map(str, parts))):
                node = SENTINEL
                for part in reversed(parts):
                    node = [node] if part == 0 else {part: node}
                self.assert_refused(
                    gateway_only({"agentccGateway.config": node}),
                    "agentccGateway.config: credential fields must be Secret-backed variable references, never literals",
                )
        # Non-provider credentials may use the same gateway-only Secret, but must be listed.
        self.render(gateway_only({"agentccGateway.config.secrets.vault.token": "${CUSTOM_PROVIDER_TOKEN}"}))
        self.assert_refused(
            gateway_only({"agentccGateway.config.secrets.vault.token": "${UNLISTED_TOKEN}"}),
            "agentccGateway.config: credential reference is not in agentccGateway.providerCredentials.keys or its chart-owned Secret path",
        )

    def test_credential_headers_are_not_inline(self):
        paths = [
            ("providers", "custom", "headers"), ("otel", "headers"),
            ("audit", "sinks", 0, "headers"), ("alerting", "channels", 0, "headers"),
            ("guardrails", "rules", 0, "config", "headers"),
        ]
        for parts in paths:
            for header in ("Authorization", "X-Api-Key", "api-key", "x-goog-api-key", "X-Custom-Token"):
                with self.subTest(path=".".join(map(str, parts)), header=header):
                    node = {header: SENTINEL}
                    for part in reversed(parts):
                        node = [node] if part == 0 else {part: node}
                    self.assert_refused(
                        gateway_only({"agentccGateway.config": node}),
                        "agentccGateway.config: credential fields must be Secret-backed variable references, never literals",
                    )
        self.render(gateway_only({"agentccGateway.config.providers.custom.headers": {"Authorization": "${CUSTOM_PROVIDER_TOKEN}"}}))

    def test_refuses_credential_file_and_url_literals(self):
        for path in ("providers.vertex.credentials_file", "cache.gcs.credentials_file"):
            with self.subTest(path=path):
                self.assert_refused(
                    gateway_only({"agentccGateway.config." + path: SENTINEL}),
                    "agentccGateway.config: credentials_file must use the gateway-only gcpCredentials mount",
                )
        self.render(gateway_only({
            "agentccGateway.gcpCredentials.existingSecret": "synthetic-vertex",
            "agentccGateway.config.providers.vertex.credentials_file": "${GOOGLE_APPLICATION_CREDENTIALS}",
        }))
        self.assert_refused(
            gateway_only({"agentccGateway.config.cluster.redis_url": "redis://user:" + SENTINEL + "@redis:6379"}),
            "agentccGateway.config: inline URL credentials are refused with agentccGateway.providerCredentials.gatewayOnly",
        )

    def test_protected_env_reference_cannot_escape_credential_fields(self):
        for name in ("OPENAI_API_KEY", "CUSTOM_PROVIDER_TOKEN", "AZURE_CLIENT_SECRET"):
            for ref in ("${" + name + "}", "$" + name):
                for config in (
                    {"providers": {"custom": {"base_url": "https://example.invalid/" + ref}}},
                    {"logging": {"format": ref}},
                    {"model_map": {ref: "synthetic"}},
                    {"mcp": {"servers": {"remote": {"args": [ref]}}}},
                    # A dotted map key must not spoof an approved credential path.
                    {"providers.custom.api_key": ref},
                    {"providers": {"custom": {"api_key.more": ref}}},
                ):
                    with self.subTest(name=name, braced="{" in ref, surface=next(iter(config))):
                        self.assert_refused(
                            gateway_only({"agentccGateway.config": config}),
                            "agentccGateway.config: provider credential references are refused outside credential fields",
                        )
        self.render(gateway_only({"agentccGateway.config.providers.custom.base_url": "https://example.invalid/${NON_SECRET_HOST}"}))

    def test_chart_owned_references_cannot_be_plain_env_overrides(self):
        base = {
            "agentccGateway.controlPlaneSync": True,
            "agentccGateway.config.control_plane.admin_token": "${AGENTCC_CONTROL_PLANE_TOKEN}",
        }
        self.render(gateway_only(base))
        for name, path in (("AGENTCC_ADMIN_TOKEN", "admin.token"), ("AGENTCC_CONTROL_PLANE_TOKEN", "control_plane.admin_token")):
            with self.subTest(name=name):
                self.assert_refused(
                    gateway_only({
                        "agentccGateway.controlPlaneSync": True,
                        "agentccGateway.config." + path: "${" + name + "}",
                        "agentccGateway.extraEnv": {name: SENTINEL},
                    }),
                    "agentccGateway.config: credential reference is not in agentccGateway.providerCredentials.keys or its chart-owned Secret path",
                )
        self.assert_refused(
            gateway_only({
                "agentccGateway.gcpCredentials.existingSecret": "synthetic-vertex",
                "agentccGateway.config.providers.vertex.credentials_file": "${GOOGLE_APPLICATION_CREDENTIALS}",
                "agentccGateway.extraEnv": {"GOOGLE_APPLICATION_CREDENTIALS": SENTINEL},
            }),
            "agentccGateway.config: credentials_file must use the gateway-only gcpCredentials mount",
        )

    def test_hosted_merge_must_require_gateway_only(self):
        # No hosted block at this base. When it lands, exercise the actual render
        # (not a text search that a comment could satisfy).
        if (CHART / "templates/hosted-validation.yaml").exists():
            self.assert_refused(
                gateway_only({"hosted.enabled": True, SWITCH: False}),
                "hosted.enabled requires agentccGateway.providerCredentials.gatewayOnly=true",
            )

    # ---- c2.5 C2-C6 / P2 / R1-001..003: actual enabled consumers ----

    def test_optional_products_positive_exclusivity(self):
        docs = self.render(gateway_only(optional_products()))
        specs = self.specs(docs)
        for suffix in ("Deployment/futureagi-omega-gateway", "Deployment/futureagi-hub-checker"):
            self.find(specs, suffix)
        account = effective_service_account(self.find(specs, "Deployment/" + GATEWAY))
        for where, spec in specs.items():
            if where != "Deployment/" + GATEWAY:
                self.assertFalse(exposures(spec, FIXED_NAMES | set(KEYS)), "provider exposure on " + where)
                self.assertNotEqual(effective_service_account(spec), account, "shared gateway identity")
        # The ordinary self-host path still supports optional products.
        self.render(optional_products())

    def test_optional_secret_reader_custody(self):
        for credential in ("providerCredentials", "gcpCredentials"):
            for path in OPTIONAL_READERS:
                for spelling in (SECRET, " " + SECRET + " ", '"' + SECRET + '"'):
                    with self.subTest(credential=credential, path=path, spelling=spelling):
                        values = gateway_only(optional_products())
                        values["agentccGateway." + credential + ".existingSecret"] = SECRET
                        values[path] = spelling
                        self.assert_refused(values, "must name a Secret no other workload reads", path)

    def test_optional_secret_fallbacks(self):
        values = gateway_only(optional_products())
        values.update({"omega.hostEvents.ca.existingSecret": "", "omega.gateway.upstreamCa.existingSecret": SECRET})
        self.assert_refused(values, "must name a Secret no other workload reads", "omega.gateway.upstreamCa.existingSecret")
        values = gateway_only(optional_products())
        values.update({"omega.internalTls.existingSecret": "", "omega.internalTls.certManager.issuerRef.name": "synthetic-issuer",
                       "agentccGateway.providerCredentials.existingSecret": "futureagi-backend-omega-tls"})
        self.assert_refused(values, "must name a Secret no other workload reads", "omega.internalTls.existingSecret")

    def test_env_name_spellings_every_path(self):
        for path in ENV_PATHS:
            for name in ("OPENAI_API_KEY", " OPENAI_API_KEY ", '"OPENAI_API_KEY"', "'OPENAI_API_KEY'", "OpenAi_Api_Key"):
                with self.subTest(path=path, spelling=name):
                    values = gateway_only(optional_products())
                    values.update(env_input(path, name))
                    message = REFUSED_GATEWAY_ENV.format("OPENAI_API_KEY") if path == "agentccGateway.extraEnv" else REFUSED_NON_GATEWAY.format(path.replace("[]", "[0]") + ".OPENAI_API_KEY")
                    self.assert_refused(values, message)

    def test_env_yaml_syntax_refused(self):
        for path in ENV_PATHS:
            for name in ("OPENAI_API_KEY # synthetic", "OPENAI_API_KEY\n  synthetic: injected"):
                with self.subTest(path=path, multiline="\n" in name):
                    values = gateway_only(optional_products())
                    values.update(env_input(path, name))
                    self.assert_refused(values, "environment names must be plain variable names")

    def test_env_names_serialized_literally(self):
        # Without custody, arbitrary names cannot become YAML syntax or another name.
        for path in ENV_PATHS:
            if path == "secrets.extra":
                continue
            with self.subTest(path=path):
                values = optional_products()
                values.update(env_input(path, '"SYNTHETIC_NAME"'))
                docs = self.render(values)
                names = [entry["name"] for _, spec in pod_specs(docs) for c in containers(spec) for entry in c.get("env", [])]
                self.assertIn('"SYNTHETIC_NAME"', names)
                self.assertNotIn("SYNTHETIC_NAME", names)

    def test_secret_name_spellings_every_volume(self):
        for credential in ("providerCredentials", "gcpCredentials"):
            for component in ("backend", "worker", "fiCollector", "bootstrap"):
                for projected in (False, True):
                    for spelling in (" " + SECRET + " ", '"' + SECRET + '"'):
                        with self.subTest(credential=credential, component=component, projected=projected, spelling=spelling):
                            volume = {"name": "synthetic"}
                            volume.update({"projected": {"sources": [{"secret": {"name": spelling}}]}} if projected else {"secret": {"secretName": spelling}})
                            self.assert_refused(gateway_only({"agentccGateway." + credential + ".existingSecret": SECRET, component + ".extraVolumes": [volume]}), component + ".extraVolumes: it mounts agentccGateway." + credential + ".existingSecret")

    def test_secret_name_spellings_env_from(self):
        for credential in ("providerCredentials", "gcpCredentials"):
            with self.subTest(credential=credential):
                self.assert_refused(gateway_only({"agentccGateway." + credential + ".existingSecret": " " + SECRET + " ", "config.extraEnvFrom": [{"secretRef": {"name": SECRET}}]}), "config.extraEnvFrom: it reads agentccGateway." + credential + ".existingSecret")

    def test_shared_secret_name_spellings(self):
        for path in SHARED_READERS:
            for spelling in (" " + SECRET + " ", '"' + SECRET + '"'):
                with self.subTest(path=path, spelling=spelling):
                    self.assert_refused(gateway_only({path: spelling}), REFUSED_SHARED.format(path))

    def test_secret_name_syntax_and_exact_match(self):
        for path in SHARED_READERS + OPTIONAL_READERS + ("agentccGateway.providerCredentials.existingSecret", "agentccGateway.gcpCredentials.existingSecret"):
            with self.subTest(path=path):
                values = gateway_only(optional_products())
                values[path] = "Synthetic-Invalid"
                self.assert_refused(values, "Secret names must be DNS subdomains")
        values = gateway_only(optional_products())
        for path in OPTIONAL_READERS:
            values[path] = SECRET + "-other"
        self.render(values)
        for path in ENV_PATHS:
            with self.subTest(path=path):
                values = gateway_only(optional_products())
                values.update(env_input(path, "OPENAI_API_KEY_SUFFIX"))
                self.render(values)

    def test_effective_accounts_every_path(self):
        for path in ("serviceAccount", "bootstrap.serviceAccount", "omega.gateway.serviceAccount"):
            for create in (False, True):
                for spelling in ("synthetic-gateway", " synthetic-gateway ", '"synthetic-gateway"'):
                    with self.subTest(path=path, create=create, spelling=spelling):
                        values = gateway_only(optional_products())
                        values.update({"agentccGateway.serviceAccount.name": "synthetic-gateway", path + ".create": create, path + ".name": spelling})
                        self.assert_refused(values, "agentccGateway.serviceAccount.name must differ")
        self.assert_refused(gateway_only({"agentccGateway.serviceAccount.create": False}), REFUSED_SA_NAME)

    def test_default_account_spellings(self):
        for spelling in (" default ", '"default"', "'default'"):
            with self.subTest(spelling=spelling):
                self.assert_refused(gateway_only({"agentccGateway.serviceAccount.name": spelling}), REFUSED_DEFAULT_SA)
        for path in ("agentccGateway", "omega.gateway", "bootstrap", ""):
            key = (path + "." if path else "") + "serviceAccount.name"
            with self.subTest(path=key):
                values = gateway_only(optional_products())
                values[key] = "Synthetic-Invalid"
                self.assert_refused(values, "ServiceAccount names must be DNS subdomains")

    def test_omega_cloud_identity_custody(self):
        for key in ("iam.gke.io/gcp-service-account", "eks.amazonaws.com/role-arn", "azure.workload.identity/client-id"):
            for spelling in ("synthetic-cloud-identity", " synthetic-cloud-identity "):
                with self.subTest(annotation=key, padded=spelling.startswith(" ")):
                    values = gateway_only(optional_products())
                    values.update({"agentccGateway.serviceAccount.annotations": {key: "synthetic-cloud-identity"}, "omega.gateway.serviceAccount.annotations": {key: spelling}})
                    self.assert_refused(values, "agentccGateway.serviceAccount.annotations: cloud identity must not be shared with other workloads")
        values = gateway_only(optional_products())
        values.update({"agentccGateway.serviceAccount.annotations": {"iam.gke.io/gcp-service-account": "synthetic-gateway"}, "omega.gateway.serviceAccount.annotations": {"iam.gke.io/gcp-service-account": "synthetic-omega"}})
        self.render(values)

    def test_hosted_valid_baseline_requires_custody(self):
        values = hosted_values()
        docs = self.render(values)
        gateway = env_of(self.find(self.specs(docs), "Deployment/" + GATEWAY), GATEWAY_CONTAINER)
        entries = {entry["name"]: entry for entry in gateway}
        self.assertEqual(entries["AGENTCC_DEPLOYMENT_PROFILE"].get("value"), "hosted")
        self.assertEqual(entries["AGENTCC_AUTH_ENABLED"].get("value"), "true")
        self.assertEqual(entries["AGENTCC_SYNC_INTERVAL"].get("value"), "60s")
        self.assert_refused(dict(values, **{SWITCH: False}), "hosted.enabled requires agentccGateway.providerCredentials.gatewayOnly=true")

    def test_hosted_provider_names_cannot_override_pins(self):
        for name in HOSTED_NAMES:
            for spelling in (name, name.lower(), " " + name.lower() + " ", '"' + name + '"'):
                with self.subTest(name=name, spelling=spelling):
                    values = hosted_values()
                    values["agentccGateway.providerCredentials.keys"] = KEYS + [spelling]
                    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", spelling):
                        self.assert_refused(values, "hosted refuses safety-setting or test-endpoint providerCredentials.keys")
                    else:
                        # The schema refuses the malformed name. Helm 3.18+ and Helm 4 word schema
                        # errors differently from older Helm 3 ("at '/agentccGateway/providerCredentials/keys/6':
                        # ... does not match pattern" vs "agentccGateway.providerCredentials.keys.6: Does not match pattern").
                        run = helm_template(values, BUNDLED)
                        self.assertFalse(run.stdout.strip(), "refusal emitted a partial manifest")
                        self.assertTrue(run.returncode != 0, "the chart rendered a malformed providerCredentials.keys entry")
                        self.assertTrue(SENTINEL not in run.stdout + run.stderr, "Helm's output carries the synthetic value")
                        self.assertRegex(run.stderr, r"agentccGateway[./]providerCredentials[./]keys")
                        self.assertRegex(run.stderr, r"(?i)does not match pattern")

    def test_hosted_reserved_normalization_without_schema(self):
        # Older Helm lacks --skip-schema-validation. Render the actual helper
        # in a minimal synthetic chart (no defaults/Secret files copied) to
        # exercise admission independently of the production schema.
        with tempfile.TemporaryDirectory(prefix="custody-names-") as directory:
            chart = Path(directory)
            (chart / "templates").mkdir()
            (chart / "Chart.yaml").write_text("apiVersion: v2\nname: custody-names\nversion: 0.0.1\n")
            (chart / "templates/_custody.tpl").write_text((CHART / "templates/_provider_credentials.tpl").read_text())
            (chart / "templates/probe.yaml").write_text('{{- if include "futureagi.providerCredentials.hostedReserved" .Values.name -}}{{ fail "synthetic reserved name refused" }}{{- end -}}')
            for name in HOSTED_NAMES:
                for spelling in (name, " " + name.lower() + " ", '"' + name + '"', "'" + name + "'"):
                    with self.subTest(name=name, spelling=spelling):
                        run = subprocess.run([os.environ.get("HELM", "helm"), "template", "synthetic", str(chart), "--set-json", "name=" + json.dumps(spelling)], capture_output=True, text=True, check=False)
                        self.assertNotEqual(run.returncode, 0)
                        self.assertIn("synthetic reserved name refused", run.stderr)
                        self.assertFalse(run.stdout.strip())
            run = subprocess.run([os.environ.get("HELM", "helm"), "template", "synthetic", str(chart), "--set", "name=CUSTOM_PROVIDER_TOKEN"], capture_output=True, text=True, check=False)
            self.assertEqual(run.returncode, 0, "non-reserved provider name refused")

    def test_lowercase_custom_credential_stays_protected(self):
        for config in ({"logging": {"format": "${synthetic_token}"}}, {"model_map": {"${synthetic_token}": "synthetic"}}):
            self.assert_refused(gateway_only({"agentccGateway.providerCredentials.keys": KEYS + ["synthetic_token"], "agentccGateway.config": config}), "agentccGateway.config: provider credential references are refused outside credential fields")

    def test_pod_source_census(self):
        sources = {str(p.relative_to(CHART / "templates")): p.read_text() for p in (CHART / "templates").rglob("*") if p.suffix in (".tpl", ".yaml") and p.name not in ("secret.yaml", "secrets.yaml", "external-secrets.yaml")}
        self.assertEqual(source_census(sources), POD_SOURCE_CENSUS, "pod input surface changed: derive paths and add custody controls before updating census")

    def test_census_detects_new_workload_input(self):
        source = {"synthetic.yaml": "kind: Deployment\n{{ .Values.backend.extraEnv }}"}
        changed = {"synthetic.yaml": source["synthetic.yaml"] + "\n{{ .Values.backend.newEnv }}"}
        self.assertNotEqual(source_census(source), source_census(changed))
        self.assertNotEqual(source_census(source), source_census(dict(source, **{"new.yaml": "kind: Job\n{{ .Values.new.env }}"})))

    def test_self_host_keeps_opaque_sources(self):
        self.render({
            "config.extraEnvFrom": [{"secretRef": {"name": "synthetic-other"}}],
            "agentccGateway.extraEnvFrom": [{"configMapRef": {"name": "synthetic-gw"}}],
            "agentccGateway.existingConfigMap": "synthetic-config",
        })


if __name__ == "__main__":
    unittest.main(verbosity=2)
