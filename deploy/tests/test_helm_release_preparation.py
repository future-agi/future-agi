"""Daemon-free contract tests; fake tools do not qualify a signed release."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "deploy/helm/futureagi"
SPEC = importlib.util.spec_from_file_location(
    "helm_release_preparation", CHART / "hack/prepare-release.py"
)
p = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(p)
DIGEST = "sha256:" + "a" * 64


def archive_bytes(
    version="1.2.3",
    *,
    chart_update=None,
    values_update=None,
    component_update=None,
    extra=(),
):
    chart = {
        "apiVersion": "v2",
        "name": "futureagi",
        "type": "application",
        "version": version,
        "appVersion": "v" + version,
    }
    values = {
        "image": {
            "pinDigests": True,
            "tag": "",
            "digests": {
                name: "sha256:" + hashlib.sha256(name.encode()).hexdigest()
                for name in p.COMPONENTS
            },
        }
    }
    values.update(
        {
            name: {"image": {"repository": repository, "tag": "", "digest": ""}}
            for name, repository in p.COMPONENT_REPOSITORIES.items()
        }
    )
    for name, override in (component_update or {}).items():
        if isinstance(override, dict):
            values[name]["image"].update(override)
        else:
            values[name]["image"] = override
    if chart_update:
        chart.update(chart_update)
    if values_update:
        values["image"].update(values_update)
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for name, data in [
            ("futureagi/Chart.yaml", yaml.safe_dump(chart).encode()),
            ("futureagi/values.yaml", yaml.safe_dump(values).encode()),
        ]:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
        for info, data in extra:
            archive.addfile(info, io.BytesIO(data) if data is not None else None)
    return output.getvalue()


class FakeTools:
    def __init__(self, payload=None):
        self.payload = payload if payload is not None else archive_bytes()
        self.calls = []
        self.responses = {}

    def __call__(self, argv):
        self.calls.append(argv)
        key = tuple(argv[:2])
        if key in self.responses:
            value = self.responses[key]
            if isinstance(value, Exception):
                raise value
            return value
        if key == ("helm", "version"):
            return "v4.3.0+test\n"
        if key == ("oras", "resolve"):
            return DIGEST + "\n"
        if key == ("cosign", "verify"):
            return json.dumps(
                [{"critical": {"image": {"docker-manifest-digest": DIGEST}}}]
            )
        if key == ("helm", "pull"):
            Path(argv[-1], "futureagi@sha256-test.tgz").write_bytes(self.payload)
            return "Pulled chart\n"
        raise AssertionError(argv)


class ReleasePreparationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="helm-prepare-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.destination = self.root / "release"

    def test_exact_publisher_binding_precedes_digest_pull(self):
        tools = FakeTools()
        result = p.prepare("1.2.3", self.destination, tools)
        ref = p.REPOSITORY + "@" + DIGEST
        self.assertEqual(
            [call[:2] for call in tools.calls],
            [
                ["helm", "version"],
                ["oras", "resolve"],
                ["cosign", "verify"],
                ["helm", "pull"],
            ],
        )
        self.assertEqual(
            tools.calls[2],
            [
                "cosign",
                "verify",
                "--new-bundle-format=false",
                ref,
                "--certificate-identity",
                p.WORKFLOW + "@refs/tags/v1.2.3",
                "--certificate-oidc-issuer",
                p.ISSUER,
            ],
        )
        self.assertEqual(tools.calls[3][2], "oci://" + ref)
        receipt = json.loads((self.destination / "release-lock.json").read_text())
        self.assertEqual(receipt, result)
        self.assertEqual(
            receipt["chart_sha256"], hashlib.sha256(tools.payload).hexdigest()
        )
        self.assertFalse(receipt["migration_compatibility_qualified"])
        self.assertFalse(receipt["cluster_modified"])
        self.assertEqual(receipt["argocd_source"]["targetRevision"], DIGEST)
        self.assertEqual(receipt["flux_source"]["ref"], {"digest": DIGEST})
        subject = receipt["flux_source"]["verify"]["matchOIDCIdentity"][0]["subject"]
        import re

        self.assertRegex(p.WORKFLOW + "@refs/tags/v1.2.3", subject)
        self.assertIsNone(re.fullmatch(subject, p.WORKFLOW + "@refs/tags/v1.2.30"))
        self.assertEqual(self.destination.stat().st_mode & 0o777, 0o700)
        self.assertEqual(
            (self.destination / "release-lock.json").stat().st_mode & 0o777, 0o600
        )

    def test_beta_and_rc_bind_the_exact_tag(self):
        for version in ("1.2.3-beta.1", "1.2.3-rc.2"):
            with self.subTest(version=version):
                tools = FakeTools(archive_bytes(version))
                result = p.prepare(version, self.root / version, tools)
                self.assertEqual(
                    result["signer_identity"], p.WORKFLOW + "@refs/tags/v" + version
                )

    def test_invalid_versions_make_no_external_calls(self):
        for version in (
            "latest",
            "v1.2.3",
            "1.2.*",
            "1.02.3",
            "1.2.3\n",
            "../1.2.3",
            "1.2.3+build",
            "1.2.3-rc.01",
            "1.2.3;echo x",
        ):
            with self.subTest(version=version):
                tools = FakeTools()
                with self.assertRaises(p.Refused):
                    p.prepare(version, self.destination, tools)
                self.assertEqual(tools.calls, [])

    def test_existing_output_is_never_overwritten(self):
        self.destination.mkdir()
        (self.destination / "operator-values.yaml").write_text("keep me")
        tools = FakeTools()
        with self.assertRaises(p.Refused):
            p.prepare("1.2.3", self.destination, tools)
        self.assertEqual(tools.calls, [])
        self.assertEqual(
            (self.destination / "operator-values.yaml").read_text(), "keep me"
        )

    def test_broken_destination_symlink_is_refused(self):
        self.destination.symlink_to(self.root / "missing")
        tools = FakeTools()
        with self.assertRaises(p.Refused):
            p.prepare("1.2.3", self.destination, tools)
        self.assertEqual(tools.calls, [])

    def test_helm3_and_malformed_digest_refuse(self):
        for key, value in [
            (("helm", "version"), "v3.22.0+test"),
            (("oras", "resolve"), "sha256:bad"),
            (("oras", "resolve"), DIGEST + "\n" + DIGEST),
        ]:
            with self.subTest(key=key, value=value):
                tools = FakeTools()
                tools.responses[key] = value
                with self.assertRaises(p.Refused):
                    p.prepare("1.2.3", self.destination, tools)
                self.assertFalse(
                    any(call[:2] == ["helm", "pull"] for call in tools.calls)
                )
                self.assertFalse(self.destination.exists())

    def test_signature_failure_does_not_pull(self):
        tools = FakeTools()
        tools.responses[("cosign", "verify")] = p.Refused("signature rejected")
        with self.assertRaisesRegex(p.Refused, "signature rejected"):
            p.prepare("1.2.3", self.destination, tools)
        self.assertEqual(len(tools.calls), 3)
        self.assertFalse(self.destination.exists())

    def test_signature_output_must_bind_digest(self):
        for value in (
            [],
            {},
            [{"critical": {"image": {"docker-manifest-digest": "sha256:" + "b" * 64}}}],
            [None],
            [{"critical": {"image": None}}],
        ):
            with self.subTest(value=value):
                tools = FakeTools()
                tools.responses[("cosign", "verify")] = json.dumps(value)
                with self.assertRaises(p.Refused):
                    p.prepare("1.2.3", self.destination, tools)
                self.assertEqual(len(tools.calls), 3)

    def test_metadata_mismatch_never_publishes_success(self):
        cases = [
            {"chart_update": {"version": "1.2.4"}},
            {"chart_update": {"appVersion": "v1.2.4"}},
            {"chart_update": {"name": "another-chart"}},
            {"chart_update": {"dependencies": [{"name": "unreviewed"}]}},
            {"values_update": {"pinDigests": False}},
            {"values_update": {"tag": "latest"}},
            {"values_update": {"digests": {}}},
        ]
        for case in cases:
            with self.subTest(case=case):
                with self.assertRaises(p.Refused):
                    p.prepare(
                        "1.2.3", self.destination, FakeTools(archive_bytes(**case))
                    )
                self.assertFalse(self.destination.exists())
                self.assertEqual(list(self.root.iterdir()), [])

    def test_ineffective_component_pins_refuse(self):
        for component in p.COMPONENTS:
            for override in (
                {"tag": "other-release"},
                {"repository": "other/image"},
                {"digest": "sha256:" + "b" * 64},
                None,
                "not-a-map",
            ):
                with self.subTest(component=component, override=override):
                    with self.assertRaises(p.Refused):
                        p.prepare(
                            "1.2.3",
                            self.destination,
                            FakeTools(
                                archive_bytes(component_update={component: override})
                            ),
                        )
                    self.assertFalse(self.destination.exists())

    def test_explicit_component_pin_must_match_the_stamped_digest(self):
        updates = {
            name: {
                "tag": "v1.2.3",
                "digest": "sha256:" + hashlib.sha256(name.encode()).hexdigest(),
            }
            for name in p.COMPONENTS
        }
        result = p.prepare(
            "1.2.3",
            self.destination,
            FakeTools(archive_bytes(component_update=updates)),
        )
        self.assertEqual(
            result["component_digests"],
            {name: row["digest"] for name, row in updates.items()},
        )

    def test_component_repository_contract_matches_chart_defaults(self):
        values = yaml.safe_load((CHART / "values.yaml").read_text())
        self.assertEqual(
            {name: values[name]["image"]["repository"] for name in p.COMPONENTS},
            p.COMPONENT_REPOSITORIES,
        )

    def test_archive_traversal_links_and_duplicates_refuse_without_extraction(self):
        for name, kind in [
            ("../escaped", tarfile.REGTYPE),
            ("/absolute", tarfile.REGTYPE),
            ("futureagi/Chart.yaml", tarfile.REGTYPE),
            ("futureagi/link", tarfile.SYMTYPE),
            ("futureagi/device", tarfile.CHRTYPE),
        ]:
            with self.subTest(name=name, kind=kind):
                info = tarfile.TarInfo(name)
                info.type = kind
                info.linkname = "../../escaped" if kind == tarfile.SYMTYPE else ""
                with self.assertRaises(p.Refused):
                    p.prepare(
                        "1.2.3",
                        self.destination,
                        FakeTools(
                            archive_bytes(
                                extra=[(info, b"" if kind == tarfile.REGTYPE else None)]
                            )
                        ),
                    )
                self.assertFalse(self.destination.exists())
                self.assertFalse((self.root / "escaped").exists())

    def test_duplicate_yaml_keys_are_rejected(self):
        with self.assertRaises(p.Refused):
            yaml.load("name: futureagi\nname: another\n", Loader=p.UniqueSafeLoader)

    def test_cannot_treat_successful_transport_as_qualification(self):
        result = p.prepare("1.2.3", self.destination, FakeTools())
        self.assertEqual(result["kind"], "verified_helm_release")
        self.assertIs(result["migration_compatibility_qualified"], False)
        self.assertNotIn("eligible", result)


class GitOpsExamplePolicyTests(unittest.TestCase):
    def documents(self, name):
        return list(yaml.safe_load_all((CHART / "examples/gitops" / name).read_text()))

    def test_argo_uses_digest_source_and_bounded_sync(self):
        spec = self.documents("argocd-application.yaml")[0]["spec"]
        self.assertEqual(spec["project"], "futureagi")
        self.assertEqual(spec["source"]["repoURL"], "oci://" + p.REPOSITORY)
        self.assertEqual(spec["source"]["path"], ".")
        self.assertNotIn("chart", spec["source"])
        self.assertEqual(
            spec["source"]["targetRevision"],
            "sha256:REPLACE_WITH_VERIFIED_CHART_DIGEST",
        )
        policy = spec["syncPolicy"]
        self.assertEqual(
            policy["automated"], {"enabled": True, "prune": False, "selfHeal": False}
        )
        self.assertEqual(policy["retry"], {"limit": 0})
        self.assertNotIn("syncOptions", policy)

    def test_argo_project_cannot_deploy_cluster_resources(self):
        spec = self.documents("argocd-project.yaml")[0]["spec"]
        self.assertEqual(spec["clusterResourceWhitelist"], [])
        self.assertEqual(spec["sourceRepos"], ["oci://" + p.REPOSITORY])
        self.assertEqual(
            spec["destinations"],
            [{"server": "https://kubernetes.default.svc", "namespace": "futureagi"}],
        )
        for item in spec["namespaceResourceWhitelist"]:
            self.assertNotIn("*", item.values())
            self.assertNotIn(
                item["kind"],
                (
                    "Role",
                    "RoleBinding",
                    "ClusterRole",
                    "ClusterRoleBinding",
                    "Namespace",
                ),
            )

    def test_flux_authenticates_digest_and_keeps_failed_state(self):
        source, release = self.documents("flux-helmrelease.yaml")
        self.assertEqual(
            source["spec"]["ref"],
            {"digest": "sha256:REPLACE_WITH_VERIFIED_CHART_DIGEST"},
        )
        trust = source["spec"]["verify"]
        self.assertEqual(trust["provider"], "cosign")
        self.assertNotIn("secretRef", trust)
        self.assertNotIn("v.*", trust["matchOIDCIdentity"][0]["subject"])
        spec = release["spec"]
        self.assertEqual(spec["serviceAccountName"], "futureagi-reconciler")
        self.assertEqual(spec["storageNamespace"], "futureagi")
        self.assertIs(spec["install"]["createNamespace"], False)
        for action in ("install", "upgrade"):
            self.assertEqual(spec[action]["strategy"], {"name": "RemediateOnFailure"})
            self.assertEqual(
                spec[action]["remediation"],
                {"retries": 0, "remediateLastFailure": False},
            )
        self.assertEqual(spec["test"], {"enable": True, "ignoreFailures": False})

    def test_flux_reconciler_has_no_cluster_or_rbac_authority(self):
        documents = self.documents("flux-rbac.yaml")
        self.assertEqual(
            [d["kind"] for d in documents],
            ["Namespace", "ServiceAccount", "Role", "RoleBinding"],
        )
        self.assertIs(documents[1]["automountServiceAccountToken"], False)
        for rule in documents[2]["rules"]:
            self.assertNotIn("rbac.authorization.k8s.io", rule["apiGroups"])
            self.assertNotIn("namespaces", rule["resources"])
            self.assertNotIn("pods/exec", rule["resources"])
            self.assertNotIn("*", rule["apiGroups"] + rule["resources"] + rule["verbs"])
        self.assertEqual(documents[3]["roleRef"]["kind"], "Role")


if __name__ == "__main__":
    unittest.main()
