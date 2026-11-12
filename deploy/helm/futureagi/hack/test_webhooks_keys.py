"""Offline webhook key wiring controls; never emit values or rendered secrets.

Uses only chart fixtures and synthetic settings. Requires PyYAML like the other
rendered chart checks. The values-default probe evaluates inside Helm rather
than opening or displaying a values file.
"""

import base64
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml

CHART = Path(__file__).resolve().parent.parent
KEY = "WEBHOOKS_ENCRYPTION_KEYS"
SECRET = "synthetic-webhook-key-custody"
REFUSAL = "WEBHOOKS_ENCRYPTION_KEYS must come only from secrets.existingSecret"
SECRET_KEY_REFUSAL = "secrets.extra keys must contain only letters, digits, '-', '_' or '.'"
SOURCE = "synthetic-envfrom"


def webhook_origin(container, objects):
    """Model Kubernetes env precedence for this name using key inventories only.

    objects maps (reference kind, name) to sets of key names, not secret values.
    envFrom entries apply in order, then explicit env entries. An absent optional
    secretKeyRef is skipped, leaving any envFrom binding in place (INFOFLOW-01).
    This is an origin oracle, not a Kubernetes startup or crypto implementation.
    """
    origin = None
    for entry in container.get("envFrom", []):
        for kind in ("configMapRef", "secretRef"):
            if kind not in entry:
                continue
            name = entry[kind]["name"]
            for key in objects.get((kind, name), set()):
                if entry.get("prefix", "") + key == KEY:
                    origin = (kind, name, key)
    for entry in container.get("env", []):
        if entry.get("name") != KEY:
            continue
        if "value" in entry:
            origin = ("literal",)
            continue
        ref = entry.get("valueFrom", {}).get("secretKeyRef")
        if ref is None:
            raise AssertionError("unexpected webhook environment source")
        if ref["key"] in objects.get(("secretRef", ref["name"]), set()):
            origin = ("secretRef", ref["name"], ref["key"])
        elif not ref.get("optional", False):
            raise AssertionError("required webhook key would block container startup")
    return origin


