"""omega.hostEvents: the FA -> Omega EE revocation channel in the rendered chart (item 157).

The sender (futureagi/accounts/host_events, settings in config.py) runs in two pod types:
  * the API: a drain right after each membership or user change commits
    (accounts/signals/host_event_signals.py) and the trigger-admission check
    (accounts/views/omega_trigger_admission.py);
  * the Temporal workers: the 3 s drain (tfc/temporal/schedules/omega_host_events.py,
    queue tasks_s, which any worker Deployment may poll).
The bootstrap Job registers that schedule and picks its interval from whether it sees the
delegation issuer (hourly without it). With omega.enabled each of them must get the channel, and a
missing setting must fail the install instead of leaving the sender MISCONFIGURED. Without Omega
nothing changes.

Fail closed also means the chart is the only source of the channel's settings: an extraEnv or
secrets.extra entry naming one of them is refused (with omega.enabled any entry, since a blank one
left a worker MISCONFIGURED; without it any non-blank one, since delegation would then run with no
channel at all). The check sees names as Kubernetes does: the chart quotes every name it renders,
refuses one with whitespace around it, and takes another case, quotes or a comment in a name, or
any other OMEGA_HOST_EVENTS_* name, for the reserved one. config.extraEnvFrom, which the chart
cannot read, cannot supply them either: where it feeds a pod the chart pins every reserved name it
does not set to "" in the container's env, which Kubernetes prefers to envFrom. A url is refused
unless the sender (accounts/host_events/config.py _check_url) accepts it too, and EE's private CA
reaches the sender through its own OMEGA_HOST_EVENTS_CA_FILE, never through the pod-wide
REQUESTS_CA_BUNDLE.

Omega EE's NetworkPolicy admits the senders by one pod label,
omega.futureagi.ai/host-events-sender: "true". The chart puts it on exactly the pods that get the
channel (the API and every worker Deployment, from the same condition) and on no other, never in a
selector, and refuses it anywhere in the values: the chart is its only writer.

No cluster and no network: `helm template` and `helm lint` only, skipped when helm is absent.
HELM names the binary (default: from PATH).
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

CHART = Path(__file__).resolve().parent.parent
HELM = os.environ.get("HELM", "helm")
SECRET_DIR = "/var/run/secrets/futureagi/omega-host-events"
SECRET_FILE = f"{SECRET_DIR}/secret"
VOLUME = "omega-host-events"
URL = "https://omega-os.omega.svc:8443/host/events"
KEY_ID = "fa-host-events-1"
# Placeholder names only: the Secret holds the value, never the chart.
SECRET_NAME = "omega-host-events"
CONFIGURED = {
    "omega": {
        "hostEvents": {"url": URL, "keyId": KEY_ID, "existingSecret": SECRET_NAME}
    }
}
CHANNEL_ENV = (
    "OMEGA_HOST_EVENTS_URL",
    "OMEGA_HOST_EVENTS_KEY_ID",
    "OMEGA_HOST_EVENTS_SECRET_FILE",
    "OMEGA_HOST_EVENTS_ALLOW_HTTP",
    "OMEGA_HOST_EVENTS_CA_FILE",
)
# The variables only the chart may set (from these values) on the API and worker pods.
RESERVED = {
    "OMEGA_SQUAD_DELEGATION_ISSUER": "omega.issuer.url",
    "OMEGA_HOST_EVENTS_URL": "omega.hostEvents.url",
    "OMEGA_HOST_EVENTS_KEY_ID": "omega.hostEvents.keyId",
    "OMEGA_HOST_EVENTS_SECRET_FILE": "omega.hostEvents.existingSecret",
    "OMEGA_HOST_EVENTS_ALLOW_HTTP": "omega.hostEvents.allowHttp",
    "OMEGA_HOST_EVENTS_CA_FILE": "omega.hostEvents.ca",
}
# EE's private CA (omega-internal-ca): ci/omega.yaml's omega.gateway.upstreamCa, which the sender
# trusts too unless omega.hostEvents.ca names another.
CA_DIR = "/etc/futureagi/omega-host-events-ca"
CA_FILE = f"{CA_DIR}/ca.crt"
CA_VOLUME = "omega-host-events-ca"
UPSTREAM_CA = {
    "secret": {
        "secretName": "omega-internal-ca",
        "defaultMode": 288,
        "items": [{"key": "ca.crt", "path": "ca.crt"}],
    }
}
# URLs accounts/host_events/config.py _check_url refuses (urlsplit's port: digits, 0-65535,
# split at the first colon; a hostname; no user or fragment), so the chart must refuse them.
SENDER_REFUSES = (
    "https://omega-os.omega.svc:65536/host/events",
    "https://omega-os.omega.svc:99999/host/events",
    "https://omega-os.omega.svc:bogus/host/events",
    "https://omega-os.omega.svc:-1/host/events",
    "https://omega-os.omega.svc:80:90/host/events",
    "https://omega-os.omega.svc:８４４３/host/events",
    "https://:8443/host/events",
    "https://[::::]:8443/host/events",
    "https://user@omega-os.omega.svc:8443/host/events",
    "https://omega-os.omega.svc:8443/host/events#fragment",
    "ftp://omega-os.omega.svc:8443/host/events",
    # EE serves only POST <base>/host/events (HOST_EVENTS_PATH); a bare EE base URL, such as
    # a copy of omega.gateway.ee.url, would render READY and never deliver a revocation.
    "https://omega-os.omega.svc:8443",
    "https://omega-os.omega.svc:8443/",
    "https://omega-os.omega.svc:08443",
    "https://omega-os.omega.svc:8443/host/events/",
    "https://omega-os.omega.svc:8443/hostevents",
    "https://omega-os.omega.svc:8443/host/events/x",
)
# The chart refuses these too (a stricter rule), though the sender would take them.
CHART_ALSO_REFUSES = (
    "https://omega-os.omega.svc:0/host/events",
    "https://omega-os.omega.svc:/host/events",
    "https://[::1]:8443/host/events",
)
# Both take these.
BOTH_ACCEPT = (
    "https://omega-os.omega.svc:8443/host/events",
    "https://omega-os.omega.svc:1/host/events",
    "https://omega-os.omega.svc:65535/host/events",
    "https://omega-os.omega.svc/host/events",
    "https://omega-os.omega.svc.cluster.local.:8443/host/events",
    "https://10.0.0.7:8443/host/events",
    "https://omega-os.omega.svc:08443/host/events",
    "https://ee.example.com/omega-os/host/events",
)
REQUIRED = {
    "url": "omega.hostEvents.url is required with omega.enabled",
    "keyId": "omega.hostEvents.keyId is required with omega.enabled",
    "existingSecret": "omega.hostEvents.existingSecret is required with omega.enabled",
}
# The label Omega EE's NetworkPolicy (omega-os values-futureagi.yaml) admits senders by.
SENDER_LABEL = "omega.futureagi.ai/host-events-sender"
WORKLOAD_KINDS = ("Deployment", "StatefulSet", "DaemonSet", "Job", "Pod")
# config.extraEnvFrom feeds the API, worker and bootstrap containers from a source the chart
# cannot read. ENV_FROM_SOURCE is what such a source could hold: a delegation issuer with its
# signing key (mounted with backend.extraVolumes), and a channel of its own.
ENV_FROM = {
    "config": {"extraEnvFrom": [{"configMapRef": {"name": "omega-delegation-env"}}]}
}
ENV_FROM_SOURCE = {
    "OMEGA_SQUAD_DELEGATION_ISSUER": "https://futureagi.example.com/omega",
    "OMEGA_SQUAD_DELEGATION_KEY_ID": "envfrom-key",
    "OMEGA_HOST_EVENTS_URL": "http://elsewhere.example.com/host/events",
    "OMEGA_HOST_EVENTS_KEY_ID": "envfrom-host-events",
    "OMEGA_HOST_EVENTS_SECRET_FILE": "/etc/elsewhere/secret",
    "OMEGA_HOST_EVENTS_ALLOW_HTTP": "1",
    "OMEGA_HOST_EVENTS_CA_FILE": "/etc/elsewhere/ca.crt",
}


def _docs(text):
    return [d for d in yaml.safe_load_all(text) if d]


def _pod_spec(doc):
    return doc["spec"]["template"]["spec"]


def _env(container):
    return {e["name"]: e.get("value") for e in container.get("env", [])}


def _template_labels(doc):
    if doc["kind"] == "Pod":
        return doc["metadata"].get("labels") or {}
    return doc["spec"]["template"]["metadata"].get("labels") or {}


def _effective_env(container, source):
    """The process environment: Kubernetes prefers a container's env to its envFrom sources.
    A valueFrom entry (a Secret's value) shows as None."""
    env = dict(source) if container.get("envFrom") else {}
    for entry in container.get("env", []):
        env[entry["name"]] = entry.get("value")
    return env


def _at(scope, name):
    """How the chart's messages name an env entry: scope.NAME, or scope["name"] (Go %q) when the
    name is not a plain variable name."""
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        return f"{scope}.{name}"
    return f"{scope}[{json.dumps(name)}]"


def _backend_modules():
    """(accounts.host_events.config, accounts.omega_session), or (None, None) outside a backend
    environment (they need cryptography)."""
    backend = CHART.parent.parent.parent / "futureagi"
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))
    try:
        from accounts import omega_session
        from accounts.host_events import config
    except ImportError:
        return None, None
    return config, omega_session