class WebhookKeyRenderTests(unittest.TestCase):
    def render(self, *, existing=True, upgrade=False, chart=CHART, settings=(), fixtures=(),
               json_settings=()):
        command = [
            os.environ.get("HELM", "helm"), "template", "webhook-test", str(chart),
            "--namespace", "webhook-test", "-f", str(CHART / "examples/bundled.yaml"),
        ]
        for fixture in fixtures:
            command.extend(["-f", str(CHART / fixture)])
        command.extend([
            "--set", f"secrets.existingSecret={SECRET if existing else ''}",
            "--set", "secrets.allowGeneratedKeysWithoutCluster=true",
            "--set", "backend.collectStatic=true",
            "--set", "worker.allQueues.enabled=true",
            "--set", "worker.exactAggregation.enabled=true",
            "--set-json", 'worker.queues=[{"name":"webhooks"},{"name":"tasks_xl"}]',
        ])
        if upgrade:
            command.append("--is-upgrade")
        for setting in settings:
            command.extend(["--set", setting])
        for scope, value in json_settings:
            command.extend(["--set-json", f"{scope}={json.dumps(value)}"])
        return subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)

    def documents(self, result):
        self.assertEqual(result.returncode, 0, "synthetic chart render failed")
        try:
            return [doc for doc in yaml.safe_load_all(result.stdout) if doc]
        except yaml.YAMLError:
            self.fail("synthetic chart render is not valid YAML")

    def container_specs(self, documents):
        for doc in documents:
            spec = doc.get("spec", {})
            if doc.get("kind") == "CronJob":
                spec = spec.get("jobTemplate", {}).get("spec", {})
            pod = spec if doc.get("kind") == "Pod" else spec.get("template", {}).get("spec", {})
            for group in ("initContainers", "containers"):
                for container in pod.get(group, []):
                    identity = (doc["kind"], doc["metadata"]["name"], group, container["name"])
                    yield identity, container

    def containers(self, documents):
        for identity, container in self.container_specs(documents):
            yield identity, container.get("env", [])

    def backend_containers(self, documents):
        selected = {
            identity: env for identity, env in self.containers(documents)
            if any(entry.get("name") == "INTEGRATION_ENCRYPTION_KEY" for entry in env)
        }
        # A pinned, nonempty inventory prevents a missing consumer/helper from
        # making the implication pass vacuously. Both queue forms are exercised.
        expected = {
            ("Deployment", "webhook-test-futureagi-backend", "containers", "backend"),
            ("Deployment", "webhook-test-futureagi-backend", "initContainers", "collectstatic"),
            ("Deployment", "webhook-test-futureagi-worker", "containers", "worker"),
            ("Deployment", "webhook-test-futureagi-worker-exact-aggregation", "containers", "worker"),
            ("Deployment", "webhook-test-futureagi-worker-webhooks", "containers", "worker"),
            ("Deployment", "webhook-test-futureagi-worker-tasks-xl", "containers", "worker"),
        }
        expected.add(("Job", "webhook-test-futureagi-bootstrap", "containers", "bootstrap"))
        self.assertEqual(set(selected), expected, "backend workload census changed")
        return selected

    def assert_wiring(self, documents):
        selected = self.backend_containers(documents)
        expected = {"name": KEY, "valueFrom": {"secretKeyRef": {
            "name": SECRET, "key": KEY, "optional": True,
        }}}
        for identity, env in selected.items():
            entries = [entry for entry in env if entry.get("name") == KEY]
            self.assertTrue(entries == [expected], f"wrong optional webhook reference in {identity}")
        for identity, env in self.containers(documents):
            if identity not in selected:
                self.assertFalse(any(entry.get("name") == KEY for entry in env),
                                 f"webhook key leaked to non-backend container {identity}")

    def test_existing_secret_wires_all_backend_workloads(self):
        for upgrade in (False, True):
            with self.subTest(upgrade=upgrade):
                self.assert_wiring(self.documents(self.render(upgrade=upgrade)))

    def test_external_datastores_keep_the_application_secret_reference(self):
        self.assert_wiring(self.documents(self.render(fixtures=("examples/external.yaml",))))

    def test_without_existing_secret_webhook_variable_is_absent(self):
        for upgrade in (False, True):
            with self.subTest(upgrade=upgrade):
                documents = self.documents(self.render(existing=False, upgrade=upgrade))
                self.backend_containers(documents)
                self.assertTrue(all(
                    [entry for entry in env if entry.get("name") == KEY] == [{"name": KEY, "value": ""}]
                    for env in self.backend_containers(documents).values()
                ), "each Python consumer must have exactly one explicit empty webhook entry")

    def test_chart_secret_never_contains_webhook_keys(self):
        for existing in (False, True):
            for upgrade in (False, True):
                with self.subTest(existing=existing, upgrade=upgrade):
                    documents = self.documents(self.render(existing=existing, upgrade=upgrade))
                    secrets = [doc for doc in documents if doc.get("kind") == "Secret"]
                    self.assertTrue(secrets, "expected the chart-owned Secret")
                    for secret in secrets:
                        self.assertFalse(KEY in (secret.get("data") or {}) or
                                         KEY in (secret.get("stringData") or {}),
                                         "chart generated/stored webhook key material")

    def test_values_have_no_webhook_key_default(self):
        with tempfile.TemporaryDirectory(prefix="webhook-default-probe-") as directory:
            chart = Path(directory) / "chart"
            shutil.copytree(CHART, chart)
            # Neither the test nor its output reads a values file. Helm fails
            # with a fixed message if an effective default key exists anywhere.
            probe = chart / "templates/webhook-default-probe.yaml"
            probe.write_text(
                '{{- if regexMatch "(?i)webhooks_?encryption_?keys" (toJson .Values) -}}'
                '{{- fail "webhook key values default is forbidden" -}}{{- end -}}\n'
            )
            self.documents(self.render(chart=chart))

    def test_inline_overrides_cannot_replace_secret_reference(self):
        names = {
            "exact": KEY,
            "trailing-space": KEY + " ",
            "leading-space": " " + KEY,
            "trailing-tab": KEY + "\t",
            "newline-injected": 'ZZ_PROBE_INJ\n  value: "1"\n- name: ' + KEY,
            # Literal quotes and backslashes must survive the render; unquoted
            # YAML would decode these names to the reserved webhook variable.
            "escaped-hex": r'"\x57EBHOOKS_ENCRYPTION_KEYS"',
            "escaped-unicode": r'"\u0057EBHOOKS_ENCRYPTION_KEYS"',
        }
        for existing in (False, True):
            for scope in ("config.extraEnv", "backend.extraEnv", "worker.extraEnv",
                          "worker.queues[0].extraEnv", "secrets.extra"):
                for variant, name in names.items():
                    with self.subTest(existing=existing, scope=scope, variant=variant):
                        sentinel = os.urandom(16).hex()
                        result = self.render(existing=existing, json_settings=((scope, {name: sentinel}),))
                        self.assertFalse(sentinel in result.stderr, "refusal leaked the synthetic sentinel")
                        self.assertFalse(base64.b64encode(sentinel.encode()).decode() in result.stderr,
                                         "refusal leaked the encoded synthetic sentinel")
                        escaped = variant in ("escaped-hex", "escaped-unicode")
                        if escaped and scope != "secrets.extra" and result.returncode == 0:
                            documents = self.documents(result)
                            if existing:
                                self.assert_wiring(documents)
                            else:
                                self.assert_mask(documents)
                            selected = self.backend_containers(documents)
                            literal_seen = False
                            for identity, container in self.container_specs(documents):
                                if identity not in selected:
                                    continue
                                literal_seen |= any(entry.get("name") == name
                                                    for entry in container.get("env", []))
                                for named_key in (False, True):
                                    objects = {("secretRef", SECRET): {KEY} if named_key else set()}
                                    expected = (("secretRef", SECRET, KEY) if named_key else None) if existing else ("literal",)
                                    self.assertTrue(webhook_origin(container, objects) == expected,
                                                    "escaped name replaced the webhook source")
                            self.assertTrue(literal_seen, "escaped extraEnv name was not rendered literally")
                        else:
                            self.assertNotEqual(result.returncode, 0, "inline webhook key was accepted")
                            refusal = SECRET_KEY_REFUSAL if escaped and scope == "secrets.extra" else REFUSAL
                            self.assertTrue(refusal in result.stderr, "fixed webhook refusal is missing")
                            if escaped:
                                self.assertFalse(name in result.stderr, "refusal repeated the supplied name")
                            self.assertFalse(result.stdout, "refusal emitted manifests")

    def envfrom_settings(self, kind, prefix=None, index=0):
        settings = [f"config.extraEnvFrom[{index}].{kind}.name={SOURCE}"]
        if prefix is not None:
            settings.append(f"config.extraEnvFrom[{index}].prefix={prefix}")
        return tuple(settings)

    def assert_mask(self, documents):
        selected = self.backend_containers(documents)
        for identity, env in self.containers(documents):
            entries = [entry for entry in env if entry.get("name") == KEY]
            expected = [{"name": KEY, "value": ""}] if identity in selected else []
            self.assertTrue(entries == expected, f"wrong webhook empty mask in {identity}")

    def assert_envfrom(self, documents, sources, *, existing, named_key=True, masked=False):
        selected = self.backend_containers(documents)
        if masked or not existing:
            self.assert_mask(documents)
        else:
            self.assert_wiring(documents)
        objects = {("secretRef", SECRET): {KEY} if named_key else set()}
        for source in sources:
            for kind in ("configMapRef", "secretRef"):
                if kind in source:
                    # Key names only: no object data or credential literals.
                    objects[(kind, source[kind]["name"])] = {KEY, KEY[len(source.get("prefix", "")):]}
        for identity, container in self.container_specs(documents):
            if identity not in selected:
                continue
            self.assertTrue(container.get("envFrom") == sources, f"missing opaque source in {identity}")
            if masked or not existing:
                expected = ("literal",)
            elif named_key:
                expected = ("secretRef", SECRET, KEY)
            else:
                # Disclosed offline/missing-object residual: the optional ref
                # cannot mask an opaque source when lookup cannot see the key.
                last = sources[-1]
                kind = "configMapRef" if "configMapRef" in last else "secretRef"
                expected = (kind, last[kind]["name"], KEY[len(last.get("prefix", "")):])
            self.assertTrue(webhook_origin(container, objects) == expected,
                            f"wrong webhook origin in {identity}")

    def lookup_chart(self, expression):
        tmp = tempfile.TemporaryDirectory(prefix="webhook-lookup-")
        self.addCleanup(tmp.cleanup)
        chart = Path(tmp.name) / "chart"
        shutil.copytree(CHART, chart)
        template = chart / "templates/_env.tpl"
        text = template.read_text()
        lookup = 'lookup "v1" "Secret" $root.Release.Namespace $v.secrets.existingSecret'
        self.assertEqual(text.count(lookup), 1, "update the named-Secret lookup stand-in")
        template.write_text(text.replace(lookup, expression))
        return chart

    def test_unprefixed_envfrom_renders_with_explicit_webhook_entry(self):
        for kind in ("configMapRef", "secretRef"):
            for prefix in (None, ""):
                for existing in (False, True):
                    for upgrade in (False, True):
                        with self.subTest(kind=kind, prefix=prefix, existing=existing, upgrade=upgrade):
                            documents = self.documents(self.render(
                                existing=existing, upgrade=upgrade, settings=self.envfrom_settings(kind, prefix)))
                            source = {kind: {"name": SOURCE}}
                            if prefix is not None:
                                source["prefix"] = prefix
                            self.assert_envfrom(documents, [source], existing=existing)

    def test_reserved_name_prefixes_render_with_explicit_webhook_entry(self):
        for kind in ("configMapRef", "secretRef"):
            for cut in range(1, len(KEY) + 1):
                for existing in (False, True):
                    with self.subTest(kind=kind, cut=cut, existing=existing):
                        prefix = KEY[:cut]
                        documents = self.documents(self.render(
                            existing=existing, settings=self.envfrom_settings(kind, prefix)))
                        self.assert_envfrom(documents, [{kind: {"name": SOURCE}, "prefix": prefix}],
                                            existing=existing)

    def test_later_envfrom_source_cannot_override_explicit_webhook_entry(self):
        for kind in ("configMapRef", "secretRef"):
            for existing in (False, True):
                for optional in (False, True):
                    with self.subTest(kind=kind, existing=existing, optional=optional):
                        settings = (
                            *self.envfrom_settings("configMapRef", "CUSTOM_"),
                            *self.envfrom_settings(kind, index=1),
                            f"config.extraEnvFrom[1].{kind}.optional={str(optional).lower()}",
                        )
                        documents = self.documents(self.render(existing=existing, settings=settings))
                        self.assert_envfrom(documents, [
                            {"configMapRef": {"name": SOURCE}, "prefix": "CUSTOM_"},
                            {kind: {"name": SOURCE, "optional": optional}},
                        ], existing=existing)

    def test_live_named_secret_without_key_masks_opaque_sources(self):
        for shape in ('(dict "metadata" (dict "name" $v.secrets.existingSecret))',
                      '(dict "data" (dict))', '(dict "data" nil)',
                      '(dict "data" (dict "UNRELATED" (randAlphaNum 24 | b64enc)))'):
            chart = self.lookup_chart(shape)
            for kind in ("configMapRef", "secretRef"):
                for upgrade in (False, True):
                    with self.subTest(shape=shape, kind=kind, upgrade=upgrade):
                        documents = self.documents(self.render(
                            chart=chart, upgrade=upgrade, settings=self.envfrom_settings(kind)))
                        self.assert_envfrom(documents, [{kind: {"name": SOURCE}}],
                                            existing=True, named_key=False, masked=True)

    def test_live_named_secret_with_key_keeps_optional_reference(self):
        # Membership, not truthiness of the data value, selects the reference.
        for value in ('(randAlphaNum 24 | b64enc)', '""'):
            chart = self.lookup_chart(f'(dict "data" (dict "{KEY}" {value}))')
            for kind in ("configMapRef", "secretRef"):
                for upgrade in (False, True):
                    with self.subTest(kind=kind, upgrade=upgrade, empty=value == '""'):
                        documents = self.documents(self.render(
                            chart=chart, upgrade=upgrade, settings=self.envfrom_settings(kind)))
                        self.assert_envfrom(documents, [{kind: {"name": SOURCE}}], existing=True)

    def test_missing_named_secret_keeps_optional_reference_and_exposes_residual(self):
        chart = self.lookup_chart('dict')
        for kind in ("configMapRef", "secretRef"):
            for upgrade in (False, True):
                with self.subTest(kind=kind, upgrade=upgrade):
                    documents = self.documents(self.render(
                        chart=chart, upgrade=upgrade, settings=self.envfrom_settings(kind)))
                    self.assert_envfrom(documents, [{kind: {"name": SOURCE}}],
                                        existing=True, named_key=False)

    def test_named_secret_lookup_error_fails_closed_and_unset_skips_lookup(self):
        chart = self.lookup_chart('(fail "synthetic webhook lookup forbidden")')
        for upgrade in (False, True):
            with self.subTest(upgrade=upgrade):
                result = self.render(chart=chart, upgrade=upgrade)
                self.assertNotEqual(result.returncode, 0, "lookup error was ignored")
                self.assertIn("synthetic webhook lookup forbidden", result.stderr)
                self.assertFalse(result.stdout.strip(), "lookup refusal emitted manifests")
                self.assert_mask(self.documents(self.render(chart=chart, upgrade=upgrade, existing=False)))

    def test_safe_prefixes_keep_named_secret_as_only_source(self):
        # Both source types really reach every Python consumer. The source key
        # inventories contain the reserved name and suffixes; no values needed.
        for prefix in ("CUSTOM_", KEY + "_", "webhooks_", "WEBHOOKS_OTHER_"):
            for existing in (False, True):
                for upgrade in (False, True):
                    with self.subTest(prefix=prefix, existing=existing, upgrade=upgrade):
                        settings = (*self.envfrom_settings("configMapRef", prefix),
                                    *self.envfrom_settings("secretRef", prefix, index=1))
                        documents = self.documents(self.render(existing=existing, upgrade=upgrade,
                                                               settings=settings))
                        selected = self.backend_containers(documents)
                        if existing:
                            self.assert_wiring(documents)
                        expected_sources = [
                            {"configMapRef": {"name": SOURCE}, "prefix": prefix},
                            {"secretRef": {"name": SOURCE}, "prefix": prefix},
                        ]
                        for identity, container in self.container_specs(documents):
                            if identity not in selected:
                                continue
                            self.assertTrue(container.get("envFrom") == expected_sources,
                                            f"missing prefixed source in {identity}")
                            for named_key in (False, True):
                                objects = {
                                    ("configMapRef", SOURCE): {KEY, "ENCRYPTION_KEYS", "OTHER"},
                                    ("secretRef", SOURCE): {KEY, "KEYS", "OTHER"},
                                    ("secretRef", SECRET): {KEY} if named_key else set(),
                                }
                                expected = (("secretRef", SECRET, KEY) if named_key else None) if existing else ("literal",)
                                self.assertTrue(webhook_origin(container, objects) == expected,
                                                f"webhook key did not stay confined in {identity}")

    def test_origin_oracle_detects_optional_reference_fallback(self):
        # Calibrate against rendered env. With no named Secret, prove the mask
        # wins, then remove it only in the model to restore the original bug.
        # With an offline optional ref, retain and expose the documented residual.
        for existing in (False, True):
            documents = self.documents(self.render(existing=existing))
            selected = self.backend_containers(documents)
            for identity, rendered in self.container_specs(documents):
                if identity not in selected:
                    continue
                for kind in ("configMapRef", "secretRef"):
                    for prefix in ("", "WEBHOOKS_", KEY[:-1]):
                        for named_key in (False, True):
                            with self.subTest(identity=identity, kind=kind, prefix=prefix,
                                              existing=existing, named_key=named_key):
                                container = copy.deepcopy(rendered)
                                container["envFrom"] = [
                                    {kind: {"name": "synthetic-first"}},
                                    {kind: {"name": SOURCE}, "prefix": prefix},
                                ]
                                source_key = KEY[len(prefix):]
                                objects = {
                                    (kind, "synthetic-first"): {KEY},
                                    (kind, SOURCE): {source_key},
                                    ("secretRef", SECRET): {KEY} if named_key else set(),
                                }
                                if not existing:
                                    self.assertTrue(webhook_origin(container, objects) == ("literal",),
                                                    "rendered mask did not defeat opaque fallback")
                                    container["env"] = [entry for entry in container["env"]
                                                        if entry.get("name") != KEY]
                                expected = (("secretRef", SECRET, KEY) if existing and named_key
                                            else (kind, SOURCE, source_key))
                                self.assertTrue(webhook_origin(container, objects) == expected,
                                                "oracle missed envFrom fallback or precedence")


if __name__ == "__main__":
    unittest.main()