def _sender_check_url():
    """accounts/host_events/config.py's own URL check, or None outside a backend environment
    (config.py imports accounts.omega_session, which needs cryptography)."""
    config, _ = _backend_modules()
    return None if config is None else config._check_url


@unittest.skipUnless(shutil.which(HELM), f"{HELM} is not installed")
class HostEventsChartTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.tmp = Path(cls._tmp.name)
        # ci/omega.yaml's Omega settings without the channel: what an operator who has not
        # set omega.hostEvents renders.
        omega = yaml.safe_load((CHART / "ci/omega.yaml").read_text())
        omega["omega"].pop("hostEvents", None)
        cls.omega_base = cls.write("omega-base.yaml", omega)
        cls.issuer = omega["omega"]["issuer"]["url"]

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    @classmethod
    def write(cls, name, values):
        path = cls.tmp / name
        path.write_text(yaml.safe_dump(values))
        return path

    def helm(self, files, *args, chart=CHART):
        command = [
            HELM,
            "template",
            "futureagi",
            str(chart),
            "--namespace",
            "futureagi",
            "--set", "secrets.allowGeneratedKeysWithoutCluster=true",
        ]
        for file in files:
            command += ["-f", str(file)]
        return subprocess.run(
            command + list(args), text=True, capture_output=True, check=False
        )

    def omega(self, *values, extra=(), args=(), base=None):
        files = [CHART / "examples/external.yaml", CHART / "examples/ingress.yaml"]
        files += [*extra, base or self.omega_base]
        files += [self.write(f"values-{i}.yaml", v) for i, v in enumerate(values)]
        return self.helm(files, *args)

    def omega_off(self, *values, args=()):
        files = [CHART / "examples/external.yaml", CHART / "ci/gitops.yaml"]
        files += [self.write(f"off-{i}.yaml", v) for i, v in enumerate(values)]
        return self.helm(files, *args)

    def rendered(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        return _docs(result.stdout)

    def refused(self, result, *messages):
        self.assertNotEqual(result.returncode, 0, "rendered despite the missing values")
        for message in messages:
            self.assertIn(message, result.stderr)

    def configured(self, **host_events):
        values = {"omega": {"hostEvents": {**CONFIGURED["omega"]["hostEvents"]}}}
        values["omega"]["hostEvents"].update(host_events)
        return values

    def senders(self, docs):
        """The API Deployment and every Temporal worker Deployment, with their app container."""
        found = []
        for doc in docs:
            if doc["kind"] != "Deployment":
                continue
            component = doc["metadata"]["labels"].get("app.kubernetes.io/component", "")
            if component == "backend" or component.startswith("worker"):
                name = "backend" if component == "backend" else "worker"
                (container,) = [
                    c for c in _pod_spec(doc)["containers"] if c["name"] == name
                ]
                found.append((doc, container))
        return found

    def assert_channel(
        self, docs, *, allow_http=False, url=URL, min_workers=1, ca=UPSTREAM_CA
    ):
        senders = self.senders(docs)
        names = [d["metadata"]["name"] for d, _ in senders]
        self.assertIn("futureagi-backend", names)
        self.assertGreaterEqual(len(names) - 1, min_workers, names)
        for doc, container in senders:
            where = doc["metadata"]["name"]
            env = _env(container)
            with self.subTest(pod=where):
                self.assertEqual(env.get("OMEGA_SQUAD_DELEGATION_ISSUER"), self.issuer)
                self.assertEqual(env.get("OMEGA_HOST_EVENTS_URL"), url)
                self.assertEqual(env.get("OMEGA_HOST_EVENTS_KEY_ID"), KEY_ID)
                self.assertEqual(env.get("OMEGA_HOST_EVENTS_SECRET_FILE"), SECRET_FILE)
                self.assertEqual(
                    env.get("OMEGA_HOST_EVENTS_ALLOW_HTTP"), "1" if allow_http else None
                )
                spec = _pod_spec(doc)
                volumes = {v["name"]: v for v in spec.get("volumes", [])}
                self.assertEqual(
                    volumes.get(VOLUME, {}).get("secret"),
                    {
                        "secretName": SECRET_NAME,
                        # 0440: root-owned, readable through the fsGroup only.
                        "defaultMode": 288,
                        "items": [{"key": "secret", "path": "secret"}],
                    },
                )
                mounts = [
                    m for m in container.get("volumeMounts", []) if m["name"] == VOLUME
                ]
                self.assertEqual(
                    mounts,
                    [{"name": VOLUME, "mountPath": SECRET_DIR, "readOnly": True}],
                )
                self.assertTrue(spec["securityContext"].get("fsGroup"), where)
                self.assert_ca(doc, container, ca)
                # The issuer's private key stays on the API pods.
                if where != "futureagi-backend":
                    self.assertNotIn("omega-issuer", volumes)
                    self.assertNotIn("OMEGA_SQUAD_DELEGATION_PRIVATE_KEY_FILE", env)

    def assert_ca(self, doc, container, ca):
        """EE's CA on the sender alone: OMEGA_HOST_EVENTS_CA_FILE and a read-only mount, never
        the pod-wide trust store (global.caBundle is not set in these renders)."""
        env = _env(container)
        volumes = {v["name"]: v for v in _pod_spec(doc).get("volumes", [])}
        mounts = [
            m for m in container.get("volumeMounts", []) if m["name"] == CA_VOLUME
        ]
        for pod_wide in ("REQUESTS_CA_BUNDLE", "SSL_CERT_FILE", "CURL_CA_BUNDLE"):
            self.assertNotIn(pod_wide, env)
        if ca is None:
            self.assertNotIn("OMEGA_HOST_EVENTS_CA_FILE", env)
            self.assertNotIn(CA_VOLUME, volumes)
            self.assertEqual(mounts, [])
            return
        self.assertEqual(env.get("OMEGA_HOST_EVENTS_CA_FILE"), CA_FILE)
        volume = dict(volumes.get(CA_VOLUME, {}))
        volume.pop("name", None)
        self.assertEqual(volume, ca)
        self.assertEqual(
            mounts, [{"name": CA_VOLUME, "mountPath": CA_DIR, "readOnly": True}]
        )

    # -- configured ---------------------------------------------------------------------------

    def test_every_sender_pod_gets_the_channel_and_its_secret_volume(self):
        self.assert_channel(self.rendered(self.omega(CONFIGURED)))

    def test_every_dedicated_worker_queue_gets_the_channel(self):
        docs = self.rendered(
            self.omega(CONFIGURED, extra=[CHART / "ci/worker-queues.yaml"])
        )
        self.assert_channel(docs, min_workers=3)

    def test_another_secret_key_is_mounted_at_the_same_path(self):
        docs = self.rendered(self.omega(self.configured(secretKey="hmac")))
        for doc, _ in self.senders(docs):
            volumes = {v["name"]: v for v in _pod_spec(doc)["volumes"]}
            self.assertEqual(
                volumes[VOLUME]["secret"]["items"], [{"key": "hmac", "path": "secret"}]
            )

    def test_plain_http_only_with_allow_http(self):
        http = "http://omega-os.omega.svc:8787/host/events"
        self.refused(
            self.omega(self.configured(url=http)),
            "omega.hostEvents.url must be https",
            "omega.hostEvents.allowHttp",
        )
        docs = self.rendered(self.omega(self.configured(url=http, allowHttp=True)))
        self.assert_channel(docs, allow_http=True, url=http)

    def test_the_bootstrap_job_registers_the_drain_schedule_as_configured(self):
        docs = self.rendered(self.omega(CONFIGURED))
        (job,) = [
            d
            for d in docs
            if d["kind"] == "Job"
            and d["metadata"]["labels"].get("app.kubernetes.io/component")
            == "bootstrap"
        ]
        (container,) = _pod_spec(job)["containers"]
        env = _env(container)
        # It sees the delegation issuer, so the 3 s drain is registered, not the hourly one;
        # it sends nothing, so it gets no channel secret.
        self.assertEqual(env.get("OMEGA_SQUAD_DELEGATION_ISSUER"), self.issuer)
        self.assertEqual(env.get("REGISTER_TEMPORAL_SCHEDULES"), "true")
        self.assertFalse(set(CHANNEL_ENV) & set(env))
        volumes = {v["name"] for v in _pod_spec(job).get("volumes", [])}
        self.assertNotIn(VOLUME, volumes)
        self.assertNotIn(CA_VOLUME, volumes)

    # -- fail closed --------------------------------------------------------------------------

    def test_omega_without_the_channel_refuses_to_render_naming_each_value(self):
        self.refused(self.omega(), *REQUIRED.values())

    def test_each_missing_or_blank_value_is_named(self):
        for key, message in REQUIRED.items():
            for blank in ("", "   "):
                with self.subTest(key=key, blank=repr(blank)):
                    result = self.omega(self.configured(**{key: blank}))
                    self.refused(result, message)
                    for other, other_message in REQUIRED.items():
                        if other != key:
                            self.assertNotIn(other_message, result.stderr)
        for blank in ("", "  "):
            with self.subTest(key="secretKey", blank=repr(blank)):
                self.refused(
                    self.omega(self.configured(secretKey=blank)),
                    "omega.hostEvents.secretKey is required with omega.enabled",
                )

    def test_a_key_id_the_sender_refuses_is_refused(self):
        # accounts/host_events/signing.py KEY_ID: a letter or digit, then [A-Za-z0-9_.-], 64 at most.
        for key_id in ("bad key id", "-leading-dash", "k" * 65, "kid/with/slash"):
            with self.subTest(key_id=key_id):
                self.refused(
                    self.omega(self.configured(keyId=key_id)),
                    "omega.hostEvents.keyId must be",
                )
        self.rendered(self.omega(self.configured(keyId="k" * 64)))

    # -- EE's CA -------------------------------------------------------------------------------

    def test_the_sender_trusts_ee_with_its_own_ca_file(self):
        # omega.gateway.upstreamCa (ci/omega.yaml: the omega-internal-ca Secret) by default.
        self.assert_channel(self.rendered(self.omega(CONFIGURED)))
        # omega.hostEvents.ca wins, from a ConfigMap or a Secret.
        docs = self.rendered(
            self.omega(
                self.configured(
                    ca={"existingConfigMap": "omega-ee-ca", "key": "root.pem"}
                )
            )
        )
        self.assert_channel(
            docs,
            ca={
                "configMap": {
                    "name": "omega-ee-ca",
                    "defaultMode": 292,
                    "items": [{"key": "root.pem", "path": "ca.crt"}],
                }
            },
        )
        docs = self.rendered(
            self.omega(self.configured(ca={"existingSecret": "ee-ca"}))
        )
        self.assert_channel(
            docs,
            ca={
                "secret": {
                    "secretName": "ee-ca",
                    "defaultMode": 288,
                    "items": [{"key": "ca.crt", "path": "ca.crt"}],
                }
            },
        )

    def test_without_a_private_ca_the_default_trust_is_kept(self):
        omega = yaml.safe_load(self.omega_base.read_text())
        omega["omega"]["gateway"].pop("upstreamCa", None)
        base = self.write("omega-public-ca.yaml", omega)
        self.assert_channel(self.rendered(self.omega(CONFIGURED, base=base)), ca=None)

    def test_a_ca_from_both_a_secret_and_a_configmap_is_refused(self):
        self.refused(
            self.omega(
                self.configured(ca={"existingSecret": "a", "existingConfigMap": "b"})
            ),
            "omega.hostEvents.ca: set existingSecret or existingConfigMap, not both",
        )

    # -- names stay strings -------------------------------------------------------------------

    def test_secret_and_key_names_stay_strings(self):
        # Valid Secret and key names that YAML would read as a boolean or a number unquoted.
        args = (
            "--set-string",
            "omega.hostEvents.existingSecret=123",
            "--set-string",
            "omega.hostEvents.secretKey=true",
            "--set-string",
            "omega.hostEvents.ca.existingSecret=0x1F",
            "--set-string",
            "omega.hostEvents.ca.key=null",
        )
        docs = self.rendered(self.omega(CONFIGURED, args=args))
        for doc, _ in self.senders(docs):
            with self.subTest(pod=doc["metadata"]["name"]):
                volumes = {v["name"]: v for v in _pod_spec(doc)["volumes"]}
                secret = volumes[VOLUME]["secret"]
                self.assertEqual(secret["secretName"], "123")
                self.assertEqual(secret["items"], [{"key": "true", "path": "secret"}])
                ca = volumes[CA_VOLUME]["secret"]
                self.assertEqual(ca["secretName"], "0x1F")
                self.assertEqual(ca["items"], [{"key": "null", "path": "ca.crt"}])

    # -- urls ---------------------------------------------------------------------------------

    def test_a_url_the_sender_refuses_is_refused(self):
        for url in SENDER_REFUSES + CHART_ALSO_REFUSES:
            with self.subTest(url=url):
                self.refused(
                    self.omega(self.configured(url=url)), "omega.hostEvents.url must be"
                )
        for url in ("http://omega-os.omega.svc:65536/host/events", "http://:8787/x"):
            with self.subTest(url=url, allowHttp=True):
                self.refused(
                    self.omega(self.configured(url=url, allowHttp=True)),
                    "omega.hostEvents.url must be",
                )
        for url in BOTH_ACCEPT:
            with self.subTest(url=url):
                self.assert_channel(
                    self.rendered(self.omega(self.configured(url=url))), url=url
                )

    def test_the_url_lists_match_the_sender(self):
        check = _sender_check_url()
        if check is None:
            self.skipTest("accounts.host_events.config is not importable here")

        def accepted(url):
            try:
                check(url, False)
            except ValueError:
                return False
            return True

        for url in SENDER_REFUSES:
            self.assertFalse(accepted(url), url)
        for url in BOTH_ACCEPT:
            self.assertTrue(accepted(url), url)

    # -- the chart is the only source of the channel ------------------------------------------

    def test_a_blank_worker_override_of_the_url_is_refused(self):
        # The review's repro: valid omega.hostEvents, but every worker would get a blank URL.
        result = self.omega(
            CONFIGURED, {"worker": {"extraEnv": {"OMEGA_HOST_EVENTS_URL": "   "}}}
        )
        self.refused(
            result,
            "worker.extraEnv.OMEGA_HOST_EVENTS_URL: the chart sets it from omega.hostEvents.url",
        )

    def test_every_override_of_the_channel_is_refused_by_name(self):
        queues = yaml.safe_load((CHART / "ci/worker-queues.yaml").read_text())
        queue_env = {"OMEGA_HOST_EVENTS_SECRET_FILE": "/tmp/elsewhere"}
        queues["worker"]["queues"][1]["extraEnv"] = queue_env
        placeholder = "placeholder-not-a-secret"
        overrides = {
            "config": {"extraEnv": {"OMEGA_HOST_EVENTS_ALLOW_HTTP": "1"}},
            "backend": {"extraEnv": {"OMEGA_SQUAD_DELEGATION_ISSUER": ""}},
            "worker": {
                "extraEnv": {
                    "OMEGA_HOST_EVENTS_KEY_ID": " ",
                    "OMEGA_HOST_EVENTS_CA_FILE": "/etc/ssl/cert.pem",
                }
            },
            "secrets": {"extra": {"OMEGA_HOST_EVENTS_URL": placeholder}},
        }
        result = self.omega(
            CONFIGURED, overrides, extra=[self.write("queues.yaml", queues)]
        )
        expected = {
            "config.extraEnv": "OMEGA_HOST_EVENTS_ALLOW_HTTP",
            "backend.extraEnv": "OMEGA_SQUAD_DELEGATION_ISSUER",
            "worker.extraEnv": "OMEGA_HOST_EVENTS_KEY_ID",
            "worker.queues[1].extraEnv": "OMEGA_HOST_EVENTS_SECRET_FILE",
            "secrets.extra": "OMEGA_HOST_EVENTS_URL",
        }
        self.refused(
            result,
            *(
                f"{scope}.{name}: the chart sets it from {RESERVED[name]}"
                for scope, name in expected.items()
            ),
            "worker.extraEnv.OMEGA_HOST_EVENTS_CA_FILE: the chart sets it from omega.hostEvents.ca",
        )
        # The message names the setting, never its value.
        self.assertNotIn(placeholder, result.stderr)
        self.assertNotIn("/tmp/elsewhere", result.stderr)

    def test_delegation_without_omega_enabled_is_refused(self):
        issuer = "https://futureagi.example.com/omega"
        scopes = {
            "config.extraEnv": {
                "config": {"extraEnv": {"OMEGA_SQUAD_DELEGATION_ISSUER": issuer}}
            },
            "backend.extraEnv": {
                "backend": {"extraEnv": {"OMEGA_SQUAD_DELEGATION_ISSUER": issuer}}
            },
            "worker.extraEnv": {"worker": {"extraEnv": {"OMEGA_HOST_EVENTS_URL": URL}}},
            "secrets.extra": {
                "secrets": {"extra": {"OMEGA_SQUAD_DELEGATION_ISSUER": issuer}}
            },
        }
        for scope, values in scopes.items():
            with self.subTest(scope=scope):
                (name,) = list(values.values())[0][scope.split(".")[1]]
                self.refused(
                    self.omega_off(values),
                    f"{scope}.{name} configures Omega delegation without omega.enabled",
                )
        # Blank, they leave delegation off: the render goes through.
        blank = {
            "config": {"extraEnv": {"OMEGA_SQUAD_DELEGATION_ISSUER": ""}},
            "worker": {"extraEnv": {"OMEGA_HOST_EVENTS_URL": "  "}},
        }
        result = self.omega_off(blank)
        self.assertEqual(result.returncode, 0, result.stderr)

    # -- names as Kubernetes sees them --------------------------------------------------------

    def test_env_names_reach_kubernetes_as_written(self):
        # Valid variable names that YAML reads as a boolean unquoted: an unquoted name could reach
        # Kubernetes as another name than the check saw, or as none.
        docs = self.rendered(
            self.omega_off(
                {
                    "config": {"extraEnv": {"NO": "1"}},
                    "worker": {"extraEnv": {"ON": "1"}},
                }
            )
        )
        found = {}
        for doc in docs:
            if doc["kind"] != "Deployment":
                continue
            for container in _pod_spec(doc)["containers"]:
                found[(doc["metadata"]["name"], container["name"])] = [
                    e["name"] for e in container.get("env", [])
                ]
        backend = found[("futureagi-backend", "backend")]
        worker = found[("futureagi-worker", "worker")]
        self.assertIn("NO", backend)
        self.assertIn("NO", worker)
        self.assertIn("ON", worker)
        for names in (backend, worker):
            self.assertTrue(all(isinstance(n, str) for n in names), names)

    def test_a_name_with_whitespace_around_it_is_refused(self):
        # Kubernetes gets every name as written: "LOG_FORMAT " would not be LOG_FORMAT.
        for top, name in (("config", "LOG_FORMAT "), ("worker", "\tLOG_FORMAT")):
            with self.subTest(top=top, name=name):
                self.refused(
                    self.omega_off({top: {"extraEnv": {name: "json"}}}),
                    f"{_at(top + '.extraEnv', name)}: a variable name with whitespace around it",
                )

    def test_a_disguised_override_of_the_channel_is_refused(self):
        # Spellings that reached a pod as a reserved name (whitespace around it, which YAML
        # dropped from the unquoted name) or pass for one: another case, quotes or a comment in
        # the name, any other OMEGA_HOST_EVENTS_* name.
        queues = yaml.safe_load((CHART / "ci/worker-queues.yaml").read_text())
        queues_file = self.write("queues-disguised.yaml", queues)
        cases = (
            ("worker.extraEnv", "OMEGA_HOST_EVENTS_URL ", "omega.hostEvents.url"),
            ("backend.extraEnv", "OMEGA_HOST_EVENTS_URL\t", "omega.hostEvents.url"),
            ("config.extraEnv", " OMEGA_HOST_EVENTS_KEY_ID", "omega.hostEvents.keyId"),
            (
                "worker.queues[1].extraEnv",
                "OMEGA_HOST_EVENTS_SECRET_FILE ",
                "omega.hostEvents.existingSecret",
            ),
            ("secrets.extra", "OMEGA_HOST_EVENTS_CA_FILE ", "omega.hostEvents.ca"),
            ("backend.extraEnv", "omega_squad_delegation_issuer", "omega.issuer.url"),
            ("worker.extraEnv", "omega_host_events_url", "omega.hostEvents.url"),
            ("worker.extraEnv", "'OMEGA_HOST_EVENTS_URL'", "omega.hostEvents"),
            ("worker.extraEnv", "OMEGA_HOST_EVENTS_URL #", "omega.hostEvents"),
            ("config.extraEnv", "X.OMEGA_SQUAD_DELEGATION_ISSUER", "omega.issuer.url"),
            ("backend.extraEnv", "OMEGA_HOST_EVENTS_RETRY_SECONDS", "omega.hostEvents"),
        )
        for scope, name, source in cases:
            if scope.startswith("worker.queues"):
                values = {
                    "worker": {"queues": [dict(q) for q in queues["worker"]["queues"]]}
                }
                values["worker"]["queues"][1]["extraEnv"] = {name: "   "}
            elif scope == "secrets.extra":
                values = {"secrets": {"extra": {name: "placeholder-not-a-secret"}}}
            else:
                values = {scope.split(".")[0]: {"extraEnv": {name: "   "}}}
            with self.subTest(scope=scope, name=name):
                result = self.omega(CONFIGURED, values, extra=[queues_file])
                self.refused(
                    result,
                    f"omega: {_at(scope, name)}: the chart sets it from {source}",
                )
                self.assertNotIn("placeholder-not-a-secret", result.stderr)

    def test_a_disguised_issuer_without_omega_is_refused(self):
        issuer = "https://futureagi.example.com/omega"
        for scope, name in (
            ("config.extraEnv", "OMEGA_SQUAD_DELEGATION_ISSUER "),
            ("backend.extraEnv", "\tOMEGA_SQUAD_DELEGATION_ISSUER"),
            ("worker.extraEnv", "omega_host_events_url"),
            ("secrets.extra", "OMEGA_SQUAD_DELEGATION_ISSUER "),
        ):
            top, key = scope.split(".")
            with self.subTest(scope=scope, name=name):
                self.refused(
                    self.omega_off({top: {key: {name: issuer}}}),
                    f"omega: {_at(scope, name)} configures Omega delegation without omega.enabled",
                )

    # -- config.extraEnvFrom ------------------------------------------------------------------

    def python_containers(self, docs):
        """Every container of the API, worker and bootstrap pods, init containers included."""
        found = []
        for doc in docs:
            if doc["kind"] not in ("Deployment", "Job"):
                continue
            component = doc["metadata"]["labels"].get("app.kubernetes.io/component", "")
            if component in ("backend", "bootstrap") or component.startswith("worker"):
                spec = _pod_spec(doc)
                for container in spec.get("initContainers", []) + spec["containers"]:
                    found.append((doc, container))
        return found

    def signing_key(self):
        """A private key file the session exchange would sign with (owned by this user, 0600)."""
        _, session = _backend_modules()
        if session is None:
            return None
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        path = self.tmp / "envfrom-private.pem"
        path.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        path.chmod(0o600)
        return str(path)

    def test_extra_env_from_cannot_turn_delegation_or_the_channel_on(self):
        # The review's repro: omega.enabled false, config.extraEnvFrom naming a source with the
        # delegation settings. Every API, worker and bootstrap container it feeds must still see
        # no issuer and no channel, so the session exchange refuses to sign and the sender stays
        # DISABLED.
        config, session = _backend_modules()
        source = dict(ENV_FROM_SOURCE)
        key_file = self.signing_key()
        if key_file:
            source["OMEGA_SQUAD_DELEGATION_PRIVATE_KEY_FILE"] = key_file
            # The control: the source alone would turn delegation on.
            session.HostAssertionSigner.from_environment(source)
        docs = self.rendered(self.omega_off(ENV_FROM))
        fed = [(d, c) for d, c in self.python_containers(docs) if c.get("envFrom")]
        pods = {d["metadata"]["name"] for d, _ in fed}
        for pod in ("futureagi-backend", "futureagi-worker", "futureagi-bootstrap"):
            self.assertIn(pod, pods)
        for doc, container in fed:
            with self.subTest(pod=doc["metadata"]["name"], container=container["name"]):
                effective = _effective_env(container, source)
                for name in RESERVED:
                    self.assertEqual(effective.get(name), "", name)
                if config is not None:
                    self.assertEqual(
                        config.load_config(effective).state, config.DISABLED
                    )
                if key_file:
                    with self.assertRaises(session.DelegationUnavailable):
                        session.HostAssertionSigner.from_environment(effective)

    def test_with_omega_extra_env_from_cannot_redirect_the_channel(self):
        docs = self.rendered(self.omega(CONFIGURED, ENV_FROM))
        chart = {
            "OMEGA_SQUAD_DELEGATION_ISSUER": self.issuer,
            "OMEGA_HOST_EVENTS_URL": URL,
            "OMEGA_HOST_EVENTS_KEY_ID": KEY_ID,
            "OMEGA_HOST_EVENTS_SECRET_FILE": SECRET_FILE,
            "OMEGA_HOST_EVENTS_CA_FILE": CA_FILE,
            # allowHttp off: pinned blank, so the source's 1 cannot let a plain-http URL through.
            "OMEGA_HOST_EVENTS_ALLOW_HTTP": "",
        }
        senders = self.senders(docs)
        self.assertGreaterEqual(len(senders), 3)
        for doc, container in senders:
            with self.subTest(pod=doc["metadata"]["name"]):
                self.assertTrue(container.get("envFrom"))
                effective = _effective_env(container, ENV_FROM_SOURCE)
                self.assertEqual({k: effective.get(k) for k in chart}, chart)
        (job,) = [
            d
            for d in docs
            if d["kind"] == "Job" and d["metadata"]["name"] == "futureagi-bootstrap"
        ]
        (container,) = _pod_spec(job)["containers"]
        effective = _effective_env(container, ENV_FROM_SOURCE)
        # The bootstrap Job registers the 3 s drain (the issuer) and sends nothing.
        self.assertEqual(effective["OMEGA_SQUAD_DELEGATION_ISSUER"], self.issuer)
        for name in CHANNEL_ENV:
            self.assertEqual(effective.get(name), "", name)

    def test_without_extra_env_from_nothing_is_pinned(self):
        docs = self.rendered(self.omega(CONFIGURED))
        for doc, container in self.senders(docs):
            with self.subTest(pod=doc["metadata"]["name"]):
                self.assertNotIn("envFrom", container)
                # Unset by the chart (allowHttp off) and not pinned: no envFrom to override.
                self.assertNotIn("OMEGA_HOST_EVENTS_ALLOW_HTTP", _env(container))

    # -- the label Omega EE admits senders by -------------------------------------------------

    def assert_sender_label(self, docs):
        """omega.futureagi.ai/host-events-sender: "true" on the pod template of exactly the
        Deployments that get the channel, on no other pod, in no selector and on no object's own
        labels. Returns the senders' names."""
        senders = {
            d["metadata"]["name"]
            for d, c in self.senders(docs)
            if _env(c).get("OMEGA_HOST_EVENTS_URL")
        }
        for doc in docs:
            name = f"{doc['kind']} {doc['metadata']['name']}"
            with self.subTest(object=name):
                self.assertNotIn(SENDER_LABEL, doc["metadata"].get("labels") or {})
                if doc["kind"] not in WORKLOAD_KINDS:
                    continue
                labels = _template_labels(doc)
                if doc["kind"] == "Deployment" and doc["metadata"]["name"] in senders:
                    self.assertEqual(labels.get(SENDER_LABEL), "true")
                else:
                    self.assertNotIn(SENDER_LABEL, labels)
                selector = (doc["spec"].get("selector") or {}).get("matchLabels") or {}
                self.assertNotIn(SENDER_LABEL, selector)
        return senders

    def test_the_sender_label_is_on_exactly_the_pods_that_send(self):
        queues = yaml.safe_load((CHART / "ci/worker-queues.yaml").read_text())
        queues["worker"]["queues"].append({"name": "tasks_label_check"})
        docs = self.rendered(
            self.omega(CONFIGURED, extra=[self.write("queues-label.yaml", queues)])
        )
        senders = self.assert_sender_label(docs)
        workers = {
            d["metadata"]["name"]
            for d in docs
            if d["kind"] == "Deployment"
            and d["metadata"]["labels"]["app.kubernetes.io/component"].startswith(
                "worker"
            )
        }
        # The all-queues worker, the exact-aggregation worker and one per queue.
        self.assertEqual(len(workers), 2 + len(queues["worker"]["queues"]), workers)
        self.assertIn("futureagi-worker-tasks-label-check", workers)
        self.assertEqual(senders, workers | {"futureagi-backend"})
        # Pods that send nothing are in this render and unlabelled.
        names = {d["metadata"]["name"] for d in docs if d["kind"] in WORKLOAD_KINDS}
        for pod in (
            "futureagi-bootstrap",
            "futureagi-omega-gateway",
            "futureagi-frontend",
        ):
            self.assertIn(pod, names)

    def test_the_sender_label_from_the_values_is_refused_naming_where(self):
        label = {SENDER_LABEL: "true"}
        placements = {
            "commonLabels": {"commonLabels": label},
            "frontend.podLabels": {"frontend": {"podLabels": label}},
            "codeExecutor.podLabels": {"codeExecutor": {"podLabels": label}},
            "serving.podLabels": {"serving": {"podLabels": label}},
            "fiCollector.podLabels": {"fiCollector": {"podLabels": label}},
            "agentccGateway.podLabels": {"agentccGateway": {"podLabels": label}},
            "omega.gateway.podLabels": {"omega": {"gateway": {"podLabels": label}}},
            "hub.checker.podLabels": {"hub": {"checker": {"podLabels": label}}},
            "bootstrap.podAnnotations": {"bootstrap": {"podAnnotations": label}},
            # The senders' own: the chart is the label's only writer.
            "backend.podLabels": {"backend": {"podLabels": label}},
            "worker.podLabels": {"worker": {"podLabels": label}},
            "worker.queues[0].podLabels": {
                "worker": {"queues": [{"name": "tasks_s", "podLabels": label}]}
            },
        }
        for path, values in placements.items():
            for omega_on in (True, False):
                with self.subTest(path=path, omega=omega_on):
                    result = (
                        self.omega(CONFIGURED, values)
                        if omega_on
                        else self.omega_off(values)
                    )
                    self.refused(result, f"omega: {path} holds {SENDER_LABEL}")

    def test_a_scalar_name_cannot_inject_the_trusted_sender_label(self):
        # The review's repro had no reserved map key: YAML interpreted the unquoted
        # nameOverride scalar as another label on EVERY pod and immutable selector,
        # including code-executor, while keeping the release's trusted instance label.
        for omega_on in (False, True):
            for override in ("nameOverride", "fullnameOverride"):
                with self.subTest(omega=omega_on, override=override):
                    values = {
                        "fullnameOverride": "futureagi",
                        override: f'futureagi\n{SENDER_LABEL}: "true"',
                        "codeExecutor": {"enabled": True},
                    }
                    files = [CHART / "examples/bundled.yaml"]
                    if omega_on:
                        files += [
                            CHART / "examples/ingress.yaml",
                            CHART / "ci/omega.yaml",
                        ]
                    files.append(self.write("name-label-injection.yaml", values))
                    # The schema rejects this before templates, including in lint
                    # (which otherwise ignores template fail() in Helm 3).
                    self.refused(self.helm(files), override, "pattern")
                    lint = subprocess.run(
                        [HELM, "lint", "--strict", str(CHART),
                         "--set", "secrets.allowGeneratedKeysWithoutCluster=true"]
                        + [arg for file in files for arg in ("-f", str(file))],
                        text=True,
                        capture_output=True,
                        check=False,
                    )
                    self.assertNotEqual(
                        lint.returncode, 0, "lint accepted the injected label"
                    )
                    self.assertIn(override, lint.stdout + lint.stderr)
                    self.assertIn("pattern", lint.stdout + lint.stderr)

    def test_name_validation_also_runs_without_the_values_schema(self):
        # The render-time guard remains effective when schema validation is
        # skipped. Older Helm lacks --skip-schema-validation; copy only the chart.
        chart = self.tmp / "chart-without-schema"
        shutil.copytree(CHART, chart)
        (chart / "values.schema.json").unlink()
        for override in ("nameOverride", "fullnameOverride"):
            values = {override: f'futureagi\n{SENDER_LABEL}: "true"'}
            overlay = self.write("no-schema-injection.yaml", values)
            self.refused(
                self.helm([chart / "examples/bundled.yaml", overlay], chart=chart),
                f"{override} must be empty or a DNS label",
            )

    def test_non_dns_name_overrides_are_refused_before_truncation(self):
        for override in ("nameOverride", "fullnameOverride"):
            for invalid in (
                "Futureagi",
                "futureagi.example",
                "future_agi",
                "-futureagi",
                "futureagi-",
                "futureagi ",
                "futureagi\n",
                "futureagi\r\n",
                "a" * 64,
                "a" * 63 + f'\n{SENDER_LABEL}: "true"',
            ):
                with self.subTest(override=override, invalid=repr(invalid)):
                    self.refused(
                        self.omega_off({override: invalid}),
                        override,
                    )

    def test_valid_names_remain_strings_in_labels_and_selectors(self):
        # DNS names may look like YAML booleans/numbers/null: quote, do not rely
        # on validation alone. Other scalar label inputs must be quoted too.
        for name in ("", "futureagi-test", "true", "123", "null", "a" * 63):
            for omega_on in (False, True):
                with self.subTest(name=name, omega=omega_on):
                    values = {
                        "nameOverride": name,
                        "fullnameOverride": "futureagi",
                        "codeExecutor": {"enabled": True},
                    }
                    files = [CHART / "examples/bundled.yaml"]
                    if omega_on:
                        files += [
                            CHART / "examples/ingress.yaml",
                            CHART / "ci/omega.yaml",
                        ]
                    files.append(self.write("valid-name.yaml", values))
                    docs = self.rendered(self.helm(files))
                    self.assert_sender_label(docs)
                    for doc in docs:
                        labels = doc["metadata"].get("labels", {})
                        self.assertEqual(
                            labels.get("app.kubernetes.io/name"), name or "futureagi"
                        )
                        self.assertTrue(
                            all(isinstance(value, str) for value in labels.values())
                        )
                        if doc["kind"] in WORKLOAD_KINDS:
                            labels = _template_labels(doc)
                            self.assertEqual(
                                labels["app.kubernetes.io/name"], name or "futureagi"
                            )
                            selector = (
                                doc["spec"].get("selector", {}).get("matchLabels", {})
                            )
                            self.assertTrue(
                                all(
                                    isinstance(value, str)
                                    for value in selector.values()
                                )
                            )
                            for key, value in selector.items():
                                self.assertEqual(value, labels[key])

    # -- Omega off ----------------------------------------------------------------------------

    def test_without_omega_nothing_changes(self):
        # The gitops set generates no secret anew, so two renders compare byte for byte.
        gitops = [CHART / "examples/external.yaml", CHART / "ci/gitops.yaml"]
        for files in ([CHART / "examples/bundled.yaml"], gitops):
            result = self.helm(files)
            self.assertEqual(result.returncode, 0, result.stderr)
            for marker in (
                "OMEGA_HOST_EVENTS",
                "OMEGA_SQUAD_DELEGATION",
                VOLUME,
                SECRET_DIR,
            ):
                self.assertNotIn(marker, result.stdout)
        base = self.helm(gitops)
        # The channel's values alone (omega.enabled false) render exactly the same manifests.
        off = self.write("off.yaml", self.configured(allowHttp=True))
        with_values = self.helm(gitops + [off])
        self.assertEqual(with_values.returncode, 0, with_values.stderr)
        self.assertEqual(with_values.stdout, base.stdout)

    # -- every CI values set ------------------------------------------------------------------

    def test_every_ci_values_set_still_renders(self):
        script = (CHART / "hack/check.sh").read_text()
        block = re.search(r"^value_sets=\((.*?)^\)", script, re.M | re.S).group(1)
        sets = dict(re.findall(r'^\s*"([^"|]+)\|([^"]+)"', block, re.M))
        used = {f for files in sets.values() for f in files.split()}
        for path in sorted((CHART / "ci").glob("*.yaml")):
            self.assertIn(
                f"ci/{path.name}", used, f"no check.sh set renders ci/{path.name}"
            )
        for name, files in sets.items():
            with self.subTest(set=name):
                result = self.helm([CHART / f for f in files.split()])
                self.assertEqual(result.returncode, 0, result.stderr[-2000:])
                docs = _docs(result.stdout)
                if name == "omega":
                    # ci/omega.yaml carries the channel's placeholders.
                    self.assert_channel(docs)
                    self.assert_sender_label(docs)
                else:
                    for _doc, container in self.senders(docs):
                        env = _env(container)
                        # Where config.extraEnvFrom feeds the pod (ci/all-components.yaml), the
                        # chart pins the channel's names blank; never to a value.
                        stray = {
                            n
                            for n in set(CHANNEL_ENV) & set(env)
                            if not (container.get("envFrom") and env[n] == "")
                        }
                        self.assertFalse(stray)
                    self.assertNotIn(SENDER_LABEL, result.stdout)


if __name__ == "__main__":
    unittest.main()
