"""bin/update's planner (deploy/update/plan.py) without Docker or network.

A duck-typed FakeInstallation stands in for deploy.update.install.Installation
(reusing its real image-to-component mapping), manifests are built as valid
dicts and checked with validate_manifest, and deployment files are real files
in a temporary checkout. A few tests drive a real Installation through a fake
docker runner, and one feeds the plan a manifest from
scripts/generate_update_manifest.py.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import re
import shlex
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from deploy.update.install import (  # noqa: E402
    COMPONENT_VARIABLES, VERSION_VARIABLES, InstallError, Installation, component_for,
)
from deploy.update.manifest import (  # noqa: E402
    COMPONENT_REPOSITORIES, ManifestError, deployment_hashes, is_version, manifest_hash, parse_image_ref,
    validate_manifest,
)
from deploy.update.plan import (  # noqa: E402
    BEFORE_ROLLOUT_STATUSES, BLOCKED, RESUMABLE_STATUSES, STAGED, UP_TO_DATE, UPDATE_AVAILABLE, _strip_variant,
    build_plan, command_prefix, plan_json, plan_text, policy_allows, referenced_variables, stopped_rollout,
    unfinished,
)
from deploy.update.settings import load_settings  # noqa: E402
from deploy.update.trust import Verification  # noqa: E402

TARGET = "v1.52.0"
PREVIOUS = "v1.51.0"
VERIFIED = Verification(True, method="public key", signer="/etc/futureagi/cosign.pub", tool="cosign")
DEFAULT_FILES = ("docker-compose.yml", ".env.example", "deploy/.env.production.example")
ENV_EXAMPLE = "# Future AGI settings\nFUTURE_AGI_VERSION=\nSECRET_KEY=\n# OPTIONAL_SETTING=1\n"
PRODUCTION_EXAMPLE = "FUTURE_AGI_VERSION=\nFRONTEND_VERSION=\nPG_PASSWORD=\n"

# Distributed service -> (image repository, env variable), like docker-compose.distributed.yml.
DISTRIBUTED = {
    "agentcc-gateway": ("futureagi/agentcc-gateway", "AGENTCC_GATEWAY_VERSION"),
    "backend": ("futureagi/future-agi", "FUTURE_AGI_VERSION"),
    "code-executor": ("futureagi/code-executor", "CODE_EXECUTOR_VERSION"),
    "fi-collector": ("futureagi/fi-collector", "FI_COLLECTOR_VERSION"),
    "frontend": ("futureagi/frontend", "FRONTEND_VERSION"),
    "serving": ("futureagi/serving", "SERVING_VERSION"),
    "simulation-runner": ("futureagi/future-agi-simulation-runner", "SIMULATION_RUNNER_VERSION"),
    "worker": ("futureagi/future-agi", "FUTURE_AGI_VERSION"),
}


def digest_for(component: str, version: str = TARGET) -> str:
    return "sha256:" + hashlib.sha256(f"{component}@{version}".encode()).hexdigest()


def raw_image(image: str) -> str:
    """How the repository's compose files write a Future AGI image: its tag comes from the component's key.

    Any other image is written as it resolves.
    """

    try:
        ref = parse_image_ref(image)
    except ManifestError:
        return image
    component = component_for(ref)
    if component is None:
        return image
    name = image.split("@", 1)[0]
    head, _, last = name.rpartition("/")
    return f"{head + '/' if head else ''}{last.split(':', 1)[0]}:${{{COMPONENT_VARIABLES[component]}:-latest}}"


# What Installation.service_model() sets every version key to while probing which key selects each image.
PROBE = "bin-update-probe-"
# The Future AGI services of the repository's compose files for each mode, under any profile, as written there:
# FakeInstallation.service_model() lists those the active configuration leaves out as a profile's.
REPOSITORY_SERVICES: Dict[str, Dict[str, str]] = {
    "standalone": {"app": "futureagi/standalone:${FUTURE_AGI_VERSION:-latest}",
                   "serving": "futureagi/serving:${SERVING_VERSION:-latest}",
                   "code-executor": "futureagi/code-executor:${CODE_EXECUTOR_VERSION:-latest}"},
    "distributed": {service: f"{repository}:${{{variable}:-latest}}"
                    for service, (repository, variable) in DISTRIBUTED.items()},
}
REPOSITORY_SERVICES["production"] = REPOSITORY_SERVICES["distributed"]


def interpolate(text: str, env: Mapping[str, str]) -> str:
    """`text` as Compose interpolates it with `env`: $VAR, ${VAR}, ${VAR:-default}, ${VAR-default}, ${VAR:?err}, $$."""

    out: List[str] = []
    index = 0
    while index < len(text):
        if text.startswith("$$", index):
            out.append("$")
            index += 2
        elif text.startswith("${", index):
            depth, end = 1, index + 2
            while end < len(text) and depth:
                depth += {"{": 1, "}": -1}.get(text[end], 0)
                end += 1
            body = text[index + 2:end - 1]
            name, operator, default = body, "", ""
            for candidate in (":-", ":?", "-", "?"):
                head, found, tail = body.partition(candidate)
                if found and head.isidentifier():
                    name, operator, default = head, candidate, tail
                    break
            value = env.get(name)
            if value is None or (operator.startswith(":") and value == ""):
                value = interpolate(default, env) if operator.endswith("-") else ""
            out.append(value)
            index = end
        elif text.startswith("$", index) and (text[index + 1:index + 2].isalpha() or text[index + 1:index + 2] == "_"):
            end = index + 1
            while end < len(text) and (text[end].isalnum() or text[end] == "_"):
                end += 1
            out.append(env.get(text[index + 1:end], ""))
            index = end
        else:
            out.append(text[index])
            index += 1
    return "".join(out)


def probe_variable(image: str) -> Optional[str]:
    """The version key whose probe value starts `image`'s tag (Installation.service_model's rule), or None."""

    rest = image.split("@", 1)[0]
    last = rest.rsplit("/", 1)[-1]
    tag = last.split(":", 1)[1] if ":" in last else ""
    return next((key for key in VERSION_VARIABLES if tag.startswith(PROBE + key)), None)


def make_manifest(root: Path, *, version: str = TARGET, channel: str = "stable", minimum: str = PREVIOUS,
                  registry: str = "docker.io", files: Optional[List[str]] = None) -> Dict[str, Any]:
    """A valid manifest whose deployment hashes are the checkout's files right now."""

    components = {}
    for name, repository in COMPONENT_REPOSITORIES.items():
        suffix = "-gpu" if name == "serving_gpu" else ""
        components[name] = {"image": f"{registry}/{repository}:{version}{suffix}",
                            "digest": digest_for(name, version), "platforms": ["linux/amd64"]}
    manifest = {
        "schema_version": 1,
        "product": "future-agi",
        "channel": channel,
        "version": version,
        "source_sha": "0123456789abcdef0123456789abcdef01234567",
        "published_at": "2026-10-19T00:00:00Z",
        "minimum_version": minimum,
        "release_notes": f"https://github.com/future-agi/future-agi/releases/tag/{version}",
        "signature": {"bundle": "future-agi-update-manifest.sigstore.json"},
        "components": components,
        "helm": {"chart": "oci://ghcr.io/future-agi/charts/futureagi", "version": version[1:],
                 "digest": "sha256:" + "4" * 64},
        "deployment": {"files": deployment_hashes(root, list(DEFAULT_FILES if files is None else files))},
        "migration": {"requires_bootstrap": True, "rollback": "manual"},
    }
    return validate_manifest(manifest)


class FakeInstallation:
    """The parts of Installation that build_plan reads, with recorded calls."""

    def __init__(self, root: Path, *, services: Mapping[str, Any], env: Optional[Mapping[str, str]] = None,
                 mode: str = "standalone", production: bool = False, project: str = "futureagi",
                 stack_reasons: Optional[List[str]] = None) -> None:
        self.root = Path(root).resolve()
        self.mode = mode
        self.production = production
        self.env_file = self.root / ("deploy/.env.production" if production else ".env")
        self.env = dict(env or {})
        definitions = {}
        for name, value in services.items():
            definitions[name] = value if isinstance(value, dict) else {"image": value}
        self._config = {"name": project, "services": definitions}
        self._containers: List[Dict[str, Any]] = []
        self.images: Dict[str, Dict[str, Any]] = {}
        self.stack_reasons = list(stack_reasons or [])
        self.image_calls: List[str] = []
        self.checked_containers: Optional[List[Dict[str, Any]]] = None
        self.config_calls = 0
        # service -> its image as the compose files write it (before interpolation). By default a Future AGI
        # service's image follows its component's version key, as in the repository's compose files.
        self.raw_images: Dict[str, Optional[str]] = {}
        # service -> raw image of a Compose profile that is not enabled: service_model() lists every profile's
        # services, `config` only the active ones.
        self.profile_services: Dict[str, str] = {}
        # Whether service_model() could resolve every profile (Installation.service_model's "complete").
        self.model_complete = True
        self.model_calls = 0
        # Whether service_model() lists the repository's services of this mode that are not active (REPOSITORY_SERVICES).
        self.repository_services = True

    # --- the interface build_plan uses ---
    @property
    def project(self) -> str:
        return self._config["name"]

    def relative(self, path: Path) -> str:
        return Installation.relative(self, path)  # type: ignore[arg-type]

    def identity(self) -> Dict[str, Any]:
        return {"mode": self.mode, "production": self.production, "env_file": self.relative(self.env_file),
                "project": self.project}

    def config(self, refresh: bool = False) -> Dict[str, Any]:
        self.config_calls += 1
        return self._config

    def raw_definition(self, name: str) -> Optional[str]:
        """Service `name`'s image as the compose files write it (None: no image)."""

        if name in self.profile_services:
            return self.profile_services[name]
        if name in self.raw_images:
            return self.raw_images[name]
        if name not in self._config["services"]:
            return REPOSITORY_SERVICES[self.mode].get(name)
        image = (self._config["services"].get(name) or {}).get("image")
        return raw_image(image) if image else None

    def service_model(self, refresh: bool = False) -> Dict[str, Any]:
        """Like Installation.service_model: the probe resolves every image with each version key at its probe value.

        An active service shows its active image; one of a profile that is not enabled, its probe image. Without
        model_complete only the active services are listed.
        """

        self.model_calls += 1
        sentinels = {key: PROBE + key for key in VERSION_VARIABLES}
        active = self._config.get("services") or {}
        names = set(active)
        if self.model_complete:
            names |= set(self.profile_services) | (set(REPOSITORY_SERVICES[self.mode]) if self.repository_services
                                                    else set())
        services = {}
        for name in sorted(names):
            raw = self.raw_definition(name)
            probed = interpolate(raw, sentinels) if raw else ""
            services[name] = {
                "image": (active.get(name) or {}).get("image") or "" if name in active else probed,
                "variable": probe_variable(probed) if probed else None,
                "active": name in active,
                "buildable": "build" in (active.get(name) or {}),
            }
        return {"services": services, "complete": self.model_complete}

    def compose_hint(self) -> str:
        # Like Installation.compose_hint: `cd <checkout> && ` first when bin/update ran elsewhere (caller_cwd).
        caller = getattr(self, "caller_cwd", None)
        cd = "" if caller is None or Path(caller) == self.root else f"cd {shlex.quote(str(self.root))} && "
        if self.production:
            return (f"{cd}docker compose --env-file {shlex.quote(self.relative(self.env_file))} "
                    "-f docker-compose.distributed.yml -f deploy/docker-compose.production.yml")
        return f"{cd}docker compose"

    def futureagi_services(self, config=None):
        return Installation.futureagi_services(self, config)  # type: ignore[arg-type]

    def containers(self) -> List[Dict[str, Any]]:
        return [dict(c) for c in self._containers]

    def image(self, reference: str) -> Optional[Dict[str, Any]]:
        self.image_calls.append(reference)
        record = self.images.get(reference)
        return dict(record) if record else None

    def check_stack(self, containers: List[Dict[str, Any]]) -> List[str]:
        self.checked_containers = containers
        return list(self.stack_reasons)

    # --- test helpers ---
    def run(self, service: str, label: str = "", *, image_id: Optional[str] = None, inspectable: bool = True,
            status: str = "running", image: Optional[str] = None, oneoff: bool = False) -> "FakeInstallation":
        """Add a container of `service` whose image carries version label `label`."""

        if image_id is None:
            image_id = "sha256:" + hashlib.sha256(f"{service}|{label}|{len(self._containers)}".encode()).hexdigest()
        if image_id and inspectable:
            self.images[image_id] = {"id": image_id, "repo_digests": [], "version": label, "revision": ""}
        definition = self._config["services"].get(service, {})
        container = {
            "id": f"container-{len(self._containers)}", "service": service, "image_id": image_id,
            "image": definition.get("image", "") if image is None else image, "status": status, "exit_code": 0,
            "restart_count": 0, "health": "none", "started_at": "2026-10-01T00:00:00Z", "command": [],
        }
        if oneoff:
            container["oneoff"] = True
        self._containers.append(container)
        return self


class PlanTestCase(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.write("docker-compose.yml", "services:\n  app:\n    image: futureagi/standalone:${FUTURE_AGI_VERSION:-latest}\n")
        self.write("docker-compose.distributed.yml", "services: {}\n")
        self.write("deploy/docker-compose.production.yml", "services: {}\n")
        self.write(".env.example", ENV_EXAMPLE)
        self.write("deploy/.env.production.example", PRODUCTION_EXAMPLE)

    def write(self, relative: str, text: str) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def manifest(self, **kwargs) -> Dict[str, Any]:
        return make_manifest(self.root, **kwargs)

    def settings(self, *, policy: Optional[str] = None, channel: Optional[str] = None,
                 environ: Optional[Mapping[str, str]] = None):
        return load_settings(self.root, {"FUTURE_AGI_UPDATE_POLICY": policy, "FUTURE_AGI_UPDATE_CHANNEL": channel},
                             environ={} if environ is None else environ)

    def plan(self, installation, *, manifest: Optional[Dict[str, Any]] = None,
             verification: Optional[Verification] = None, settings=None,
             last_receipt: Optional[Mapping[str, Any]] = None, source: str = "/srv/manifests/m.json",
             **keywords):
        return build_plan(manifest if manifest is not None else self.manifest(),
                          verification if verification is not None else VERIFIED,
                          installation, settings if settings is not None else self.settings(),
                          manifest_source=source, last_receipt=last_receipt, **keywords)

    # --- installations ---
    def standalone(self, tag: str = PREVIOUS, *, label: Optional[str] = None, extra_env=None,
                   **kwargs) -> FakeInstallation:
        """Standalone with app (and postgres); app runs an image labelled `label` (default: the tag if a version)."""

        env = {"FUTURE_AGI_VERSION": tag, "SECRET_KEY": "not-a-real-secret-value"}
        env.update(extra_env or {})
        installation = FakeInstallation(self.root, services={
            "app": f"futureagi/standalone:{tag}", "postgres": "postgres:16-alpine",
        }, env=env, mode="standalone", **kwargs)
        installation.run("app", label if label is not None else (tag if is_version(tag) else ""))
        installation.run("postgres", "16.4")
        return installation

    def distributed(self, version: str = PREVIOUS, *, tags: Optional[Mapping[str, str]] = None,
                    labels: Optional[Mapping[str, str]] = None, mode: str = "distributed",
                    production: bool = False, extra_env=None, **kwargs) -> FakeInstallation:
        """Every Distributed service at `version`; `tags` overrides by env variable."""

        tags = dict(tags or {})
        labels = dict(labels or {})
        services: Dict[str, str] = {"postgres": "postgres:16-alpine", "redis": "redis:7-alpine"}
        env = {"SECRET_KEY": "not-a-real-secret-value", "PG_PASSWORD": "not-a-real-password"}
        for service, (repository, variable) in DISTRIBUTED.items():
            tag = tags.get(variable, version)
            services[service] = f"{repository}:{tag}"
            env[variable] = tag
        env.update(extra_env or {})
        installation = FakeInstallation(self.root, services=services, env=env, mode=mode, production=production,
                                        **kwargs)
        for service, (_, variable) in DISTRIBUTED.items():
            tag = tags.get(variable, version)
            installation.run(service, labels.get(service, labels.get(variable, tag if is_version(tag) else "")))
        installation.run("postgres", "16.4")
        return installation

    # --- assertions ---
    def component(self, plan, name: str) -> Dict[str, Any]:
        matches = [item for item in plan["components"] if item["component"] == name]
        self.assertEqual(len(matches), 1, f"expected one planned {name}: {plan['components']}")
        return matches[0]

    def assertReason(self, plan, text: str) -> None:
        self.assertIn(text, plan["reasons"], "\n".join(plan["reasons"]))

    def assertReasonContaining(self, plan, fragment: str) -> str:
        found = [r for r in plan["reasons"] if fragment in r]
        self.assertTrue(found, f"no reason contains {fragment!r}: {plan['reasons']}")
        return found[0]

    def assertNoReasonContaining(self, plan, fragment: str) -> None:
        self.assertFalse([r for r in plan["reasons"] if fragment in r], plan["reasons"])

    def assertBlocked(self, plan) -> None:
        self.assertEqual(plan["decision"], BLOCKED, plan["reasons"])
        self.assertFalse(plan["resume"])


# --------------------------------------------------------------------------
class PolicyAllowsTests(unittest.TestCase):
    def test_patch_allows_same_minor(self):
        self.assertTrue(policy_allows("v1.51.0", "v1.51.9", "patch"))

    def test_patch_refuses_next_minor(self):
        self.assertFalse(policy_allows("v1.51.9", "v1.52.0", "patch"))

    def test_patch_refuses_next_major(self):
        self.assertFalse(policy_allows("v1.51.0", "v2.51.0", "patch"))

    def test_minor_allows_patch_and_minor(self):
        self.assertTrue(policy_allows("v1.51.0", "v1.51.1", "minor"))
        self.assertTrue(policy_allows("v1.51.0", "v1.99.0", "minor"))

    def test_minor_refuses_next_major(self):
        self.assertFalse(policy_allows("v1.99.9", "v2.0.0", "minor"))

    def test_major_allows_everything_newer(self):
        self.assertTrue(policy_allows("v1.0.0", "v9.0.0", "major"))
        self.assertTrue(policy_allows("v1.0.0", "v1.0.1", "major"))

    def test_prerelease_target_counts_by_its_numbers(self):
        self.assertTrue(policy_allows("v1.52.0-rc.1", "v1.52.0", "patch"))
        self.assertTrue(policy_allows("v1.51.0", "v1.51.1-beta.2", "patch"))
        self.assertFalse(policy_allows("v1.51.3", "v1.52.0-beta.1", "patch"))
        self.assertFalse(policy_allows("v1.99.0", "v2.0.0-rc.1", "minor"))

    def test_zero_major_minor_jump_is_a_minor(self):
        self.assertTrue(policy_allows("v0.9.0", "v0.10.0", "minor"))
        self.assertFalse(policy_allows("v0.9.0", "v0.10.0", "patch"))

    def test_unknown_policy_raises(self):
        for policy in ("", "Minor", "PATCH", "any", "latest"):
            with self.subTest(policy=policy):
                with self.assertRaises(ValueError) as caught:
                    policy_allows("v1.0.0", "v1.0.1", policy)
                self.assertIn(f"unknown update policy {policy!r}", str(caught.exception))

    def test_invalid_versions_raise(self):
        for current, target in (("1.0.0", "v1.0.1"), ("v1.0.0", "latest"), ("v1.0", "v1.0.1"), ("v01.0.0", "v1.0.1")):
            with self.subTest(current=current, target=target):
                with self.assertRaises(ManifestError):
                    policy_allows(current, target, "major")


class StripVariantTests(unittest.TestCase):
    def test_plain_version_for_regular_components(self):
        for component in ("standalone", "backend", "frontend", "serving", "fi_collector"):
            with self.subTest(component=component):
                self.assertEqual(_strip_variant("v1.51.0", component), "v1.51.0")

    def test_gpu_component_strips_the_suffix(self):
        self.assertEqual(_strip_variant("v1.51.0-gpu", "serving_gpu"), "v1.51.0")
        self.assertEqual(_strip_variant("v1.52.0-rc.1-gpu", "serving_gpu"), "v1.52.0-rc.1")

    def test_gpu_component_needs_the_suffix(self):
        self.assertIsNone(_strip_variant("v1.51.0", "serving_gpu"))

    def test_regular_component_with_gpu_suffix_is_not_a_version(self):
        self.assertIsNone(_strip_variant("v1.51.0-gpu", "serving"))

    def test_non_version_tags(self):
        for tag, component in (("latest", "standalone"), ("local", "backend"), ("v1.51", "frontend"),
                               ("latest-gpu", "serving_gpu"), ("v1.51-gpu", "serving_gpu"), ("-gpu", "serving_gpu"),
                               ("main", "standalone"), ("1.51.0", "standalone")):
            with self.subTest(tag=tag, component=component):
                self.assertIsNone(_strip_variant(tag, component))


# --------------------------------------------------------------------------
class DecisionTests(PlanTestCase):
    def test_tags_equal_to_target_are_up_to_date(self):
        plan = self.plan(self.standalone(TARGET))
        self.assertEqual(plan["decision"], UP_TO_DATE)
        self.assertEqual(plan["env_changes"], {})
        self.assertEqual(plan["reasons"], [])
        self.assertEqual(plan["notes"], [])
        self.assertFalse(plan["resume"])
        self.assertEqual([item["action"] for item in plan["components"]], ["keep"])
        self.assertEqual(plan["current_versions"], [TARGET])

    def test_older_tags_are_update_available(self):
        plan = self.plan(self.standalone(PREVIOUS))
        self.assertEqual(plan["decision"], UPDATE_AVAILABLE, plan["reasons"])
        self.assertEqual(plan["env_changes"], {"FUTURE_AGI_VERSION": TARGET})
        self.assertEqual(self.component(plan, "standalone")["action"], "update")
        self.assertFalse(plan["resume"])
        self.assertEqual(plan["current_versions"], [PREVIOUS])

    def test_distributed_env_changes_name_every_version_variable_once(self):
        plan = self.plan(self.distributed(PREVIOUS))
        self.assertEqual(plan["decision"], UPDATE_AVAILABLE, plan["reasons"])
        self.assertEqual(plan["env_changes"], {
            "AGENTCC_GATEWAY_VERSION": TARGET, "CODE_EXECUTOR_VERSION": TARGET, "FI_COLLECTOR_VERSION": TARGET,
            "FRONTEND_VERSION": TARGET, "FUTURE_AGI_VERSION": TARGET, "SERVING_VERSION": TARGET,
            "SIMULATION_RUNNER_VERSION": TARGET,
        })

    def test_env_changes_only_hold_version_keys_that_change(self):
        installation = self.distributed(TARGET, tags={"FRONTEND_VERSION": PREVIOUS, "SERVING_VERSION": "v1.51.4"})
        plan = self.plan(installation)
        self.assertEqual(plan["decision"], UPDATE_AVAILABLE, plan["reasons"])
        self.assertEqual(plan["env_changes"], {"FRONTEND_VERSION": TARGET, "SERVING_VERSION": TARGET})
        self.assertEqual(self.component(plan, "backend")["action"], "keep")
        self.assertEqual(self.component(plan, "frontend")["action"], "update")
        self.assertEqual(plan["current_versions"], [PREVIOUS, "v1.51.4", TARGET])

    def test_env_changes_never_contain_non_version_keys(self):
        plan = self.plan(self.distributed(PREVIOUS))
        self.assertTrue(set(plan["env_changes"]) <= set(COMPONENT_VARIABLES.values()))
        self.assertNotIn("SECRET_KEY", json.dumps(plan))
        self.assertNotIn("not-a-real-secret-value", json.dumps(plan))

    def test_component_record_for_a_version_tag(self):
        manifest = self.manifest()
        item = self.component(self.plan(self.standalone(PREVIOUS), manifest=manifest), "standalone")
        self.assertEqual(item, {
            "component": "standalone", "variable": "FUTURE_AGI_VERSION", "services": ["app"],
            "configured": PREVIOUS, "current_version": PREVIOUS, "current_source": "FUTURE_AGI_VERSION in .env",
            "current_tags": [PREVIOUS], "target_tag": TARGET,
            "target_image": f"docker.io/futureagi/standalone:{TARGET}",
            "target_digest": manifest["components"]["standalone"]["digest"], "action": "update",
        })

    def test_components_sorted_by_name_and_services_grouped(self):
        plan = self.plan(self.distributed(PREVIOUS))
        names = [item["component"] for item in plan["components"]]
        self.assertEqual(names, sorted(names))
        self.assertEqual(names, ["agentcc_gateway", "backend", "code_executor", "fi_collector", "frontend",
                                 "serving", "simulation_runner"])
        self.assertEqual(self.component(plan, "backend")["services"], ["backend", "worker"])

    def test_non_futureagi_services_are_not_planned(self):
        plan = self.plan(self.distributed(PREVIOUS))
        planned_services = {s for item in plan["components"] for s in item["services"]}
        self.assertNotIn("postgres", planned_services)
        self.assertNotIn("redis", planned_services)

    def test_plan_header(self):
        manifest = self.manifest()
        installation = self.standalone(PREVIOUS, project="acme-fagi")
        plan = self.plan(installation, manifest=manifest, source="https://example.invalid/m.json")
        self.assertEqual(plan["schema_version"], 2)
        self.assertEqual(plan["target_version"], TARGET)
        self.assertEqual(plan["channel"], "stable")
        self.assertEqual(plan["published_at"], "2026-10-19T00:00:00Z")
        self.assertEqual(plan["minimum_version"], PREVIOUS)
        self.assertEqual(plan["policy"], "minor")
        self.assertEqual(plan["manifest_hash"], manifest_hash(manifest))
        self.assertEqual(plan["manifest_source"], "https://example.invalid/m.json")
        self.assertEqual(plan["installation"], {"mode": "standalone", "project": "acme-fagi", "env_file": ".env",
                                                "root": str(self.root)})
        self.assertEqual(plan["signature"], VERIFIED.as_dict())
        self.assertEqual(plan["deployment_drift"], [])
        self.assertEqual(plan["missing_env_keys"], [])

    def test_manifest_source_defaults_to_empty(self):
        plan = build_plan(self.manifest(), VERIFIED, self.standalone(), self.settings())
        self.assertEqual(plan["manifest_source"], "")

    def test_plan_has_exactly_the_documented_keys(self):
        plan = self.plan(self.standalone())
        self.assertEqual(set(plan), {
            "schema_version", "decision", "resume", "next_command", "target_version", "channel", "published_at",
            "minimum_version", "policy", "manifest_hash", "manifest_source", "installation", "current_versions",
            "signature", "components", "env_changes", "deployment_drift", "missing_env_keys", "reasons", "notes",
            "before_next_command", "staged_version", "stopped_version", "stopped_kind", "command_options",
            "step_notes",
        })

    def test_building_a_plan_changes_nothing(self):
        manifest = self.manifest()
        before_manifest = copy.deepcopy(manifest)
        installation = self.distributed(PREVIOUS)
        before_env = dict(installation.env)
        before_files = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.plan(installation, manifest=manifest)
        self.assertEqual(manifest, before_manifest)
        self.assertEqual(installation.env, before_env)
        self.assertEqual({p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}, before_files)

    def test_config_is_read_from_the_installation(self):
        installation = self.standalone()
        self.plan(installation)
        self.assertGreaterEqual(installation.config_calls, 1)

    def test_current_versions_are_unique_and_sorted_by_version(self):
        manifest = self.manifest(version="v1.12.0", minimum="v1.0.0")
        installation = self.distributed("v1.10.0", tags={"FRONTEND_VERSION": "v1.9.0", "SERVING_VERSION": "v1.9.0"})
        plan = self.plan(installation, manifest=manifest)
        self.assertEqual(plan["current_versions"], ["v1.9.0", "v1.10.0"])

    def test_prerelease_install_updates_to_its_release(self):
        plan = self.plan(self.standalone("v1.52.0-rc.2"))
        self.assertEqual(plan["decision"], UPDATE_AVAILABLE, plan["reasons"])
        self.assertEqual(plan["env_changes"], {"FUTURE_AGI_VERSION": TARGET})

    def test_beta_channel_install_takes_a_beta_manifest(self):
        manifest = self.manifest(version="v1.52.0-beta.1", channel="beta")
        plan = self.plan(self.standalone(PREVIOUS), manifest=manifest, settings=self.settings(channel="beta"))
        self.assertEqual(plan["decision"], UPDATE_AVAILABLE, plan["reasons"])
        self.assertEqual(plan["env_changes"], {"FUTURE_AGI_VERSION": "v1.52.0-beta.1"})
        self.assertEqual(plan["channel"], "beta")


# --------------------------------------------------------------------------
class LatestInstallTests(PlanTestCase):
    """Installs whose tags name no release: the running image's OCI version label decides."""

    def test_latest_with_older_label_updates_and_pins(self):
        plan = self.plan(self.standalone("latest", label=PREVIOUS))
        item = self.component(plan, "standalone")
        self.assertEqual(plan["decision"], UPDATE_AVAILABLE, plan["reasons"])
        self.assertEqual(item["current_version"], PREVIOUS)
        self.assertEqual(item["current_source"], "running image (follows :latest)")
        self.assertEqual(item["current_tags"], ["latest"])
        self.assertEqual(item["configured"], "latest")
        self.assertEqual(item["action"], "update")
        self.assertEqual(plan["env_changes"], {"FUTURE_AGI_VERSION": TARGET})
        self.assertEqual(plan["current_versions"], [PREVIOUS])

    def test_latest_already_at_target_is_up_to_date_without_restart(self):
        plan = self.plan(self.standalone("latest", label=TARGET))
        item = self.component(plan, "standalone")
        self.assertEqual(item["action"], "pin")
        self.assertEqual(item["current_version"], TARGET)
        self.assertEqual(plan["decision"], UP_TO_DATE, plan["reasons"])
        self.assertEqual(plan["env_changes"], {})
        self.assertIn("images that follow :latest are pinned to the release's version tag when an update runs",
                      plan["notes"])

    def test_pin_is_written_when_another_component_updates(self):
        installation = self.distributed(PREVIOUS, tags={"FRONTEND_VERSION": "latest"},
                                        labels={"frontend": TARGET})
        plan = self.plan(installation)
        self.assertEqual(self.component(plan, "frontend")["action"], "pin")
        self.assertEqual(plan["decision"], UPDATE_AVAILABLE, plan["reasons"])
        self.assertEqual(plan["env_changes"]["FRONTEND_VERSION"], TARGET)
        self.assertEqual(plan["env_changes"]["FUTURE_AGI_VERSION"], TARGET)
        self.assertIn("images that follow :latest are pinned to the release's version tag when an update runs",
                      plan["notes"])

    def test_untagged_image_follows_latest(self):
        installation = FakeInstallation(self.root, services={"app": "futureagi/standalone"},
                                        env={"FUTURE_AGI_VERSION": "", "SECRET_KEY": "x"}).run("app", PREVIOUS)
        plan = self.plan(installation)
        item = self.component(plan, "standalone")
        self.assertEqual(item["current_tags"], ["latest"])
        self.assertEqual(item["current_version"], PREVIOUS)
        self.assertEqual(item["configured"], "")
        self.assertEqual(plan["decision"], UPDATE_AVAILABLE, plan["reasons"])

    def test_minor_alias_tag_uses_the_label(self):
        plan = self.plan(self.standalone("v1.51", label="v1.51.3"))
        item = self.component(plan, "standalone")
        self.assertEqual(item["current_version"], "v1.51.3")
        self.assertEqual(item["current_source"], "running image (follows :v1.51)")
        self.assertEqual(plan["env_changes"], {"FUTURE_AGI_VERSION": TARGET})

    def test_minor_alias_at_target_is_pinned_not_kept(self):
        plan = self.plan(self.standalone("v1.52", label=TARGET))
        self.assertEqual(self.component(plan, "standalone")["action"], "pin")
        self.assertEqual(plan["decision"], UP_TO_DATE, plan["reasons"])

    def test_latest_without_any_label_is_blocked_with_a_hint(self):
        installation = self.standalone("latest", label="", extra_env={"FUTURE_AGI_VERSION": ""})
        plan = self.plan(installation)
        self.assertBlocked(plan)
        self.assertReason(plan, (
            "cannot tell which version of standalone is installed: FUTURE_AGI_VERSION is unset and no running "
            "container carries a version label. Set FUTURE_AGI_VERSION=vX.Y.Z to the release you run, then "
            "check again"))
        item = self.component(plan, "standalone")
        self.assertIsNone(item["current_version"])
        self.assertEqual(item["current_source"], "")
        self.assertEqual(plan["current_versions"], [])

    def test_latest_without_label_names_the_configured_value(self):
        plan = self.plan(self.standalone("latest", label=""))
        self.assertReasonContaining(plan, "FUTURE_AGI_VERSION is latest and no running container carries a version label")

    def test_variable_absent_from_env_reads_as_unset(self):
        installation = FakeInstallation(self.root, services={"app": "futureagi/standalone:latest"},
                                        env={}).run("app", "")
        plan = self.plan(installation)
        self.assertReasonContaining(plan, "FUTURE_AGI_VERSION is unset")

    def test_non_version_label_does_not_count(self):
        for label in ("dev", "1.51.0", "v1.51", "latest", "v1.51.0-gpu"):
            with self.subTest(label=label):
                plan = self.plan(self.standalone("latest", label=label))
                self.assertBlocked(plan)
                self.assertReasonContaining(plan, "cannot tell which version of standalone is installed")

    def test_non_version_labels_are_ignored_next_to_a_version_label(self):
        installation = self.distributed(PREVIOUS, tags={"FUTURE_AGI_VERSION": "latest"},
                                        labels={"backend": PREVIOUS, "worker": "dev"})
        plan = self.plan(installation)
        self.assertEqual(self.component(plan, "backend")["current_version"], PREVIOUS)
        self.assertNoReasonContaining(plan, "different versions")

    def test_latest_without_containers_is_blocked(self):
        installation = FakeInstallation(self.root, services={"app": "futureagi/standalone:latest"},
                                        env={"FUTURE_AGI_VERSION": "latest"})
        plan = self.plan(installation)
        self.assertBlocked(plan)
        self.assertReasonContaining(plan, "cannot tell which version of standalone is installed")
        self.assertEqual(installation.image_calls, [])

    def test_container_without_image_id_is_not_inspected(self):
        installation = FakeInstallation(self.root, services={"app": "futureagi/standalone:latest"},
                                        env={"FUTURE_AGI_VERSION": "latest"}).run("app", PREVIOUS, image_id="")
        plan = self.plan(installation)
        self.assertEqual(installation.image_calls, [])
        self.assertReasonContaining(plan, "cannot tell which version of standalone is installed")

    def test_uninspectable_image_is_ignored(self):
        installation = FakeInstallation(self.root, services={"app": "futureagi/standalone:latest"},
                                        env={"FUTURE_AGI_VERSION": "latest"}).run("app", PREVIOUS, inspectable=False)
        plan = self.plan(installation)
        self.assertEqual(len(installation.image_calls), 1)
        self.assertReasonContaining(plan, "cannot tell which version of standalone is installed")

    def test_containers_of_other_services_are_not_inspected(self):
        installation = self.standalone(PREVIOUS)
        postgres_ids = [c["image_id"] for c in installation.containers() if c["service"] == "postgres"]
        self.plan(installation)
        self.assertTrue(postgres_ids)
        for image_id in postgres_ids:
            self.assertNotIn(image_id, installation.image_calls)

    def test_containers_of_services_missing_from_config_are_ignored(self):
        installation = self.standalone("latest", label="").run("old-app", PREVIOUS)
        plan = self.plan(installation)
        self.assertReasonContaining(plan, "cannot tell which version of standalone is installed")

    def test_two_containers_with_different_labels_are_blocked(self):
        installation = self.distributed(PREVIOUS, tags={"FUTURE_AGI_VERSION": "latest"},
                                        labels={"backend": "v1.50.0", "worker": PREVIOUS})
        plan = self.plan(installation)
        self.assertBlocked(plan)
        self.assertReason(plan, "backend containers run different versions (v1.50.0, v1.51.0)")
        self.assertIsNone(self.component(plan, "backend")["current_version"])

    def test_two_containers_of_one_service_with_different_labels_are_blocked(self):
        installation = FakeInstallation(self.root, services={"app": "futureagi/standalone:latest"},
                                        env={"FUTURE_AGI_VERSION": "latest", "SECRET_KEY": "x"})
        installation.run("app", PREVIOUS).run("app", TARGET)
        plan = self.plan(installation)
        self.assertReason(plan, "standalone containers run different versions (v1.51.0, v1.52.0)")

    def test_two_containers_with_the_same_label_agree(self):
        installation = self.distributed(PREVIOUS, tags={"FUTURE_AGI_VERSION": "latest"},
                                        labels={"backend": PREVIOUS, "worker": PREVIOUS})
        plan = self.plan(installation)
        self.assertEqual(self.component(plan, "backend")["current_version"], PREVIOUS)
        self.assertEqual(plan["decision"], UPDATE_AVAILABLE, plan["reasons"])

    def test_labels_are_ignored_when_the_tag_names_a_release(self):
        plan = self.plan(self.standalone(PREVIOUS, label="v1.40.0"))
        item = self.component(plan, "standalone")
        self.assertEqual(item["current_version"], PREVIOUS)
        self.assertEqual(item["current_source"], "FUTURE_AGI_VERSION in .env")
        self.assertNoReasonContaining(plan, "older than")

    def test_latest_label_newer_than_release_is_a_downgrade(self):
        plan = self.plan(self.standalone("latest", label="v1.53.0"))
        self.assertBlocked(plan)
        self.assertReason(plan, "standalone runs v1.53.0, newer than this release (v1.52.0); bin/update never downgrades")

    def test_latest_label_older_than_minimum_is_blocked(self):
        plan = self.plan(self.standalone("latest", label="v1.50.0"))
        self.assertBlocked(plan)
        self.assertReasonContaining(plan, "this install runs v1.50.0, older than v1.51.0")


# --------------------------------------------------------------------------
class MixedAndLocalTagTests(PlanTestCase):
    def test_mixed_tags_across_services_of_one_component_are_blocked(self):
        installation = self.distributed(PREVIOUS)
        installation._config["services"]["worker"]["image"] = "futureagi/future-agi:latest"
        plan = self.plan(installation)
        self.assertBlocked(plan)
        self.assertReason(plan, "backend services resolve to different tags (v1.51.0, latest)")
        item = self.component(plan, "backend")
        self.assertEqual(item["current_tags"], [PREVIOUS, "latest"])
        self.assertIsNone(item["current_version"])
        self.assertNotIn(None, plan["current_versions"])

    def test_two_different_version_tags_are_blocked(self):
        installation = self.distributed(PREVIOUS)
        installation._config["services"]["worker"]["image"] = "futureagi/future-agi:v1.50.0"
        plan = self.plan(installation)
        self.assertReason(plan, "backend services resolve to different tags (v1.51.0, v1.50.0)")

    def test_same_tag_on_many_services_is_listed_once(self):
        item = self.component(self.plan(self.distributed(PREVIOUS)), "backend")
        self.assertEqual(item["current_tags"], [PREVIOUS])

    def test_local_standalone_is_blocked(self):
        plan = self.plan(self.standalone("local", label=PREVIOUS))
        self.assertBlocked(plan)
        self.assertReason(plan, (
            "standalone runs images built from this checkout (FUTURE_AGI_VERSION=local). bin/update manages "
            "published releases only; keep using `./bin/install --from-source`"))
        self.assertIsNone(self.component(plan, "standalone")["current_version"])

    def test_local_backend_in_distributed_is_blocked(self):
        plan = self.plan(self.distributed(PREVIOUS, tags={"FUTURE_AGI_VERSION": "local"}))
        self.assertBlocked(plan)
        self.assertReason(plan, (
            "backend runs images built from this checkout (FUTURE_AGI_VERSION=local). bin/update manages "
            "published releases only; keep using `./bin/install --from-source`"))

    def test_local_fi_collector_in_distributed_is_a_note(self):
        plan = self.plan(self.distributed(TARGET, tags={"FI_COLLECTOR_VERSION": "local"}))
        self.assertEqual(plan["decision"], UPDATE_AVAILABLE, plan["reasons"])
        self.assertEqual(plan["reasons"], [])
        self.assertIn(
            "fi-collector is built from this checkout today (FI_COLLECTOR_VERSION unset); the update switches it "
            "to the published futureagi/fi-collector image of the release", plan["notes"])
        item = self.component(plan, "fi_collector")
        self.assertEqual(item["action"], "update")
        self.assertIsNone(item["current_version"])
        self.assertEqual(item["current_source"], "built from this checkout")
        self.assertEqual(plan["env_changes"], {"FI_COLLECTOR_VERSION": TARGET})
        self.assertEqual(plan["current_versions"], [TARGET])

    def test_local_fi_collector_does_not_count_toward_policy_or_minimum(self):
        plan = self.plan(self.distributed(PREVIOUS, tags={"FI_COLLECTOR_VERSION": "local"}),
                         settings=self.settings(policy="patch"))
        self.assertNoReasonContaining(plan, "fi_collector")
        self.assertReasonContaining(plan, "v1.51.0 -> v1.52.0 is outside the 'patch' update policy")

    def test_local_fi_collector_in_production_is_blocked(self):
        installation = self.distributed(TARGET, tags={"FI_COLLECTOR_VERSION": "local"}, mode="production",
                                        production=True)
        plan = self.plan(installation)
        self.assertBlocked(plan)
        self.assertReasonContaining(plan, "fi_collector runs images built from this checkout (FI_COLLECTOR_VERSION=local)")

    def test_local_fi_collector_in_standalone_is_blocked(self):
        installation = self.standalone(TARGET)
        installation._config["services"]["fi-collector"] = {"image": "futureagi/fi-collector:local"}
        plan = self.plan(installation)
        self.assertBlocked(plan)
        self.assertReasonContaining(plan, "fi_collector runs images built from this checkout")


# --------------------------------------------------------------------------
class VersionRuleTests(PlanTestCase):
    def test_downgrade_is_blocked_per_component(self):
        installation = self.distributed(TARGET, tags={"FRONTEND_VERSION": "v1.53.0"})
        plan = self.plan(installation)
        self.assertBlocked(plan)
        self.assertReason(plan, "frontend runs v1.53.0, newer than this release (v1.52.0); bin/update never downgrades")
        self.assertEqual([r for r in plan["reasons"] if "never downgrades" in r], [plan["reasons"][0]])
        self.assertEqual(self.component(plan, "frontend")["action"], "update")
        self.assertEqual(self.component(plan, "backend")["action"], "keep")

    def test_each_newer_component_is_named(self):
        installation = self.distributed("v1.53.1", tags={"FRONTEND_VERSION": "v1.53.0"})
        plan = self.plan(installation)
        downgrades = [r for r in plan["reasons"] if "never downgrades" in r]
        self.assertEqual(len(downgrades), len(plan["components"]))
        self.assertIn("frontend runs v1.53.0, newer than this release (v1.52.0); bin/update never downgrades",
                      downgrades)
        self.assertNoReasonContaining(plan, "update policy")

    def test_release_is_newer_than_its_prerelease_install(self):
        manifest = self.manifest(version="v1.52.0-rc.1", channel="beta")
        plan = self.plan(self.standalone(TARGET), manifest=manifest, settings=self.settings(channel="beta"))
        self.assertReason(plan, "standalone runs v1.52.0, newer than this release (v1.52.0-rc.1); bin/update never downgrades")

    def test_newer_prerelease_is_a_downgrade(self):
        manifest = self.manifest(version="v1.52.0-beta.1", channel="beta")
        plan = self.plan(self.standalone("v1.52.0-rc.1"), manifest=manifest, settings=self.settings(channel="beta"))
        self.assertReasonContaining(plan, "newer than this release (v1.52.0-beta.1)")

    def test_minimum_version_uses_the_oldest_component(self):
        installation = self.distributed(PREVIOUS, tags={"FRONTEND_VERSION": "v1.50.9"})
        plan = self.plan(installation)
        self.assertBlocked(plan)
        self.assertReason(plan, (
            "this install runs v1.50.9, older than v1.51.0, the oldest release v1.52.0 can be applied to in place. "
            "Upgrade first: check out v1.51.0, set the version keys in .env to v1.51.0, then run `./bin/install` "
            "(it keeps pinned keys as they are; INSTALLATION.md › Upgrading)"))

    def test_minimum_version_is_inclusive(self):
        plan = self.plan(self.standalone(PREVIOUS), manifest=self.manifest(minimum=PREVIOUS))
        self.assertEqual(plan["decision"], UPDATE_AVAILABLE, plan["reasons"])
        self.assertNoReasonContaining(plan, "older than")

    def test_prerelease_of_the_minimum_is_older_than_it(self):
        plan = self.plan(self.standalone("v1.51.0-rc.3"))
        self.assertReasonContaining(plan, "this install runs v1.51.0-rc.3, older than v1.51.0")

    def test_minimum_equal_to_target(self):
        plan = self.plan(self.standalone(PREVIOUS), manifest=self.manifest(minimum=TARGET))
        self.assertReasonContaining(plan, "this install runs v1.51.0, older than v1.52.0")
        self.assertEqual(self.plan(self.standalone(TARGET), manifest=self.manifest(minimum=TARGET))["decision"],
                         UP_TO_DATE)

    def test_patch_policy_allows_a_patch(self):
        plan = self.plan(self.standalone("v1.52.0"), manifest=self.manifest(version="v1.52.3"),
                         settings=self.settings(policy="patch"))
        self.assertEqual(plan["decision"], UPDATE_AVAILABLE, plan["reasons"])
        self.assertEqual(plan["policy"], "patch")

    def test_patch_policy_refuses_a_minor(self):
        plan = self.plan(self.standalone("v1.51.9"), settings=self.settings(policy="patch"))
        self.assertBlocked(plan)
        self.assertReason(plan, (
            "v1.51.9 -> v1.52.0 is outside the 'patch' update policy (FUTURE_AGI_UPDATE_POLICY, from command "
            "line); rerun with --policy major to allow it"))

    def test_minor_policy_allows_a_minor(self):
        plan = self.plan(self.standalone("v1.51.9"))
        self.assertEqual(plan["decision"], UPDATE_AVAILABLE, plan["reasons"])

    def test_minor_policy_refuses_a_major_and_names_the_default(self):
        manifest = self.manifest(version="v2.0.0", minimum="v1.0.0")
        plan = self.plan(self.standalone("v1.99.0"), manifest=manifest)
        self.assertBlocked(plan)
        self.assertReason(plan, (
            "v1.99.0 -> v2.0.0 is outside the 'minor' update policy (FUTURE_AGI_UPDATE_POLICY, from default); "
            "rerun with --policy major to allow it"))

    def test_major_policy_allows_a_major(self):
        manifest = self.manifest(version="v2.0.0", minimum="v1.0.0")
        plan = self.plan(self.standalone("v1.99.0"), manifest=manifest, settings=self.settings(policy="major"))
        self.assertEqual(plan["decision"], UPDATE_AVAILABLE, plan["reasons"])

    def test_policy_uses_the_oldest_component(self):
        installation = self.distributed("v1.52.2", tags={"FRONTEND_VERSION": "v1.51.9"})
        plan = self.plan(installation, manifest=self.manifest(version="v1.52.3"),
                         settings=self.settings(policy="patch"))
        self.assertReasonContaining(plan, "v1.51.9 -> v1.52.3 is outside the 'patch' update policy")
        self.assertNoReasonContaining(plan, "v1.52.2 ->")

    def test_policy_not_applied_when_up_to_date(self):
        plan = self.plan(self.standalone(TARGET), settings=self.settings(policy="patch"))
        self.assertEqual(plan["decision"], UP_TO_DATE, plan["reasons"])

    def test_policy_not_applied_to_a_downgrade(self):
        plan = self.plan(self.standalone("v2.0.0"), settings=self.settings(policy="patch"))
        self.assertNoReasonContaining(plan, "update policy")
        self.assertReasonContaining(plan, "never downgrades")

    def test_policy_source_environment(self):
        plan = self.plan(self.standalone("v1.51.9"),
                         settings=self.settings(environ={"FUTURE_AGI_UPDATE_POLICY": "patch"}))
        self.assertReasonContaining(plan, "(FUTURE_AGI_UPDATE_POLICY, from environment)")

    def test_policy_source_update_conf(self):
        self.write("update.conf", "FUTURE_AGI_UPDATE_POLICY=patch\n")
        plan = self.plan(self.standalone("v1.51.9"))
        self.assertReasonContaining(plan, "(FUTURE_AGI_UPDATE_POLICY, from update.conf)")

    def test_minimum_and_policy_can_both_block(self):
        manifest = self.manifest(version="v2.0.0", minimum="v1.60.0")
        plan = self.plan(self.standalone("v1.51.0"), manifest=manifest)
        self.assertReasonContaining(plan, "older than v1.60.0")
        self.assertReasonContaining(plan, "outside the 'minor' update policy")

    def test_downgrade_and_minimum_on_different_components(self):
        installation = self.distributed(PREVIOUS, tags={"FRONTEND_VERSION": "v1.53.0", "SERVING_VERSION": "v1.50.0"})
        plan = self.plan(installation)
        self.assertReasonContaining(plan, "frontend runs v1.53.0")
        self.assertReasonContaining(plan, "this install runs v1.50.0, older than v1.51.0")


# --------------------------------------------------------------------------
class TrustAndSourceTests(PlanTestCase):
    def test_channel_mismatch_is_blocked(self):
        manifest = self.manifest(version="v1.52.0-beta.1", channel="beta")
        plan = self.plan(self.standalone(PREVIOUS), manifest=manifest)
        self.assertBlocked(plan)
        self.assertReason(plan, "the manifest is for the beta channel, but this install follows stable "
                                "(FUTURE_AGI_UPDATE_CHANNEL)")
        self.assertEqual(plan["reasons"][0], "the manifest is for the beta channel, but this install follows stable "
                                             "(FUTURE_AGI_UPDATE_CHANNEL)")

    def test_stable_manifest_on_beta_channel_is_blocked(self):
        plan = self.plan(self.standalone(PREVIOUS), settings=self.settings(channel="beta"))
        self.assertReason(plan, "the manifest is for the stable channel, but this install follows beta "
                                "(FUTURE_AGI_UPDATE_CHANNEL)")

    def test_unverified_signature_is_blocked(self):
        verification = Verification(False, method="keyless", signer="https://x/wf@refs/tags/v1.52.0",
                                    error="signature verification failed: none of the expected identities matched")
        plan = self.plan(self.standalone(PREVIOUS), verification=verification)
        self.assertBlocked(plan)
        self.assertReason(plan, "the release signature is not verified: signature verification failed: none of the "
                                "expected identities matched")
        self.assertEqual(plan["signature"], verification.as_dict())
        self.assertFalse(plan["signature"]["verified"])

    def test_unverified_signature_blocks_an_up_to_date_install(self):
        plan = self.plan(self.standalone(TARGET), verification=Verification(False, error="no signature bundle"))
        self.assertBlocked(plan)
        self.assertReason(plan, "the release signature is not verified: no signature bundle")
        self.assertEqual(plan["env_changes"], {})

    def test_registry_mismatch_is_blocked(self):
        installation = FakeInstallation(self.root, services={"app": f"ghcr.io/futureagi/standalone:{PREVIOUS}"},
                                        env={"FUTURE_AGI_VERSION": PREVIOUS, "SECRET_KEY": "x"}).run("app", PREVIOUS)
        plan = self.plan(installation)
        self.assertBlocked(plan)
        self.assertReason(plan, (
            "service app pulls ghcr.io/futureagi/standalone, but the release names docker.io/futureagi/standalone "
            "for standalone. bin/update verifies images only from the registry the release publishes to (docker.io) "
            "and does not support registry mirrors yet: update this install by hand (INSTALLATION.md › Upgrading), "
            "or point app at docker.io/futureagi/standalone, then check again"))

    def test_mirror_manifest_against_docker_hub_install_is_blocked(self):
        plan = self.plan(self.standalone(PREVIOUS), manifest=self.manifest(registry="registry.example.com:5000"))
        self.assertReasonContaining(plan, "service app pulls docker.io/futureagi/standalone, but the release names "
                                          "registry.example.com:5000/futureagi/standalone")

    def test_mirror_manifest_matches_a_mirror_install(self):
        installation = FakeInstallation(
            self.root, services={"app": f"registry.example.com:5000/futureagi/standalone:{PREVIOUS}"},
            env={"FUTURE_AGI_VERSION": PREVIOUS, "SECRET_KEY": "x"}).run("app", PREVIOUS)
        plan = self.plan(installation, manifest=self.manifest(registry="registry.example.com:5000"))
        self.assertEqual(plan["decision"], UPDATE_AVAILABLE, plan["reasons"])
        self.assertEqual(self.component(plan, "standalone")["target_image"],
                         f"registry.example.com:5000/futureagi/standalone:{TARGET}")

    def test_docker_hub_spellings_are_the_same_registry(self):
        for image in (f"futureagi/standalone:{PREVIOUS}", f"docker.io/futureagi/standalone:{PREVIOUS}",
                      f"index.docker.io/futureagi/standalone:{PREVIOUS}",
                      f"registry-1.docker.io/futureagi/standalone:{PREVIOUS}"):
            with self.subTest(image=image):
                installation = FakeInstallation(self.root, services={"app": image},
                                                env={"FUTURE_AGI_VERSION": PREVIOUS, "SECRET_KEY": "x"})
                installation.run("app", PREVIOUS)
                plan = self.plan(installation)
                self.assertNoReasonContaining(plan, "registry")
                self.assertEqual(plan["decision"], UPDATE_AVAILABLE, plan["reasons"])

    def test_registry_host_case_does_not_matter(self):
        installation = FakeInstallation(self.root, services={"app": f"GHCR.IO/futureagi/standalone:{PREVIOUS}"},
                                        env={"FUTURE_AGI_VERSION": PREVIOUS, "SECRET_KEY": "x"}).run("app", PREVIOUS)
        plan = self.plan(installation, manifest=self.manifest(registry="ghcr.io"))
        self.assertNoReasonContaining(plan, "registry")

    def test_registry_mismatch_names_each_service(self):
        installation = self.distributed(PREVIOUS)
        installation._config["services"]["worker"]["image"] = f"ghcr.io/futureagi/future-agi:{PREVIOUS}"
        plan = self.plan(installation)
        self.assertReasonContaining(plan, "service worker pulls ghcr.io/futureagi/future-agi")
        self.assertNoReasonContaining(plan, "service backend pulls")

    def test_compose_digest_pin_is_blocked(self):
        pinned = f"futureagi/standalone:{PREVIOUS}@sha256:{'a' * 64}"
        installation = FakeInstallation(self.root, services={"app": pinned},
                                        env={"FUTURE_AGI_VERSION": PREVIOUS, "SECRET_KEY": "x"}).run("app", PREVIOUS)
        plan = self.plan(installation)
        self.assertBlocked(plan)
        self.assertReason(plan, (
            f"service app pins {pinned} by digest in a compose file; bin/update only changes the version tags in the "
            "env file. Remove that digest pin (deploy/README.md › Pin a release) before updating"))

    def test_digest_pin_blocks_even_at_the_target(self):
        pinned = f"futureagi/standalone:{TARGET}@sha256:{'b' * 64}"
        installation = FakeInstallation(self.root, services={"app": pinned},
                                        env={"FUTURE_AGI_VERSION": TARGET, "SECRET_KEY": "x"}).run("app", TARGET)
        plan = self.plan(installation)
        self.assertBlocked(plan)
        self.assertReasonContaining(plan, "by digest in a compose file")

    def test_digest_only_reference_is_blocked(self):
        pinned = f"futureagi/standalone@sha256:{'c' * 64}"
        installation = FakeInstallation(self.root, services={"app": pinned}, env={"SECRET_KEY": "x"}).run("app", PREVIOUS)
        plan = self.plan(installation)
        self.assertBlocked(plan)
        self.assertReasonContaining(plan, f"service app pins {pinned} by digest")
        self.assertEqual(self.component(plan, "standalone")["current_tags"], ["latest"])

    def test_check_stack_reasons_are_propagated(self):
        stack = ["project 'futureagi' runs Distributed services (backend) but .env selects the Standalone stack."]
        installation = self.standalone(TARGET, stack_reasons=stack)
        plan = self.plan(installation)
        self.assertBlocked(plan)
        self.assertEqual(plan["reasons"], stack)
        self.assertEqual([c["service"] for c in installation.checked_containers], ["app", "postgres"])

    def test_no_futureagi_services_is_blocked(self):
        installation = FakeInstallation(self.root, services={
            "postgres": "postgres:16-alpine",
            "builder": {"build": {"context": "."}},
            "broken": {"image": "futureagi/Standalone:v1.51.0"},
            "lookalike": {"image": "futureagi-standalone:v1.51.0"},
        }, env={"SECRET_KEY": "x"}).run("postgres", "16.4")
        plan = self.plan(installation)
        self.assertBlocked(plan)
        self.assertReason(plan, "the resolved Compose configuration has no Future AGI services")
        self.assertEqual(plan["components"], [])
        self.assertEqual(plan["current_versions"], [])
        self.assertEqual(plan["env_changes"], {})

    def test_config_without_services_is_blocked(self):
        installation = FakeInstallation(self.root, services={}, env={"SECRET_KEY": "x"})
        installation._config.pop("services")
        plan = self.plan(installation)
        self.assertReason(plan, "the resolved Compose configuration has no Future AGI services")

    def test_every_problem_is_reported_at_once(self):
        installation = self.distributed(PREVIOUS, tags={"FRONTEND_VERSION": "v1.53.0"},
                                        stack_reasons=["stack problem"])
        manifest = self.manifest(version="v1.52.0-beta.1", channel="beta")
        self.write("docker-compose.yml", "drifted\n")
        plan = self.plan(installation, manifest=manifest, verification=Verification(False, error="bad"),
                         last_receipt={"kind": "rollback", "status": "failed_during_rollout",
                                       "rolls_back": "/state/r.json"})
        joined = "\n".join(plan["reasons"])
        for fragment in ("beta channel", "not verified: bad", "stack problem", "never downgrades",
                         "deployment files differ", "a rollback stopped"):
            self.assertIn(fragment, joined)
        self.assertEqual(plan["reasons"][:3], [
            "the manifest is for the beta channel, but this install follows stable (FUTURE_AGI_UPDATE_CHANNEL)",
            "the release signature is not verified: bad", "stack problem"])


# --------------------------------------------------------------------------
class DeploymentDriftTests(PlanTestCase):
    def test_matching_files_are_not_drift(self):
        plan = self.plan(self.standalone(PREVIOUS))
        self.assertEqual(plan["deployment_drift"], [])

    def test_modified_file_is_drift(self):
        manifest = self.manifest()
        self.write("docker-compose.yml", "services: {changed: true}\n")
        plan = self.plan(self.standalone(PREVIOUS), manifest=manifest)
        self.assertBlocked(plan)
        self.assertEqual(plan["deployment_drift"], ["docker-compose.yml"])
        # Round 6 (U33): this checkout is no git work tree, so no git command is printed.
        self.assertReason(plan, (
            f"this checkout's deployment files differ from v1.52.0's (docker-compose.yml). New images must run with "
            f"their own compose files, and git cannot be used in this checkout ({self.root}) to put it back: replace "
            "it with release v1.52.0's version (your env file stays as it is), then check again"))

    def test_drift_blocks_an_up_to_date_install(self):
        manifest = self.manifest()
        self.write("docker-compose.yml", "other\n")
        plan = self.plan(self.standalone(TARGET), manifest=manifest)
        self.assertBlocked(plan)

    def test_missing_file_is_drift(self):
        manifest = self.manifest()
        (self.root / "docker-compose.yml").unlink()
        plan = self.plan(self.standalone(PREVIOUS), manifest=manifest)
        self.assertEqual(plan["deployment_drift"], ["docker-compose.yml"])

    def test_drift_is_sorted_and_complete(self):
        manifest = self.manifest()
        self.write("docker-compose.yml", "x\n")
        self.write("deploy/.env.production.example", "X=1\n")
        plan = self.plan(self.standalone(PREVIOUS), manifest=manifest)
        self.assertEqual(plan["deployment_drift"], ["deploy/.env.production.example", "docker-compose.yml"])
        self.assertReasonContaining(plan, "differ from v1.52.0's (deploy/.env.production.example, docker-compose.yml)")

    def test_file_the_release_lacks_must_stay_absent(self):
        files = list(DEFAULT_FILES) + ["docker-compose.override.example.yml"]
        manifest = self.manifest(files=files)
        self.assertEqual(self.plan(self.standalone(PREVIOUS), manifest=manifest)["deployment_drift"], [])
        self.write("docker-compose.override.example.yml", "services: {}\n")
        self.assertEqual(self.plan(self.standalone(PREVIOUS), manifest=manifest)["deployment_drift"],
                         ["docker-compose.override.example.yml"])

    def test_directory_contents_are_compared(self):
        self.write("futureagi/scripts/property_catalog_oss/schema.sql", "create table t();\n")
        files = list(DEFAULT_FILES) + ["futureagi/scripts/property_catalog_oss"]
        manifest = self.manifest(files=files)
        self.write("futureagi/scripts/property_catalog_oss/__pycache__/x.cpython-39.pyc", "bytecode")
        self.assertEqual(self.plan(self.standalone(PREVIOUS), manifest=manifest)["deployment_drift"], [])
        self.write("futureagi/scripts/property_catalog_oss/extra.sql", "select 1;\n")
        self.assertEqual(self.plan(self.standalone(PREVIOUS), manifest=manifest)["deployment_drift"],
                         ["futureagi/scripts/property_catalog_oss"])

    def test_only_files_the_manifest_lists_are_compared(self):
        manifest = self.manifest(files=["docker-compose.yml"])
        self.write("docker-compose.distributed.yml", "changed\n")
        self.assertEqual(self.plan(self.standalone(PREVIOUS), manifest=manifest)["deployment_drift"], [])


# --------------------------------------------------------------------------
class MissingEnvKeyTests(PlanTestCase):
    def test_missing_key_blocks_a_non_production_install(self):
        self.write(".env.example", ENV_EXAMPLE + "NEW_SETTING=on\n")
        plan = self.plan(self.standalone(PREVIOUS))
        self.assertBlocked(plan)
        self.assertEqual(plan["missing_env_keys"], ["NEW_SETTING"])
        self.assertReason(plan, (
            ".env lacks settings this release's .env.example adds (NEW_SETTING). Run `./bin/install --no-up -y` to "
            "add them (it keeps every existing value and starts nothing), then check again"))

    def test_missing_key_blocks_an_up_to_date_distributed_install(self):
        self.write(".env.example", ENV_EXAMPLE + "NEW_SETTING=on\n")
        plan = self.plan(self.distributed(TARGET))
        self.assertBlocked(plan)
        self.assertReasonContaining(plan, "./bin/install --no-up -y")

    def test_missing_keys_listed_once_in_file_order(self):
        self.write(".env.example", "ZETA=1\nFUTURE_AGI_VERSION=\nALPHA=2\nZETA=3\nexport MIDDLE=4\nSECRET_KEY=\n")
        plan = self.plan(self.standalone(PREVIOUS))
        self.assertEqual(plan["missing_env_keys"], ["ZETA", "ALPHA", "MIDDLE"])
        self.assertReasonContaining(plan, "adds (ZETA, ALPHA, MIDDLE)")

    def test_commented_keys_are_not_required(self):
        self.write(".env.example", ENV_EXAMPLE + "# NEW_SETTING=on\n   # OTHER=1\n")
        plan = self.plan(self.standalone(PREVIOUS))
        self.assertEqual(plan["missing_env_keys"], [])
        self.assertEqual(plan["decision"], UPDATE_AVAILABLE, plan["reasons"])

    def test_empty_value_counts_as_present(self):
        plan = self.plan(self.standalone(PREVIOUS, extra_env={"SECRET_KEY": ""}))
        self.assertEqual(plan["missing_env_keys"], [])

    def test_no_example_file_means_no_missing_keys(self):
        (self.root / ".env.example").unlink()
        manifest = self.manifest()
        plan = self.plan(FakeInstallation(self.root, services={"app": f"futureagi/standalone:{PREVIOUS}"},
                                          env={}).run("app", PREVIOUS), manifest=manifest)
        self.assertEqual(plan["missing_env_keys"], [])
        self.assertEqual(plan["decision"], UPDATE_AVAILABLE, plan["reasons"])

    def test_non_production_ignores_the_production_example(self):
        self.write("deploy/.env.production.example", PRODUCTION_EXAMPLE + "PROD_ONLY=1\n")
        plan = self.plan(self.standalone(PREVIOUS))
        self.assertEqual(plan["missing_env_keys"], [])

    def test_production_missing_keys_are_only_a_note(self):
        self.write("deploy/.env.production.example", PRODUCTION_EXAMPLE + "NEW_SETTING=on\nOTHER=2\n")
        installation = self.distributed(PREVIOUS, mode="production", production=True)
        plan = self.plan(installation)
        self.assertEqual(plan["decision"], UPDATE_AVAILABLE, plan["reasons"])
        self.assertEqual(plan["missing_env_keys"], ["NEW_SETTING", "OTHER"])
        self.assertIn(
            "deploy/.env.production lacks keys that deploy/.env.production.example sets: NEW_SETTING, OTHER. "
            "Compose uses their defaults; add them explicitly if you need other values", plan["notes"])
        self.assertNoReasonContaining(plan, "lacks")

    def test_production_note_names_an_env_file_elsewhere(self):
        self.write("deploy/.env.production.example", PRODUCTION_EXAMPLE + "NEW_SETTING=on\n")
        installation = self.distributed(PREVIOUS, mode="production", production=True)
        installation.env_file = self.root / "deploy/prod.env"
        plan = self.plan(installation)
        self.assertIn(
            "deploy/prod.env lacks keys that deploy/.env.production.example sets: NEW_SETTING. "
            "Compose uses their defaults; add them explicitly if you need other values", plan["notes"])

    def test_production_ignores_the_root_example(self):
        self.write(".env.example", ENV_EXAMPLE + "STANDALONE_ONLY=1\n")
        installation = self.distributed(PREVIOUS, mode="production", production=True)
        plan = self.plan(installation)
        self.assertEqual(plan["missing_env_keys"], [])

    def test_production_up_to_date_with_missing_keys_stays_up_to_date(self):
        self.write("deploy/.env.production.example", PRODUCTION_EXAMPLE + "NEW_SETTING=on\n")
        plan = self.plan(self.distributed(TARGET, mode="production", production=True))
        self.assertEqual(plan["decision"], UP_TO_DATE, plan["reasons"])
        self.assertTrue(any("lacks keys" in note for note in plan["notes"]))

    def test_missing_keys_not_reported_while_drift_exists(self):
        self.write(".env.example", ENV_EXAMPLE + "NEW_SETTING=on\n")
        manifest = self.manifest()
        self.write("docker-compose.yml", "drift\n")
        plan = self.plan(self.standalone(PREVIOUS), manifest=manifest)
        self.assertBlocked(plan)
        self.assertEqual(plan["deployment_drift"], ["docker-compose.yml"])
        self.assertNoReasonContaining(plan, "lacks")
        self.assertFalse([n for n in plan["notes"] if "lacks" in n], plan["notes"])

    def test_production_missing_keys_not_noted_while_drift_exists(self):
        self.write("deploy/.env.production.example", PRODUCTION_EXAMPLE + "NEW_SETTING=on\n")
        manifest = self.manifest()
        self.write("docker-compose.yml", "drift\n")
        plan = self.plan(self.distributed(PREVIOUS, mode="production", production=True), manifest=manifest)
        self.assertFalse([n for n in plan["notes"] if "lacks" in n], plan["notes"])

    def test_missing_keys_field_is_empty_while_drift_exists(self):
        # The keys come from the checkout's example, not the release's, while the checkout differs from the
        # release: the JSON plan must not report them either (the text plan already hides them).
        self.write(".env.example", ENV_EXAMPLE + "NEW_SETTING=on\n")
        manifest = self.manifest()
        self.write("docker-compose.yml", "drift\n")
        plan = self.plan(self.standalone(PREVIOUS), manifest=manifest)
        self.assertEqual(plan["missing_env_keys"], [])


# --------------------------------------------------------------------------
class GpuAndSharedVariableTests(PlanTestCase):
    def gpu(self, tag: str, label: Optional[str] = None) -> FakeInstallation:
        installation = FakeInstallation(self.root, services={
            "app": f"futureagi/standalone:{TARGET}", "serving": f"futureagi/serving:{tag}",
        }, env={"FUTURE_AGI_VERSION": TARGET, "SERVING_VERSION": tag, "SECRET_KEY": "x"})
        installation.run("app", TARGET)
        installation.run("serving", label if label is not None else (tag[:-4] if tag.endswith("-gpu") else ""))
        return installation

    def test_gpu_variant_plans_the_gpu_tag(self):
        manifest = self.manifest()
        plan = self.plan(self.gpu("v1.51.0-gpu"), manifest=manifest)
        item = self.component(plan, "serving_gpu")
        self.assertEqual(plan["decision"], UPDATE_AVAILABLE, plan["reasons"])
        self.assertEqual(item["variable"], "SERVING_VERSION")
        self.assertEqual(item["current_version"], PREVIOUS)
        self.assertEqual(item["current_tags"], ["v1.51.0-gpu"])
        self.assertEqual(item["target_tag"], "v1.52.0-gpu")
        self.assertEqual(item["target_image"], "docker.io/futureagi/serving:v1.52.0-gpu")
        self.assertEqual(item["target_digest"], manifest["components"]["serving_gpu"]["digest"])
        self.assertNotEqual(item["target_digest"], manifest["components"]["serving"]["digest"])
        self.assertEqual(plan["env_changes"], {"SERVING_VERSION": "v1.52.0-gpu"})
        self.assertEqual([c["component"] for c in plan["components"]], ["serving_gpu", "standalone"])

    def test_gpu_variant_at_target_is_kept(self):
        plan = self.plan(self.gpu("v1.52.0-gpu"))
        self.assertEqual(self.component(plan, "serving_gpu")["action"], "keep")
        self.assertEqual(plan["decision"], UP_TO_DATE, plan["reasons"])

    def test_latest_gpu_follows_the_label_and_pins_the_gpu_tag(self):
        plan = self.plan(self.gpu("latest-gpu", label=PREVIOUS))
        item = self.component(plan, "serving_gpu")
        self.assertEqual(item["current_version"], PREVIOUS)
        self.assertEqual(item["current_source"], "running image (follows :latest-gpu)")
        self.assertEqual(plan["env_changes"], {"SERVING_VERSION": "v1.52.0-gpu"})

    def test_latest_gpu_at_target_is_up_to_date(self):
        plan = self.plan(self.gpu("latest-gpu", label=TARGET))
        self.assertEqual(self.component(plan, "serving_gpu")["action"], "pin")
        self.assertEqual(plan["decision"], UP_TO_DATE, plan["reasons"])

    def test_gpu_prerelease(self):
        manifest = self.manifest(version="v1.52.0-rc.1", channel="beta")
        plan = self.plan(self.gpu("v1.51.0-gpu"), manifest=manifest, settings=self.settings(channel="beta"))
        self.assertEqual(self.component(plan, "serving_gpu")["target_tag"], "v1.52.0-rc.1-gpu")

    def test_gpu_without_label_hint_keeps_the_gpu_variant(self):
        # Following the hint literally must not move a GPU install to the CPU image.
        plan = self.plan(self.gpu("latest-gpu", label=""))
        self.assertBlocked(plan)
        reason = self.assertReasonContaining(plan, "cannot tell which version of serving_gpu is installed")
        self.assertIn("SERVING_VERSION=vX.Y.Z-gpu", reason)

    def test_cpu_and_gpu_serving_needing_two_values_conflict(self):
        installation = FakeInstallation(self.root, services={
            "serving": f"futureagi/serving:{PREVIOUS}", "serving-gpu": f"futureagi/serving:{PREVIOUS}-gpu",
        }, env={"FUTURE_AGI_VERSION": TARGET, "SERVING_VERSION": PREVIOUS, "SECRET_KEY": "x"})
        installation.run("serving", PREVIOUS).run("serving-gpu", PREVIOUS)
        plan = self.plan(installation)
        self.assertBlocked(plan)
        self.assertReason(plan, "SERVING_VERSION would need two values (v1.52.0, v1.52.0-gpu)")

    def test_conflict_with_a_kept_component_is_detected(self):
        # serving already runs v1.52.0 from SERVING_VERSION; writing SERVING_VERSION=v1.52.0-gpu for the GPU
        # service would change it too, and apply's config check refuses that mix. The plan must agree.
        installation = FakeInstallation(self.root, services={
            "serving": f"futureagi/serving:{TARGET}", "serving-gpu": f"futureagi/serving:{PREVIOUS}-gpu",
        }, env={"FUTURE_AGI_VERSION": TARGET, "SERVING_VERSION": TARGET, "SECRET_KEY": "x"})
        installation.run("serving", TARGET).run("serving-gpu", PREVIOUS)
        plan = self.plan(installation)
        self.assertEqual(self.component(plan, "serving")["action"], "keep")
        self.assertBlocked(plan)
        self.assertReasonContaining(plan, "SERVING_VERSION would need two values")

    def test_cpu_and_gpu_serving_both_at_target_are_up_to_date(self):
        # Nothing is written when both are kept, so their different tags are no conflict.
        installation = FakeInstallation(self.root, services={
            "serving": f"futureagi/serving:{TARGET}", "serving-gpu": f"futureagi/serving:{TARGET}-gpu",
        }, env={"FUTURE_AGI_VERSION": TARGET, "SERVING_VERSION": TARGET, "SECRET_KEY": "x"})
        installation.run("serving", TARGET).run("serving-gpu", TARGET)
        plan = self.plan(installation)
        self.assertEqual(plan["decision"], UP_TO_DATE, plan["reasons"])
        self.assertNoReasonContaining(plan, "two values")

    def test_components_sharing_a_variable_with_one_target_do_not_conflict(self):
        installation = FakeInstallation(self.root, services={
            "app": f"futureagi/standalone:{PREVIOUS}", "backend": f"futureagi/future-agi:{PREVIOUS}",
        }, env={"FUTURE_AGI_VERSION": PREVIOUS, "SECRET_KEY": "x"})
        installation.run("app", PREVIOUS).run("backend", PREVIOUS)
        plan = self.plan(installation)
        self.assertNoReasonContaining(plan, "two values")
        self.assertEqual(plan["env_changes"], {"FUTURE_AGI_VERSION": TARGET})

    def test_kept_component_conflicts_with_a_pinned_one(self):
        installation = FakeInstallation(self.root, services={
            "serving": f"futureagi/serving:{TARGET}", "serving-gpu": "futureagi/serving:latest-gpu",
        }, env={"FUTURE_AGI_VERSION": TARGET, "SERVING_VERSION": TARGET, "SECRET_KEY": "x"})
        installation.run("serving", TARGET).run("serving-gpu", TARGET)
        plan = self.plan(installation)
        self.assertEqual(self.component(plan, "serving")["action"], "keep")
        self.assertEqual(self.component(plan, "serving_gpu")["action"], "pin")
        self.assertBlocked(plan)
        self.assertReason(plan, "SERVING_VERSION would need two values (v1.52.0, v1.52.0-gpu)")

    def test_kept_and_updated_components_needing_one_value_do_not_conflict(self):
        installation = FakeInstallation(self.root, services={
            "app": f"futureagi/standalone:{TARGET}", "backend": "futureagi/future-agi:latest",
        }, env={"FUTURE_AGI_VERSION": TARGET, "SECRET_KEY": "x"})
        installation.run("app", TARGET).run("backend", PREVIOUS)
        plan = self.plan(installation)
        self.assertEqual(self.component(plan, "standalone")["action"], "keep")
        self.assertEqual(self.component(plan, "backend")["action"], "update")
        self.assertNoReasonContaining(plan, "two values")
        self.assertEqual(plan["env_changes"], {"FUTURE_AGI_VERSION": TARGET})

    def test_conflict_is_reported_once_per_variable(self):
        installation = FakeInstallation(self.root, services={
            "serving": f"futureagi/serving:{PREVIOUS}", "serving-gpu": f"futureagi/serving:{PREVIOUS}-gpu",
            "serving-gpu-2": f"futureagi/serving:{PREVIOUS}-gpu",
        }, env={"FUTURE_AGI_VERSION": TARGET, "SERVING_VERSION": PREVIOUS, "SECRET_KEY": "x"})
        installation.run("serving", PREVIOUS).run("serving-gpu", PREVIOUS).run("serving-gpu-2", PREVIOUS)
        plan = self.plan(installation)
        self.assertEqual([r for r in plan["reasons"] if "two values" in r],
                         ["SERVING_VERSION would need two values (v1.52.0, v1.52.0-gpu)"])


# --------------------------------------------------------------------------
class ResumeTests(PlanTestCase):
    def receipt(self, **fields) -> Dict[str, Any]:
        record = {"kind": "apply", "status": "failed_during_rollout", "target_version": TARGET,
                  "_path": str(self.root / ".futureagi-updates/receipt-20261019T000000Z-1.json")}
        record.update(fields)
        return record

    def test_resumable_statuses(self):
        self.assertEqual(RESUMABLE_STATUSES, ("failed_during_rollout", "interrupted_during_rollout"))

    def test_failed_rollout_to_same_target_resumes_even_when_up_to_date(self):
        for status in RESUMABLE_STATUSES:
            with self.subTest(status=status):
                plan = self.plan(self.standalone(TARGET), last_receipt=self.receipt(status=status))
                self.assertEqual(plan["decision"], UPDATE_AVAILABLE, plan["reasons"])
                self.assertTrue(plan["resume"])
                self.assertEqual(plan["env_changes"], {})
                self.assertIn(f"the previous update to v1.52.0 stopped while containers were starting ({status}); "
                              "apply finishes it", plan["notes"])

    def test_resume_with_components_still_to_update(self):
        installation = self.distributed(TARGET, tags={"FRONTEND_VERSION": PREVIOUS})
        plan = self.plan(installation, last_receipt=self.receipt())
        self.assertEqual(plan["decision"], UPDATE_AVAILABLE)
        self.assertTrue(plan["resume"])
        self.assertEqual(plan["env_changes"], {"FRONTEND_VERSION": TARGET})

    def test_receipt_without_kind_counts_as_apply(self):
        record = self.receipt()
        record.pop("kind")
        plan = self.plan(self.standalone(TARGET), last_receipt=record)
        self.assertTrue(plan["resume"])

    def test_failed_rollout_to_another_release_is_blocked(self):
        record = self.receipt(target_version="v1.51.5")
        plan = self.plan(self.standalone(PREVIOUS), last_receipt=record)
        self.assertBlocked(plan)
        self.assertReason(plan, (
            "the previous update to v1.51.5 stopped while containers were starting (failed_during_rollout). "
            "Finish it (check out v1.51.5 with `git checkout v1.51.5`, or at least its deployment files (your env "
            "file stays as it is), then ./bin/update apply --manifest https://github.com/future-agi/future-agi/"
            "releases/download/v1.51.5/future-agi-update-manifest.json) or roll it back (./bin/update rollback "
            "--receipt "
            f"{record['_path']} "
            "--yes) first"))
        self.assertFalse([n for n in plan["notes"] if "apply finishes it" in n])

    def test_interrupted_rollout_to_another_release_is_blocked(self):
        plan = self.plan(self.standalone(TARGET),
                         last_receipt=self.receipt(target_version="v1.53.0", status="interrupted_during_rollout"))
        self.assertBlocked(plan)
        self.assertReasonContaining(plan, "the previous update to v1.53.0 stopped while containers were starting "
                                          "(interrupted_during_rollout)")

    def test_other_release_receipt_without_path(self):
        record = self.receipt(target_version="v1.51.5")
        record.pop("_path")
        plan = self.plan(self.standalone(PREVIOUS), last_receipt=record)
        self.assertReasonContaining(plan, "or roll it back (./bin/update rollback --yes) first")

    def test_stopped_rollback_is_blocked_naming_the_rollback_command(self):
        record = {"kind": "rollback", "status": "failed_during_rollout", "target_version": PREVIOUS,
                  "rolls_back": "/srv/fagi/.futureagi-updates/receipt-20261018T000000Z-7.json",
                  "_path": "/srv/fagi/.futureagi-updates/receipt-20261019T000000Z-9.json"}
        plan = self.plan(self.standalone(TARGET), last_receipt=record)
        self.assertBlocked(plan)
        self.assertReason(plan, (
            "a rollback stopped while containers were being replaced. Finish it first: ./bin/update rollback "
            "--receipt /srv/fagi/.futureagi-updates/receipt-20261018T000000Z-7.json --yes"))
        self.assertNoReasonContaining(plan, "receipt-20261019T000000Z-9")

    def test_interrupted_rollback_whose_target_matches_is_still_blocked(self):
        record = {"kind": "rollback", "status": "interrupted_during_rollout", "target_version": TARGET,
                  "rolls_back": "/r.json"}
        plan = self.plan(self.standalone(TARGET), last_receipt=record)
        self.assertBlocked(plan)
        self.assertReasonContaining(plan, "a rollback stopped while containers were being replaced")

    def test_stopped_rollback_without_rolls_back(self):
        plan = self.plan(self.standalone(TARGET), last_receipt={"kind": "rollback", "status": "failed_during_rollout"})
        self.assertReasonContaining(plan, "./bin/update rollback --receipt <receipt> --yes")

    def test_finished_or_harmless_receipts_are_ignored(self):
        for status in ("succeeded", "rolled_back", "failed_before_rollout", "interrupted_before_rollout", "running",
                       None):
            for kind in ("apply", "rollback"):
                with self.subTest(status=status, kind=kind):
                    plan = self.plan(self.standalone(TARGET),
                                     last_receipt=self.receipt(status=status, kind=kind, target_version="v1.40.0"))
                    self.assertEqual(plan["decision"], UP_TO_DATE, plan["reasons"])
                    self.assertFalse(plan["resume"])
                    self.assertEqual(plan["notes"], [])

    def test_no_receipt(self):
        for record in (None, {}):
            with self.subTest(record=record):
                plan = self.plan(self.standalone(TARGET), last_receipt=record)
                self.assertEqual(plan["decision"], UP_TO_DATE)
                self.assertFalse(plan["resume"])

    def test_resume_is_false_when_something_else_blocks(self):
        manifest = self.manifest()
        self.write("docker-compose.yml", "drift\n")
        plan = self.plan(self.standalone(TARGET), manifest=manifest, last_receipt=self.receipt())
        self.assertBlocked(plan)
        self.assertFalse(plan["resume"])


# --------------------------------------------------------------------------
class ProductionHintTests(PlanTestCase):
    """Commands a production plan prints must work when typed as printed."""

    def test_update_available_names_the_production_apply(self):
        plan = self.plan(self.distributed(PREVIOUS, mode="production", production=True))
        self.assertEqual(plan["decision"], UPDATE_AVAILABLE, plan["reasons"])
        self.assertIn("./bin/update --production apply", plan_text(plan))

    def test_stopped_update_rollback_hint_carries_production(self):
        record = {"kind": "apply", "status": "failed_during_rollout", "target_version": "v1.51.5",
                  "_path": "/srv/r.json"}
        plan = self.plan(self.distributed(PREVIOUS, mode="production", production=True), last_receipt=record)
        reason = self.assertReasonContaining(plan, "roll it back")
        self.assertIn("./bin/update --production rollback --receipt /srv/r.json --yes", reason)

    def test_stopped_rollback_hint_carries_production(self):
        record = {"kind": "rollback", "status": "interrupted_during_rollout", "rolls_back": "/srv/a.json"}
        plan = self.plan(self.distributed(PREVIOUS, mode="production", production=True), last_receipt=record)
        reason = self.assertReasonContaining(plan, "a rollback stopped")
        self.assertIn("./bin/update --production rollback --receipt /srv/a.json --yes", reason)

    def test_non_production_hints_have_no_production_flag(self):
        record = {"kind": "rollback", "status": "interrupted_during_rollout", "rolls_back": "/srv/a.json"}
        plan = self.plan(self.distributed(PREVIOUS), last_receipt=record)
        self.assertNotIn("--production", "\n".join(plan["reasons"]))
        self.assertNotIn("--production", plan_text(self.plan(self.distributed(PREVIOUS))))

    def test_resume_names_the_production_apply(self):
        record = {"kind": "apply", "status": "interrupted_during_rollout", "target_version": TARGET}
        plan = self.plan(self.distributed(TARGET, mode="production", production=True), last_receipt=record)
        self.assertTrue(plan["resume"])
        self.assertIn("  Decision      update available: if the release's initialization jobs have not run yet, run "
                      "them first with `docker compose --env-file deploy/.env.production -f "
                      "docker-compose.distributed.yml -f deploy/docker-compose.production.yml run --rm ...` "
                      "(deploy/README.md › Upgrading a production install. Run it from a shell that does not export "
                      "any key of deploy/.env.production (Compose prefers your shell over the env file).); then run "
                      "./bin/update --production apply (finishes the stopped update)\n", plan_text(plan))

    def test_production_env_file_elsewhere_is_named_in_every_hint(self):
        installation = self.distributed(PREVIOUS, mode="production", production=True)
        installation.env_file = self.root / "deploy/prod.env"
        record = {"kind": "apply", "status": "failed_during_rollout", "target_version": "v1.51.5",
                  "_path": "/srv/r.json"}
        plan = self.plan(installation, last_receipt=record)
        self.assertEqual(plan["installation"]["env_file"], "deploy/prod.env")
        self.assertIn("./bin/update --production --env-file deploy/prod.env rollback --receipt /srv/r.json --yes",
                      self.assertReasonContaining(plan, "roll it back"))
        record = {"kind": "rollback", "status": "failed_during_rollout", "rolls_back": "/srv/a.json"}
        reason = self.assertReasonContaining(self.plan(installation, last_receipt=record), "a rollback stopped")
        self.assertIn("./bin/update --production --env-file deploy/prod.env rollback --receipt /srv/a.json --yes",
                      reason)
        self.assertIn("update available: run ./bin/update --production --env-file deploy/prod.env apply --stage ",
                      plan_text(self.plan(installation)))

    def test_command_prefix(self):
        self.assertEqual(command_prefix(False, ".env"), "./bin/update")
        self.assertEqual(command_prefix(False, "deploy/.env.production"), "./bin/update")
        self.assertEqual(command_prefix(True, "deploy/.env.production"), "./bin/update --production")
        self.assertEqual(command_prefix(True, "/etc/fagi/prod.env"),
                         "./bin/update --production --env-file /etc/fagi/prod.env")


# --------------------------------------------------------------------------
class PlanTextTests(PlanTestCase):
    def test_up_to_date(self):
        text = plan_text(self.plan(self.standalone(TARGET)))
        self.assertTrue(text.startswith("Future AGI update check\n"))
        self.assertTrue(text.endswith("\n"))
        self.assertIn("  Installation  standalone (Compose project futureagi, .env)\n", text)
        self.assertIn("  Installed     v1.52.0\n", text)
        self.assertIn("  Release       v1.52.0 (stable, published 2026-10-19T00:00:00Z)\n", text)
        self.assertIn("  Signature     verified (public key: /etc/futureagi/cosign.pub)\n", text)
        self.assertIn("  Decision      up to date, nothing to do\n", text)
        self.assertNotIn("✗", text)

    def test_update_available(self):
        manifest = self.manifest()
        text = plan_text(self.plan(self.standalone(PREVIOUS), manifest=manifest))
        self.assertIn("  Decision      update available: run ./bin/update apply\n", text)
        self.assertNotIn("finishes the stopped update", text)
        self.assertIn("  Components\n", text)
        digest = manifest["components"]["standalone"]["digest"]
        line = [row for row in text.splitlines() if row.strip().startswith("standalone")][0]
        self.assertIn("v1.51.0", line)
        self.assertIn("->", line)
        self.assertIn("v1.52.0", line)
        self.assertTrue(line.endswith(digest[:19] + "…"), line)
        self.assertNotIn(digest, text)

    def test_resume(self):
        record = {"kind": "apply", "status": "interrupted_during_rollout", "target_version": TARGET}
        text = plan_text(self.plan(self.standalone(TARGET), last_receipt=record))
        self.assertIn("update available: run ./bin/update apply (finishes the stopped update)", text)
        self.assertIn("  · the previous update to v1.52.0 stopped", text)

    def test_blocked_lists_reasons_and_notes(self):
        installation = self.distributed(PREVIOUS, tags={"FI_COLLECTOR_VERSION": "local", "FRONTEND_VERSION": "latest"},
                                        labels={"frontend": ""})
        plan = self.plan(installation, verification=Verification(False, error="no signature bundle"))
        text = plan_text(plan)
        self.assertIn("  Decision      blocked (see below)\n", text)
        self.assertIn("  Signature     NOT verified: no signature bundle\n", text)
        for reason in plan["reasons"]:
            self.assertIn(f"  ✗ {reason}\n", text)
        for note in plan["notes"]:
            self.assertIn(f"  · {note}\n", text)
        self.assertTrue(plan["notes"])

    def test_unknown_current_version_renders(self):
        installation = FakeInstallation(self.root, services={"app": "futureagi/standalone:latest"},
                                        env={"FUTURE_AGI_VERSION": "latest"})
        plan = self.plan(installation)
        text = plan_text(plan)
        self.assertIn("  Installed     unknown\n", text)
        line = [row for row in text.splitlines() if row.strip().startswith("standalone")][0]
        self.assertIn(" ? ", line)

    def test_actions_render_their_arrows(self):
        installation = self.distributed(TARGET, tags={"FRONTEND_VERSION": "latest", "SERVING_VERSION": PREVIOUS},
                                        labels={"frontend": TARGET})
        text = plan_text(self.plan(installation))
        rows = {row.split()[0]: row for row in text.splitlines() if row.startswith("    ")}
        self.assertIn(" keep ", rows["backend"])
        self.assertIn(" pin  ", rows["frontend"])
        self.assertIn(" ->   ", rows["serving"])

    def test_no_components_section_without_components(self):
        installation = FakeInstallation(self.root, services={"postgres": "postgres:16"}, env={})
        text = plan_text(self.plan(installation))
        self.assertNotIn("Components", text)
        self.assertIn("  Installed     unknown\n", text)
        self.assertIn("  ✗ the resolved Compose configuration has no Future AGI services\n", text)

    def test_production_header(self):
        installation = self.distributed(PREVIOUS, mode="production", production=True, project="fagi-prod")
        text = plan_text(self.plan(installation))
        self.assertIn("  Installation  production (Compose project fagi-prod, deploy/.env.production)\n", text)

    def test_every_decision_renders_for_handmade_plans(self):
        base = self.plan(self.standalone(PREVIOUS))
        for decision in (UP_TO_DATE, UPDATE_AVAILABLE, BLOCKED):
            for resume in (False, True):
                with self.subTest(decision=decision, resume=resume):
                    plan = copy.deepcopy(base)
                    plan["decision"], plan["resume"] = decision, resume
                    plan["current_versions"] = []
                    for item in plan["components"]:
                        item["current_version"] = None
                    text = plan_text(plan)
                    self.assertIn("  Decision      ", text)
                    self.assertIn("unknown", text)

    def test_unknown_decision_or_action_is_an_error(self):
        plan = self.plan(self.standalone(PREVIOUS))
        broken = copy.deepcopy(plan)
        broken["decision"] = "maybe"
        with self.assertRaises(KeyError):
            plan_text(broken)
        broken = copy.deepcopy(plan)
        broken["components"][0]["action"] = "skip"
        with self.assertRaises(KeyError):
            plan_text(broken)


class PlanJsonTests(PlanTestCase):
    def test_json_is_stable_and_sorted(self):
        manifest = self.manifest()
        first = plan_json(self.plan(self.distributed(PREVIOUS), manifest=manifest))
        second = plan_json(self.plan(self.distributed(PREVIOUS), manifest=manifest))
        self.assertEqual(first, second)
        self.assertTrue(first.endswith("}\n"))
        decoded = json.loads(first)
        self.assertEqual(first, json.dumps(decoded, sort_keys=True, indent=2, ensure_ascii=True) + "\n")
        self.assertEqual(list(decoded), sorted(decoded))

    def test_json_round_trips_the_plan(self):
        plan = self.plan(self.distributed(PREVIOUS, tags={"FI_COLLECTOR_VERSION": "local"}))
        self.assertEqual(json.loads(plan_json(plan)), plan)

    def test_json_is_ascii(self):
        pinned = f"futureagi/standalone:{PREVIOUS}@sha256:{'a' * 64}"
        installation = FakeInstallation(self.root, services={"app": pinned},
                                        env={"FUTURE_AGI_VERSION": PREVIOUS}).run("app", PREVIOUS)
        text = plan_json(self.plan(installation))
        text.encode("ascii")
        self.assertIn("\\u203a", text)

    def test_json_of_every_decision(self):
        for installation in (self.standalone(TARGET), self.standalone(PREVIOUS), self.standalone("local")):
            with self.subTest(tag=installation.env["FUTURE_AGI_VERSION"]):
                plan = self.plan(installation)
                decoded = json.loads(plan_json(plan))
                self.assertEqual(decoded["decision"], plan["decision"])
                self.assertIsInstance(decoded["components"], list)

    def test_json_keeps_none_current_version(self):
        plan = self.plan(self.standalone("local"))
        decoded = json.loads(plan_json(plan))
        self.assertIsNone(decoded["components"][0]["current_version"])


# --------------------------------------------------------------------------
class InactiveProfileTests(PlanTestCase):
    """Version keys whose services are outside the active Compose profiles (absent from `docker compose config`)."""

    NOTE = ("SIMULATION_RUNNER_VERSION pins a service outside the active Compose profiles; it is set to v1.52.0 "
            "without pulling it")

    def production(self, version: str = PREVIOUS, *, runner: Optional[str] = PREVIOUS, **kwargs) -> FakeInstallation:
        """Production whose simulation runner ('workers' profile) is inactive; its pin stays in the env file."""

        installation = self.distributed(version, mode="production", production=True, **kwargs)
        installation._config["services"].pop("simulation-runner")
        installation._containers = [c for c in installation._containers if c["service"] != "simulation-runner"]
        if runner is None:
            installation.env.pop("SIMULATION_RUNNER_VERSION")
        else:
            installation.env["SIMULATION_RUNNER_VERSION"] = runner
        return installation

    def test_older_inactive_pin_moves_with_the_release(self):
        manifest = self.manifest()
        plan = self.plan(self.production(), manifest=manifest)
        self.assertEqual(plan["decision"], UPDATE_AVAILABLE, plan["reasons"])
        self.assertEqual(plan["reasons"], [])
        self.assertEqual(plan["env_changes"], {key: TARGET for key in VERSION_VARIABLES})
        self.assertEqual(self.component(plan, "simulation_runner"), {
            "component": "simulation_runner", "variable": "SIMULATION_RUNNER_VERSION", "services": [],
            "configured": PREVIOUS, "current_version": PREVIOUS,
            "current_source": "SIMULATION_RUNNER_VERSION in deploy/.env.production (no active service)",
            "current_tags": [PREVIOUS], "target_tag": TARGET,
            "target_image": f"docker.io/futureagi/future-agi-simulation-runner:{TARGET}",
            "target_digest": manifest["components"]["simulation_runner"]["digest"], "action": "update",
            "inactive": True,
        })
        self.assertIn(self.NOTE, plan["notes"])

    def test_inactive_pins_follow_the_active_components_and_only_they_are_flagged(self):
        plan = self.plan(self.production())
        self.assertEqual([item["component"] for item in plan["components"]],
                         ["agentcc_gateway", "backend", "code_executor", "fi_collector", "frontend", "serving",
                          "simulation_runner"])
        for item in plan["components"][:-1]:
            self.assertNotIn("inactive", item)
            self.assertTrue(item["services"])

    def test_inactive_pin_at_the_target_is_kept(self):
        plan = self.plan(self.production(TARGET, runner=TARGET))
        self.assertEqual(plan["decision"], UP_TO_DATE, plan["reasons"])
        self.assertEqual(plan["env_changes"], {})
        item = self.component(plan, "simulation_runner")
        self.assertEqual((item["action"], item["inactive"], item["current_version"]), ("keep", True, TARGET))
        self.assertFalse([n for n in plan["notes"] if "outside the active" in n], plan["notes"])

    def test_inactive_pin_kept_while_the_rest_updates(self):
        plan = self.plan(self.production(PREVIOUS, runner=TARGET))
        self.assertEqual(plan["decision"], UPDATE_AVAILABLE, plan["reasons"])
        self.assertNotIn("SIMULATION_RUNNER_VERSION", plan["env_changes"])
        self.assertEqual(self.component(plan, "simulation_runner")["action"], "keep")

    def test_stale_inactive_pin_alone_is_an_update(self):
        plan = self.plan(self.production(TARGET, runner=PREVIOUS))
        self.assertEqual(plan["decision"], UPDATE_AVAILABLE, plan["reasons"])
        self.assertEqual(plan["env_changes"], {"SIMULATION_RUNNER_VERSION": TARGET})
        self.assertEqual(plan["current_versions"], [TARGET])
        self.assertIn(self.NOTE, plan["notes"])

    def test_newer_inactive_pin_is_never_downgraded(self):
        plan = self.plan(self.production(PREVIOUS, runner="v1.53.0"))
        self.assertBlocked(plan)
        self.assertReason(plan, (
            "SIMULATION_RUNNER_VERSION in deploy/.env.production pins v1.53.0 for a service outside the active "
            "Compose profiles, newer than this release (v1.52.0); bin/update never downgrades"))
        self.assertFalse([n for n in plan["notes"] if "outside the active" in n], plan["notes"])
        self.assertEqual(plan["current_versions"], [PREVIOUS])

    def test_inactive_prerelease_pin_updates_to_its_release(self):
        plan = self.plan(self.production(TARGET, runner="v1.52.0-rc.1"))
        self.assertEqual(plan["env_changes"], {"SIMULATION_RUNNER_VERSION": TARGET})
        self.assertEqual(self.component(plan, "simulation_runner")["current_version"], "v1.52.0-rc.1")

    def test_inactive_pin_does_not_count_toward_minimum_or_policy(self):
        plan = self.plan(self.production(TARGET, runner="v1.40.0"), settings=self.settings(policy="patch"))
        self.assertEqual(plan["decision"], UPDATE_AVAILABLE, plan["reasons"])
        self.assertEqual(plan["reasons"], [])
        self.assertEqual(plan["current_versions"], [TARGET])
        self.assertEqual(plan["env_changes"], {"SIMULATION_RUNNER_VERSION": TARGET})

    def test_values_that_name_no_release_are_left_alone(self):
        for value in (None, "", "latest", "local", "v1.51", "dev", "1.51.0", f"{PREVIOUS}-gpu"):
            with self.subTest(value=value):
                plan = self.plan(self.production(TARGET, runner=value))
                self.assertEqual(plan["decision"], UP_TO_DATE, plan["reasons"])
                self.assertFalse([c for c in plan["components"] if c["component"] == "simulation_runner"])
                self.assertNotIn("SIMULATION_RUNNER_VERSION", plan["env_changes"])
                self.assertFalse([n for n in plan["notes"] if "outside the active" in n], plan["notes"])

    def test_inactive_gpu_serving_keeps_its_variant(self):
        manifest = self.manifest()
        installation = self.production(TARGET, runner=TARGET)
        installation._config["services"].pop("serving")
        installation._containers = [c for c in installation._containers if c["service"] != "serving"]
        installation.env["SERVING_VERSION"] = f"{PREVIOUS}-gpu"
        plan = self.plan(installation, manifest=manifest)
        item = self.component(plan, "serving_gpu")
        self.assertEqual((item["action"], item["inactive"], item["services"], item["current_version"],
                          item["current_tags"], item["target_tag"], item["target_digest"]),
                         ("update", True, [], PREVIOUS, [f"{PREVIOUS}-gpu"], "v1.52.0-gpu",
                          manifest["components"]["serving_gpu"]["digest"]))
        self.assertEqual(plan["env_changes"], {"SERVING_VERSION": "v1.52.0-gpu"})
        self.assertIn("SERVING_VERSION pins a service outside the active Compose profiles; it is set to v1.52.0-gpu "
                      "without pulling it", plan["notes"])

    def test_inactive_cpu_serving(self):
        installation = self.production(TARGET, runner=TARGET)
        installation._config["services"].pop("serving")
        installation._containers = [c for c in installation._containers if c["service"] != "serving"]
        installation.env["SERVING_VERSION"] = PREVIOUS
        plan = self.plan(installation)
        self.assertEqual(self.component(plan, "serving")["inactive"], True)
        self.assertEqual(plan["env_changes"], {"SERVING_VERSION": TARGET})

    def test_standalone_profile_pins_move_too(self):
        # docker-compose.yml puts serving and code-executor behind the ml and sandbox profiles.
        plan = self.plan(self.standalone(TARGET, extra_env={"SERVING_VERSION": PREVIOUS,
                                                            "CODE_EXECUTOR_VERSION": PREVIOUS}))
        self.assertEqual(plan["decision"], UPDATE_AVAILABLE, plan["reasons"])
        self.assertEqual(plan["env_changes"], {"CODE_EXECUTOR_VERSION": TARGET, "SERVING_VERSION": TARGET})
        self.assertEqual([(c["component"], c["action"], c.get("inactive", False)) for c in plan["components"]],
                         [("standalone", "keep", False), ("code_executor", "update", True),
                          ("serving", "update", True)])
        self.assertEqual(plan["current_versions"], [TARGET])

    def test_shared_key_maps_to_the_stack_of_the_install(self):
        for mode, component in (("standalone", "standalone"), ("distributed", "backend"),
                                ("production", "backend")):
            with self.subTest(mode=mode):
                installation = FakeInstallation(
                    self.root, services={"frontend": f"futureagi/frontend:{TARGET}"}, mode=mode,
                    production=mode == "production",
                    env={"FRONTEND_VERSION": TARGET, "FUTURE_AGI_VERSION": PREVIOUS}).run("frontend", TARGET)
                item = self.component(self.plan(installation), component)
                self.assertEqual((item["variable"], item["inactive"], item["target_tag"]),
                                 ("FUTURE_AGI_VERSION", True, TARGET))

    def test_no_inactive_pins_without_active_services(self):
        installation = FakeInstallation(self.root, services={"postgres": "postgres:16"},
                                        env={"SIMULATION_RUNNER_VERSION": PREVIOUS}).run("postgres", "16.4")
        plan = self.plan(installation)
        self.assertReason(plan, "the resolved Compose configuration has no Future AGI services")
        self.assertEqual(plan["components"], [])
        self.assertEqual(plan["env_changes"], {})

    def test_inactive_rows_render_without_a_digest(self):
        manifest = self.manifest()
        plan = self.plan(self.production(TARGET, runner=PREVIOUS), manifest=manifest)
        text = plan_text(plan)
        rows = {row.split()[0]: row for row in text.splitlines() if row.startswith("    ")}
        self.assertIn(" ->   v1.52.0 ", rows["simulation_runner"])
        self.assertTrue(rows["simulation_runner"].endswith("not pulled (profile not active)"), rows)
        self.assertNotIn(manifest["components"]["simulation_runner"]["digest"][:19], text)
        self.assertTrue(rows["backend"].endswith(manifest["components"]["backend"]["digest"][:19] + "…"))
        self.assertIn(f"  · {self.NOTE}\n", text)
        kept = plan_text(self.plan(self.production(TARGET, runner=TARGET)))
        self.assertIn(" keep v1.52.0 ", [row for row in kept.splitlines() if "simulation_runner" in row][0])

    def test_inactive_pins_round_trip_through_json(self):
        plan = self.plan(self.production())
        decoded = json.loads(plan_json(plan))
        self.assertEqual(decoded, plan)
        self.assertIs(decoded["components"][-1]["inactive"], True)


# --------------------------------------------------------------------------
class EnvVersusRunningImageTests(PlanTestCase):
    """A version in the env file that the running containers do not carry: the file changed without a restart."""

    def receipt(self, **fields) -> Dict[str, Any]:
        record = {"kind": "apply", "status": "failed_during_rollout", "target_version": TARGET,
                  "_path": "/srv/fagi/.futureagi-updates/receipt-20261019T000000Z-1.json"}
        record.update(fields)
        return record

    def test_env_changed_without_restart_is_blocked(self):
        plan = self.plan(self.standalone(PREVIOUS, label="v1.50.0"))
        self.assertBlocked(plan)
        self.assertReason(plan, (
            "service app runs v1.50.0, but FUTURE_AGI_VERSION in .env is v1.51.0: the env file was changed without "
            "restarting the stack. Set FUTURE_AGI_VERSION back to v1.50.0 (bin/update then updates it), or restart "
            "the stack so it runs v1.51.0, then check again"))
        item = self.component(plan, "standalone")
        self.assertEqual((item["current_version"], item["current_source"]), (PREVIOUS, "FUTURE_AGI_VERSION in .env"))

    def test_env_set_to_the_target_by_hand_is_not_up_to_date(self):
        plan = self.plan(self.standalone(TARGET, label=PREVIOUS))
        self.assertBlocked(plan)
        self.assertReasonContaining(plan, "service app runs v1.51.0, but FUTURE_AGI_VERSION in .env is v1.52.0: "
                                          "the env file was changed without restarting the stack")

    def test_every_labelled_service_is_named(self):
        installation = self.distributed(PREVIOUS, labels={"FUTURE_AGI_VERSION": "v1.50.0"})
        plan = self.plan(installation)
        reasons = [r for r in plan["reasons"] if "without restarting" in r]
        self.assertEqual(len(reasons), 1, plan["reasons"])
        self.assertTrue(reasons[0].startswith("services backend, worker run v1.50.0, but FUTURE_AGI_VERSION in .env "
                                              "is v1.51.0"), reasons[0])

    def test_production_names_its_env_file(self):
        installation = self.distributed(PREVIOUS, mode="production", production=True, labels={"frontend": "v1.50.0"})
        plan = self.plan(installation)
        self.assertReasonContaining(plan, "service frontend runs v1.50.0, but FRONTEND_VERSION in "
                                          "deploy/.env.production is v1.51.0")

    def test_gpu_hint_keeps_the_variant(self):
        installation = FakeInstallation(self.root, services={
            "app": f"futureagi/standalone:{TARGET}", "serving": f"futureagi/serving:{TARGET}-gpu",
        }, env={"FUTURE_AGI_VERSION": TARGET, "SERVING_VERSION": f"{TARGET}-gpu", "SECRET_KEY": "x"})
        installation.run("app", TARGET).run("serving", PREVIOUS)
        plan = self.plan(installation)
        self.assertBlocked(plan)
        reason = self.assertReasonContaining(plan, "service serving runs v1.51.0, but SERVING_VERSION in .env is "
                                                   "v1.52.0-gpu")
        self.assertIn("Set SERVING_VERSION back to v1.51.0-gpu (bin/update then updates it)", reason)

    def test_gpu_label_matching_the_tag_is_no_change(self):
        installation = FakeInstallation(self.root, services={"serving": f"futureagi/serving:{PREVIOUS}-gpu"},
                                        env={"SERVING_VERSION": f"{PREVIOUS}-gpu"}).run("serving", PREVIOUS)
        self.assertNoReasonContaining(self.plan(installation), "without restarting")

    def test_stopped_rollout_to_the_env_tag_resumes(self):
        for status in RESUMABLE_STATUSES:
            for kind in ("apply", None):
                with self.subTest(status=status, kind=kind):
                    record = self.receipt(status=status)
                    if kind is None:
                        record.pop("kind")
                    plan = self.plan(self.standalone(TARGET, label=PREVIOUS), last_receipt=record)
                    self.assertEqual(plan["decision"], UPDATE_AVAILABLE, plan["reasons"])
                    self.assertTrue(plan["resume"])
                    self.assertNoReasonContaining(plan, "without restarting")

    def test_stopped_gpu_rollout_resumes(self):
        installation = FakeInstallation(self.root, services={"serving": f"futureagi/serving:{TARGET}-gpu"}, env={
            "FUTURE_AGI_VERSION": TARGET, "SERVING_VERSION": f"{TARGET}-gpu", "SECRET_KEY": "x",
        }).run("serving", PREVIOUS)
        plan = self.plan(installation, last_receipt=self.receipt())
        self.assertEqual(plan["decision"], UPDATE_AVAILABLE, plan["reasons"])
        self.assertTrue(plan["resume"])

    def test_stopped_rollout_to_another_release_keeps_its_own_reason(self):
        record = self.receipt(status="interrupted_during_rollout", target_version="v1.51.5")
        plan = self.plan(self.standalone("v1.51.5", label=PREVIOUS), last_receipt=record)
        self.assertBlocked(plan)
        self.assertReasonContaining(plan, "the previous update to v1.51.5 stopped while containers were starting")
        self.assertNoReasonContaining(plan, "without restarting")

    def test_stopped_rollback_keeps_its_own_reason(self):
        # The rollback wrote the previous version back before the containers went back to it.
        record = {"kind": "rollback", "status": "failed_during_rollout", "target_version": PREVIOUS,
                  "rolls_back": "/srv/fagi/.futureagi-updates/receipt-20261018T000000Z-7.json"}
        plan = self.plan(self.standalone(PREVIOUS, label=TARGET), last_receipt=record)
        self.assertBlocked(plan)
        self.assertReasonContaining(plan, "a rollback stopped while containers were being replaced")
        self.assertNoReasonContaining(plan, "without restarting")

    def test_other_receipts_do_not_excuse_it(self):
        for record in (self.receipt(status="succeeded"), self.receipt(status="failed_before_rollout"),
                       self.receipt(status="interrupted_before_rollout"), self.receipt(target_version="v1.51.5"),
                       None):
            with self.subTest(record=record):
                plan = self.plan(self.standalone(TARGET, label=PREVIOUS), last_receipt=record)
                self.assertBlocked(plan)
                self.assertReasonContaining(plan, "the env file was changed without restarting the stack")

    def test_non_version_labels_are_ignored(self):
        for label in ("", "dev", "latest", "1.50.0", "v1.50"):
            with self.subTest(label=label):
                plan = self.plan(self.standalone(PREVIOUS, label=label))
                self.assertEqual(plan["decision"], UPDATE_AVAILABLE, plan["reasons"])
                self.assertNoReasonContaining(plan, "without restarting")

    def test_mixed_labels_are_not_reported_as_an_env_change(self):
        plan = self.plan(self.distributed(PREVIOUS, labels={"backend": "v1.50.0", "worker": PREVIOUS}))
        self.assertNoReasonContaining(plan, "without restarting")

    def test_labels_of_one_service_agreeing_with_the_tag(self):
        installation = FakeInstallation(self.root, services={"app": f"futureagi/standalone:{PREVIOUS}"},
                                        env={"FUTURE_AGI_VERSION": PREVIOUS, "SECRET_KEY": "x"})
        installation.run("app", PREVIOUS).run("app", PREVIOUS)
        plan = self.plan(installation)
        self.assertEqual(plan["decision"], UPDATE_AVAILABLE, plan["reasons"])


# --------------------------------------------------------------------------
class Result:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


class FakeDocker:
    """Answers the docker / docker compose calls a real Installation makes while planning."""

    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config
        # `config --profiles`, and the services of profiles that are not enabled: name -> (profile, raw image).
        self.profiles: List[str] = []
        self.profile_services: Dict[str, Any] = {}
        # service -> its image as the compose files write it, when not raw_image() of the resolved one.
        self.raw: Dict[str, str] = {}
        self.records: List[Dict[str, Any]] = []
        self.images: Dict[str, Dict[str, Any]] = {}
        self.calls: List[List[str]] = []

    def container(self, service: str, image: str, label: str, command: Optional[List[str]] = None) -> "FakeDocker":
        index = len(self.records)
        image_id = "sha256:" + hashlib.sha256(f"{service}{label}{index}".encode()).hexdigest()
        self.records.append({
            "Id": f"{index:064x}", "Image": image_id, "RestartCount": 0,
            "Config": {"Image": image, "Labels": {"com.docker.compose.service": service,
                                                  "com.docker.compose.project": self.config["name"]},
                       "Entrypoint": None, "Cmd": command or []},
            "State": {"Status": "running", "ExitCode": 0, "StartedAt": "2026-10-01T00:00:00Z"},
        })
        self.images[image_id] = {"Id": image_id, "RepoDigests": [],
                                 "Config": {"Labels": {"org.opencontainers.image.version": label}}}
        return self

    def __call__(self, args, **kwargs):
        args = list(args)
        self.calls.append(args)
        env = kwargs.get("env") or {}
        if args[:2] == ["docker", "compose"] and "config" in args and "--profiles" in args:
            return Result(0, "".join(f"{profile}\n" for profile in self.profiles))
        if args[:2] == ["docker", "compose"] and "config" in args and "--no-interpolate" in args:
            return Result(15, "", "invalid spec: ./${AGENTCC_CONFIG_PATH:-x}:/y:ro: too many colons")
        if args[:2] == ["docker", "compose"] and "config" in args and "--services" in args:
            # The active services (G1): every service of the configuration, and those of the enabled profiles.
            enabled = set(filter(None, str(env.get("COMPOSE_PROFILES", "")).split(",")))
            names = list(self.config["services"]) + [name for name, (profile, _) in self.profile_services.items()
                                                     if profile in enabled]
            return Result(0, "".join(f"{name}\n" for name in names))
        if args[:2] == ["docker", "compose"] and "config" in args:
            if not any(str(env.get(key, "")).startswith(PROBE) for key in VERSION_VARIABLES):
                return Result(0, json.dumps(self.config))
            # The probe: Compose resolves every image with the environment's values, which win over the env file.
            enabled = set(filter(None, str(env.get("COMPOSE_PROFILES", "")).split(",")))
            services = {}
            for name, definition in self.config["services"].items():
                raw = self.raw.get(name) or (raw_image(definition["image"]) if "image" in definition else None)
                services[name] = dict(definition, **({"image": interpolate(raw, env)} if raw else {}))
            for name, (profile, raw) in self.profile_services.items():
                if profile in enabled:
                    services[name] = {"image": interpolate(raw, env), "profiles": [profile]}
            return Result(0, json.dumps(dict(self.config, services=services)))
        if args[:2] == ["docker", "ps"]:
            return Result(0, "".join(r["Id"] + "\n" for r in self.records))
        if args[:2] == ["docker", "inspect"]:
            return Result(0, json.dumps([r for r in self.records if r["Id"] in args[2:]]))
        if args[:3] == ["docker", "image", "inspect"]:
            record = self.images.get(args[3])
            return Result(0, json.dumps([record])) if record else Result(1, "", "Error: No such image")
        return Result(99, "", f"unexpected call {args}")


class RealInstallationTests(PlanTestCase):
    """build_plan against a real Installation, with docker answered by a fake."""

    def installation(self, docker: FakeDocker, *, env: str, production: bool = False) -> Installation:
        self.write("deploy/.env.production" if production else ".env", env)
        installation = Installation(self.root, production=production, environ={}, runner=docker,
                                    compose=["docker", "compose"])
        # These fixtures model local Compose containers; keep any deliberately conflicting labels intact.
        if production:
            files = ["docker-compose.distributed.yml", "deploy/docker-compose.production.yml"]
        elif "COMPOSE_FILE=docker-compose.distributed.yml" in env:
            files = ["docker-compose.distributed.yml"]
        else:
            files = ["docker-compose.yml"]
        provenance = {
            "com.docker.compose.project.working_dir": str(self.root),
            "com.docker.compose.project.config_files": ",".join(str(self.root / path) for path in files),
        }
        if production:
            provenance["com.docker.compose.project.environment_file"] = str(installation.env_file)
        for record in docker.records:
            for key, value in provenance.items():
                record["Config"]["Labels"].setdefault(key, value)
        return installation

    def test_plan_refuses_missing_compose_provenance(self):
        for field in ("working_dir", "config_files"):
            with self.subTest(field=field):
                docker = FakeDocker({"name": "futureagi", "services": {
                    "app": {"image": f"futureagi/standalone:{PREVIOUS}"}}})
                docker.container("app", f"futureagi/standalone:{PREVIOUS}", PREVIOUS)
                installation = self.installation(docker, env=f"FUTURE_AGI_VERSION={PREVIOUS}\nSECRET_KEY=abc\n")
                del docker.records[0]["Config"]["Labels"][f"com.docker.compose.project.{field}"]

                with self.assertRaisesRegex(InstallError, f"ownership.*{field}"):
                    self.plan(installation)

    def test_plan_refuses_another_checkouts_provenance(self):
        foreign_config = self.write("other-checkout/docker-compose.yml", "services: {}\n")
        for field, path in (("working_dir", foreign_config.parent), ("config_files", foreign_config)):
            with self.subTest(field=field):
                docker = FakeDocker({"name": "futureagi", "services": {
                    "app": {"image": f"futureagi/standalone:{PREVIOUS}"}}})
                docker.container("app", f"futureagi/standalone:{PREVIOUS}", PREVIOUS)
                docker.records[0]["Config"]["Labels"][f"com.docker.compose.project.{field}"] = str(path)
                installation = self.installation(docker, env=f"FUTURE_AGI_VERSION={PREVIOUS}\nSECRET_KEY=abc\n")

                with self.assertRaisesRegex(InstallError, f"ownership.*{field}"):
                    self.plan(installation)

    def test_production_plan_requires_its_explicit_environment_file(self):
        env = f"FUTURE_AGI_VERSION={PREVIOUS}\nFRONTEND_VERSION={PREVIOUS}\nPG_PASSWORD=pw-value\n"
        foreign_env = self.write("deploy/other.env", env)
        key = "com.docker.compose.project.environment_file"
        for recorded in (None, str(foreign_env)):
            with self.subTest(recorded=recorded):
                docker = FakeDocker({"name": "fagi-prod", "services": {
                    "backend": {"image": f"futureagi/future-agi:{PREVIOUS}"}}})
                docker.container("backend", f"futureagi/future-agi:{PREVIOUS}", PREVIOUS)
                if recorded is not None:
                    docker.records[0]["Config"]["Labels"][key] = recorded
                installation = self.installation(docker, production=True, env=env)
                if recorded is None:
                    del docker.records[0]["Config"]["Labels"][key]

                with self.assertRaisesRegex(InstallError, "ownership.*environment_file"):
                    self.plan(installation)

    def test_standalone_latest_install_reads_the_running_label(self):
        docker = FakeDocker({"name": "futureagi", "services": {
            "app": {"image": "futureagi/standalone:latest"}, "postgres": {"image": "postgres:16"}}})
        docker.container("app", "futureagi/standalone:latest", PREVIOUS).container("postgres", "postgres:16", "")
        installation = self.installation(docker, env="FUTURE_AGI_VERSION=\nSECRET_KEY=abc\n")
        plan = self.plan(installation)
        self.assertEqual(plan["decision"], UPDATE_AVAILABLE, plan["reasons"])
        item = self.component(plan, "standalone")
        self.assertEqual(item["current_version"], PREVIOUS)
        self.assertEqual(item["current_source"], "running image (follows :latest)")
        self.assertEqual(plan["env_changes"], {"FUTURE_AGI_VERSION": TARGET})
        self.assertEqual(plan["installation"], {"mode": "standalone", "project": "futureagi", "env_file": ".env",
                                                "root": str(self.root)})
        self.assertFalse([c for c in docker.calls if c[:3] == ["docker", "compose", "up"]])
        self.assertFalse([c for c in docker.calls if "pull" in c])

    def test_standalone_without_containers(self):
        docker = FakeDocker({"name": "futureagi", "services": {"app": {"image": f"futureagi/standalone:{PREVIOUS}"}}})
        plan = self.plan(self.installation(docker, env=f"FUTURE_AGI_VERSION={PREVIOUS}\nSECRET_KEY=abc\n"))
        self.assertBlocked(plan)
        # T53 (install.py's check_stack) rewords the remedy; the plan carries it as its reason.
        reason = self.assertReasonContaining(plan, "no containers of Compose project 'futureagi' exist")
        self.assertIn("./bin/install", reason)
        self.assertEqual(plan["reasons"], [reason])

    def test_standalone_env_with_distributed_containers(self):
        docker = FakeDocker({"name": "futureagi", "services": {"app": {"image": f"futureagi/standalone:{PREVIOUS}"}}})
        docker.container("backend", f"futureagi/future-agi:{PREVIOUS}", PREVIOUS)
        plan = self.plan(self.installation(docker, env=f"FUTURE_AGI_VERSION={PREVIOUS}\nSECRET_KEY=abc\n"))
        self.assertBlocked(plan)
        self.assertReasonContaining(plan, "project 'futureagi' runs Distributed services (backend) but .env selects "
                                          "the Standalone stack")

    def test_distributed_gpu_serving_maps_to_serving_gpu(self):
        services = {"backend": {"image": f"futureagi/future-agi:{PREVIOUS}"},
                    "serving": {"image": f"futureagi/serving:{PREVIOUS}-gpu"},
                    "fi-collector": {"image": "futureagi/fi-collector:local", "build": {"context": "fi-collector"}}}
        docker = FakeDocker({"name": "fagi", "services": services})
        docker.container("backend", f"futureagi/future-agi:{PREVIOUS}", PREVIOUS)
        docker.container("serving", f"futureagi/serving:{PREVIOUS}-gpu", PREVIOUS)
        docker.container("fi-collector", "futureagi/fi-collector:local", "dev")
        installation = self.installation(docker, env=(
            "COMPOSE_FILE=docker-compose.distributed.yml\n"
            f"FUTURE_AGI_VERSION={PREVIOUS}\nSERVING_VERSION={PREVIOUS}-gpu\nSECRET_KEY=abc\n"))
        self.assertEqual(installation.mode, "distributed")
        plan = self.plan(installation)
        self.assertEqual(plan["decision"], UPDATE_AVAILABLE, plan["reasons"])
        self.assertEqual(plan["env_changes"], {"FI_COLLECTOR_VERSION": TARGET, "FUTURE_AGI_VERSION": TARGET,
                                               "SERVING_VERSION": "v1.52.0-gpu"})
        self.assertEqual(self.component(plan, "serving_gpu")["services"], ["serving"])
        self.assertTrue(any("fi-collector is built from this checkout" in n for n in plan["notes"]))

    def test_production_notes_missing_keys_and_accepts_check_only_bootstrap(self):
        self.write("deploy/.env.production.example", PRODUCTION_EXAMPLE + "NEW_PROD_SETTING=1\n")
        manifest = self.manifest()
        services = {"backend": {"image": f"futureagi/future-agi:{PREVIOUS}"},
                    "frontend": {"image": f"futureagi/frontend:{PREVIOUS}"},
                    "postgres-schema-bootstrap": {"image": f"futureagi/future-agi:{PREVIOUS}"}}
        docker = FakeDocker({"name": "fagi-prod", "services": services})
        docker.container("backend", f"futureagi/future-agi:{PREVIOUS}", PREVIOUS)
        docker.container("frontend", f"futureagi/frontend:{PREVIOUS}", PREVIOUS)
        docker.container("postgres-schema-bootstrap", f"futureagi/future-agi:{PREVIOUS}", PREVIOUS,
                         command=["python", "bootstrap.py", "--check"])
        installation = self.installation(docker, production=True, env=(
            f"FUTURE_AGI_VERSION={PREVIOUS}\nFRONTEND_VERSION={PREVIOUS}\nPG_PASSWORD=pw-value\n"))
        plan = self.plan(installation, manifest=manifest)
        self.assertEqual(plan["decision"], UPDATE_AVAILABLE, plan["reasons"])
        self.assertEqual(plan["installation"]["mode"], "production")
        self.assertEqual(plan["installation"]["env_file"], "deploy/.env.production")
        self.assertEqual(plan["missing_env_keys"], ["NEW_PROD_SETTING"])
        self.assertTrue(any("deploy/.env.production lacks keys" in n for n in plan["notes"]))
        self.assertEqual(self.component(plan, "backend")["services"], ["backend", "postgres-schema-bootstrap"])

    def test_production_flag_on_a_migrating_install_is_blocked(self):
        services = {"backend": {"image": f"futureagi/future-agi:{TARGET}"},
                    "postgres-schema-bootstrap": {"image": f"futureagi/future-agi:{TARGET}"}}
        docker = FakeDocker({"name": "fagi", "services": services})
        docker.container("backend", f"futureagi/future-agi:{TARGET}", TARGET)
        docker.container("postgres-schema-bootstrap", f"futureagi/future-agi:{TARGET}", TARGET,
                         command=["python", "bootstrap.py", "--apply"])
        installation = self.installation(docker, production=True, env=(
            f"FUTURE_AGI_VERSION={TARGET}\nFRONTEND_VERSION={TARGET}\nPG_PASSWORD=pw-value\n"))
        plan = self.plan(installation)
        self.assertBlocked(plan)
        self.assertReasonContaining(plan, "--production was given, but this install was started without the "
                                          "production overlay")


class GeneratedManifestTests(PlanTestCase):
    """The plan accepts a manifest from scripts/generate_update_manifest.py for this checkout."""

    def generate(self, version: str) -> Dict[str, Any]:
        spec = importlib.util.spec_from_file_location("generate_update_manifest_for_plan_tests",
                                                      ROOT / "scripts" / "generate_update_manifest.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        self.write("deploy/update/deployment-files.txt", "# test list\ndocker-compose.yml\n.env.example\n")
        self.write("deploy/update/upgrade-floor", f"# floor\n{PREVIOUS}\n")
        components = []
        for name, repository in COMPONENT_REPOSITORIES.items():
            suffix = "-gpu" if name == "serving_gpu" else ""
            components.append(f"{name}=docker.io/{repository}:{version}{suffix}@{digest_for(name, version)}")
        args = argparse.Namespace(
            version=version, channel="stable", source_sha="f" * 40, published_at="2026-10-19T12:00:00Z",
            minimum_version=None, component=components, helm_chart="oci://ghcr.io/future-agi/charts/futureagi",
            helm_digest="sha256:" + "9" * 64, bundle_name=module.BUNDLE_ASSET, signature_identity=None,
            signature_issuer="https://token.actions.githubusercontent.com", repo_root=str(self.root), output=None)
        return module.build(args)

    def test_generated_manifest_plans_an_update(self):
        manifest = self.generate(TARGET)
        self.assertEqual(manifest, validate_manifest(manifest))
        plan = self.plan(self.standalone(PREVIOUS), manifest=manifest)
        self.assertEqual(plan["decision"], UPDATE_AVAILABLE, plan["reasons"])
        self.assertEqual(plan["minimum_version"], PREVIOUS)
        self.assertEqual(plan["deployment_drift"], [])
        self.assertEqual(self.component(plan, "standalone")["target_digest"], digest_for("standalone"))

    def test_generated_manifest_detects_later_drift(self):
        manifest = self.generate(TARGET)
        self.write(".env.example", ENV_EXAMPLE + "LATE=1\n")
        plan = self.plan(self.standalone(PREVIOUS), manifest=manifest)
        self.assertEqual(plan["deployment_drift"], [".env.example"])
        self.assertNoReasonContaining(plan, "lacks settings")


# --------------------------------------------------------------------------
class StoppedResumeTests(PlanTestCase):
    """A resumed apply that stopped before restarting anything changed nothing: the update it resumed still stands."""

    STOPPED = "/srv/fagi/.futureagi-updates/receipt-20261019T000000Z-1.json"
    RETRY = "/srv/fagi/.futureagi-updates/receipt-20261019T010000Z-2.json"

    def retry(self, **fields) -> Dict[str, Any]:
        record = {"kind": "apply", "status": "failed_before_rollout", "target_version": TARGET,
                  "resumes": self.STOPPED, "_path": self.RETRY}
        record.update(fields)
        return record

    def test_stopped_rollout_helper(self):
        resumable = {"kind": "apply", "status": "failed_during_rollout", "_path": self.STOPPED}
        self.assertIsNone(stopped_rollout(None))
        self.assertIsNone(stopped_rollout({}))
        self.assertEqual(stopped_rollout(resumable), resumable)
        self.assertIsNot(stopped_rollout(resumable), resumable)
        self.assertEqual(stopped_rollout(self.retry())["_path"], self.STOPPED)
        self.assertEqual(stopped_rollout(self.retry())["status"], "failed_before_rollout")
        self.assertEqual(BEFORE_ROLLOUT_STATUSES, ("failed_before_rollout", "interrupted_before_rollout"))
        for record in (self.retry(resumes=None), self.retry(resumes=""), self.retry(status="succeeded"),
                       self.retry(status="rolled_back"), self.retry(status="running")):
            with self.subTest(record=record):
                self.assertIsNone(stopped_rollout(record))

    def test_retry_that_stopped_before_the_restart_still_resumes(self):
        for status in BEFORE_ROLLOUT_STATUSES:
            with self.subTest(status=status):
                plan = self.plan(self.standalone(TARGET), last_receipt=self.retry(status=status))
                self.assertEqual(plan["decision"], UPDATE_AVAILABLE, plan["reasons"])
                self.assertTrue(plan["resume"])
                self.assertEqual(plan["env_changes"], {})
                self.assertIn(f"the previous update to v1.52.0 stopped while containers were starting, and the run "
                              f"that resumed it stopped before restarting anything ({status}); apply finishes it",
                              plan["notes"])

    def test_containers_still_on_the_old_release_are_not_an_env_change(self):
        plan = self.plan(self.standalone(TARGET, label=PREVIOUS), last_receipt=self.retry())
        self.assertEqual(plan["decision"], UPDATE_AVAILABLE, plan["reasons"])
        self.assertTrue(plan["resume"])
        self.assertNoReasonContaining(plan, "without restarting")

    def test_half_replaced_stack_is_not_up_to_date(self):
        installation = self.distributed(TARGET, labels={"backend": PREVIOUS})
        plan = self.plan(installation, last_receipt=self.retry(status="interrupted_before_rollout"))
        self.assertEqual(plan["decision"], UPDATE_AVAILABLE, plan["reasons"])
        self.assertTrue(plan["resume"])

    def test_journal_of_a_killed_retry_resumes(self):
        # check plans a killed run's journal with the status recovery will record; a journal has no _path.
        journal = {"kind": "apply", "status": "interrupted_before_rollout", "stage": "pulled",
                   "target_version": TARGET, "resumes": self.STOPPED}
        plan = self.plan(self.standalone(TARGET, label=PREVIOUS), last_receipt=journal)
        self.assertEqual(plan["decision"], UPDATE_AVAILABLE, plan["reasons"])
        self.assertTrue(plan["resume"])

    def test_newer_release_names_the_receipt_that_can_be_rolled_back(self):
        plan = self.plan(self.standalone(TARGET, label=PREVIOUS), manifest=self.manifest(version="v1.53.0"),
                         last_receipt=self.retry())
        self.assertBlocked(plan)
        self.assertReason(plan, (
            "the previous update to v1.52.0 stopped while containers were starting, and the run that resumed it "
            "stopped before restarting anything (failed_before_rollout). Finish it (check out v1.52.0 with `git "
            "checkout v1.52.0`, or at least its deployment files (your env file stays as it is), then ./bin/update "
            "apply --manifest https://github.com/future-agi/future-agi/releases/download/v1.52.0/"
            f"future-agi-update-manifest.json) or roll it back (./bin/update rollback --receipt {self.STOPPED} --yes) "
            "first"))
        self.assertNoReasonContaining(plan, self.RETRY)
        self.assertNoReasonContaining(plan, "without restarting")

    def test_production_hint_carries_its_flags(self):
        installation = self.distributed(TARGET, mode="production", production=True)
        plan = self.plan(installation, manifest=self.manifest(version="v1.53.0"), last_receipt=self.retry())
        self.assertReasonContaining(plan, f"(./bin/update --production rollback --receipt {self.STOPPED} --yes)")

    def test_a_fresh_run_that_stopped_before_the_restart_is_still_ignored(self):
        plan = self.plan(self.standalone(TARGET), last_receipt=self.retry(resumes=None))
        self.assertEqual(plan["decision"], UP_TO_DATE, plan["reasons"])
        self.assertFalse(plan["resume"])
        self.assertEqual(plan["notes"], [])


# --------------------------------------------------------------------------
STATE = "/srv/fagi/.futureagi-updates"


def receipt(name: str, **fields) -> Dict[str, Any]:
    record = {"kind": "apply", "status": "succeeded", "target_version": TARGET,
              "_path": f"{STATE}/receipt-20261019T00000{name}Z-{name}.json"}
    record.update(fields)
    return record


class UnfinishedHistoryTests(PlanTestCase):
    """Findings 7 and 12: runs that stopped before restarting anything never hide an unfinished update or rollback."""

    def stopped_apply(self, **fields) -> Dict[str, Any]:
        return receipt("1", status="failed_during_rollout", **fields)

    def rollback_before_rollout(self, status: str = "failed_before_rollout") -> Dict[str, Any]:
        return receipt("2", kind="rollback", status=status, target_version=PREVIOUS,
                       rolls_back=self.stopped_apply()["_path"])

    def test_unfinished_walks_past_runs_that_changed_nothing(self):
        stopped = self.stopped_apply()
        for status in BEFORE_ROLLOUT_STATUSES:
            for kind in ("apply", "rollback"):
                with self.subTest(status=status, kind=kind):
                    later = receipt("2", kind=kind, status=status, target_version=PREVIOUS)
                    self.assertEqual(unfinished([stopped, later]), stopped)
                    self.assertEqual(unfinished([stopped, later, dict(later, _path="x")]), stopped)

    def test_the_first_receipt_that_changed_something_decides(self):
        stopped = self.stopped_apply()
        for status in ("succeeded", "rolled_back", "running", None):
            with self.subTest(status=status):
                self.assertIsNone(unfinished([stopped, receipt("2", status=status)]))
        self.assertIsNone(unfinished([]))
        self.assertIsNone(unfinished(None))
        self.assertIsNone(unfinished([None, {}]))
        rollback = receipt("3", kind="rollback", status="interrupted_during_rollout")
        self.assertEqual(unfinished([stopped, rollback]), rollback)
        self.assertIsNot(unfinished([rollback]), rollback)
        self.assertEqual(unfinished([rollback, receipt("4", status="interrupted_before_rollout")]), rollback)

    def test_staged_applies_are_unfinished_staged_rollbacks_are_not(self):
        staged = receipt("1", status=STAGED)
        self.assertEqual(STAGED, "staged")
        self.assertEqual(unfinished([staged]), staged)
        self.assertEqual(unfinished([staged, receipt("2", status="interrupted_before_rollout")]), staged)
        self.assertIsNone(unfinished([staged, receipt("2", status="succeeded")]))
        self.assertIsNone(unfinished([receipt("1", kind="rollback", status=STAGED)]))
        self.assertEqual(stopped_rollout(staged), staged)

    def test_half_replaced_stack_after_a_rollback_stopped_before_the_restart_still_resumes(self):
        installation = self.distributed(TARGET, labels={"backend": TARGET, "worker": PREVIOUS})
        for status in BEFORE_ROLLOUT_STATUSES:
            with self.subTest(status=status):
                plan = self.plan(installation, history=[self.stopped_apply(), self.rollback_before_rollout(status)])
                self.assertEqual(plan["decision"], UPDATE_AVAILABLE, plan["reasons"])
                self.assertTrue(plan["resume"])
                self.assertIn("the previous update to v1.52.0 stopped while containers were starting "
                              "(failed_during_rollout); apply finishes it", plan["notes"])

    def test_standalone_still_on_the_old_app_resumes_instead_of_blaming_the_env_file(self):
        plan = self.plan(self.standalone(TARGET, label=PREVIOUS),
                         history=[self.stopped_apply(), self.rollback_before_rollout()])
        self.assertEqual(plan["decision"], UPDATE_AVAILABLE, plan["reasons"])
        self.assertTrue(plan["resume"])
        self.assertNoReasonContaining(plan, "without restarting")

    def test_a_stopped_rollback_stays_blocked_after_its_retry_stopped_before_the_restart(self):
        first = receipt("1", kind="rollback", status="failed_during_rollout", target_version=PREVIOUS,
                        rolls_back=f"{STATE}/receipt-20261018T000000Z-7.json")
        retry = receipt("2", kind="rollback", status="interrupted_before_rollout", target_version=PREVIOUS,
                        rolls_back=first["rolls_back"])
        plan = self.plan(self.standalone(PREVIOUS, label=TARGET), history=[first, retry])
        self.assertBlocked(plan)
        self.assertReason(plan, (
            "a rollback stopped while containers were being replaced. Finish it first: ./bin/update rollback "
            f"--receipt {STATE}/receipt-20261018T000000Z-7.json --yes"))
        self.assertNoReasonContaining(plan, "without restarting")

    def test_last_receipt_alone_is_a_history_of_one(self):
        record = self.stopped_apply()
        self.assertEqual(self.plan(self.standalone(TARGET), last_receipt=record),
                         self.plan(self.standalone(TARGET), history=[record]))
        # history wins over last_receipt when both are given
        plan = self.plan(self.standalone(TARGET), last_receipt=record, history=[])
        self.assertEqual(plan["decision"], UP_TO_DATE, plan["reasons"])
        # The newest receipt alone does not tell what the whole history does.
        newest = self.rollback_before_rollout()
        self.assertFalse(self.plan(self.standalone(TARGET), last_receipt=newest)["resume"])
        self.assertTrue(self.plan(self.standalone(TARGET), last_receipt=newest, history=[record, newest])["resume"])


class ReceiptScopeTests(PlanTestCase):
    """Finding 8: another install's receipts (same checkout, other env file or project) are not this one's."""

    def staging(self) -> FakeInstallation:
        installation = self.distributed(PREVIOUS, mode="production", production=True, project="fagi-staging")
        installation.env_file = self.root / "deploy/.env.staging"
        return installation

    def other_install(self, **fields) -> Dict[str, Any]:
        record = receipt("1", status="failed_during_rollout", mode="production", production=True,
                         env_file="deploy/.env.production", project="fagi")
        record.update(fields)
        return record

    def test_another_installs_stopped_update_is_not_resumed(self):
        installation = self.staging()
        for record in (self.other_install(), self.other_install(project="fagi-staging"),
                       self.other_install(env_file="deploy/.env.staging", project="other"),
                       self.other_install(env_file="deploy/.env.staging", project="fagi-staging", mode="distributed",
                                          production=False)):
            with self.subTest(record=record):
                plan = self.plan(installation, last_receipt=record)
                self.assertFalse(plan["resume"])
                self.assertFalse([n for n in plan["notes"] if "stopped" in n], plan["notes"])
                plan = self.plan(installation, history=[record])
                self.assertFalse(plan["resume"])

    def test_this_installs_receipts_still_count(self):
        installation = self.staging()
        record = self.other_install(env_file="deploy/.env.staging", project="fagi-staging")
        plan = self.plan(installation, history=[record, self.other_install(status="succeeded")])
        self.assertEqual(plan["decision"], UPDATE_AVAILABLE, plan["reasons"])
        self.assertTrue(plan["resume"])

    def test_another_installs_stopped_rollback_does_not_block(self):
        installation = self.staging()
        record = self.other_install(kind="rollback", rolls_back=f"{STATE}/receipt-a.json")
        plan = self.plan(installation, history=[record])
        self.assertNoReasonContaining(plan, "a rollback stopped")


class LiteralTagTests(PlanTestCase):
    """Findings 5 and 11: a version key bin/update would change must hold a tag it can write back."""

    def test_interpolated_key_that_changes_is_blocked(self):
        installation = self.distributed(PREVIOUS)
        installation.env["FRONTEND_VERSION"] = "${FUTURE_AGI_VERSION}"
        plan = self.plan(installation)
        self.assertBlocked(plan)
        self.assertReason(plan, (
            "FRONTEND_VERSION in .env is ${FUTURE_AGI_VERSION}, not a literal image tag: bin/update writes plain tags "
            "only, so updating it would replace that value outright. Set FRONTEND_VERSION=v1.51.0 (the tag it "
            "selects now), then check again"))
        self.assertEqual([r for r in plan["reasons"] if "literal" in r], [plan["reasons"][0]])
        # Plain tags and unset keys are fine, and a kept key is never written.
        for env in ({"FUTURE_AGI_VERSION": PREVIOUS}, {"FUTURE_AGI_VERSION": ""}, {"FUTURE_AGI_VERSION": "latest"}):
            with self.subTest(env=env):
                installation = self.standalone(PREVIOUS, extra_env=env, label=PREVIOUS)
                self.assertNoReasonContaining(self.plan(installation), "literal")
        plan = self.plan(self.standalone(TARGET, extra_env={"FUTURE_AGI_VERSION": "${RELEASE}"}))
        self.assertEqual(plan["decision"], UP_TO_DATE, plan["reasons"])

    def test_every_non_tag_value_is_refused(self):
        for value in ("${RELEASE}", "$RELEASE", "${RELEASE:-v1.51.0}", "v1.51.0 # pinned", "v1.51.0 ", "'v1.51.0'",
                      "-v1.51.0", "v1.51.0/x"):
            with self.subTest(value=value):
                plan = self.plan(self.standalone(PREVIOUS, extra_env={"FUTURE_AGI_VERSION": value}))
                self.assertBlocked(plan)
                reason = self.assertReasonContaining(plan, f"FUTURE_AGI_VERSION in .env is {value}, not a literal")
                self.assertIn("Set FUTURE_AGI_VERSION=v1.51.0", reason)

    def test_production_names_its_env_file(self):
        installation = self.distributed(PREVIOUS, mode="production", production=True)
        installation.env["SERVING_VERSION"] = "${FUTURE_AGI_VERSION}"
        reason = self.assertReasonContaining(self.plan(installation), "SERVING_VERSION in deploy/.env.production is "
                                                                      "${FUTURE_AGI_VERSION}, not a literal image tag")
        self.assertIn("Set SERVING_VERSION=v1.51.0", reason)


class RawComposeTests(PlanTestCase):
    """Findings 14 and 25: the compose files, before interpolation, decide what a version key changes."""

    HARBOR = "harbor.corp/dockerhub/futureagi/standalone"

    def proxied(self) -> FakeInstallation:
        installation = FakeInstallation(self.root, services={
            "app": f"{self.HARBOR}:{PREVIOUS}", "serving": f"futureagi/serving:{PREVIOUS}",
            "code-executor": f"futureagi/code-executor:{PREVIOUS}", "postgres": "postgres:16-alpine",
        }, env={"FUTURE_AGI_VERSION": PREVIOUS, "SERVING_VERSION": PREVIOUS, "CODE_EXECUTOR_VERSION": PREVIOUS,
                "SECRET_KEY": "x"})
        installation.raw_images["app"] = self.HARBOR + ":${FUTURE_AGI_VERSION:-latest}"
        for service in ("app", "serving", "code-executor"):
            installation.run(service, PREVIOUS)
        return installation

    def test_unrecognised_image_that_follows_a_version_key_is_blocked(self):
        plan = self.plan(self.proxied())
        self.assertBlocked(plan)
        self.assertReason(plan, (
            f"service app takes its image from FUTURE_AGI_VERSION, but that resolves to {self.HARBOR}:v1.51.0, which "
            "is not a Future AGI image bin/update knows, so the update would change it without verifying it against "
            "the release's signed digests. bin/update verifies images only from the registry the release publishes "
            "to (docker.io) and does not support registry mirrors yet: stop app using FUTURE_AGI_VERSION (set its "
            "image in a docker-compose.override.yml, say) and update it by hand, then check again"))
        self.assertNoReasonContaining(plan, "A mirror must keep")

    def test_a_key_an_active_service_uses_is_never_an_inactive_pin(self):
        plan = self.plan(self.proxied())
        self.assertFalse([c for c in plan["components"] if c["variable"] == "FUTURE_AGI_VERSION"], plan["components"])
        self.assertFalse([n for n in plan["notes"] if "outside the active" in n], plan["notes"])

    def test_override_that_sets_the_image_directly_is_blocked(self):
        for image, env in (("futureagi/serving:latest-gpu", {}),
                           (f"futureagi/serving:{PREVIOUS}-gpu", {"SERVING_VERSION": f"{PREVIOUS}-gpu"})):
            with self.subTest(image=image):
                installation = self.standalone(TARGET, extra_env=env)
                installation._config["services"]["serving"] = {"image": image}
                installation.raw_images["serving"] = image
                installation.run("serving", PREVIOUS)
                plan = self.plan(installation)
                self.assertBlocked(plan)
                self.assertReason(plan, (
                    f"service serving's image is set directly in a compose file ({image}), not through "
                    "SERVING_VERSION: bin/update can only change SERVING_VERSION in .env. Make serving use it "
                    "(image: futureagi/serving:${SERVING_VERSION}) or remove that image setting (a "
                    "docker-compose.override.yml, say), then check again"))

    def test_an_image_following_another_components_key_is_blocked(self):
        installation = self.distributed(PREVIOUS)
        installation.raw_images["frontend"] = "futureagi/frontend:${FUTURE_AGI_VERSION}"
        plan = self.plan(installation)
        self.assertBlocked(plan)
        self.assertReasonContaining(plan, "service frontend's image (futureagi/frontend:v1.51.0) follows "
                                          "FUTURE_AGI_VERSION, not FRONTEND_VERSION: bin/update changes "
                                          "FRONTEND_VERSION in .env for frontend")

    def test_compose_files_as_the_repository_writes_them_pass(self):
        self.assertEqual(referenced_variables("futureagi/future-agi:${FUTURE_AGI_VERSION:?must be set}"),
                         {"FUTURE_AGI_VERSION"})
        installation = self.distributed(PREVIOUS)
        installation.raw_images.update({
            "backend": "futureagi/future-agi:${FUTURE_AGI_VERSION:?must be set for production}",
            "worker": "futureagi/future-agi:$FUTURE_AGI_VERSION",
            "frontend": "futureagi/frontend:${FRONTEND_VERSION:-${FUTURE_AGI_VERSION}}",
        })
        plan = self.plan(installation)
        self.assertEqual(plan["decision"], UPDATE_AVAILABLE, plan["reasons"])
        # Services that are not Future AGI images may use any other variable.
        installation.raw_images["postgres"] = "postgres:${PG_VERSION:-16}-alpine"
        self.assertEqual(self.plan(installation)["decision"], UPDATE_AVAILABLE)
        # A compose digest pin keeps its own reason only.
        pinned = f"futureagi/standalone:{PREVIOUS}@sha256:{'a' * 64}"
        installation = FakeInstallation(self.root, services={"app": pinned},
                                        env={"FUTURE_AGI_VERSION": PREVIOUS, "SECRET_KEY": "x"}).run("app", PREVIOUS)
        installation.raw_images["app"] = pinned
        self.assertEqual(len(self.plan(installation)["reasons"]), 1)

    def test_referenced_variables(self):
        self.assertEqual(referenced_variables("futureagi/x:${A:-${B:-latest}}-$C"), {"A", "B", "C"})
        self.assertEqual(referenced_variables("futureagi/x:$${NOT}v1"), set())
        self.assertEqual(referenced_variables("futureagi/x:$$$A"), {"A"})
        self.assertEqual(referenced_variables("futureagi/x:v1.51.0"), set())
        self.assertEqual(referenced_variables("${A:?must be set}"), {"A"})


class InactiveServiceContainerTests(PlanTestCase):
    """Finding 15: containers of services the active profiles leave out would keep running the old release."""

    def test_running_container_of_an_inactive_service_is_blocked(self):
        installation = self.distributed(PREVIOUS)
        installation.profile_services["worker-default"] = "futureagi/future-agi:${FUTURE_AGI_VERSION:-latest}"
        installation.run("worker-default", PREVIOUS, image=f"futureagi/future-agi:{PREVIOUS}")
        plan = self.plan(installation)
        self.assertBlocked(plan)
        self.assertReason(plan, (
            "service worker-default has containers on Future AGI images but is not in the active Compose "
            "configuration (a profile that is not enabled now): worker-default (running, v1.51.0). The update to "
            "v1.52.0 does not replace its containers, so they would keep running as they are next to it. Enable the "
            "profile (COMPOSE_PROFILES in .env), or remove it with `docker compose rm -sf worker-default`, then "
            "check again"))

    def test_every_such_service_is_named_once_and_production_carries_its_files(self):
        installation = self.distributed(PREVIOUS, mode="production", production=True)
        for service in ("worker-tasks", "worker-default", "worker-default"):
            installation.profile_services[service] = "futureagi/future-agi:${FUTURE_AGI_VERSION:-latest}"
            installation.run(service, PREVIOUS, image=f"futureagi/future-agi:{PREVIOUS}", status="running")
        reason = self.assertReasonContaining(self.plan(installation), "services worker-default, worker-tasks have")
        self.assertIn("COMPOSE_PROFILES in deploy/.env.production", reason)
        self.assertIn("`docker compose --env-file deploy/.env.production -f docker-compose.distributed.yml -f "
                      "deploy/docker-compose.production.yml rm -sf worker-default worker-tasks`", reason)

    def test_one_off_and_other_containers_do_not_count(self):
        installation = self.distributed(PREVIOUS)
        installation.run("backend-shell", PREVIOUS, image=f"futureagi/future-agi:{PREVIOUS}", oneoff=True)
        installation.run("old-postgres", "15", image="postgres:15")
        installation.run("custom", "", image="sha256:" + "1" * 64)
        installation.profile_services["stray"] = "futureagi/future-agi:${FUTURE_AGI_VERSION:-latest}"
        installation.run("stray", PREVIOUS, image=f"futureagi/future-agi:{PREVIOUS}")
        plan = self.plan(installation)
        reason = self.assertReasonContaining(plan, "not in the active Compose configuration")
        self.assertTrue(reason.startswith("service stray has"), reason)

    def test_a_stack_mismatch_is_reported_instead(self):
        stack = ["project 'futureagi' runs Distributed services (backend) but .env selects the Standalone stack."]
        installation = self.standalone(PREVIOUS, stack_reasons=stack)
        installation.run("backend", PREVIOUS, image=f"futureagi/future-agi:{PREVIOUS}")
        plan = self.plan(installation)
        self.assertEqual(plan["reasons"], stack)
        # Without the mismatch the same container is a stray (an orphan: the standalone files do not define it).
        installation.stack_reasons = []
        self.assertTrue([n for n in self.plan(installation)["notes"] if n.startswith("service backend (running")])


class QuotedCommandTests(PlanTestCase):
    """Finding 22: every command a plan prints works when pasted, whatever the paths hold."""

    def test_env_file_path_is_shell_quoted(self):
        self.assertEqual(command_prefix(True, "deploy/prod eu.env"),
                         "./bin/update --production --env-file 'deploy/prod eu.env'")
        self.assertEqual(command_prefix(True, "/etc/fagi/it's.env"),
                         "./bin/update --production --env-file '/etc/fagi/it'\"'\"'s.env'")
        self.assertEqual(shlex.split(command_prefix(True, "deploy/prod eu.env")),
                         ["./bin/update", "--production", "--env-file", "deploy/prod eu.env"])

    def test_plan_commands_are_quoted(self):
        installation = self.distributed(PREVIOUS, mode="production", production=True)
        installation.env_file = self.root / "deploy/prod eu.env"
        path = "/srv/my fagi/.futureagi-updates/receipt-20261019T000000Z-1.json"
        record = receipt("1", status="failed_during_rollout", target_version="v1.51.5", _path=path)
        reason = self.assertReasonContaining(self.plan(installation, last_receipt=record), "roll it back")
        self.assertIn(f"./bin/update --production --env-file 'deploy/prod eu.env' rollback --receipt '{path}' --yes",
                      reason)
        rollback = receipt("2", kind="rollback", status="failed_during_rollout", rolls_back=path)
        reason = self.assertReasonContaining(self.plan(installation, last_receipt=rollback), "a rollback stopped")
        self.assertIn(f"rollback --receipt '{path}' --yes", reason)
        plan = self.plan(installation)
        self.assertEqual(plan["next_command"], "./bin/update --production --env-file 'deploy/prod eu.env' apply --stage")
        self.assertIn(f"run {plan['next_command']} ", plan_text(plan))


class UnmergeableEnvTests(PlanTestCase):
    """Finding 23: never send an .env bin/install's merge misreads through bin/install."""

    def with_env(self, text: str, *, encoding: str = "utf-8") -> FakeInstallation:
        self.write(".env.example", ENV_EXAMPLE + "NEW_SETTING=on\n")
        (self.root / ".env").write_bytes(text.encode(encoding))
        return self.standalone(PREVIOUS)

    def test_definitions_bin_install_misreads_get_a_by_hand_instruction(self):
        for text, kind in (("export SECRET_KEY=abc\n", "an export prefix"),
                           ("SECRET_KEY = abc\n", "spaces around ="),
                           ("SECRET_KEY =abc\n", "spaces around ="),
                           ("SECRET_KEY= abc\n", "spaces around ="),
                           ("  SECRET_KEY=abc\n", "leading indentation"),
                           ("\tSECRET_KEY=abc\n", "leading indentation"),
                           ("﻿SECRET_KEY=abc\n", "a byte order mark")):
            with self.subTest(text=text):
                plan = self.plan(self.with_env(text + f"FUTURE_AGI_VERSION={PREVIOUS}\n"))
                self.assertBlocked(plan)
                self.assertEqual(plan["missing_env_keys"], ["NEW_SETTING"])
                self.assertNoReasonContaining(plan, "./bin/install --no-up")
                self.assertReason(plan, (
                    ".env lacks settings this release's .env.example adds (NEW_SETTING). Copy them from .env.example "
                    "into .env by hand, then check again: ./bin/install cannot merge this .env (it misreads "
                    f"definitions written with {kind} and would add empty duplicates of them)"))

    def test_every_kind_is_named_once_and_a_plain_env_still_gets_bin_install(self):
        plan = self.plan(self.with_env("export A=1\nexport B=2\n  C = 3\n"))
        self.assertReasonContaining(plan, "written with an export prefix, spaces around =, leading indentation and")
        plan = self.plan(self.with_env(f"# a comment\n  # an indented comment\nFUTURE_AGI_VERSION={PREVIOUS}\n"
                                       "SECRET_KEY='a b = c'\nX=\n"))
        self.assertReasonContaining(plan, "Run `./bin/install --no-up -y` to add them")


class MixedRunningVersionTests(PlanTestCase):
    """Finding 24: the containers of a component must all run the version the env file names."""

    def test_part_of_the_stack_restarted_on_the_env_version_is_blocked(self):
        plan = self.plan(self.distributed(TARGET, labels={"backend": TARGET, "worker": PREVIOUS}))
        self.assertBlocked(plan)
        self.assertReason(plan, (
            "backend containers run different versions (v1.51.0 (worker), v1.52.0 (backend)) while FUTURE_AGI_VERSION "
            "in .env is v1.52.0: part of the stack was restarted on another version. Make them agree before "
            "updating: restart the stack (`docker compose up -d`) so every service runs v1.52.0, or first set "
            "FUTURE_AGI_VERSION to the version they should all run; then check again"))

    def test_a_newer_running_container_is_never_downgraded(self):
        plan = self.plan(self.distributed(PREVIOUS, labels={"backend": "v1.53.0"}))
        self.assertBlocked(plan)
        self.assertReasonContaining(plan, "backend containers run different versions (v1.51.0 (worker), "
                                          "v1.53.0 (backend))")
        self.assertReason(plan, "backend runs v1.53.0, newer than this release (v1.52.0); bin/update never downgrades")

    def test_an_unfinished_update_excuses_a_half_replaced_stack(self):
        installation = self.distributed(TARGET, labels={"backend": TARGET, "worker": PREVIOUS})
        plan = self.plan(installation, history=[receipt("1", status="interrupted_during_rollout")])
        self.assertEqual(plan["decision"], UPDATE_AVAILABLE, plan["reasons"])
        plan = self.plan(installation, history=[receipt("1", status=STAGED)])
        self.assertEqual(plan["decision"], UPDATE_AVAILABLE, plan["reasons"])

    def test_one_off_containers_do_not_count(self):
        installation = self.standalone(TARGET)
        installation.run("app", PREVIOUS, oneoff=True).run("app", "v1.53.0", oneoff=True)
        plan = self.plan(installation)
        self.assertEqual(plan["decision"], UP_TO_DATE, plan["reasons"])
        installation.run("app", PREVIOUS)
        self.assertReasonContaining(self.plan(installation), "standalone containers run different versions")


class EnvDigestPinTests(PlanTestCase):
    """Finding 26: a digest pinned through the env value is the env file's to fix, not a compose file's."""

    def test_digest_in_the_env_value_names_the_env_file(self):
        digest = "sha256:" + "a" * 64
        installation = self.distributed(PREVIOUS, mode="production", production=True,
                                        tags={"FI_COLLECTOR_VERSION": f"{PREVIOUS}@{digest}"})
        plan = self.plan(installation)
        self.assertBlocked(plan)
        self.assertReason(plan, (
            f"FI_COLLECTOR_VERSION in deploy/.env.production is {PREVIOUS}@{digest}, which pins a digest; bin/update "
            "writes plain version tags and checks every image against the release's signed digest itself. Set "
            f"FI_COLLECTOR_VERSION=v1.51.0 (without @{digest}), then check again"))
        self.assertNoReasonContaining(plan, "in a compose file")
        self.assertEqual(len([r for r in plan["reasons"] if "FI_COLLECTOR_VERSION" in r]), 1, plan["reasons"])

    def test_a_shared_key_is_reported_once(self):
        digest = "sha256:" + "b" * 64
        plan = self.plan(self.distributed(PREVIOUS, tags={"FUTURE_AGI_VERSION": f"{PREVIOUS}@{digest}"}))
        self.assertEqual(len([r for r in plan["reasons"] if "which pins a digest" in r]), 1, plan["reasons"])


class ReadinessAndJournalTests(PlanTestCase):
    """Finding 27: check refuses what apply refuses."""

    def test_invalid_standalone_readiness_settings_block_an_update(self):
        for key, value, bounds in (("INSTALL_READY_TIMEOUT_SECONDS", "30", "[60,1800]"),
                                   ("INSTALL_STABILITY_SECONDS", "soon", "[5,120]"),
                                   ("INSTALL_READY_MAX_SECONDS", "99999", "[300,7200]")):
            with self.subTest(key=key):
                plan = self.plan(self.standalone(PREVIOUS, extra_env={key: value}))
                self.assertBlocked(plan)
                self.assertReason(plan, (
                    f".env: {key} must be an integer in {bounds} (got {value!r}). bin/update waits for the app with "
                    "it as bin/install does; fix it, then check again"))

    def test_readiness_settings_matter_only_when_apply_would_wait(self):
        bad = {"INSTALL_READY_TIMEOUT_SECONDS": "30"}
        self.assertEqual(self.plan(self.standalone(TARGET, extra_env=bad))["decision"], UP_TO_DATE)
        self.assertEqual(self.plan(self.distributed(PREVIOUS, extra_env=bad))["decision"], UPDATE_AVAILABLE)
        resumed = self.plan(self.standalone(TARGET, extra_env=bad), history=[receipt("1", status="failed_during_rollout")])
        self.assertBlocked(resumed)

    def test_journal_reason_blocks_even_an_up_to_date_install(self):
        reason = ("an interrupted bin/update run for deploy/.env.production must be recovered before apply or "
                  "rollback can run for this install: the next `./bin/update --production apply` recovers it")
        plan = self.plan(self.standalone(TARGET), journal_reason=reason)
        self.assertBlocked(plan)
        self.assertEqual(plan["reasons"], [reason])
        self.assertIsNone(plan["next_command"])


class FallbackManifestTests(PlanTestCase):
    """Findings 37 and 45: a manifest that is only the newest release that has one."""

    NOTE = "the newest release v1.53.0 has no update manifest yet; using v1.52.0, the newest release that has one"

    def test_drift_does_not_send_the_operator_to_the_older_release(self):
        manifest = self.manifest()
        self.write("docker-compose.yml", "services: {v1.53.0: true}\n")
        plan = self.plan(self.standalone(PREVIOUS), manifest=manifest, fallback_note=self.NOTE, newer_release="v1.53.0")
        self.assertBlocked(plan)
        self.assertNoReasonContaining(plan, "git checkout")
        self.assertReason(plan, (
            "this checkout's deployment files differ from v1.52.0's (docker-compose.yml), but v1.52.0 is not the "
            "newest release: the newest one has no update manifest yet. Wait until its manifest is published and "
            "check again, or pass --manifest with the manifest URL of the release this checkout is on"))

    def test_an_install_on_the_newest_release_is_up_to_date(self):
        manifest = self.manifest()
        self.write("docker-compose.yml", "services: {v1.53.0: true}\n")
        self.write(".env.example", ENV_EXAMPLE + "V153_SETTING=1\n")
        installation = self.standalone("v1.53.0", extra_env={"SERVING_VERSION": "v1.53.0",
                                                             "CODE_EXECUTOR_VERSION": PREVIOUS})
        plan = self.plan(installation, manifest=manifest, fallback_note=self.NOTE, newer_release="v1.53.0")
        self.assertEqual(plan["decision"], UP_TO_DATE, plan["reasons"])
        self.assertEqual(plan["reasons"], [])
        self.assertEqual(plan["env_changes"], {})
        self.assertEqual(plan["missing_env_keys"], [])
        self.assertIsNone(plan["next_command"])
        self.assertEqual({item["action"] for item in plan["components"]}, {"keep"})
        self.assertIn("this install already runs v1.53.0, the same as or newer than v1.52.0, the newest release that "
                      "has an update manifest: there is nothing to update until the newer release's manifest is "
                      "published", plan["notes"])
        self.assertIn("  Decision      up to date, nothing to do\n", plan_text(plan))

    def test_an_install_on_the_fallback_release_is_up_to_date_despite_the_newer_checkout(self):
        manifest = self.manifest()
        self.write("docker-compose.yml", "services: {v1.53.0: true}\n")
        plan = self.plan(self.standalone(TARGET), manifest=manifest, fallback_note=self.NOTE, newer_release="v1.53.0")
        self.assertEqual(plan["decision"], UP_TO_DATE, plan["reasons"])

    def test_other_problems_still_block(self):
        plan = self.plan(self.standalone("v1.53.0"), fallback_note=self.NOTE, newer_release="v1.53.0",
                         verification=Verification(False, error="no signature bundle"))
        self.assertBlocked(plan)
        self.assertEqual(plan["reasons"], ["the release signature is not verified: no signature bundle"])
        plan = self.plan(self.distributed("v1.53.0", labels={"worker": PREVIOUS}), fallback_note=self.NOTE,
                         newer_release="v1.53.0")
        self.assertReasonContaining(plan, "backend containers run different versions")
        # Without the fallback a newer install is still a downgrade, and so it is when only part of it is newer.
        self.assertReasonContaining(self.plan(self.standalone("v1.53.0")), "never downgrades")
        installation = self.distributed("v1.53.0", tags={"FRONTEND_VERSION": PREVIOUS})
        plan = self.plan(installation, fallback_note=self.NOTE, newer_release="v1.53.0")
        self.assertBlocked(plan)
        self.assertReasonContaining(plan, "backend runs v1.53.0, newer than this release")


class StagedUpdateTests(PlanTestCase):
    """D3: `apply --stage` leaves an update for a later `apply` to finish."""

    def staged(self, **fields) -> Dict[str, Any]:
        record = receipt("1", status=STAGED, manifest_source=(
            "https://github.com/future-agi/future-agi/releases/download/v1.52.0/future-agi-update-manifest.json"))
        record.update(fields)
        return record

    def test_staged_update_of_the_release_is_finished_by_apply(self):
        plan = self.plan(self.standalone(TARGET, label=PREVIOUS), history=[self.staged()])
        self.assertEqual(plan["decision"], UPDATE_AVAILABLE, plan["reasons"])
        self.assertTrue(plan["resume"])
        self.assertEqual(plan["env_changes"], {})
        self.assertEqual(plan["next_command"], "./bin/update apply")
        self.assertNoReasonContaining(plan, "without restarting")
        self.assertIn("the update to v1.52.0 is staged (its images are pulled and verified, nothing was restarted); "
                      "./bin/update apply finishes it", plan["notes"])

    def test_production_staged_update_reminds_of_the_initialization_jobs(self):
        installation = self.distributed(TARGET, mode="production", production=True,
                                        labels={"FUTURE_AGI_VERSION": PREVIOUS})
        plan = self.plan(installation, history=[self.staged()])
        self.assertEqual(plan["decision"], UPDATE_AVAILABLE, plan["reasons"])
        self.assertEqual(plan["next_command"], "./bin/update --production apply")
        self.assertIn("the update to v1.52.0 is staged (its images are pulled and verified, nothing was restarted); "
                      "if the release's initialization jobs have not run yet, run them with `docker compose --env-file "
                      "deploy/.env.production -f docker-compose.distributed.yml -f deploy/docker-compose.production.yml "
                      "run --rm ...` (deploy/README.md › Upgrading a production install. Run it from a shell that "
                      "does not export any key of deploy/.env.production (Compose prefers your shell over the env "
                      "file).); then ./bin/update --production apply finishes it", plan["notes"])
        self.assertFalse([n for n in plan["notes"] if "two steps" in n], plan["notes"])
        self.assertIn("  Decision      update available: if the release's initialization jobs have not run yet, run "
                      "them first with `docker compose --env-file deploy/.env.production -f "
                      "docker-compose.distributed.yml -f deploy/docker-compose.production.yml run --rm ...` "
                      "(deploy/README.md › Upgrading a production install. Run it from a shell that does not export "
                      "any key of deploy/.env.production (Compose prefers your shell over the env file).); then run "
                      "./bin/update --production apply (finishes the staged update)\n", plan_text(plan))

    def test_a_newer_release_waits_for_the_staged_one(self):
        plan = self.plan(self.standalone(TARGET, label=PREVIOUS), manifest=self.manifest(version="v1.53.0"),
                         history=[self.staged()])
        self.assertBlocked(plan)
        self.assertReason(plan, (
            "the update to v1.52.0 is staged (its images are pulled and verified, .env selects them, and nothing "
            "was restarted). Finish it first (check out v1.52.0 with `git checkout v1.52.0`, or at least its "
            "deployment files (your env file stays as it is), then ./bin/update apply --manifest "
            "https://github.com/future-agi/future-agi/releases/download/v1.52.0/future-agi-update-manifest.json) or "
            f"undo it (./bin/update rollback --receipt {STATE}/receipt-20261019T000001Z-1.json --yes), then check "
            "again"))
        self.assertNoReasonContaining(plan, "without restarting")

    def test_the_finish_command_names_a_manifest_that_keeps_naming_the_staged_release(self):
        latest = "https://github.com/future-agi/future-agi/releases/latest/download/future-agi-update-manifest.json"
        release = "https://github.com/future-agi/future-agi/releases/download/v1.52.0/future-agi-update-manifest.json"
        manifest = self.manifest(version="v1.53.0")
        # Staged: the stack still runs the release before it (one that runs the staged release is not staged).
        installation = self.distributed(TARGET, mode="production", production=True, labels=ALL_PREVIOUS)
        for source, shown in ((latest, release), ("", release), ("/srv/my releases/m.json", "'/srv/my releases/m.json'")):
            with self.subTest(source=source):
                plan = self.plan(installation, manifest=manifest, history=[self.staged(manifest_source=source)])
                reason = self.assertReasonContaining(plan, "is staged")
                self.assertIn("Finish it first (check out v1.52.0 with `git checkout v1.52.0`, or at least its "
                              "deployment files (your env file stays as it is), run its initialization jobs with `"
                              f"{PRODUCTION_HINT} run --rm ...` (deploy/README.md › Upgrading a production install. "
                              "Run it from a shell that does not export any key of deploy/.env.production (Compose "
                              "prefers your shell over the env file).) if they have not run yet, then "
                              f"./bin/update --production apply --manifest {shown})", reason)
        self.write("update.conf", "FUTURE_AGI_UPDATE_MANIFEST_URL=/srv/m.json\n")
        plan = self.plan(installation, manifest=manifest, history=[self.staged(manifest_source="")])
        self.assertIn("apply --manifest <the manifest of v1.52.0>", self.assertReasonContaining(plan, "is staged"))

    def test_fresh_production_update_starts_with_the_stage(self):
        plan = self.plan(self.distributed(PREVIOUS, mode="production", production=True))
        self.assertEqual(plan["decision"], UPDATE_AVAILABLE, plan["reasons"])
        self.assertFalse(plan["resume"])
        self.assertEqual(plan["next_command"], "./bin/update --production apply --stage")
        self.assertIn(
            "production installs update in two steps: ./bin/update --production apply --stage (backs up, then pulls "
            "and verifies the release; restarts nothing), run the release's initialization jobs with `docker compose "
            "--env-file deploy/.env.production -f docker-compose.distributed.yml -f "
            "deploy/docker-compose.production.yml run --rm ...` (deploy/README.md › Upgrading a production install. "
            "Run it from a shell that does not export any key of deploy/.env.production (Compose prefers your shell "
            "over the env file).), then ./bin/update --production apply",
            plan["notes"])
        self.assertIn("  Decision      update available: run ./bin/update --production apply --stage (the first of two "
                      "steps, see below)\n", plan_text(plan))

    def test_a_release_without_initialization_needs_no_stage(self):
        manifest = self.manifest()
        manifest["migration"]["requires_bootstrap"] = False
        plan = self.plan(self.distributed(PREVIOUS, mode="production", production=True), manifest=manifest)
        self.assertEqual(plan["next_command"], "./bin/update --production apply")
        self.assertFalse([n for n in plan["notes"] if "two steps" in n], plan["notes"])

    def test_resuming_a_stopped_production_rollout_names_the_initialization_jobs(self):
        installation = self.distributed(TARGET, mode="production", production=True,
                                        labels={"worker": PREVIOUS})
        plan = self.plan(installation, history=[receipt("1", status="failed_during_rollout")])
        self.assertEqual(plan["decision"], UPDATE_AVAILABLE, plan["reasons"])
        self.assertEqual(plan["next_command"], "./bin/update --production apply")
        self.assertIn("deploy/.env.production already selects v1.52.0: if the release's initialization jobs have not "
                      "run yet (the production bootstrap jobs only check the schema), run them first with `docker "
                      "compose --env-file deploy/.env.production -f docker-compose.distributed.yml -f "
                      "deploy/docker-compose.production.yml run --rm ...` (deploy/README.md › Upgrading a production "
                      "install. Run it from a shell that does not export any key of deploy/.env.production (Compose "
                      "prefers your shell over the env file).)", plan["notes"])

    def test_non_production_installs_apply_directly(self):
        for installation in (self.standalone(PREVIOUS), self.distributed(PREVIOUS)):
            with self.subTest(mode=installation.mode):
                plan = self.plan(installation)
                self.assertEqual(plan["next_command"], "./bin/update apply")
                self.assertFalse([n for n in plan["notes"] if "two steps" in n], plan["notes"])


class NextCommandTests(PlanTestCase):
    """D7: the plan names the command that moves the install forward, and only then."""

    def test_next_command_by_decision(self):
        self.assertEqual(self.plan(self.standalone(PREVIOUS))["next_command"], "./bin/update apply")
        self.assertIsNone(self.plan(self.standalone(TARGET))["next_command"])
        self.assertIsNone(self.plan(self.standalone("local"))["next_command"])
        resumed = self.plan(self.standalone(TARGET), history=[receipt("1", status="interrupted_during_rollout")])
        self.assertEqual(resumed["next_command"], "./bin/update apply")

    def test_plan_text_prints_it(self):
        plan = self.plan(self.standalone(PREVIOUS))
        plan["next_command"] = "./bin/update apply --stage"
        self.assertIn("  Decision      update available: run ./bin/update apply --stage (the first of two steps, see "
                      "below)\n", plan_text(plan))
        del plan["next_command"]
        self.assertIn("  Decision      update available: run ./bin/update apply\n", plan_text(plan))

    def test_next_command_round_trips_through_json(self):
        plan = self.plan(self.distributed(PREVIOUS, mode="production", production=True))
        self.assertEqual(json.loads(plan_json(plan))["next_command"], "./bin/update --production apply --stage")
        self.assertIsNone(json.loads(plan_json(self.plan(self.standalone(TARGET))))["next_command"])


class PullPolicyTests(PlanTestCase):
    """Findings 16 and 43: the restart may start only the images bin/update verified.

    apply runs `up --pull never` where Compose has it; where it does not, a
    Future AGI service whose pull_policy pulls again would have `up` fetch
    whatever its tag points at after the digest check, so the plan refuses.
    """

    def with_policies(self, installation, policies, *, supported):
        for service, policy in policies.items():
            installation._config["services"][service]["pull_policy"] = policy
        calls = []

        def supports_up_pull_never():
            calls.append(True)
            return supported

        installation.supports_up_pull_never = supports_up_pull_never
        return calls

    def test_a_policy_that_pulls_again_blocks_without_up_pull_never(self):
        for policy in ("always", "daily", "weekly", "every_12h", "ALWAYS"):
            with self.subTest(policy=policy):
                installation = self.standalone(PREVIOUS)
                self.with_policies(installation, {"app": policy}, supported=False)
                plan = self.plan(installation)
                self.assertBlocked(plan)
                self.assertReason(plan, (
                    f"service app sets pull_policy: {policy}, so the restart would pull its image again after "
                    "bin/update verified it, and this Docker Compose (`docker compose`) has no `up --pull never` to "
                    "prevent that. Update Docker Compose, or remove that pull_policy (a docker-compose.override.yml, "
                    "say), then check again"))

    def test_several_services_are_named_together(self):
        installation = self.distributed(PREVIOUS, mode="production", production=True)
        self.with_policies(installation, {"worker": "daily", "backend": "always"}, supported=False)
        reason = self.assertReasonContaining(self.plan(installation), "pull_policy")
        self.assertTrue(reason.startswith("services backend, worker set pull_policy: always, daily, so the restart "
                                          "would pull their images again after bin/update verified them"), reason)
        self.assertIn("(`docker compose --env-file deploy/.env.production -f docker-compose.distributed.yml -f "
                      "deploy/docker-compose.production.yml`)", reason)

    def test_compose_with_up_pull_never_overrides_it(self):
        installation = self.standalone(PREVIOUS)
        calls = self.with_policies(installation, {"app": "always"}, supported=True)
        plan = self.plan(installation)
        self.assertEqual(plan["decision"], UPDATE_AVAILABLE, plan["reasons"])
        self.assertEqual(calls, [True])

    def test_only_a_future_agi_service_that_pulls_again_counts(self):
        for policy in ("missing", "if_not_present", "never", "build", "", None):
            with self.subTest(policy=policy):
                installation = self.standalone(PREVIOUS)
                # postgres is not an image bin/update verifies: its pull_policy is its own business.
                calls = self.with_policies(installation, {"app": policy, "postgres": "always"}, supported=False)
                plan = self.plan(installation)
                self.assertEqual(plan["decision"], UPDATE_AVAILABLE, plan["reasons"])
                self.assertEqual(calls, [], "Compose is asked only when a policy pulls again")
                self.with_policies(installation, {"app": "always"}, supported=False)
                self.assertReasonContaining(self.plan(installation), "service app sets pull_policy: always")


class StoppedUpdateCommandTests(PlanTestCase):
    """An update stopped mid-rollout for another release: both ways out are commands that run as printed."""

    NEWER = "v1.53.0"

    def stopped(self, **fields):
        record = {"kind": "apply", "status": "failed_during_rollout", "target_version": TARGET,
                  "_path": "/srv/my fagi/.futureagi-updates/receipt-20261019T000000Z-1.json"}
        record.update(fields)
        return record

    def reason(self, record, settings=None, installation=None):
        plan = self.plan(installation or self.standalone(TARGET, label=PREVIOUS),
                         manifest=self.manifest(version=self.NEWER), last_receipt=record, settings=settings)
        self.assertBlocked(plan)
        return self.assertReasonContaining(plan, f"the previous update to {TARGET} stopped")

    def test_the_finish_command_names_the_manifest_the_stopped_run_used(self):
        reason = self.reason(self.stopped(manifest_source="/srv/release files/v1.52.0.json"))
        # Round 6 (U7): its checkout comes first, as for a staged update of another release.
        self.assertIn("Finish it (check out v1.52.0 with `git checkout v1.52.0`, or at least its deployment files "
                      "(your env file stays as it is), then ./bin/update apply --manifest '/srv/release "
                      "files/v1.52.0.json') or roll it back (./bin/update rollback --receipt "
                      "'/srv/my fagi/.futureagi-updates/receipt-20261019T000000Z-1.json' --yes) first", reason)
        # Without one (and with no GitHub latest URL to derive it from) the release is named instead.
        settings = self.settings(environ={"FUTURE_AGI_UPDATE_MANIFEST_URL": "/srv/manifests/latest.json"})
        reason = self.reason(self.stopped(), settings=settings)
        self.assertIn("then ./bin/update apply --manifest <the manifest of v1.52.0>) or roll it back (./bin/update "
                      "rollback --receipt", reason)

    def test_githubs_latest_url_becomes_the_stopped_releases_own(self):
        latest = "https://github.com/acme/fagi/releases/latest/download/future-agi-update-manifest.json"
        reason = self.reason(self.stopped(manifest_source=latest))
        self.assertIn("then ./bin/update apply --manifest https://github.com/acme/fagi/releases/download/v1.52.0/"
                      "future-agi-update-manifest.json)", reason)

    def test_a_production_install_keeps_its_flags_in_both_commands(self):
        installation = self.distributed(TARGET, mode="production", production=True)
        installation.env_file = self.root / "deploy" / "prod eu.env"
        reason = self.reason(self.stopped(manifest_source="/srv/m.json"), installation=installation)
        prefix = "./bin/update --production --env-file 'deploy/prod eu.env'"
        self.assertIn(f"then {prefix} apply --manifest /srv/m.json) or roll it back ({prefix} rollback --receipt",
                      reason)
        # Round 6 (U7): the release's initialization jobs come before it, with this install's env file.
        self.assertIn("Finish it (check out v1.52.0 with `git checkout v1.52.0`, or at least its deployment files "
                      "(your env file stays as it is), run its initialization jobs with `docker compose --env-file "
                      "'deploy/prod eu.env' -f docker-compose.distributed.yml -f deploy/docker-compose.production.yml "
                      "run --rm ...`", reason)

    def test_a_run_that_is_still_a_journal_is_undone_by_a_plain_rollback(self):
        # check and apply --dry-run plan with a killed run's journal: it has no receipt until the rollback
        # recovers it, and then it is the newest update.
        record = self.stopped(status="interrupted_during_rollout", manifest_source="/srv/m.json")
        record.pop("_path")
        reason = self.reason(record)
        self.assertIn("or roll it back (./bin/update rollback --yes) first", reason)
        self.assertNotIn("<receipt>", reason)


# --------------------------------------------------------------------------
# Round 3
STAGED_SOURCE = "https://github.com/future-agi/future-agi/releases/download/v1.52.0/future-agi-update-manifest.json"
PRODUCTION_HINT = ("docker compose --env-file deploy/.env.production -f docker-compose.distributed.yml -f "
                   "deploy/docker-compose.production.yml")
JOBS_DOC = "(deploy/README.md › Upgrading a production install)"
WORKER_RAW = "futureagi/future-agi:${FUTURE_AGI_VERSION:-latest}"
ALL_PREVIOUS = {key: PREVIOUS for key in VERSION_VARIABLES}


class StagedInstallReportTests(PlanTestCase):
    """R2 (E3): a staged install is reported as what runs, with the staged release next to it."""

    def staged(self, **fields) -> Dict[str, Any]:
        record = receipt("1", status=STAGED, manifest_source=STAGED_SOURCE)
        record.update(fields)
        return record

    def test_the_installed_line_says_what_runs_and_what_is_staged(self):
        plan = self.plan(self.standalone(TARGET, label=PREVIOUS), history=[self.staged()])
        self.assertEqual((plan["decision"], plan["resume"], plan["staged_version"]), (UPDATE_AVAILABLE, True, TARGET))
        # What runs: the finishing run records these as its from_versions.
        self.assertEqual(plan["current_versions"], [PREVIOUS])
        text = plan_text(plan)
        self.assertIn("  Installed     v1.51.0 running, v1.52.0 staged\n", text)
        self.assertIn("  Decision      update available: run ./bin/update apply (finishes the staged update)\n", text)
        self.assertNotIn("stopped update", text)

    def test_components_show_the_running_version_and_the_update(self):
        installation = self.distributed(TARGET, labels=ALL_PREVIOUS)
        plan = self.plan(installation, history=[self.staged()])
        self.assertEqual(plan["decision"], UPDATE_AVAILABLE, plan["reasons"])
        self.assertEqual(plan["current_versions"], [PREVIOUS])
        for item in plan["components"]:
            with self.subTest(component=item["component"]):
                self.assertEqual((item["current_version"], item["action"], item["target_tag"]),
                                 (PREVIOUS, "update", TARGET))
        self.assertEqual(self.component(plan, "backend")["current_source"],
                         "running image (FUTURE_AGI_VERSION in .env selects the staged v1.52.0)")
        # The env file already selects the release: finishing the update writes nothing.
        self.assertEqual(plan["env_changes"], {})
        self.assertIn("    backend            v1.51.0    ->   v1.52.0        ", plan_text(plan))

    def test_the_version_rules_are_not_applied_again_when_finishing(self):
        # The staging run judged policy and minimum_version; finishing it does not ask for --policy again.
        plan = self.plan(self.standalone(TARGET, label=PREVIOUS), history=[self.staged()],
                         settings=self.settings(policy="patch"))
        self.assertEqual(plan["decision"], UPDATE_AVAILABLE, plan["reasons"])

    def test_a_newer_release_while_one_is_staged_reports_the_staged_one(self):
        plan = self.plan(self.standalone(TARGET, label=PREVIOUS), manifest=self.manifest(version="v1.53.0"),
                         history=[self.staged()])
        self.assertBlocked(plan)
        self.assertEqual((plan["current_versions"], plan["staged_version"]), ([PREVIOUS], TARGET))
        self.assertIn("  Installed     v1.51.0 running, v1.52.0 staged\n", plan_text(plan))
        self.assertReasonContaining(plan, "the update to v1.52.0 is staged")

    def test_a_stopped_rollout_is_not_called_staged(self):
        plan = self.plan(self.standalone(TARGET, label=PREVIOUS),
                         history=[receipt("1", status="failed_during_rollout")])
        self.assertEqual((plan["decision"], plan["staged_version"]), (UPDATE_AVAILABLE, None))
        text = plan_text(plan)
        # S3: what runs, and the update the env file selects, as for a staged one.
        self.assertIn("  Installed     v1.51.0 running, update to v1.52.0 stopped part-way\n", text)
        self.assertIn("  Decision      update available: run ./bin/update apply (finishes the stopped update)\n", text)

    def test_json_carries_the_staged_release(self):
        plan = json.loads(plan_json(self.plan(self.standalone(TARGET, label=PREVIOUS), history=[self.staged()])))
        self.assertEqual((plan["staged_version"], plan["current_versions"]), (TARGET, [PREVIOUS]))
        self.assertIsNone(json.loads(plan_json(self.plan(self.standalone(PREVIOUS))))["staged_version"])


class InitializationReminderTests(PlanTestCase):
    """R3, R5 (E3): finishing a production update reminds of the release's initialization jobs, with this install's
    Compose prefix, and the two-step note names the same prefix."""

    def production(self, version: str = TARGET, *, env_file: Optional[str] = None, **kwargs) -> FakeInstallation:
        installation = self.distributed(version, mode="production", production=True, **kwargs)
        if env_file:
            installation.env_file = self.root / env_file
        return installation

    def reminder(self, hint: str = PRODUCTION_HINT, env_file: str = "deploy/.env.production") -> str:
        return (f"if the release's initialization jobs have not run yet, run them first with `{hint} run --rm ...` "
                f"(deploy/README.md › Upgrading a production install. {SHELL_RULE.format(env=env_file)})")

    def test_a_staged_production_update_names_the_jobs_before_apply(self):
        plan = self.plan(self.production(labels=ALL_PREVIOUS), history=[receipt("1", status=STAGED)])
        self.assertEqual((plan["decision"], plan["resume"]), (UPDATE_AVAILABLE, True))
        # The command stays one that runs as printed; the reminder comes with it.
        self.assertEqual(plan["next_command"], "./bin/update --production apply")
        self.assertEqual(plan["before_next_command"], self.reminder())
        self.assertIn(f"  Decision      update available: {self.reminder()}; then run ./bin/update --production apply "
                      "(finishes the staged update)\n", plan_text(plan))
        self.assertEqual(json.loads(plan_json(plan))["before_next_command"], self.reminder())

    def test_a_stopped_production_rollout_names_them_too(self):
        plan = self.plan(self.production(labels={"worker": PREVIOUS}),
                         history=[receipt("1", status="failed_during_rollout")])
        self.assertEqual((plan["decision"], plan["resume"]), (UPDATE_AVAILABLE, True))
        self.assertIn(f"  Decision      update available: {self.reminder()}; then run ./bin/update --production apply "
                      "(finishes the stopped update)\n", plan_text(plan))

    def test_a_custom_env_file_is_carried_into_every_command(self):
        prefix = "./bin/update --production --env-file 'deploy/prod eu.env'"
        hint = ("docker compose --env-file 'deploy/prod eu.env' -f docker-compose.distributed.yml -f "
                "deploy/docker-compose.production.yml")
        plan = self.plan(self.production(env_file="deploy/prod eu.env", labels=ALL_PREVIOUS),
                         history=[receipt("1", status=STAGED)])
        self.assertEqual(plan["next_command"], f"{prefix} apply")
        self.assertEqual(plan["before_next_command"], self.reminder(hint, "deploy/prod eu.env"))
        rule = SHELL_RULE.format(env="deploy/prod eu.env")
        staged_note = (f"if the release's initialization jobs have not run yet, run them with `{hint} run --rm ...` "
                       f"(deploy/README.md › Upgrading a production install. {rule}); then {prefix} apply finishes "
                       "it")
        self.assertTrue([n for n in plan["notes"] if staged_note in n], plan["notes"])
        # R5: the two-step note of a fresh update names the prefix the jobs must run with, not the docs' $COMPOSE.
        fresh = self.plan(self.production(PREVIOUS, env_file="deploy/prod eu.env"))
        self.assertEqual((fresh["next_command"], fresh["before_next_command"]), (f"{prefix} apply --stage", None))
        self.assertIn(
            f"production installs update in two steps: {prefix} apply --stage (backs up, then pulls and verifies the "
            f"release; restarts nothing), run the release's initialization jobs with `{hint} run --rm ...` "
            f"(deploy/README.md › Upgrading a production install. {rule}), then {prefix} apply", fresh["notes"])

    def test_no_reminder_without_initialization_jobs_or_outside_production(self):
        staged = [receipt("1", status=STAGED)]
        manifest = self.manifest()
        manifest["migration"]["requires_bootstrap"] = False
        plan = self.plan(self.production(labels=ALL_PREVIOUS), manifest=manifest, history=staged)
        self.assertEqual((plan["decision"], plan["before_next_command"]), (UPDATE_AVAILABLE, None))
        self.assertIn("  Decision      update available: run ./bin/update --production apply (finishes the staged "
                      "update)\n", plan_text(plan))
        for installation in (self.standalone(TARGET, label=PREVIOUS), self.distributed(TARGET, labels=ALL_PREVIOUS)):
            with self.subTest(mode=installation.mode):
                plan = self.plan(installation, history=staged)
                self.assertEqual((plan["decision"], plan["before_next_command"]), (UPDATE_AVAILABLE, None))
                self.assertNotIn("initialization jobs", plan_text(plan))

    def test_a_blocked_plan_names_no_next_step(self):
        plan = self.plan(self.production(labels=ALL_PREVIOUS), history=[receipt("1", status=STAGED)],
                         verification=Verification(False, error="no signature bundle"))
        self.assertBlocked(plan)
        self.assertEqual((plan["next_command"], plan["before_next_command"]), (None, None))

    def test_a_plan_without_the_new_keys_still_prints(self):
        plan = self.plan(self.standalone(PREVIOUS))
        del plan["before_next_command"], plan["staged_version"]
        text = plan_text(plan)
        self.assertIn("  Installed     v1.51.0\n", text)
        self.assertIn("  Decision      update available: run ./bin/update apply\n", text)


class NewerReleaseTests(PlanTestCase):
    """R6 (E2): only a newer platform release without a manifest makes this manifest 'not the newest release'."""

    ADHOC = "the latest GitHub release has no update manifest; using v1.52.0, the newest release that has one"

    def test_drift_sends_the_operator_to_the_release_when_no_newer_one_waits(self):
        manifest = self.manifest()
        self.write("docker-compose.yml", "services: {v1.51.0: true}\n")
        plan = self.plan(self.standalone(PREVIOUS), manifest=manifest, fallback_note=self.ADHOC)
        self.assertBlocked(plan)
        # Round 6 (U33): this checkout is no git work tree, so no git command is printed.
        self.assertReason(plan, (
            "this checkout's deployment files differ from v1.52.0's (docker-compose.yml). New images must run with "
            f"their own compose files, and git cannot be used in this checkout ({self.root}) to put it back: replace "
            "it with release v1.52.0's version (your env file stays as it is), then check again"))
        self.assertNoReasonContaining(plan, "not the newest release")
        # The fallback note is a note, once.
        self.assertEqual(plan["notes"].count(self.ADHOC), 1)

    def test_an_install_on_the_release_is_simply_up_to_date(self):
        plan = self.plan(self.standalone(TARGET), fallback_note=self.ADHOC)
        self.assertEqual((plan["decision"], plan["reasons"]), (UP_TO_DATE, []))
        self.assertEqual(plan["notes"], [self.ADHOC])

    def test_an_install_newer_than_the_release_is_a_downgrade_when_no_newer_release_exists(self):
        # A backport `latest` (v1.51.3) older than the chosen release: v1.52.0 is the newest release there is.
        plan = self.plan(self.standalone("v1.53.0"), fallback_note=self.ADHOC)
        self.assertBlocked(plan)
        self.assertReason(plan, "standalone runs v1.53.0, newer than this release (v1.52.0); bin/update never "
                                "downgrades")

    def test_a_newer_release_without_a_manifest_keeps_the_wait_rules(self):
        manifest = self.manifest()
        self.write("docker-compose.yml", "services: {v1.53.0: true}\n")
        plan = self.plan(self.standalone(PREVIOUS), manifest=manifest, newer_release="v1.53.0")
        self.assertReasonContaining(plan, "but v1.52.0 is not the newest release: the newest one has no update "
                                          "manifest yet")
        self.assertNoReasonContaining(plan, "git checkout")
        plan = self.plan(self.standalone("v1.53.0"), newer_release="v1.53.0")
        self.assertEqual(plan["decision"], UP_TO_DATE, plan["reasons"])
        self.assertTrue([n for n in plan["notes"] if "nothing to update until" in n], plan["notes"])


class MixedVersionRemedyTests(PlanTestCase):
    """R11: a container newer than the env tag is never restarted onto the older tag."""

    KEEP_OFF = ("Do not restart the stack on v1.51.0: v1.53.0 may have migrated the database, and an older release "
                "does not run on a newer schema. To go back to v1.51.0, restore the backup taken before v1.53.0 "
                "started")

    def test_a_newer_running_container_is_never_restarted_onto_the_older_tag(self):
        plan = self.plan(self.distributed(PREVIOUS, labels={"backend": "v1.53.0"}))
        self.assertBlocked(plan)
        self.assertReason(plan, (
            "backend containers run different versions (v1.51.0 (worker), v1.53.0 (backend)) while FUTURE_AGI_VERSION "
            "in .env is v1.51.0: part of the stack was restarted on another version. Set FUTURE_AGI_VERSION=v1.53.0, "
            "then restart the stack (`docker compose up -d`) so every service runs v1.53.0, and check again. "
            + self.KEEP_OFF))
        self.assertNoReasonContaining(plan, "so every service runs v1.51.0")

    def test_production_names_its_compose_files(self):
        installation = self.distributed(PREVIOUS, mode="production", production=True, labels={"worker": "v1.53.0"})
        reason = self.assertReasonContaining(self.plan(installation), "backend containers run different versions")
        self.assertIn("while FUTURE_AGI_VERSION in deploy/.env.production is v1.51.0", reason)
        # S10/T19: a production stack never migrates on start, so restarting it as it is stays safe unless v1.53.0's
        # initialization jobs ran; the newer tag comes only after them, with the shell rule on the Compose command.
        self.assertIn(f"If the initialization jobs of v1.53.0 have not run, restart the stack as it is (`{PRODUCTION_HINT} "
                      "up -d`. Run it from a shell that does not export any key of deploy/.env.production (Compose "
                      "prefers your shell over the env file).) so every service runs v1.51.0, then check again: a "
                      "production install updates in two steps", reason)
        self.assertIn("If they have run, set FUTURE_AGI_VERSION=v1.53.0, then restart the stack the same way so every "
                      "service runs v1.53.0", reason)
        self.assertNotIn("migrated the database", reason)
        self.assertNotIn("restore the backup", reason)

    def test_the_gpu_variant_is_kept(self):
        installation = FakeInstallation(self.root, services={
            "serving": f"futureagi/serving:{PREVIOUS}-gpu", "serving-batch": f"futureagi/serving:{PREVIOUS}-gpu",
        }, env={"SERVING_VERSION": f"{PREVIOUS}-gpu"})
        installation.run("serving", "v1.53.0").run("serving-batch", PREVIOUS)
        reason = self.assertReasonContaining(self.plan(installation), "serving_gpu containers run different versions")
        self.assertIn("Set SERVING_VERSION=v1.53.0-gpu, then restart the stack", reason)

    def test_a_single_newer_version_is_set_back_not_restarted_onto_the_tag(self):
        plan = self.plan(self.standalone(PREVIOUS, label="v1.53.0"))
        self.assertReason(plan, (
            "service app runs v1.53.0, but FUTURE_AGI_VERSION in .env is v1.51.0: the env file was changed without "
            "restarting the stack. Set FUTURE_AGI_VERSION back to v1.53.0, the version that runs, then check again. "
            + self.KEEP_OFF))
        self.assertNoReasonContaining(plan, "restart the stack so it runs v1.51.0")

    def test_the_restart_remedy_stays_when_the_tag_is_the_newest(self):
        reason = self.assertReasonContaining(self.plan(self.distributed(TARGET, labels={"backend": PREVIOUS})),
                                             "backend containers run different versions")
        self.assertIn("restart the stack (`docker compose up -d`) so every service runs v1.52.0", reason)
        self.assertNotIn("Do not restart", reason)
        reason = self.assertReasonContaining(self.plan(self.standalone(TARGET, label=PREVIOUS)), "service app runs")
        self.assertIn("or restart the stack so it runs v1.52.0", reason)
        self.assertNotIn("Do not restart", reason)


class StrayContainerTests(PlanTestCase):
    """R9, R10 (E1): containers of Future AGI services that the active configuration leaves out."""

    def install(self, version: str = PREVIOUS, **kwargs) -> FakeInstallation:
        """A Distributed install whose 'workers' profile (worker-default, worker-tasks) is not enabled."""

        installation = self.distributed(version, **kwargs)
        installation.profile_services.update({"worker-default": WORKER_RAW, "worker-tasks": WORKER_RAW})
        return installation

    def stray(self, installation: FakeInstallation, service: str, label: str, *, status: str = "running",
              image: Optional[str] = None) -> str:
        installation.run(service, label, status=status, image=image or f"futureagi/future-agi:{label or 'latest'}")
        return installation._containers[-1]["id"]

    def note(self, plan, start: str) -> str:
        found = [note for note in plan["notes"] if note.startswith(start)]
        self.assertEqual(len(found), 1, plan["notes"])
        return found[0]

    def test_classify_strays_tells_inactive_profiles_from_orphans(self):
        from deploy.update.plan import classify_strays

        installation = self.install()
        running = self.stray(installation, "worker-default", PREVIOUS)
        exited = self.stray(installation, "worker-default", PREVIOUS, status="exited")
        again = self.stray(installation, "worker-default", PREVIOUS)
        restarting = self.stray(installation, "worker-tasks", "v1.50.0", status="restarting")
        orphan = self.stray(installation, "property-catalog-supervisor", "v1.41.1")
        installation.run("backend-shell", PREVIOUS, image=f"futureagi/future-agi:{PREVIOUS}", oneoff=True)
        installation.run("rabbitmq", "3.13", image="rabbitmq:3.13-management")
        strays = classify_strays(installation, installation.containers(), installation.config()["services"])
        image = f"futureagi/future-agi:{PREVIOUS}"
        self.assertEqual(strays, {
            "inactive": [
                {"service": "worker-default", "ids": [running, again], "running": True, "state": "running",
                 "image": image, "version": PREVIOUS},
                {"service": "worker-default", "ids": [exited], "running": False, "state": "stopped", "image": image,
                 "version": PREVIOUS},
                {"service": "worker-tasks", "ids": [restarting], "running": True, "state": "running",
                 "image": "futureagi/future-agi:v1.50.0", "version": "v1.50.0"},
            ],
            "orphans": [
                {"service": "property-catalog-supervisor", "ids": [orphan], "running": True, "state": "running",
                 "image": "futureagi/future-agi:v1.41.1", "version": "v1.41.1"},
            ],
        })

    def test_nothing_to_classify_reads_no_raw_model(self):
        from deploy.update.plan import classify_strays

        installation = self.install()
        installation.run("backend-shell", PREVIOUS, image=f"futureagi/future-agi:{PREVIOUS}", oneoff=True)
        strays = classify_strays(installation, installation.containers(), installation.config()["services"])
        # F1: the service model (every profile's services) is asked only when there is a container to classify.
        self.assertEqual((strays, installation.model_calls), ({"inactive": [], "orphans": []}, 0))

    def test_containers_sharing_an_image_inspect_its_version_label_once(self):
        from deploy.update.plan import classify_strays

        installation = self.install()
        image_id = "sha256:shared-stray-image"
        image = f"futureagi/future-agi:{PREVIOUS}"
        first = installation.run("worker-default", PREVIOUS, image_id=image_id, image=image)
        first_id = first._containers[-1]["id"]
        second_id = first.run("worker-default", PREVIOUS, image_id=image_id, image=image)._containers[-1]["id"]

        entries = classify_strays(installation, installation.containers(), installation.config()["services"])[
            "inactive"]

        self.assertEqual(entries, [{"service": "worker-default", "ids": [first_id, second_id], "running": True,
                                    "state": "running", "image": f"futureagi/future-agi:{PREVIOUS}",
                                    "version": PREVIOUS}])
        self.assertEqual(installation.image_calls.count(image_id), 1)

    def test_stray_target_check_accepts_the_gpu_tag_of_the_target_release(self):
        from deploy.update.plan import _stray_on_target

        self.assertTrue(_stray_on_target({"image": f"futureagi/serving:{TARGET}-gpu", "version": ""},
                                         "serving_gpu", TARGET))
        self.assertFalse(_stray_on_target({"image": f"futureagi/serving:{PREVIOUS}-gpu", "version": ""},
                                          "serving_gpu", TARGET))

    def test_an_orphan_never_blocks_and_is_named_once_with_its_removal(self):
        installation = self.install()
        supervisor = self.stray(installation, "property-catalog-supervisor", "v1.41.1")
        sequencer = self.stray(installation, "fi-property-catalog-sequencer", "v1.41.1", status="exited")
        plan = self.plan(installation)
        self.assertEqual((plan["decision"], plan["reasons"]), (UPDATE_AVAILABLE, []))
        self.assertEqual(self.note(plan, "services fi-property-catalog-sequencer"), (
            "services fi-property-catalog-sequencer (stopped, v1.41.1), property-catalog-supervisor (running, v1.41.1) "
            "are not defined by this release's compose files any more; bin/update leaves their containers as they "
            f"are. Once their retirement is approved, remove them with `docker rm -f {sequencer} {supervisor}`"))
        self.assertNotIn("rm -sf", "\n".join(plan["notes"]))

    def test_a_production_orphan_is_removed_by_container_id(self):
        installation = self.install(mode="production", production=True)
        supervisor = self.stray(installation, "property-catalog-supervisor", "v1.41.1")
        plan = self.plan(installation)
        self.assertEqual((plan["decision"], plan["reasons"]), (UPDATE_AVAILABLE, []))
        self.assertIn(
            "service property-catalog-supervisor (running, v1.41.1) is not defined by this release's compose files "
            "any more; bin/update leaves its containers as they are. Once its retirement is approved, remove them "
            f"with `docker rm -f {supervisor}`", plan["notes"])

    def test_an_up_to_date_install_is_never_blocked_by_strays(self):
        for status, label in (("exited", PREVIOUS), ("exited", TARGET), ("running", PREVIOUS), ("running", TARGET)):
            with self.subTest(status=status, label=label):
                installation = self.install(TARGET)
                self.stray(installation, "worker-default", label, status=status)
                plan = self.plan(installation)
                self.assertEqual((plan["decision"], plan["reasons"], plan["env_changes"]), (UP_TO_DATE, [], {}))
                state = "running" if status == "running" else "stopped"
                self.assertIn(
                    "bin/update leaves these containers of services outside the active Compose profiles as they are: "
                    f"worker-default ({state}, {label}). Enabling their profile (COMPOSE_PROFILES in .env) lets "
                    "Compose recreate them on the version .env selects; to remove them, run `docker compose rm -sf "
                    "worker-default`", plan["notes"])

    def test_a_stopped_stray_or_one_on_the_release_is_a_note_during_an_update(self):
        for status, label in (("exited", PREVIOUS), ("created", "v1.50.0"), ("running", TARGET),
                              ("restarting", TARGET)):
            with self.subTest(status=status, label=label):
                installation = self.install(PREVIOUS)
                self.stray(installation, "worker-default", label, status=status)
                plan = self.plan(installation)
                self.assertEqual((plan["decision"], plan["reasons"]), (UPDATE_AVAILABLE, []))
                state = "running" if status in ("running", "restarting") else "stopped"
                self.assertIn(f": worker-default ({state}, {label}).",
                              self.note(plan, "bin/update leaves these containers"))

    def test_a_running_stray_on_the_old_release_blocks_when_its_key_moves(self):
        for status in ("running", "restarting"):
            with self.subTest(status=status):
                installation = self.install(PREVIOUS)
                self.stray(installation, "worker-default", PREVIOUS, status=status)
                self.stray(installation, "worker-tasks", PREVIOUS, status="exited")
                plan = self.plan(installation)
                self.assertBlocked(plan)
                self.assertReason(plan, (
                    "service worker-default has containers on Future AGI images but is not in the active Compose "
                    "configuration (a profile that is not enabled now): worker-default (running, v1.51.0). The update "
                    "to v1.52.0 does not replace its containers, so they would keep running as they are next to it. "
                    "Enable the profile (COMPOSE_PROFILES in .env), or remove it with `docker compose rm -sf "
                    "worker-default`, then check again"))
                # The stopped one changes nothing: it is a note, and the blocking service is not repeated there.
                note = self.note(plan, "bin/update leaves these containers")
                self.assertIn(": worker-tasks (stopped, v1.51.0).", note)
                self.assertNotIn("worker-default", note)

    def test_a_stray_whose_key_the_update_keeps_is_a_note(self):
        installation = self.install(PREVIOUS, tags={"FRONTEND_VERSION": TARGET})
        installation.profile_services["frontend-canary"] = "futureagi/frontend:${FRONTEND_VERSION:-latest}"
        self.stray(installation, "frontend-canary", "v1.50.0", image="futureagi/frontend:v1.50.0")
        plan = self.plan(installation)
        self.assertEqual((plan["decision"], plan["reasons"]), (UPDATE_AVAILABLE, []))
        self.assertNotIn("FRONTEND_VERSION", plan["env_changes"])
        self.assertIn(": frontend-canary (running, v1.50.0).", self.note(plan, "bin/update leaves these containers"))

    def test_an_unlabelled_stray_is_judged_by_its_tag(self):
        # S40: its tag counts only for a component with no active service (apply judges it so after the restart);
        # backend is active here, so an unlabelled image created from the release's tag is not the release's image.
        installation = self.install(PREVIOUS)
        self.stray(installation, "worker-default", "", image=f"futureagi/future-agi:{TARGET}")
        plan = self.plan(installation)
        self.assertBlocked(plan)
        self.assertIn(": worker-default (running, no version label).",
                      self.assertReasonContaining(plan, "service worker-default has containers on Future AGI"))
        installation = self.install(PREVIOUS)
        self.stray(installation, "worker-default", "", image=f"futureagi/future-agi:{PREVIOUS}")
        self.assertReasonContaining(self.plan(installation), "service worker-default has containers on Future AGI")

    def test_the_note_words_each_containers_state(self):
        installation = self.install(TARGET)
        self.stray(installation, "worker-default", PREVIOUS)
        self.stray(installation, "worker-default", "v1.50.0", status="exited")
        self.stray(installation, "worker-tasks", "", status="created")
        note = self.note(self.plan(installation), "bin/update leaves these containers")
        self.assertIn(": worker-default (running, v1.51.0; stopped, v1.50.0), worker-tasks (stopped, no version "
                      "label).", note)
        self.assertTrue(note.endswith("`docker compose rm -sf worker-default worker-tasks`"), note)

    def test_a_state_two_images_share_is_worded_once(self):
        # Created from :latest and from the version tag: two entries, one state.
        installation = self.install(TARGET)
        self.stray(installation, "worker-default", PREVIOUS)
        self.stray(installation, "worker-default", PREVIOUS, image="futureagi/future-agi:latest")
        note = self.note(self.plan(installation), "bin/update leaves these containers")
        self.assertIn(": worker-default (running, v1.51.0). Enabling", note)

    def test_finishing_an_update_counts_the_keys_it_moved(self):
        # Staged: the staging run moved FUTURE_AGI_VERSION; the stray would stay on the old release.
        installation = self.install(TARGET, labels=ALL_PREVIOUS)
        self.stray(installation, "worker-default", PREVIOUS)
        staged = receipt("1", status=STAGED, previous_values={"FUTURE_AGI_VERSION": PREVIOUS})
        self.assertReasonContaining(self.plan(installation, history=[staged]), "service worker-default has containers")
        # A stopped rollout leaves every component 'keep'; the keys it moved still count, recorded or not.
        installation = self.install(TARGET, labels={"worker": PREVIOUS})
        self.stray(installation, "worker-default", PREVIOUS)
        for record in (receipt("1", status="failed_during_rollout", previous_values={"FUTURE_AGI_VERSION": PREVIOUS}),
                       receipt("1", status="failed_during_rollout")):
            with self.subTest(record=record):
                plan = self.plan(installation, history=[record])
                self.assertReasonContaining(plan, "service worker-default has containers")

    def test_a_stack_mismatch_leaves_no_stray_notes(self):
        stack = ["project 'futureagi' runs Distributed services (backend) but .env selects the Standalone stack."]
        installation = self.standalone(PREVIOUS, stack_reasons=stack)
        installation.run("backend", PREVIOUS, image=f"futureagi/future-agi:{PREVIOUS}")
        plan = self.plan(installation)
        self.assertEqual(plan["reasons"], stack)
        self.assertFalse([n for n in plan["notes"] if "backend" in n], plan["notes"])


class InactivePinRawImageTests(PlanTestCase):
    """R15: the service model (Installation.service_model) lists every profile's services; only the active ones
    decide which version keys are an inactive profile's pins."""

    def test_a_key_only_an_inactive_profiles_service_uses_stays_an_inactive_pin(self):
        manifest = self.manifest()
        installation = self.distributed(PREVIOUS, mode="production", production=True)
        installation._config["services"].pop("simulation-runner")
        installation._containers = [c for c in installation._containers if c["service"] != "simulation-runner"]
        installation.profile_services["worker-simulation-runner"] = (
            "futureagi/future-agi-simulation-runner:${SIMULATION_RUNNER_VERSION:-latest}")
        self.assertIn("worker-simulation-runner", installation.service_model()["services"])
        self.assertNotIn("worker-simulation-runner", installation.config()["services"])
        plan = self.plan(installation, manifest=manifest)
        self.assertEqual((plan["decision"], plan["reasons"]), (UPDATE_AVAILABLE, []))
        self.assertEqual(plan["env_changes"].get("SIMULATION_RUNNER_VERSION"), TARGET)
        item = self.component(plan, "simulation_runner")
        self.assertEqual((item["inactive"], item["action"], item["configured"], item["services"]),
                         (True, "update", PREVIOUS, []))
        self.assertIn("SIMULATION_RUNNER_VERSION pins a service outside the active Compose profiles; it is set to "
                      "v1.52.0 without pulling it", plan["notes"])



# --------------------------------------------------------------------------
# Round 4
# Round 6 (H3): the one sentence that follows every production Compose command printed to run by hand.
SHELL_RULE = "Run it from a shell that does not export any key of {env} (Compose prefers your shell over the env file)."
EVERY_KEY = {key: TARGET for key in VERSION_VARIABLES}


class ServiceModelTests(PlanTestCase):
    """F1 (S14, S37, S38, S39): which key selects each image comes from Installation.service_model(), never from
    `config --no-interpolate` (it rejects this repository's compose files on every Compose before v2.40.3)."""

    def test_a_future_agi_image_whose_tag_no_key_selects_is_blocked(self):
        installation = self.distributed(PREVIOUS)
        installation.raw_images["frontend"] = f"futureagi/frontend:{PREVIOUS}"
        self.assertIsNone(installation.service_model()["services"]["frontend"]["variable"])
        installation.model_calls = 0
        plan = self.plan(installation)
        self.assertGreaterEqual(installation.model_calls, 1, "the plan reads the service model")
        self.assertBlocked(plan)
        self.assertReason(plan, (
            f"service frontend's image is set directly in a compose file (futureagi/frontend:{PREVIOUS}), not through "
            "FRONTEND_VERSION: bin/update can only change FRONTEND_VERSION in .env. Make frontend use it (image: "
            "futureagi/frontend:${FRONTEND_VERSION}) or remove that image setting (a docker-compose.override.yml, "
            "say), then check again"))

    def test_a_variant_after_the_key_still_follows_it(self):
        installation = FakeInstallation(self.root, services={"serving": f"futureagi/serving:{PREVIOUS}-gpu"},
                                        env={"SERVING_VERSION": PREVIOUS}).run("serving", PREVIOUS)
        installation.raw_images["serving"] = "futureagi/serving:${SERVING_VERSION}-gpu"
        self.assertEqual(installation.service_model()["services"]["serving"]["variable"], "SERVING_VERSION")
        installation.model_calls = 0
        self.assertNoReasonContaining(self.plan(installation), "set directly in a compose file")
        self.assertGreaterEqual(installation.model_calls, 1, "the plan reads the service model")

    def test_a_key_another_component_owns_is_named(self):
        installation = self.distributed(PREVIOUS)
        installation.raw_images["frontend"] = "futureagi/frontend:${FUTURE_AGI_VERSION}"
        reason = self.assertReasonContaining(self.plan(installation), "service frontend's image")
        self.assertIn("follows FUTURE_AGI_VERSION, not FRONTEND_VERSION", reason)
        self.assertNotIn("set directly", reason)

    def test_an_unknown_stray_counts_as_inactive_when_the_model_is_incomplete(self):
        # Compose could not resolve every profile: a service the model does not list may still be a profile's.
        installation = self.distributed(PREVIOUS)
        installation.run("worker-default", PREVIOUS, image=f"futureagi/future-agi:{PREVIOUS}")
        complete = self.plan(installation)
        self.assertEqual(complete["decision"], UPDATE_AVAILABLE, complete["reasons"])
        self.assertTrue([n for n in complete["notes"] if n.startswith("service worker-default (running, v1.51.0) is "
                                                                       "not defined by this release")], complete["notes"])
        installation.model_complete = False
        plan = self.plan(installation)
        self.assertBlocked(plan)
        self.assertReasonContaining(plan, "service worker-default has containers on Future AGI images but is not in "
                                          "the active Compose configuration")

    def test_inactive_pins_come_from_the_model(self):
        installation = self.distributed(PREVIOUS, mode="production", production=True)
        installation._config["services"].pop("simulation-runner")
        installation._containers = [c for c in installation._containers if c["service"] != "simulation-runner"]
        installation.profile_services["simulation-runner"] = (
            "futureagi/future-agi-simulation-runner:${SIMULATION_RUNNER_VERSION:?must be set for production}")
        model = installation.service_model()["services"]["simulation-runner"]
        self.assertEqual((model["variable"], model["active"]), ("SIMULATION_RUNNER_VERSION", False))
        installation.model_calls = 0
        item = self.component(self.plan(installation), "simulation_runner")
        self.assertEqual((item["inactive"], item["action"]), (True, "update"))
        self.assertGreaterEqual(installation.model_calls, 1, "the plan reads the service model")
        # A key that an active service's image follows is no pin of its own, whatever that service is.
        installation._config["services"]["sim-proxy"] = {"image": f"example.com/proxy:{PREVIOUS}"}
        installation.raw_images["sim-proxy"] = "example.com/proxy:${SIMULATION_RUNNER_VERSION}"
        plan = self.plan(installation)
        self.assertFalse([c for c in plan["components"] if c["component"] == "simulation_runner"], plan["components"])
        self.assertReasonContaining(plan, "service sim-proxy takes its image from SIMULATION_RUNNER_VERSION")

    def test_a_key_no_service_follows_is_no_pin(self):
        # docker-compose.yml has no frontend: a FRONTEND_VERSION left in a Standalone .env selects nothing.
        plan = self.plan(self.standalone(PREVIOUS, extra_env={"FRONTEND_VERSION": PREVIOUS}))
        self.assertEqual(plan["decision"], UPDATE_AVAILABLE, plan["reasons"])
        self.assertFalse([c for c in plan["components"] if c["variable"] == "FRONTEND_VERSION"], plan["components"])
        self.assertEqual(plan["env_changes"], {"FUTURE_AGI_VERSION": TARGET})
        self.assertFalse([n for n in plan["notes"] if "FRONTEND_VERSION pins" in n], plan["notes"])
        # Without every profile resolved, a key no active service uses may be a profile's: it still moves.
        installation = self.standalone(PREVIOUS, extra_env={"FRONTEND_VERSION": PREVIOUS})
        installation.model_complete = False
        self.assertEqual(self.plan(installation)["env_changes"].get("FRONTEND_VERSION"), TARGET)

    def test_classify_strays_asks_the_service_model(self):
        from deploy.update.plan import classify_strays

        installation = self.distributed(PREVIOUS)
        installation.profile_services["worker-default"] = WORKER_RAW
        installation.run("worker-default", PREVIOUS, image=f"futureagi/future-agi:{PREVIOUS}")
        installation.run("gone", PREVIOUS, image=f"futureagi/future-agi:{PREVIOUS}")
        strays = classify_strays(installation, installation.containers(), installation.config()["services"])
        self.assertEqual(([e["service"] for e in strays["inactive"]], [e["service"] for e in strays["orphans"]]),
                         (["worker-default"], ["gone"]))
        self.assertEqual(installation.model_calls, 1)
        installation.model_complete = False
        strays = classify_strays(installation, installation.containers(), installation.config()["services"])
        # Without every profile resolved, neither can be told from a profile's service: both count as inactive.
        self.assertEqual(([e["service"] for e in strays["inactive"]], strays["orphans"]),
                         (["gone", "worker-default"], []))


class RealServiceModelTests(PlanTestCase):
    """F1 with a real Installation: a Compose whose `config --no-interpolate` fails still plans."""

    def installation(self, docker: FakeDocker, *, env: str, production: bool = False) -> Installation:
        return RealInstallationTests.installation(self, docker, env=env, production=production)

    def docker(self) -> FakeDocker:
        docker = FakeDocker({"name": "futureagi", "services": {
            "backend": {"image": f"futureagi/future-agi:{PREVIOUS}"},
            "frontend": {"image": f"futureagi/frontend:{PREVIOUS}"}}})
        docker.container("backend", f"futureagi/future-agi:{PREVIOUS}", PREVIOUS)
        docker.container("frontend", f"futureagi/frontend:{PREVIOUS}", PREVIOUS)
        docker.profiles = ["workers"]
        docker.profile_services["worker-default"] = ("workers", WORKER_RAW)
        return docker

    def env(self) -> str:
        return (f"COMPOSE_FILE=docker-compose.distributed.yml\nFUTURE_AGI_VERSION={PREVIOUS}\n"
                f"FRONTEND_VERSION={PREVIOUS}\nSECRET_KEY=abc\n")

    def test_the_plan_never_runs_config_no_interpolate(self):
        docker = self.docker()
        plan = self.plan(self.installation(docker, env=self.env()))
        self.assertEqual(plan["decision"], UPDATE_AVAILABLE, plan["reasons"])
        self.assertFalse([call for call in docker.calls if "--no-interpolate" in call], docker.calls)

    def test_a_stray_of_a_profile_the_probe_resolves_is_inactive_and_any_other_an_orphan(self):
        docker = self.docker()
        docker.container("worker-default", f"futureagi/future-agi:{PREVIOUS}", PREVIOUS)
        docker.container("retired", f"futureagi/future-agi:{PREVIOUS}", PREVIOUS)
        plan = self.plan(self.installation(docker, env=self.env()))
        self.assertBlocked(plan)
        self.assertReasonContaining(plan, "service worker-default has containers on Future AGI images")
        self.assertNoReasonContaining(plan, "retired")
        self.assertTrue([n for n in plan["notes"] if n.startswith("service retired (running, v1.51.0) is not defined")],
                        plan["notes"])
        self.assertTrue([call for call in docker.calls if call[-2:] == ["config", "--profiles"]], docker.calls)


class CommandAnchorTests(PlanTestCase):
    """F2 (S4, S8, S15): printed commands reproduce the run that printed them, wherever it ran."""

    OPTIONS = ["--manifest", "/srv/my releases/v1.52.0.json", "--bundle", "/srv/b.json", "--policy", "major",
               "--channel", "stable"]
    QUOTED = "--manifest '/srv/my releases/v1.52.0.json' --bundle /srv/b.json --policy major --channel stable"

    def elsewhere(self, installation: FakeInstallation) -> FakeInstallation:
        installation.caller_cwd = self.root.parent  # type: ignore[attr-defined]
        return installation

    def cd(self) -> str:
        return f"cd {shlex.quote(str(self.root))} && "

    def test_install_command(self):
        from deploy.update.plan import install_command

        installation = self.distributed(PREVIOUS, mode="production", production=True)
        installation.env_file = self.root / "deploy/prod eu.env"
        self.assertEqual(install_command(installation, "apply", ["--manifest", "/a b.json"]),
                         "./bin/update --production --env-file 'deploy/prod eu.env' apply --manifest '/a b.json'")
        installation.caller_cwd = self.root  # type: ignore[attr-defined]
        self.assertEqual(install_command(installation, "check"),
                         "./bin/update --production --env-file 'deploy/prod eu.env' check")
        self.elsewhere(installation)
        self.assertEqual(install_command(installation, "check"),
                         f"{self.cd()}./bin/update --production --env-file 'deploy/prod eu.env' check")
        self.assertEqual(shlex.split(install_command(installation, "check"))[:4],
                         ["cd", str(self.root), "&&", "./bin/update"])

    def test_a_run_from_another_directory_prints_commands_that_cd_to_the_checkout(self):
        plan = self.plan(self.elsewhere(self.standalone(PREVIOUS)))
        self.assertEqual(plan["next_command"], f"{self.cd()}./bin/update apply")
        self.assertIn(f"update available: run {self.cd()}./bin/update apply\n", plan_text(plan))
        record = receipt("1", status="failed_during_rollout", target_version="v1.51.5")
        reason = self.assertReasonContaining(self.plan(self.elsewhere(self.standalone(PREVIOUS)), last_receipt=record),
                                             "roll it back")
        self.assertIn(f"or roll it back ({self.cd()}./bin/update rollback --receipt {record['_path']} --yes) first",
                      reason)
        # Run from the checkout itself, the commands need no cd.
        installation = self.standalone(PREVIOUS)
        installation.caller_cwd = self.root  # type: ignore[attr-defined]
        self.assertEqual(self.plan(installation)["next_command"], "./bin/update apply")

    def test_compose_commands_cd_but_the_compose_program_is_named_as_it_is(self):
        installation = self.elsewhere(self.distributed(PREVIOUS, mode="production", production=True))
        plan = self.plan(installation)
        self.assertIn(f"`{self.cd()}{PRODUCTION_HINT} run --rm ...`",
                      [n for n in plan["notes"] if "initialization jobs" in n][0])
        installation._config["services"]["backend"]["pull_policy"] = "always"
        installation.supports_up_pull_never = lambda: False  # type: ignore[method-assign]
        reason = self.assertReasonContaining(self.plan(installation), "pull_policy: always")
        self.assertIn(f"this Docker Compose (`{PRODUCTION_HINT}`) has no `up --pull never`", reason)
        self.assertNotIn("cd ", reason)

    def test_install_merge_hint_cds_too(self):
        self.write(".env.example", ENV_EXAMPLE + "NEW_SETTING=on\n")
        reason = self.assertReasonContaining(self.plan(self.elsewhere(self.standalone(PREVIOUS))), "lacks settings")
        self.assertIn(f"Run `{self.cd()}./bin/install --no-up -y` to add them", reason)

    def test_apply_commands_repeat_the_release_options(self):
        plan = self.plan(self.standalone(PREVIOUS), command_options=self.OPTIONS)
        self.assertEqual(plan["command_options"], self.OPTIONS)
        self.assertEqual(plan["next_command"], f"./bin/update apply {self.QUOTED}")
        self.assertEqual(shlex.split(plan["next_command"])[2:], self.OPTIONS)
        self.assertIn(f"update available: run ./bin/update apply {self.QUOTED}\n", plan_text(plan))
        self.assertEqual(self.plan(self.standalone(PREVIOUS))["command_options"], [])

    def test_production_two_steps_repeat_them(self):
        plan = self.plan(self.distributed(PREVIOUS, mode="production", production=True), command_options=self.OPTIONS)
        self.assertEqual(plan["next_command"], f"./bin/update --production apply --stage {self.QUOTED}")
        note = [n for n in plan["notes"] if n.startswith("production installs update in two steps")][0]
        self.assertIn(f"./bin/update --production apply --stage {self.QUOTED} (backs up", note)
        self.assertTrue(note.endswith(f"then ./bin/update --production apply {self.QUOTED}"), note)
        self.assertIn("(the first of two steps, see below)", plan_text(plan))

    def test_finishing_a_staged_update_repeats_them(self):
        plan = self.plan(self.standalone(TARGET, label=PREVIOUS), history=[receipt("1", status=STAGED)],
                         command_options=["--channel", "stable"])
        self.assertEqual(plan["next_command"], "./bin/update apply --channel stable")
        self.assertIn("nothing was restarted); ./bin/update apply --channel stable finishes it", plan["notes"][0])

    def test_rollback_commands_never_carry_them(self):
        for record in (receipt("1", status="failed_during_rollout", target_version="v1.51.5"),
                       receipt("1", kind="rollback", status="failed_during_rollout", rolls_back="/r.json"),
                       receipt("1", status=STAGED, target_version="v1.51.5")):
            with self.subTest(record=record):
                plan = self.plan(self.standalone("v1.51.5", label=PREVIOUS), last_receipt=record,
                                 command_options=self.OPTIONS)
                self.assertBlocked(plan)
                text = "\n".join(plan["reasons"])
                for rollback in re.findall(r"\./bin/update rollback[^)]*", text):
                    self.assertNotIn("--manifest", rollback)
                    self.assertNotIn("--policy", rollback)
                    self.assertNotIn("--channel", rollback)

    def test_a_command_for_another_release_keeps_only_its_own_manifest(self):
        record = receipt("1", status="failed_during_rollout", target_version="v1.51.5",
                         manifest_source="/srv/v1.51.5.json")
        reason = self.assertReasonContaining(
            self.plan(self.standalone("v1.51.5", label=PREVIOUS), last_receipt=record, command_options=self.OPTIONS),
            "Finish it")
        # T16: this run's --policy and --channel belong to this run's manifest, not to that release's.
        self.assertIn("then ./bin/update apply --manifest /srv/v1.51.5.json) or roll it back", reason)
        self.assertNotIn("/srv/b.json", reason)
        self.assertNotIn("my releases", reason)
        self.assertNotIn("--policy", reason)
        self.assertNotIn("--channel", reason)


class AbandonedUpdateTests(PlanTestCase):
    """F5 (S7, S9): an unfinished apply counts only while the env file still selects its release."""

    def production(self, version: str = PREVIOUS, **kwargs) -> FakeInstallation:
        return self.distributed(version, mode="production", production=True, **kwargs)

    def staged(self, **fields) -> Dict[str, Any]:
        return receipt("1", status=STAGED, env_changes=dict(EVERY_KEY), manifest_source=STAGED_SOURCE, **fields)

    def test_unfinished_with_the_env_file(self):
        record = self.staged()
        self.assertEqual(unfinished([record]), record)
        self.assertEqual(unfinished([record], EVERY_KEY), record)
        self.assertIsNone(unfinished([record], ALL_PREVIOUS))
        self.assertIsNone(unfinished([record], dict(EVERY_KEY, FRONTEND_VERSION=PREVIOUS)))
        self.assertIsNone(unfinished([record], {key: TARGET for key in VERSION_VARIABLES if key != "SERVING_VERSION"}))
        # Rollbacks, and records that name no key, are judged as before.
        rollback = receipt("2", kind="rollback", status="failed_during_rollout", env_changes={"X": "v1"})
        self.assertEqual(unfinished([rollback], {}), rollback)
        self.assertEqual(unfinished([receipt("3", status=STAGED)], {}), receipt("3", status=STAGED))

    def test_a_resume_that_found_the_keys_set_is_judged_by_its_components(self):
        resume = receipt("2", status="failed_during_rollout", env_changes={},
                         previous_values={"FUTURE_AGI_VERSION": PREVIOUS},
                         components=[{"component": "backend", "variable": "FUTURE_AGI_VERSION", "target_tag": TARGET}])
        self.assertEqual(unfinished([resume], {"FUTURE_AGI_VERSION": TARGET}), resume)
        self.assertIsNone(unfinished([resume], {"FUTURE_AGI_VERSION": PREVIOUS}))

    def test_a_staged_update_whose_pins_were_put_back_is_a_fresh_update(self):
        plan = self.plan(self.production(), history=[self.staged()])
        self.assertEqual((plan["decision"], plan["resume"], plan["staged_version"]), (UPDATE_AVAILABLE, False, None))
        self.assertEqual(plan["next_command"], "./bin/update --production apply --stage")
        self.assertEqual(plan["env_changes"], EVERY_KEY)
        self.assertTrue([n for n in plan["notes"] if n.startswith("production installs update in two steps")])
        abandoned = [n for n in plan["notes"] if "abandoned" in n]
        self.assertEqual(abandoned, [
            f"the update to v1.52.0 staged earlier (receipt {STATE}/receipt-20261019T000001Z-1.json) is abandoned: "
            f"{_join_keys(EVERY_KEY)} in deploy/.env.production no longer select it, so this plan does not finish it "
            "and plans the update afresh"])
        text = plan_text(plan)
        self.assertNotIn("staged update", text)
        self.assertNotIn("nothing was restarted", text)
        self.assertIn("  Installed     v1.51.0\n", text)

    def test_a_stopped_update_whose_pins_were_put_back_is_a_fresh_update(self):
        record = receipt("1", status="failed_during_rollout", env_changes=dict(EVERY_KEY))
        plan = self.plan(self.production(), history=[record])
        self.assertEqual((plan["decision"], plan["resume"]), (UPDATE_AVAILABLE, False))
        self.assertEqual(plan["next_command"], "./bin/update --production apply --stage")
        self.assertFalse([n for n in plan["notes"] if "already selects" in n or "apply finishes it" in n], plan["notes"])
        self.assertEqual(len([n for n in plan["notes"] if "that stopped while containers were being replaced" in n
                              and "is abandoned" in n and record["_path"] in n]), 1, plan["notes"])

    def test_one_key_put_back_is_enough(self):
        installation = self.production(TARGET, tags={"FRONTEND_VERSION": PREVIOUS}, labels=ALL_PREVIOUS)
        plan = self.plan(installation, history=[self.staged()])
        self.assertIsNone(plan["staged_version"])
        self.assertFalse(plan["resume"])
        self.assertIn("is abandoned: FRONTEND_VERSION in deploy/.env.production no longer selects it",
                      [n for n in plan["notes"] if "abandoned" in n][0])
        # The containers still on v1.51.0 while the rest of the env file selects v1.52.0 are a hand edit now.
        self.assertReasonContaining(plan, "the env file was changed without restarting the stack")

    def test_a_staged_update_the_stack_already_runs_is_not_pending(self):
        installation = self.production(TARGET)
        installation._config["services"]["postgres-schema-bootstrap"] = {"image": f"futureagi/future-agi:{TARGET}"}
        installation.run("postgres-schema-bootstrap", TARGET, status="exited")
        plan = self.plan(installation, history=[self.staged()])
        self.assertEqual((plan["decision"], plan["resume"], plan["staged_version"]), (UP_TO_DATE, False, None))
        self.assertEqual(plan["notes"], [
            f"the update to v1.52.0 staged earlier (receipt {STATE}/receipt-20261019T000001Z-1.json) already runs: "
            "the stack was restarted on it outside bin/update, so there is nothing of it to finish"])
        text = plan_text(plan)
        self.assertNotIn("nothing was restarted", text)
        self.assertNotIn("pending", text)
        self.assertIn("  Installed     v1.52.0\n", text)
        # A stack only partly on the staged release, or stopped, still has it to finish.
        for labels, status in (({"worker": PREVIOUS}, "running"), ({}, "exited")):
            with self.subTest(labels=labels, status=status):
                installation = self.production(TARGET, labels=labels)
                for container in installation._containers:
                    container["status"] = status if status != "running" else container["status"]
                plan = self.plan(installation, history=[self.staged()])
                self.assertEqual(plan["staged_version"], TARGET)
                self.assertTrue(plan["resume"])

    def test_a_newer_release_then_updates_without_offering_the_rollback(self):
        plan = self.plan(self.production(TARGET), history=[self.staged()], manifest=self.manifest(version="v1.53.0"))
        self.assertEqual((plan["decision"], plan["reasons"]), (UPDATE_AVAILABLE, []))
        self.assertEqual(plan["next_command"], "./bin/update --production apply --stage")
        self.assertNotIn("rollback", "\n".join(plan["notes"]))
        self.assertNotIn("is staged", "\n".join(plan["notes"]))



def _join_keys(values: Mapping[str, str]) -> str:
    return ", ".join(sorted(values))


class ApplyingDecisionTests(PlanTestCase):
    """F6 (S30): the plan apply prints while it carries it out says what it does, never which command to run."""

    def test_finishing_a_staged_production_update(self):
        installation = self.distributed(TARGET, mode="production", production=True, labels=ALL_PREVIOUS)
        plan = self.plan(installation, history=[receipt("1", status=STAGED)])
        self.assertTrue(plan["before_next_command"])
        text = plan_text(plan, applying=True)
        self.assertIn("  Decision      update available: finishing the staged update to v1.52.0\n", text)
        decision = [line for line in text.splitlines() if line.startswith("  Decision")][0]
        self.assertNotIn("run ", decision)
        self.assertNotIn("initialization jobs", decision)
        self.assertIn("then run ./bin/update --production apply (finishes the staged update)", plan_text(plan))

    def test_a_fresh_update_and_a_stopped_one(self):
        self.assertIn("  Decision      update available: applying v1.52.0\n",
                      plan_text(self.plan(self.standalone(PREVIOUS)), applying=True))
        self.assertIn("  Decision      update available: applying v1.52.0\n",
                      plan_text(self.plan(self.distributed(PREVIOUS, mode="production", production=True)),
                                applying=True))
        stopped = self.plan(self.standalone(TARGET, label=PREVIOUS), history=[receipt("1", status="failed_during_rollout")])
        self.assertIn("  Decision      update available: finishing the stopped update to v1.52.0\n",
                      plan_text(stopped, applying=True))

    def test_other_decisions_read_the_same(self):
        for plan in (self.plan(self.standalone(TARGET)), self.plan(self.standalone("local"))):
            with self.subTest(decision=plan["decision"]):
                self.assertEqual(plan_text(plan, applying=True), plan_text(plan))


class InstalledLineTests(PlanTestCase):
    """S3, S28, S29: the Installed line and every message about current versions say what runs."""

    def test_a_stopped_rollout_reports_what_runs(self):
        installation = self.distributed(TARGET, mode="production", production=True,
                                        labels={"backend": PREVIOUS, "worker": PREVIOUS})
        plan = self.plan(installation, history=[receipt("1", status="failed_during_rollout")])
        self.assertEqual((plan["stopped_version"], plan["stopped_kind"], plan["staged_version"]),
                         (TARGET, "apply", None))
        # What runs is what the finishing run records as from_versions (FUTURE_AGI_UPDATE_FROM_VERSION).
        self.assertEqual(plan["current_versions"], [PREVIOUS, TARGET])
        self.assertIn("  Installed     v1.51.0, v1.52.0 running, update to v1.52.0 stopped part-way\n", plan_text(plan))
        backend = self.component(plan, "backend")
        self.assertEqual((backend["current_version"], backend["action"]), (PREVIOUS, "update"))
        self.assertEqual(backend["current_source"],
                         "running image (FUTURE_AGI_VERSION in deploy/.env.production selects v1.52.0, the update "
                         "that stopped part-way)")
        self.assertEqual(self.component(plan, "frontend")["action"], "keep")
        self.assertNotIn("FUTURE_AGI_VERSION", plan["env_changes"])

    def test_a_stopped_rollback_reports_what_runs(self):
        record = receipt("1", kind="rollback", status="failed_during_rollout", target_version=PREVIOUS,
                         rolls_back=f"{STATE}/receipt-a.json")
        plan = self.plan(self.standalone(PREVIOUS, label=TARGET), history=[record])
        self.assertEqual((plan["stopped_version"], plan["stopped_kind"], plan["current_versions"]),
                         (PREVIOUS, "rollback", [TARGET]))
        self.assertIn("  Installed     v1.52.0 running, rollback to v1.51.0 stopped part-way\n", plan_text(plan))

    def test_only_running_containers_count_as_running(self):
        installation = self.distributed(TARGET, labels=ALL_PREVIOUS)
        for container in installation._containers:
            container["status"] = "exited"
        plan = self.plan(installation, history=[receipt("1", status=STAGED)])
        self.assertEqual(plan["current_versions"], [])
        self.assertIn("  Installed     no labelled container running, v1.52.0 staged\n", plan_text(plan))
        self.assertEqual(self.component(plan, "backend")["current_source"],
                         "image of its stopped containers (FUTURE_AGI_VERSION in .env selects the staged v1.52.0)")
        installation = self.standalone(TARGET, label=PREVIOUS)
        installation.run("app", "v1.50.0", status="exited")
        plan = self.plan(installation, history=[receipt("1", status=STAGED)])
        self.assertEqual(plan["current_versions"], [PREVIOUS])
        self.assertIn("  Installed     v1.51.0 running, v1.52.0 staged\n", plan_text(plan))

    def test_a_staged_release_newer_than_the_manifest_is_staged_not_running(self):
        plan = self.plan(self.standalone(TARGET, label=PREVIOUS), manifest=self.manifest(version="v1.51.5"),
                         history=[receipt("1", status=STAGED)])
        self.assertBlocked(plan)
        self.assertReason(plan, "standalone has v1.52.0 staged, newer than this release (v1.51.5); bin/update never "
                                "downgrades")
        self.assertNoReasonContaining(plan, "standalone runs v1.52.0")

    def test_the_ahead_note_says_what_the_env_file_selects(self):
        plan = self.plan(self.standalone(TARGET, label=PREVIOUS), manifest=self.manifest(version="v1.51.5"),
                         history=[receipt("1", status=STAGED)], newer_release="v1.53.0")
        note = [n for n in plan["notes"] if "nothing to update until" in n][0]
        self.assertTrue(note.startswith(".env already selects v1.52.0, the same as or newer than v1.51.5"), note)
        self.assertNotIn("already runs v1.52.0", "\n".join(plan["notes"]))

    def test_the_minimum_version_reason_says_selects_while_a_run_is_unfinished(self):
        record = receipt("1", status="failed_during_rollout", target_version="v1.50.0")
        plan = self.plan(self.standalone("v1.50.0", label="v1.49.0"), history=[record],
                         manifest=self.manifest(minimum="v1.51.0"))
        self.assertReasonContaining(plan, ".env selects v1.50.0, older than v1.51.0")
        self.assertNoReasonContaining(plan, "this install runs v1.50.0")


class InitializationJobsPointerTests(PlanTestCase):
    """S5, S11, S19, S31, S32: every pointer to the initialization jobs names this install's Compose prefix and the
    shell rule (an exported key overrides the env file's value)."""

    def production(self, version: str = TARGET, env_file: str = "deploy/prod-eu.env", **kwargs) -> FakeInstallation:
        installation = self.distributed(version, mode="production", production=True, **kwargs)
        installation.env_file = self.root / env_file
        return installation

    def hint(self, env_file: str = "deploy/prod-eu.env") -> str:
        return (f"`docker compose --env-file {shlex.quote(env_file)} -f docker-compose.distributed.yml -f "
                f"deploy/docker-compose.production.yml run --rm ...` (deploy/README.md › Upgrading a production "
                f"install. {SHELL_RULE.format(env=env_file)})")

    def test_a_staged_update_blocking_a_newer_release_names_the_jobs_command(self):
        plan = self.plan(self.production(labels=ALL_PREVIOUS), history=[receipt("1", status=STAGED,
                                                                                manifest_source=STAGED_SOURCE)],
                         manifest=self.manifest(version="v1.53.0"))
        reason = self.assertReasonContaining(plan, "is staged")
        self.assertIn("Finish it first (check out v1.52.0 with `git checkout v1.52.0`, or at least its deployment files "
                      f"(your env file stays as it is), run its initialization jobs with {self.hint()} if they have "
                      "not run yet, then ./bin/update --production --env-file deploy/prod-eu.env apply --manifest "
                      f"{STAGED_SOURCE})", reason)
        self.assertNotIn("deploy/.env.production", reason)

    def test_the_staged_release_s_jobs_are_named_whatever_the_newer_release_needs(self):
        manifest = self.manifest(version="v1.53.0")
        manifest["migration"]["requires_bootstrap"] = False
        plan = self.plan(self.production(labels=ALL_PREVIOUS), history=[receipt("1", status=STAGED)],
                         manifest=manifest)
        self.assertIn(self.hint(), self.assertReasonContaining(plan, "is staged"))

    def test_every_pointer_carries_the_shell_rule(self):
        plans = [
            self.plan(self.production(PREVIOUS)),
            self.plan(self.production(labels=ALL_PREVIOUS), history=[receipt("1", status=STAGED)]),
            self.plan(self.production(labels={"worker": PREVIOUS}), history=[receipt("1", status="failed_during_rollout")]),
        ]
        for plan in plans:
            with self.subTest(next_command=plan["next_command"]):
                texts = plan["notes"] + plan["reasons"] + [plan["before_next_command"] or ""]
                pointers = [text for text in texts if "initialization jobs" in text and "run --rm" in text]
                self.assertTrue(pointers, texts)
                for text in pointers:
                    self.assertIn(self.hint(), text)


class ProductionEnvAheadTests(PlanTestCase):
    """S10: a production env file that selects a newer version than runs is never restarted onto."""

    def test_one_running_version(self):
        installation = self.distributed(PREVIOUS, mode="production", production=True,
                                        labels={"FUTURE_AGI_VERSION": "v1.50.0"})
        reason = self.assertReasonContaining(self.plan(installation), "without restarting the stack")
        self.assertIn("Set FUTURE_AGI_VERSION back to v1.50.0, the version that runs, then check again: a production "
                      "install updates in two steps around the release's initialization jobs (./bin/update "
                      "--production apply --stage, the jobs, then ./bin/update --production apply)", reason)
        self.assertNotIn("restart the stack so it runs v1.51.0", reason)
        self.assertNotIn("bin/update then updates it", reason)
        # Standalone and Distributed installs migrate on start: restarting onto the tag stays a remedy there.
        reason = self.assertReasonContaining(self.plan(self.distributed(PREVIOUS, labels={"FUTURE_AGI_VERSION":
                                                                                          "v1.50.0"})),
                                             "without restarting the stack")
        self.assertIn("or restart the stack so it runs v1.51.0", reason)

    def test_part_of_the_stack_on_the_newer_tag(self):
        installation = self.distributed(TARGET, mode="production", production=True, labels={"worker": PREVIOUS})
        reason = self.assertReasonContaining(self.plan(installation), "backend containers run different versions")
        # T23: the printed production Compose command carries the shell rule.
        self.assertIn("If the initialization jobs of v1.52.0 have run, restart the stack (`docker compose --env-file "
                      "deploy/.env.production -f docker-compose.distributed.yml -f deploy/docker-compose.production.yml "
                      "up -d`. Run it from a shell that does not export any key of deploy/.env.production (Compose "
                      "prefers your shell over the env file).) so every service runs v1.52.0; if they have not, set "
                      "FUTURE_AGI_VERSION back to v1.51.0 and restart the stack the same way so every service runs it",
                      reason)
        self.assertNotIn("Make them agree before updating: restart the stack", reason)


class UpgradeDocTests(PlanTestCase):
    """S16: the minimum_version reason points each kind of install at its own upgrade procedure."""

    def test_production_is_pointed_at_its_procedure(self):
        plan = self.plan(self.distributed("v1.50.0", mode="production", production=True),
                         manifest=self.manifest(minimum=PREVIOUS))
        reason = self.assertReasonContaining(plan, "older than v1.51.0")
        # T21: the procedure's $COMPOSE is this install's own Compose command, with the shell rule.
        self.assertTrue(reason.endswith(
            "Upgrade with the release's own steps first (deploy/README.md › Upgrading a production install, with "
            f"`{PRODUCTION_HINT} ...` for its $COMPOSE commands. Run it from a shell that does not export any key of "
            "deploy/.env.production (Compose prefers your shell over the env file).)"), reason)
        self.assertNotIn("INSTALLATION.md", reason)
        # Standalone and Distributed installs say the concrete stepping-stone procedure: checkout the floor,
        # point their env keys at it, then let ./bin/install keep those pins while updating the compose files.
        for installation in (self.standalone("v1.50.0"), self.distributed("v1.50.0")):
            with self.subTest(mode=installation.mode):
                reason = self.assertReasonContaining(self.plan(installation, manifest=self.manifest(minimum=PREVIOUS)),
                                                     "older than v1.51.0")
                self.assertTrue(reason.endswith("Upgrade first: check out v1.51.0, set the version keys in .env to "
                                                "v1.51.0, then run `./bin/install` (it keeps pinned keys as they "
                                                "are; INSTALLATION.md › Upgrading)"), reason)


class RegistryMirrorTests(PlanTestCase):
    """S17: bin/update verifies images from the release's registry only; no reason suggests a mirror path."""

    def test_a_production_install_on_another_registry_is_updated_by_hand(self):
        installation = self.distributed(PREVIOUS, mode="production", production=True)
        installation._config["services"]["frontend"]["image"] = f"registry.example.com/futureagi/frontend:{PREVIOUS}"
        installation.raw_images["frontend"] = "registry.example.com/futureagi/frontend:${FRONTEND_VERSION}"
        plan = self.plan(installation)
        self.assertReason(plan, (
            "service frontend pulls registry.example.com/futureagi/frontend, but the release names "
            "docker.io/futureagi/frontend for frontend. bin/update verifies images only from the registry the release "
            "publishes to (docker.io) and does not support registry mirrors yet: update this install by hand "
            f"(deploy/README.md › Upgrading a production install, with `{PRODUCTION_HINT} ...` for its $COMPOSE "
            "commands. Run it from a shell that does not export any key of deploy/.env.production (Compose prefers "
            "your shell over the env file).), or point frontend at docker.io/futureagi/frontend, then check again"))

    def test_no_reason_suggests_a_mirror_path(self):
        installation = self.standalone(PREVIOUS, extra_env={"SERVING_VERSION": PREVIOUS})
        installation._config["services"]["app"]["image"] = f"registry.example.com/mirror/standalone:{PREVIOUS}"
        installation.raw_images["app"] = "registry.example.com/mirror/standalone:${FUTURE_AGI_VERSION}"
        installation._config["services"]["serving"] = {"image": f"futureagi/serving:{PREVIOUS}"}
        installation.run("serving", PREVIOUS)
        plan = self.plan(installation)
        reason = self.assertReasonContaining(plan, "service app takes its image from FUTURE_AGI_VERSION")
        self.assertIn("does not support registry mirrors yet", reason)
        self.assertNotIn("futureagi/<image> path", "\n".join(plan["reasons"]))
        self.assertNotIn("A mirror must", "\n".join(plan["reasons"]))


class StrayJudgementTests(PlanTestCase):
    """S40, S42, S43: strays are worded by their state and version, and judged with apply's rule."""

    def install(self, version: str = PREVIOUS, **kwargs) -> FakeInstallation:
        installation = self.distributed(version, **kwargs)
        installation.profile_services.update({"worker-default": WORKER_RAW, "worker-tasks": WORKER_RAW})
        return installation

    def test_on_release(self):
        from deploy.update.plan import on_release

        for image, label, tag, by_tag, expected in (
                ("futureagi/future-agi:v1.0.0", TARGET, TARGET, False, True),
                ("futureagi/future-agi:v1.0.0", "v1.0.0", TARGET, False, False),
                ("futureagi/future-agi:v1.0.0", "", TARGET, True, False),
                (f"futureagi/future-agi:{TARGET}", "", TARGET, True, True),
                (f"futureagi/future-agi:{TARGET}", "", TARGET, False, False),
                # T37: a contradicting version label wins over the tag the container was created from.
                (f"futureagi/future-agi:{TARGET}", PREVIOUS, TARGET, True, False),
                (f"futureagi/future-agi:{TARGET}@sha256:{'a' * 64}", "", TARGET, True, False),
                ("futureagi/future-agi:v1.0.0", f"{TARGET}-gpu", TARGET, False, False),
                ("futureagi/serving:v1.0.0-gpu", f"{TARGET}-gpu", f"{TARGET}-gpu", False, True),
                ("futureagi/serving:v1.0.0-gpu", TARGET, f"{TARGET}-gpu", False, True),
                ("futureagi/serving:v1.0.0-gpu", f"{TARGET}-gpu", TARGET, False, True),
                ("futureagi/serving:v1.0.0-gpu", "v1.0.0-gpu", TARGET, False, False),
                ("not a reference", TARGET, TARGET, True, False)):
            with self.subTest(image=image, label=label, tag=tag, by_tag=by_tag):
                self.assertIs(on_release(image, label, tag, by_tag=by_tag), expected)

    def test_an_unlabelled_stray_of_an_active_component_is_not_on_the_release_by_its_tag(self):
        installation = self.install()
        installation.run("worker-default", "", image=f"futureagi/future-agi:{TARGET}")
        self.assertBlocked(self.plan(installation))
        # A component with no active service has nothing verified to compare with: its tag is what there is.
        installation = self.distributed(PREVIOUS, mode="production", production=True)
        installation._config["services"].pop("simulation-runner")
        installation._containers = [c for c in installation._containers if c["service"] != "simulation-runner"]
        installation.profile_services["simulation-runner"] = (
            "futureagi/future-agi-simulation-runner:${SIMULATION_RUNNER_VERSION:-latest}")
        installation.run("simulation-runner", "", image=f"futureagi/future-agi-simulation-runner:{TARGET}")
        plan = self.plan(installation)
        self.assertEqual((plan["decision"], plan["reasons"]), (UPDATE_AVAILABLE, []))
        installation._containers[-1]["image"] = f"futureagi/future-agi-simulation-runner:{PREVIOUS}"
        self.assertReasonContaining(self.plan(installation), "service simulation-runner has containers")

    def test_the_blocking_reason_names_each_container_s_state_and_version(self):
        installation = self.install()
        installation.run("worker-default", "v1.53.0", image="futureagi/future-agi:v1.53.0")
        installation.run("worker-tasks", PREVIOUS, image=f"futureagi/future-agi:{PREVIOUS}", status="restarting")
        reason = self.assertReasonContaining(self.plan(installation), "services worker-default, worker-tasks have")
        self.assertIn("(a profile that is not enabled now): worker-default (running, v1.53.0), worker-tasks (running, "
                      "v1.51.0). The update to v1.52.0 does not replace their containers", reason)
        self.assertNotIn("old release", reason)

    def test_a_paused_stray_is_paused(self):
        from deploy.update.plan import classify_strays, container_state

        self.assertEqual([container_state(status) for status in
                          ("running", "restarting", "paused", "exited", "created", "dead", "removing", None)],
                         ["running", "running", "paused", "stopped", "stopped", "stopped", "stopped", "stopped"])
        installation = self.install(TARGET)
        installation.run("worker-default", PREVIOUS, image=f"futureagi/future-agi:{PREVIOUS}", status="paused")
        installation.run("worker-default", "v1.50.0", image="futureagi/future-agi:v1.50.0", status="exited")
        entries = classify_strays(installation, installation.containers(), installation.config()["services"])["inactive"]
        self.assertEqual([(e["state"], e["running"]) for e in entries], [("paused", False), ("stopped", False)])
        note = [n for n in self.plan(installation)["notes"] if n.startswith("bin/update leaves these containers")][0]
        self.assertIn(": worker-default (paused, v1.51.0; stopped, v1.50.0).", note)


class MigrationWarningTests(PlanTestCase):
    """S44: only images that run the database migrations (standalone, backend) may have migrated it."""

    def test_a_newer_frontend_is_not_a_database_matter(self):
        plan = self.plan(self.distributed(PREVIOUS, labels={"frontend": "v1.53.0"}))
        reason = self.assertReasonContaining(plan, "service frontend runs v1.53.0")
        self.assertNotIn("migrated", reason)
        self.assertNotIn("restore the backup", reason)
        self.assertIn("Set FRONTEND_VERSION back to v1.53.0, the version that runs, or restart the stack so it runs "
                      "v1.51.0, then check again", reason)
        # The components that run migrations keep the warning.
        for plan in (self.plan(self.standalone(PREVIOUS, label="v1.53.0")),
                     self.plan(self.distributed(PREVIOUS, labels={"backend": "v1.53.0"}))):
            with self.subTest(reasons=plan["reasons"]):
                self.assertReasonContaining(plan, "v1.53.0 may have migrated the database")

    def test_a_partly_newer_stateless_component(self):
        installation = FakeInstallation(self.root, services={
            "frontend": f"futureagi/frontend:{PREVIOUS}", "frontend-canary": f"futureagi/frontend:{PREVIOUS}",
        }, env={"FRONTEND_VERSION": PREVIOUS})
        installation.run("frontend", "v1.53.0").run("frontend-canary", PREVIOUS)
        reason = self.assertReasonContaining(self.plan(installation), "frontend containers run different versions")
        self.assertNotIn("migrated", reason)
        self.assertIn("Or restart the stack as it is, so every service runs v1.51.0", reason)


class DirectoryDriftHintTests(PlanTestCase):
    """S49, S52: a drifted directory may hold a file git does not show by default."""

    DIRECTORY = "futureagi/scripts/property_catalog_oss"

    def manifest_with_directory(self, directory: str = DIRECTORY) -> Dict[str, Any]:
        self.write(f"{directory}/schema.sql", "create table t();\n")
        return self.manifest(files=list(DEFAULT_FILES) + [directory])

    def test_the_reason_says_how_to_find_an_untracked_or_ignored_file(self):
        manifest = self.manifest_with_directory()
        self.write(f"{self.DIRECTORY}/bootstrap.log", "log\n")
        reason = self.assertReasonContaining(self.plan(self.standalone(PREVIOUS), manifest=manifest), "differ from")
        # T45/T46: without git (this checkout is no work tree) the reason says such a file may be the cause, never
        # that it is.
        self.assertTrue(reason.endswith(
            f"then check again. {self.DIRECTORY} also differs when it holds a file that is not part of v1.52.0 (a log "
            "or an editor's backup, say): move any such file out of the checkout"), reason)
        self.assertNotIn("an untracked or ignored file inside it differs", reason)
        plan = self.plan(self.standalone(PREVIOUS), manifest=manifest, newer_release="v1.53.0")
        # Round 6 (U26): with a newer release waiting this checkout may hold that release's own files: never moved out.
        self.assertNotIn("move any such file out", self.assertReasonContaining(plan, "differ from"))

    def test_a_file_entry_gets_no_such_hint_and_paths_are_quoted(self):
        manifest = self.manifest_with_directory("deploy/my scripts")
        self.write("docker-compose.yml", "changed\n")
        self.write("deploy/my scripts/extra.out", "x\n")
        reason = self.assertReasonContaining(self.plan(self.standalone(PREVIOUS), manifest=manifest), "differ from")
        # Without git there is no command to quote: the directory is named (DriftGitTests quotes the commands).
        self.assertIn("deploy/my scripts also differs when it holds a file that is not part of v1.52.0", reason)
        manifest = self.manifest()
        self.write("docker-compose.yml", "changed again\n")
        self.assertNoReasonContaining(self.plan(self.standalone(PREVIOUS), manifest=manifest), "holds a file")


class IntegratorRound4PlanEdgeTests(PlanTestCase):
    """Edges of this round's plan rules: selected_keys, the release options of a command for another release, a
    stopped update newer than the manifest, and the Decision line of apply --stage (F6)."""

    def test_selected_keys_takes_previous_values_keys_only_from_components_that_name_a_tag(self):
        from deploy.update.plan import selected_keys
        record = {"previous_values": {"FUTURE_AGI_VERSION": PREVIOUS},
                  "components": ["not a component", {"variable": "FRONTEND_VERSION", "target_tag": TARGET},
                                 {"variable": "FUTURE_AGI_VERSION", "target_tag": ""},
                                 {"variable": "FUTURE_AGI_VERSION", "target_tag": TARGET}],
                  "env_changes": {"SERVING_VERSION": TARGET, "BROKEN": None}}
        self.assertEqual(selected_keys(record), {"FUTURE_AGI_VERSION": TARGET, "SERVING_VERSION": TARGET})
        self.assertEqual(selected_keys({"previous_values": None, "components": None}), {})

    def test_a_command_for_another_release_drops_both_spellings_of_its_manifest_and_bundle(self):
        from deploy.update.plan import _without_manifest
        self.assertEqual(_without_manifest(["--manifest=/m.json", "--bundle", "/b.json", "--policy", "major",
                                            "--bundle=/c.json", "--channel", "stable"]),
                         ["--policy", "major", "--channel", "stable"])

    def test_a_stopped_update_newer_than_the_manifest_is_said_to_be_selected(self):
        record = receipt("1", status="failed_during_rollout", target_version="v1.53.0")
        plan = self.plan(self.standalone("v1.53.0", label=PREVIOUS), history=[record])
        self.assertBlocked(plan)
        self.assertReason(plan, "FUTURE_AGI_VERSION in .env selects v1.53.0 for standalone (the update that stopped "
                                "part-way), newer than this release (v1.52.0); bin/update never downgrades")
        self.assertNoReasonContaining(plan, "standalone runs v1.53.0")

    def test_apply_stage_says_it_stages(self):
        plan = self.plan(self.standalone(PREVIOUS))
        self.assertIn("  Decision      update available: staging v1.52.0 (pulls and verifies it, restarts nothing)\n",
                      plan_text(plan, applying=True, stage=True))
        # Only apply's own echo says so: check's names the command to run.
        self.assertIn("  Decision      update available: run ./bin/update apply\n", plan_text(plan, stage=True))

    def test_an_env_file_newer_than_the_release_and_the_containers_is_said_to_select_it(self):
        # The containers run v1.51.0 (or carry no label): nothing runs v1.53.0, the env file selects it.
        for label in (PREVIOUS, ""):
            with self.subTest(label=label):
                plan = self.plan(self.standalone("v1.53.0", label=label))
                self.assertBlocked(plan)
                self.assertReason(plan, "FUTURE_AGI_VERSION in .env selects v1.53.0 for standalone, newer than this "
                                        "release (v1.52.0); bin/update never downgrades")
                self.assertNoReasonContaining(plan, "standalone runs v1.53.0")

    def test_git_and_install_commands_cd_to_the_checkout_when_bin_update_ran_elsewhere(self):
        # S15: like every other printed command, the git and ./bin/install ones work pasted where bin/update ran.
        cd = f"cd {shlex.quote(str(self.root))} && "
        directory = "futureagi/scripts/property_catalog_oss"
        self.write(f"{directory}/schema.sql", "create table t();\n")
        manifest = self.manifest(files=list(DEFAULT_FILES) + [directory])
        self.write(f"{directory}/bootstrap.log", "log\n")
        self.write("docker-compose.yml", "changed\n")
        installation = self.standalone(PREVIOUS)
        installation.caller_cwd = self.root.parent  # type: ignore[attr-defined]
        reason = self.assertReasonContaining(self.plan(installation, manifest=manifest), "differ from")
        # Round 6 (U33): no git work tree here, so no git command at all (Round6DriftGitTests covers its cd).
        self.assertNotIn("git checkout", reason)
        self.assertNotIn("git fetch", reason)
        # T45/T46: no git work tree here, so no git status command (DriftGitTests covers the cd in those commands).
        self.assertTrue(reason.endswith(f"{directory} also differs when it holds a file that is not part of v1.52.0 (a "
                                        "log or an editor's backup, say): move any such file out of the checkout"),
                        reason)
        self.assertNotIn(f" in {self.root}", reason)
        installation = self.distributed(PREVIOUS, tags={"FRONTEND_VERSION": "v1.50.9"})
        installation.caller_cwd = self.root.parent  # type: ignore[attr-defined]
        self.assertReasonContaining(self.plan(installation), f"then run `{cd}./bin/install` (it keeps pinned keys")

    def test_a_next_command_that_does_not_split_is_still_read_for_its_stage(self):
        plan = self.plan(self.standalone(PREVIOUS))
        for command, step in (("./bin/update apply --manifest 'unbalanced", ""),
                              ("./bin/update apply --stage --manifest 'unbalanced", " (the first of two steps, see below)")):
            with self.subTest(command=command):
                plan["next_command"] = command
                self.assertIn(f"  Decision      update available: run {command}{step}\n", plan_text(plan))



# --------------------------------------------------------------------------
# Round 5
import os  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402

from deploy.update.plan import jobs_text, on_release  # noqa: E402

CUSTOM_ENV = "deploy/prod-eu.env"
CUSTOM_HINT = (f"docker compose --env-file {CUSTOM_ENV} -f docker-compose.distributed.yml -f "
               "deploy/docker-compose.production.yml")
# Round 6 (H3): one sentence wherever a production Compose command is printed (SHELL_RULE).
NEW_RULE = SHELL_RULE


def _decision(text: str) -> str:
    return [line for line in text.splitlines() if line.startswith("  Decision")][0]


class Round5ApplyingEchoTests(PlanTestCase):
    """G4 (T9, T14): the plan apply prints while carrying it out leaves out the notes that name what to run next, and
    says what the run does, including that it reuses the images already on this host."""

    def production(self, version: str = TARGET, **kwargs) -> FakeInstallation:
        return self.distributed(version, mode="production", production=True, **kwargs)

    def test_finishing_a_staged_update_leaves_out_its_finishing_and_jobs_note(self):
        plan = self.plan(self.production(labels=ALL_PREVIOUS), history=[receipt("1", status=STAGED)])
        self.assertTrue(plan["resume"])
        staged = [n for n in plan["notes"] if "is staged" in n]
        self.assertEqual(len(staged), 1, plan["notes"])
        self.assertEqual(plan["step_notes"], staged)
        text = plan_text(plan, applying=True)
        self.assertNotIn("finishes it", text)
        self.assertNotIn("initialization jobs", text)
        self.assertNotIn("run --rm", text)
        # check still names every step.
        self.assertIn(staged[0], plan_text(plan))

    def test_finishing_a_stopped_production_update_leaves_out_both_step_notes(self):
        plan = self.plan(self.production(labels={"worker": PREVIOUS}),
                         history=[receipt("1", status="failed_during_rollout")])
        self.assertTrue(plan["resume"])
        self.assertEqual(len(plan["step_notes"]), 2, plan["notes"])
        self.assertTrue(all(note in plan["notes"] for note in plan["step_notes"]))
        text = plan_text(plan, applying=True)
        self.assertNotIn("apply finishes it", text)
        self.assertNotIn("already selects", text)
        self.assertIn("  Decision      update available: finishing the stopped update to v1.52.0\n", text)
        for note in plan["step_notes"]:
            self.assertIn(note, plan_text(plan))

    def test_staging_leaves_out_the_two_step_note(self):
        plan = self.plan(self.production(PREVIOUS))
        two_steps = [n for n in plan["notes"] if n.startswith("production installs update in two steps")]
        self.assertEqual(plan["step_notes"], two_steps)
        text = plan_text(plan, applying=True, stage=True)
        self.assertNotIn("two steps", text)
        self.assertNotIn("·", text)
        self.assertIn(two_steps[0], plan_text(plan))

    def test_other_notes_stay(self):
        installation = self.distributed(PREVIOUS)
        installation.run("gone", PREVIOUS, image=f"futureagi/future-agi:{PREVIOUS}")
        plan = self.plan(installation, fallback_note="using v1.52.0, the newest release that has a manifest")
        self.assertEqual(plan["step_notes"], [])
        text = plan_text(plan, applying=True)
        for note in plan["notes"]:
            self.assertIn(f"  · {note}\n", text)

    def test_a_run_that_reuses_the_images_on_this_host_says_so(self):
        reuses = "reuses its images already on this host, verified again, not pulled"
        fresh = self.plan(self.standalone(PREVIOUS))
        self.assertEqual(_decision(plan_text(fresh, applying=True, stage=True, on_host=True)),
                         f"  Decision      update available: staging v1.52.0 ({reuses}; restarts nothing)")
        self.assertNotIn("pulls", _decision(plan_text(fresh, applying=True, stage=True, on_host=True)))
        self.assertEqual(_decision(plan_text(fresh, applying=True, on_host=True)),
                         f"  Decision      update available: applying v1.52.0 ({reuses})")
        staged = self.plan(self.standalone(TARGET, label=PREVIOUS), history=[receipt("1", status=STAGED)])
        self.assertEqual(_decision(plan_text(staged, applying=True, on_host=True)),
                         f"  Decision      update available: finishing the staged update to v1.52.0 ({reuses})")
        # A run that pulls (or does not say) reads as before.
        for on_host in (False, None):
            with self.subTest(on_host=on_host):
                self.assertEqual(_decision(plan_text(fresh, applying=True, stage=True, on_host=on_host)),
                                 "  Decision      update available: staging v1.52.0 (pulls and verifies it, restarts "
                                 "nothing)")
                self.assertEqual(_decision(plan_text(fresh, applying=True, on_host=on_host)),
                                 "  Decision      update available: applying v1.52.0")
        # check's Decision line names the command whatever apply would find.
        self.assertEqual(_decision(plan_text(fresh, on_host=True)), "  Decision      update available: run "
                                                                      "./bin/update apply")

    def test_step_notes_round_trip_through_json(self):
        plan = self.plan(self.production(PREVIOUS))
        self.assertEqual(json.loads(plan_json(plan))["step_notes"], plan["step_notes"])
        self.assertEqual(self.plan(self.standalone(TARGET))["step_notes"], [])

    def test_a_plan_without_step_notes_still_prints(self):
        plan = self.plan(self.production(PREVIOUS))
        del plan["step_notes"]
        self.assertIn("two steps", plan_text(plan, applying=True, stage=True))


class Round5ReleaseOptionsTests(PlanTestCase):
    """G6 (T16): a finish command for another release than this run's manifest carries only that release's
    --manifest, never this run's --channel or --policy."""

    OPTIONS = ["--manifest", "/srv/beta/v1.53.0.json", "--channel", "beta", "--policy", "major"]

    def test_a_staged_update_of_another_release(self):
        settings = self.settings(channel="beta")
        plan = self.plan(self.standalone(TARGET, label=PREVIOUS), manifest=self.manifest(version="v1.53.0",
                                                                                          channel="beta"),
                         history=[receipt("1", status=STAGED, manifest_source="/srv/stable/v1.52.0.json")],
                         settings=settings, command_options=self.OPTIONS)
        reason = self.assertReasonContaining(plan, "is staged")
        self.assertIn("then ./bin/update apply --manifest /srv/stable/v1.52.0.json) or undo it", reason)
        for word in ("--channel", "--policy", "/srv/beta"):
            self.assertNotIn(word, reason)
        # Finishing this run's own release keeps this run's options.
        plan = self.plan(self.standalone(TARGET, label=PREVIOUS), history=[receipt("1", status=STAGED)],
                         command_options=["--channel", "stable", "--policy", "major"])
        self.assertEqual(plan["next_command"], "./bin/update apply --channel stable --policy major")

    def test_a_stopped_update_of_another_release(self):
        plan = self.plan(self.standalone("v1.51.5", label=PREVIOUS), command_options=self.OPTIONS,
                         history=[receipt("1", status="failed_during_rollout", target_version="v1.51.5",
                                          manifest_source="/srv/stable/v1.51.5.json")])
        reason = self.assertReasonContaining(plan, "stopped while containers were starting")
        self.assertIn("then ./bin/update apply --manifest /srv/stable/v1.51.5.json) or roll it back", reason)
        for word in ("--channel", "--policy", "/srv/beta"):
            self.assertNotIn(word, reason)


class Round5CheckoutCommandTests(PlanTestCase):
    """G6 (T17, T54): ./bin/install and git commands carry the cd prefix, and a staged update of another release is
    finished from that release's checkout."""

    def elsewhere(self, installation: FakeInstallation) -> FakeInstallation:
        installation.caller_cwd = self.root.parent  # type: ignore[attr-defined]
        return installation

    def cd(self) -> str:
        return f"cd {shlex.quote(str(self.root))} && "

    def test_a_local_build_names_install_from_source_with_the_cd(self):
        reason = self.assertReasonContaining(self.plan(self.elsewhere(self.standalone("local"))), "built from this")
        self.assertTrue(reason.endswith(f"keep using `{self.cd()}./bin/install --from-source`"), reason)
        installation = self.elsewhere(self.distributed(PREVIOUS, tags={"FUTURE_AGI_VERSION": "local"}))
        self.assertIn(f"`{self.cd()}./bin/install --from-source`",
                      self.assertReasonContaining(self.plan(installation), "backend runs images built"))

    def test_the_staged_release_is_checked_out_before_it_is_finished(self):
        plan = self.plan(self.elsewhere(self.standalone(TARGET, label=PREVIOUS)),
                         manifest=self.manifest(version="v1.53.0"),
                         history=[receipt("1", status=STAGED, manifest_source=STAGED_SOURCE)])
        reason = self.assertReasonContaining(plan, "is staged")
        checkout = f"check out v1.52.0 with `{self.cd()}git checkout v1.52.0`"
        self.assertIn(f"Finish it first ({checkout}, or at least its deployment files (your env file stays as it "
                      f"is), then {self.cd()}./bin/update apply --manifest {STAGED_SOURCE}) or undo it", reason)

    def test_production_checks_out_then_runs_the_jobs_then_finishes(self):
        installation = self.distributed(TARGET, mode="production", production=True, labels=ALL_PREVIOUS)
        plan = self.plan(installation, manifest=self.manifest(version="v1.53.0"),
                         history=[receipt("1", status=STAGED, manifest_source=STAGED_SOURCE)])
        reason = self.assertReasonContaining(plan, "is staged")
        checkout = reason.index("`git checkout v1.52.0`")
        jobs = reason.index("run --rm ...")
        finish = reason.index(f"./bin/update --production apply --manifest {STAGED_SOURCE}")
        self.assertLess(checkout, jobs)
        self.assertLess(jobs, finish)


class Round5ProductionPointerTests(PlanTestCase):
    """G6 (T21), T22, T23, T25: production pointers name this install's Compose command, and the shell rule says
    what it is without crediting deploy/setup.sh with a requirement."""

    def production(self, version: str = PREVIOUS, **kwargs) -> FakeInstallation:
        installation = self.distributed(version, mode="production", production=True, **kwargs)
        installation.env_file = self.root / CUSTOM_ENV
        return installation

    def test_the_upgrade_procedure_names_this_installs_compose(self):
        plan = self.plan(self.production("v1.50.0"), manifest=self.manifest(minimum=PREVIOUS))
        reason = self.assertReasonContaining(plan, "older than v1.51.0")
        self.assertTrue(reason.endswith(
            f"(deploy/README.md › Upgrading a production install, with `{CUSTOM_HINT} ...` for its $COMPOSE "
            f"commands. {NEW_RULE.format(env=CUSTOM_ENV)})"), reason)
        self.assertNotIn("deploy/.env.production", reason)

    def test_the_registry_reason_names_this_installs_compose(self):
        installation = self.production()
        installation._config["services"]["frontend"]["image"] = f"registry.example.com/futureagi/frontend:{PREVIOUS}"
        installation.raw_images["frontend"] = "registry.example.com/futureagi/frontend:${FRONTEND_VERSION}"
        reason = self.assertReasonContaining(self.plan(installation), "service frontend pulls registry.example.com")
        self.assertIn(f"update this install by hand (deploy/README.md › Upgrading a production install, with "
                      f"`{CUSTOM_HINT} ...` for its $COMPOSE commands. {NEW_RULE.format(env=CUSTOM_ENV)})", reason)
        self.assertNotIn("deploy/.env.production", reason)

    def test_the_jobs_pointer_gives_the_reason_for_the_shell_rule(self):
        installation = self.production()
        text = jobs_text(installation)  # type: ignore[arg-type]
        self.assertEqual(text, f"`{CUSTOM_HINT} run --rm ...` (deploy/README.md › Upgrading a production install. "
                               f"{NEW_RULE.format(env=CUSTOM_ENV)})")
        plan = self.plan(installation)
        printed = "\n".join(plan["notes"] + plan["reasons"])
        self.assertIn(text, printed)
        self.assertNotIn("as deploy/setup.sh requires", printed)

    def test_every_production_restart_command_carries_the_shell_rule(self):
        rule = NEW_RULE.format(env=CUSTOM_ENV)
        for labels in ({"worker": PREVIOUS}, {"worker": "v1.53.0"}):
            with self.subTest(labels=labels):
                version = TARGET if labels["worker"] == PREVIOUS else PREVIOUS
                reason = self.assertReasonContaining(self.plan(self.production(version, labels=labels)),
                                                     "containers run different versions")
                self.assertIn(f"(`{CUSTOM_HINT} up -d`. {rule})", reason)
                self.assertEqual(reason.count("up -d`"), reason.count(f"up -d`. {rule}"), reason)
        # Outside production bin/update refuses exported keys itself: no rule there.
        reason = self.assertReasonContaining(self.plan(self.distributed(TARGET, labels={"worker": PREVIOUS})),
                                             "containers run different versions")
        self.assertIn("(`docker compose up -d`)", reason)
        self.assertNotIn("export any key", reason)


class Round5MixedProductionTests(PlanTestCase):
    """T19: in production a newer image never migrated the database (the stack only checks the schema), so the
    remedy restarts the stack as it is unless the newer release's initialization jobs ran, and never sends the
    operator to a backup."""

    def production(self, version: str = PREVIOUS, **kwargs) -> FakeInstallation:
        return self.distributed(version, mode="production", production=True, **kwargs)

    def test_part_of_the_stack_on_a_newer_backend(self):
        reason = self.assertReasonContaining(self.plan(self.production(labels={"backend": "v1.53.0"})),
                                             "backend containers run different versions")
        self.assertIn("If the initialization jobs of v1.53.0 have not run, restart the stack as it is", reason)
        self.assertIn("so every service runs v1.51.0, then check again: a production install updates in two steps "
                      "around the release's initialization jobs (./bin/update --production apply --stage, the jobs, "
                      "then ./bin/update --production apply)", reason)
        # The newer tag only once its jobs ran.
        jobs_ran = reason.index("If they have run, set FUTURE_AGI_VERSION=v1.53.0")
        self.assertEqual(reason.count("FUTURE_AGI_VERSION=v1.53.0"), 1, reason)
        self.assertGreater(jobs_ran, reason.index("If the initialization jobs of v1.53.0 have not run"))
        self.assertNotIn("migrated", reason)
        self.assertNotIn("backup", reason)

    def test_a_newer_stateless_component_is_restarted_as_it_is(self):
        reason = self.assertReasonContaining(self.plan(self.production(labels={"frontend": "v1.53.0"})),
                                             "service frontend runs v1.53.0")
        self.assertNotIn("backup", reason)
        installation = FakeInstallation(self.root, services={
            "frontend": f"futureagi/frontend:{PREVIOUS}", "frontend-canary": f"futureagi/frontend:{PREVIOUS}",
        }, env={"FRONTEND_VERSION": PREVIOUS}, mode="production", production=True)
        installation.run("frontend", "v1.53.0").run("frontend-canary", PREVIOUS)
        reason = self.assertReasonContaining(self.plan(installation), "frontend containers run different versions")
        self.assertIn("restart the stack as it is (`", reason)
        self.assertIn("so every service runs v1.51.0, then check again: a production install updates in two steps",
                      reason)
        self.assertNotIn("FRONTEND_VERSION=v1.53.0", reason)

    def test_one_newer_version_running_is_never_a_backup_matter(self):
        reason = self.assertReasonContaining(self.plan(self.production(labels={"FUTURE_AGI_VERSION": "v1.53.0"})),
                                             "the env file was changed without restarting the stack")
        self.assertIn("Set FUTURE_AGI_VERSION back to v1.53.0, the version that runs, then check again. Do not "
                      "restart the stack on v1.51.0 if the initialization jobs of v1.53.0 have run: an older release "
                      "does not run on a newer schema", reason)
        self.assertNotIn("backup", reason)
        self.assertNotIn("may have migrated", reason)
        # Standalone and Distributed images migrate on start: the warning stays there.
        reason = self.assertReasonContaining(self.plan(self.distributed(PREVIOUS, labels={"backend": "v1.53.0"})),
                                             "backend containers run different versions")
        self.assertIn("v1.53.0 may have migrated the database", reason)

    def test_without_the_jobs_the_stack_goes_back_to_the_version_below_the_tag(self):
        installation = self.production(TARGET, labels={"worker": PREVIOUS})
        installation._config["services"]["backend-canary"] = {"image": f"futureagi/future-agi:{TARGET}"}
        installation.raw_images["backend-canary"] = WORKER_RAW
        installation.run("backend-canary", "v1.50.0")
        reason = self.assertReasonContaining(self.plan(installation), "backend containers run different versions")
        self.assertIn("v1.50.0 (backend-canary), v1.51.0 (worker), v1.52.0 (backend)", reason)
        self.assertIn("if they have not, set FUTURE_AGI_VERSION back to v1.51.0 and restart", reason)


class Round5StoppedRolloutPutBackTests(PlanTestCase):
    """T7: an update that stopped during the rollout, its pins put back by hand while part of the stack runs its
    release, is finished through bin/update, never by a restart by hand."""

    def production(self, **kwargs) -> FakeInstallation:
        return self.distributed(PREVIOUS, mode="production", production=True, **kwargs)

    def stopped(self, **fields) -> Dict[str, Any]:
        return receipt("1", status="failed_during_rollout", env_changes=dict(EVERY_KEY), **fields)

    def test_the_remedy_is_to_select_it_again_and_finish_it(self):
        plan = self.plan(self.production(labels={"backend": TARGET}), history=[self.stopped()])
        self.assertBlocked(plan)
        reason = self.assertReasonContaining(plan, "left backend running v1.52.0")
        keys = ", ".join(f"{key}=v1.52.0" for key in sorted(EVERY_KEY))
        self.assertIn(f"the update to v1.52.0 that stopped while containers were being replaced (receipt "
                      f"{STATE}/receipt-20261019T000001Z-1.json) left backend running v1.52.0, but "
                      f"{_join_keys(EVERY_KEY)} in deploy/.env.production no longer select it. Finish that update "
                      f"rather than restarting the stack by hand: set {keys} in deploy/.env.production again, run its "
                      f"initialization jobs with {jobs_text(plan and self.production())} if they have not run yet, "
                      "then run `./bin/update --production apply`, which finishes it", reason)
        printed = "\n".join(plan["reasons"] + plan["notes"])
        self.assertNotIn("up -d", printed)
        self.assertNotIn("abandoned", printed)
        self.assertNotIn("containers run different versions", printed)
        # Done as told, the plan finishes the stopped update.
        installation = self.distributed(TARGET, mode="production", production=True,
                                        labels={key: PREVIOUS for key in DISTRIBUTED if key != "backend"})
        plan = self.plan(installation, history=[self.stopped()])
        self.assertEqual((plan["decision"], plan["resume"], plan["stopped_version"]), (UPDATE_AVAILABLE, True, TARGET))
        # A stack that runs none of it was put back by hand completely: the update is planned afresh.
        plan = self.plan(self.production(), history=[self.stopped()])
        self.assertEqual(plan["decision"], UPDATE_AVAILABLE, plan["reasons"])
        self.assertTrue([n for n in plan["notes"] if "is abandoned" in n], plan["notes"])

    def test_another_release_is_finished_with_its_own_manifest(self):
        plan = self.plan(self.distributed(PREVIOUS, labels={"backend": TARGET}),
                         history=[self.stopped(manifest_source="/srv/v1.52.0.json")],
                         manifest=self.manifest(version="v1.53.0"), command_options=["--policy", "major"])
        reason = self.assertReasonContaining(plan, "left backend running v1.52.0")
        # Round 6 (U8, U14): that release's checkout comes first, and the way back is named too.
        self.assertIn("Finish that update rather than restarting the stack by hand: check out v1.52.0 with `git "
                      "checkout v1.52.0`, or at least its deployment files (your env file stays as it is), set ",
                      reason)
        self.assertTrue(reason.endswith(
            "in .env again, then run `./bin/update apply --manifest /srv/v1.52.0.json`, which finishes it; or undo it "
            f"with `./bin/update rollback --receipt {STATE}/receipt-20261019T000001Z-1.json --yes`"), reason)


class Round5UnfinishedRowTests(PlanTestCase):
    """T8, T10, T12, T13: the component rows and current_source of an unfinished run say what is true."""

    def test_a_stopped_rollback_checked_against_the_release_it_undoes_has_no_pin(self):
        record = receipt("1", kind="rollback", status="failed_during_rollout", target_version=PREVIOUS,
                         rolls_back=f"{STATE}/receipt-a.json")
        for installation in (self.standalone(PREVIOUS, label=TARGET),
                             self.distributed(PREVIOUS, labels={"backend": TARGET, "frontend": TARGET})):
            with self.subTest(mode=installation.mode):
                plan = self.plan(installation, history=[record])
                self.assertBlocked(plan)
                self.assertNotIn("pin", [item["action"] for item in plan["components"]])
                self.assertFalse([n for n in plan["notes"] if ":latest" in n], plan["notes"])
                self.assertNotIn(" pin  ", plan_text(plan))

    def test_a_stopped_update_of_a_newer_release_has_no_pin(self):
        plan = self.plan(self.standalone("v1.53.0", label=TARGET),
                         history=[receipt("1", status="failed_during_rollout", target_version="v1.53.0")])
        self.assertBlocked(plan)
        self.assertEqual(self.component(plan, "standalone")["action"], "update")
        self.assertFalse([n for n in plan["notes"] if ":latest" in n], plan["notes"])
        # An install that follows :latest and runs the release is still pinned to its tag.
        plan = self.plan(self.standalone("latest", label=TARGET))
        self.assertEqual(self.component(plan, "standalone")["action"], "pin")
        self.assertIn("images that follow :latest are pinned to the release's version tag when an update runs",
                      plan["notes"])

    def test_nothing_to_do_reports_what_the_env_file_selects(self):
        plan = self.plan(self.standalone(TARGET, label=PREVIOUS), manifest=self.manifest(version="v1.51.5"),
                         history=[receipt("1", status=STAGED)], newer_release="v1.53.0")
        self.assertBlocked(plan)
        item = self.component(plan, "standalone")
        self.assertEqual((item["current_version"], item["action"], item["target_tag"]), (TARGET, "keep", "v1.51.5"))
        self.assertEqual(item["current_source"],
                         "FUTURE_AGI_VERSION in .env (the staged v1.52.0; its containers run v1.51.0)")
        self.assertIn("    standalone         v1.52.0    keep v1.51.5        ", plan_text(plan))
        # What runs stays the Installed line's.
        self.assertIn("  Installed     v1.51.0 running, v1.52.0 staged\n", plan_text(plan))

    def test_current_source_says_what_was_found(self):
        staged = [receipt("1", status=STAGED)]
        selects = "FUTURE_AGI_VERSION in .env selects the staged v1.52.0"
        unlabelled = self.standalone(TARGET, label="")
        paused = self.standalone(TARGET, label=PREVIOUS)
        paused._containers[0]["status"] = "paused"
        none = self.standalone(TARGET, label=PREVIOUS)
        none._containers = [c for c in none._containers if c["service"] != "app"]
        for installation, source, shown in ((unlabelled, f"running image ({selects})", None),
                                            (paused, f"image of its paused containers ({selects})", PREVIOUS),
                                            (none, f"no container ({selects})", None)):
            with self.subTest(source=source):
                plan = self.plan(installation, history=staged)
                self.assertEqual(plan["staged_version"], TARGET)
                item = self.component(plan, "standalone")
                self.assertEqual((item["current_source"], item["current_version"]), (source, shown))


class Round5StrayJudgementTests(PlanTestCase):
    """T36, T37: a version label always wins over the tag a stray was created from; an unlabelled stray created from
    a tag that names a version is judged by that version (either serving variant), as apply judges it."""

    def production(self) -> FakeInstallation:
        installation = self.distributed(PREVIOUS, mode="production", production=True)
        installation._config["services"].pop("serving")
        installation._containers = [c for c in installation._containers if c["service"] != "serving"]
        installation.profile_services["serving"] = "futureagi/serving:${SERVING_VERSION:-latest}"
        return installation

    def test_on_release(self):
        for image, label, tag, by_tag, expected in (
                (f"futureagi/serving:{TARGET}-gpu", "", TARGET, True, True),
                (f"futureagi/serving:{TARGET}", "", f"{TARGET}-gpu", True, True),
                (f"futureagi/serving:{TARGET}-gpu", "", TARGET, False, False),
                (f"futureagi/serving:{PREVIOUS}-gpu", "", TARGET, True, False),
                (f"futureagi/future-agi:{TARGET}", "v1.50.0", TARGET, True, False),
                (f"futureagi/future-agi:{PREVIOUS}", TARGET, TARGET, True, True),
                ("futureagi/future-agi:latest", "", TARGET, True, False),
                ("futureagi/future-agi", "", TARGET, True, False)):
            with self.subTest(image=image, label=label, tag=tag, by_tag=by_tag):
                self.assertIs(on_release(image, label, tag, by_tag=by_tag), expected)

    def test_an_unlabelled_stray_of_the_other_serving_variant_on_the_release(self):
        for pin, created in ((PREVIOUS, f"futureagi/serving:{TARGET}-gpu"),
                             (f"{PREVIOUS}-gpu", f"futureagi/serving:{TARGET}")):
            with self.subTest(pin=pin, created=created):
                installation = self.production()
                installation.env["SERVING_VERSION"] = pin
                installation.run("serving", "", image=created)
                plan = self.plan(installation)
                self.assertEqual((plan["decision"], plan["reasons"]), (UPDATE_AVAILABLE, []))
                item = [i for i in plan["components"] if i["variable"] == "SERVING_VERSION"][0]
                # apply judges the stray after the restart against the tag the key now selects (expected_tags).
                self.assertTrue(on_release(created, "", item["target_tag"], by_tag=True))

    def test_a_label_that_contradicts_the_tag_blocks(self):
        installation = self.distributed(PREVIOUS, mode="production", production=True)
        installation._config["services"].pop("simulation-runner")
        installation._containers = [c for c in installation._containers if c["service"] != "simulation-runner"]
        installation.profile_services["simulation-runner"] = (
            "futureagi/future-agi-simulation-runner:${SIMULATION_RUNNER_VERSION:-latest}")
        installation.run("simulation-runner", "v1.50.0", image=f"futureagi/future-agi-simulation-runner:{TARGET}")
        plan = self.plan(installation)
        self.assertBlocked(plan)
        self.assertIn(": simulation-runner (running, v1.50.0).",
                      self.assertReasonContaining(plan, "service simulation-runner has containers"))


class Round5StagedAlreadyRunsTests(PlanTestCase):
    """T58: a staged update counts as already running only when every service runs a healthy container on it (a
    one-shot job may have finished on it); containers that never started, crashed or restart still need it finished."""

    def installation(self) -> FakeInstallation:
        installation = self.distributed(TARGET, mode="production", production=True)
        installation._config["services"]["postgres-schema-bootstrap"] = {"image": f"futureagi/future-agi:{TARGET}"}
        installation.run("postgres-schema-bootstrap", TARGET, status="exited")
        return installation

    def staged(self) -> List[Dict[str, Any]]:
        return [receipt("1", status=STAGED, env_changes=dict(EVERY_KEY))]

    def container(self, installation: FakeInstallation, service: str) -> Dict[str, Any]:
        return [c for c in installation._containers if c["service"] == service][0]

    def assertStillStaged(self, installation: FakeInstallation) -> None:
        plan = self.plan(installation, history=self.staged())
        self.assertEqual((plan["decision"], plan["resume"], plan["staged_version"]), (UPDATE_AVAILABLE, True, TARGET))
        self.assertFalse([n for n in plan["notes"] if "already runs" in n], plan["notes"])

    def test_services_that_never_started_still_need_the_update_finished(self):
        # Every service running healthy on it (the job finished with 0): nothing to finish.
        plan = self.plan(self.installation(), history=self.staged())
        self.assertEqual((plan["decision"], plan["staged_version"]), (UP_TO_DATE, None))
        self.assertTrue([n for n in plan["notes"] if "already runs" in n], plan["notes"])
        installation = self.installation()
        self.container(installation, "postgres-schema-bootstrap")["exit_code"] = 1
        for service in ("backend", "worker"):
            self.container(installation, service)["status"] = "created"
        self.assertStillStaged(installation)

    def test_a_restarting_or_unhealthy_container_does_not_run_it(self):
        for field, value in (("status", "restarting"), ("health", "unhealthy"), ("status", "exited")):
            with self.subTest(field=field, value=value):
                installation = self.installation()
                self.container(installation, "backend")[field] = value
                if value == "exited":
                    self.container(installation, "backend")["exit_code"] = 137
                self.assertStillStaged(installation)

    def test_a_failed_one_shot_job_does_not_run_it(self):
        installation = self.installation()
        self.container(installation, "postgres-schema-bootstrap")["exit_code"] = 1
        self.assertStillStaged(installation)


class Round5RollbackSettledTests(PlanTestCase):
    """T57: a rollback that stopped part-way stops blocking once the stack runs, healthy, what the env file selects
    for every key it changed: finished by hand (its release) or abandoned (the values from before it)."""

    UNDONE = f"{STATE}/receipt-20261019T000001Z-1.json"

    def history(self) -> List[Dict[str, Any]]:
        update = receipt("1", env_changes=dict(EVERY_KEY), previous_values=dict(ALL_PREVIOUS))
        rollback = receipt("2", kind="rollback", status="failed_during_rollout", target_version=PREVIOUS,
                           rolls_back=self.UNDONE, from_version=TARGET, env_changes=dict(ALL_PREVIOUS),
                           previous_values=dict(EVERY_KEY))
        return [update, rollback]

    def test_a_stack_put_back_on_the_release_is_planned_as_usual(self):
        plan = self.plan(self.distributed(TARGET), history=self.history())
        self.assertEqual((plan["decision"], plan["reasons"]), (UP_TO_DATE, []))
        self.assertEqual(plan["notes"], [
            f"the rollback to v1.51.0 that stopped while containers were being replaced (receipt "
            f"{STATE}/receipt-20261019T000002Z-2.json) is abandoned: {_join_keys(EVERY_KEY)} in .env no longer select "
            "it and the stack runs what .env selects, so this plan does not finish it"])
        newer = self.plan(self.distributed(TARGET), history=self.history(), manifest=self.manifest(version="v1.53.0"))
        self.assertEqual((newer["decision"], newer["reasons"]), (UPDATE_AVAILABLE, []))
        self.assertIsNone(newer["stopped_version"])

    def test_a_rollback_finished_by_hand_is_finished(self):
        plan = self.plan(self.distributed(PREVIOUS), history=self.history())
        self.assertEqual((plan["decision"], plan["reasons"]), (UPDATE_AVAILABLE, []))
        self.assertIn(f"the rollback to v1.51.0 that stopped while containers were being replaced (receipt "
                      f"{STATE}/receipt-20261019T000002Z-2.json) was finished outside bin/update: .env selects it and "
                      "the stack runs it, so there is nothing of it to finish", plan["notes"])
        # It still blocks while the stack does not run, healthy, what the env file selects.
        cases = {
            "the rollback's release selected, the stack still on the update": self.distributed(PREVIOUS,
                                                                                               labels=EVERY_KEY),
            "part of the stack on each": self.distributed(TARGET, labels={"worker": PREVIOUS}),
        }
        crashing = self.distributed(TARGET)
        crashing._containers[0]["status"] = "restarting"
        cases["a container restarting"] = crashing
        for case, installation in cases.items():
            with self.subTest(case=case):
                plan = self.plan(installation, history=self.history())
                self.assertBlocked(plan)
                self.assertReasonContaining(plan, "a rollback stopped while containers were being replaced. Finish it "
                                                  f"first: ./bin/update rollback --receipt {self.UNDONE} --yes")


def _git_env() -> Dict[str, str]:
    # The tests' own git commands ignore the user's and the system's git configuration.
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_AUTHOR_NAME="Test",
               GIT_AUTHOR_EMAIL="test@example.com", GIT_COMMITTER_NAME="Test", GIT_COMMITTER_EMAIL="test@example.com")
    return env


@unittest.skipIf(shutil.which("git") is None, "git is not installed")
class Round5DriftGitTests(PlanTestCase):
    """T43-T47: in a git checkout the drift reason names what git shows differs and the command that fixes it; it
    never claims an untracked file is the cause unless git shows one."""

    DIRECTORY = "futureagi/scripts/property_catalog_oss"

    def git(self, *args: str) -> str:
        result = subprocess.run(["git", "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", "-C", str(self.root),
                                 *args], capture_output=True, text=True, env=_git_env(), check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def setUp(self) -> None:
        super().setUp()
        self.files = list(DEFAULT_FILES) + [self.DIRECTORY]
        self.write(".gitignore", "*.log\n")
        self.git("init", "-q")
        self.manifests: Dict[str, Dict[str, Any]] = {}
        for version, schema in ((PREVIOUS, "create table t();\n"), (TARGET, "create table t(id int);\n"),
                                ("v1.53.0", "create table t(id bigint);\n")):
            self.write(f"{self.DIRECTORY}/schema.sql", schema)
            self.git("add", "-A")
            self.git("commit", "-q", "-m", version)
            self.git("tag", version)
            self.manifests[version] = self.manifest(version=version, files=self.files)
        self.git("checkout", "-q", TARGET)

    def reason(self, **kwargs) -> str:
        installation = kwargs.pop("installation", None) or self.standalone(PREVIOUS)
        plan = self.plan(installation, manifest=self.manifests[TARGET], **kwargs)
        return self.assertReasonContaining(plan, "deployment files differ from")

    def test_a_file_changed_here_at_the_target_is_put_back_from_it(self):
        self.write(f"{self.DIRECTORY}/schema.sql", "edited\n")
        path = f"{self.DIRECTORY}/schema.sql"
        self.assertEqual(self.reason(), (
            f"this checkout's deployment files differ from v1.52.0's ({self.DIRECTORY}). This checkout is at v1.52.0, "
            f"so the differences were made here: {path} differs from v1.52.0's (`git checkout v1.52.0 -- {path}` in "
            f"{self.root} puts back v1.52.0's version, discarding the changes made here). Then check again"))
        # Reading git's status never rewrites the checkout's index.
        index = (self.root / ".git" / "index").read_bytes()
        self.reason()
        self.assertEqual((self.root / ".git" / "index").read_bytes(), index)

    def test_files_git_does_not_track_are_named(self):
        for name in ("bootstrap.log", "x.orig", ".DS_Store", "notes.txt~"):
            self.write(f"{self.DIRECTORY}/{name}", "x\n")
        reason = self.reason()
        self.assertTrue(reason.endswith(
            f"so the differences were made here: {self.DIRECTORY}/bootstrap.log, {self.DIRECTORY}/x.orig are not "
            "part of v1.52.0: move them out of the checkout. Then check again"), reason)
        self.assertNotIn("git fetch", reason)
        self.assertNotIn(".DS_Store", reason)

    def test_a_clean_checkout_of_another_release_claims_no_local_file(self):
        for checkout, newer in ((PREVIOUS, None), ("v1.53.0", None), ("v1.53.0", "v1.53.0")):
            with self.subTest(checkout=checkout, newer=newer):
                self.git("checkout", "-q", checkout)
                reason = self.reason(newer_release=newer)
                for claim in ("untracked", "not tracked by git", "ignored", "move", "made here", "holds a file"):
                    self.assertNotIn(claim, reason)
                if newer:
                    self.assertNotIn("git checkout", reason)
                else:
                    self.assertTrue(reason.endswith(
                        f"run `git fetch --tags && git checkout v1.52.0` in {self.root} (your env file is not tracked "
                        "and stays as it is), then check again"), reason)

    def test_changes_made_on_another_release_survive_its_checkout(self):
        self.git("checkout", "-q", PREVIOUS)
        self.write(f"{self.DIRECTORY}/schema.sql", "edited\n")
        self.write(f"{self.DIRECTORY}/x.orig", "x\n")
        path = f"{self.DIRECTORY}/schema.sql"
        reason = self.reason()
        self.assertEqual(reason, (
            f"this checkout's deployment files differ from v1.52.0's ({self.DIRECTORY}). First undo the changes made "
            f"here, which a checkout keeps: {path} differs from v1.52.0's (`git checkout v1.52.0 -- {path}` in "
            f"{self.root} puts back v1.52.0's version, discarding the changes made here); {self.DIRECTORY}/x.orig is "
            "not tracked by git: move it out of the checkout. New images must run with their own compose files: run "
            f"`git fetch --tags && git checkout v1.52.0` in {self.root} (your env file is not tracked and stays as it "
            "is), then check again"))

    def test_a_newer_release_waiting_puts_back_this_checkouts_own_files(self):
        self.git("checkout", "-q", "v1.53.0")
        self.write(f"{self.DIRECTORY}/schema.sql", "edited\n")
        path = f"{self.DIRECTORY}/schema.sql"
        reason = self.reason(newer_release="v1.53.0")
        self.assertTrue(reason.endswith(f". git shows changes made in this checkout: {path} was changed (`git checkout "
                                        f"HEAD -- {path}` in {self.root} puts back this checkout's version)"), reason)
        self.assertNotIn("git checkout v1.52.0", reason)

    def test_the_listing_does_not_depend_on_the_operators_git_configuration(self):
        self.git("config", "status.showUntrackedFiles", "no")
        for index in range(10):
            self.write(f"{self.DIRECTORY}/extra-{index}.orig", "x\n")
        reason = self.reason()
        self.assertIn(f"{self.DIRECTORY}/extra-7.orig and 2 more are not part of v1.52.0", reason)
        self.assertIn(f"`git status --untracked-files=all --ignored -- {self.DIRECTORY}` in {self.root} lists every "
                      "one", reason)

    def test_commands_cd_to_the_checkout_and_quote_paths(self):
        directory = "deploy/my scripts"
        self.write(f"{directory}/run.sh", "echo 1\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "scripts")
        self.git("tag", "-f", TARGET)
        manifest = self.manifest(files=self.files + [directory])
        self.write(f"{directory}/run.sh", "echo 2\n")
        installation = self.standalone(PREVIOUS)
        installation.caller_cwd = self.root.parent  # type: ignore[attr-defined]
        reason = self.assertReasonContaining(self.plan(installation, manifest=manifest), "differ from")
        cd = f"cd {shlex.quote(str(self.root))} && "
        self.assertIn(f"(`{cd}git checkout v1.52.0 -- 'deploy/my scripts/run.sh'` puts back", reason)
        self.assertNotIn(f" in {self.root}", reason)

    def test_a_missing_tag_is_fetched_first(self):
        self.git("checkout", "-q", PREVIOUS)
        self.git("tag", "-d", TARGET)
        self.write(f"{self.DIRECTORY}/schema.sql", "edited\n")
        path = f"{self.DIRECTORY}/schema.sql"
        self.assertIn(f"`git fetch --tags && git checkout v1.52.0 -- {path}` in {self.root} puts back", self.reason())

    def test_a_checkout_inside_another_repository_is_no_git_checkout_of_its_own(self):
        # Only the top level of a work tree is asked: a checkout nested in another repository gets the plain wording.
        nested = self.root / "nested"
        for relative in DEFAULT_FILES:
            target = nested / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((self.root / relative).read_bytes())
        (nested / ".git").mkdir()
        installation = FakeInstallation(nested, services={"app": f"futureagi/standalone:{PREVIOUS}"},
                                        env={"FUTURE_AGI_VERSION": PREVIOUS, "SECRET_KEY": "x"}).run("app", PREVIOUS)
        manifest = make_manifest(nested)
        (nested / "docker-compose.yml").write_text("changed\n", encoding="utf-8")
        reason = self.assertReasonContaining(self.plan(installation, manifest=manifest), "differ from")
        self.assertIn("run `git fetch --tags && git checkout v1.52.0`", reason)
        self.assertNotIn("made here", reason)



# ------------------------------------------------------- integrator, round 5 --

from unittest import mock  # noqa: E402

import deploy.update.plan as plan_module  # noqa: E402


class IntegratorRound5StackRemedyTests(PlanTestCase):
    """T55 (plan side): while check_stack says the containers are not this stack's, its remedy comes alone; the
    version-key remedies would name keys of an env file the install may not use."""

    OVERLAY_REASON = ("this install runs the production overlay (check-only bootstrap jobs). Use `./bin/update "
                      "--production check`, which uses deploy/.env.production and the overlay as deploy/setup.sh "
                      "does")

    def installation(self, docker: FakeDocker, *, env: str, production: bool = False) -> Installation:
        return RealInstallationTests.installation(self, docker, env=env, production=production)

    def overlay_containers(self, docker: FakeDocker, label: str = PREVIOUS, *, check: bool = True) -> FakeDocker:
        docker.container("backend", f"futureagi/future-agi:{PREVIOUS}", label)
        docker.container("frontend", f"futureagi/frontend:{PREVIOUS}", label)
        return docker.container("postgres-schema-bootstrap", f"futureagi/future-agi:{PREVIOUS}", label,
                                command=["python", "bootstrap.py"] + (["--check"] if check else []))

    def test_a_production_install_checked_without_production_is_told_only_to_add_it(self):
        # The checkout also has a .env (FUTURE_AGI_VERSION unset): the Standalone app it selects has no container.
        self.write("deploy/.env.production", f"FUTURE_AGI_VERSION={PREVIOUS}\nFRONTEND_VERSION={PREVIOUS}\n")
        docker = self.overlay_containers(FakeDocker({"name": "futureagi", "services": {
            "app": {"image": "futureagi/standalone:latest"}}}))
        plan = self.plan(self.installation(docker, env="FUTURE_AGI_VERSION=\nSECRET_KEY=abc\n"))
        self.assertBlocked(plan)
        self.assertEqual(plan["reasons"], [self.OVERLAY_REASON])
        self.assertNotIn("Set FUTURE_AGI_VERSION", plan_text(plan))

    def test_production_given_to_a_stack_started_without_the_overlay_gets_that_remedy_alone(self):
        # The env file selects v1.51.0 while the containers run v1.50.0: no `up -d` remedy for a stack that is
        # not the overlay's.
        services = {"backend": {"image": f"futureagi/future-agi:{PREVIOUS}"},
                    "frontend": {"image": f"futureagi/frontend:{PREVIOUS}"},
                    "postgres-schema-bootstrap": {"image": f"futureagi/future-agi:{PREVIOUS}"}}
        docker = self.overlay_containers(FakeDocker({"name": "fagi-prod", "services": services}), "v1.50.0",
                                         check=False)
        installation = self.installation(docker, production=True, env=(
            f"FUTURE_AGI_VERSION={PREVIOUS}\nFRONTEND_VERSION={PREVIOUS}\nPG_PASSWORD=pw-value\n"))
        plan = self.plan(installation)
        self.assertBlocked(plan)
        self.assertEqual(plan["reasons"], [
            "--production was given, but this install was started without the production overlay (its bootstrap "
            "jobs apply migrations). Run bin/update without --production"])

    def test_a_stopped_latest_install_is_told_how_to_start_it_and_nothing_else(self):
        docker = FakeDocker({"name": "futureagi", "services": {"app": {"image": "futureagi/standalone:latest"}}})
        plan = self.plan(self.installation(docker, env="FUTURE_AGI_VERSION=\nSECRET_KEY=abc\n"))
        self.assertBlocked(plan)
        self.assertEqual(len(plan["reasons"]), 1, plan["reasons"])
        self.assertTrue(plan["reasons"][0].startswith("no containers of Compose project 'futureagi' exist"),
                        plan["reasons"])

    def test_without_a_stack_reason_the_version_key_remedy_stays(self):
        docker = FakeDocker({"name": "futureagi", "services": {"app": {"image": "futureagi/standalone:latest"}}})
        docker.container("app", "futureagi/standalone:latest", "")
        plan = self.plan(self.installation(docker, env="FUTURE_AGI_VERSION=\nSECRET_KEY=abc\n"))
        self.assertReasonContaining(plan, "cannot tell which version of standalone is installed: FUTURE_AGI_VERSION "
                                          "is unset and no running container carries a version label. Set "
                                          "FUTURE_AGI_VERSION=vX.Y.Z to the release you run")


@unittest.skipIf(shutil.which("git") is None, "git is not installed")
class IntegratorRound5GitEdgeTests(PlanTestCase):
    """The edges of the git helpers behind the drift reason (T43-T47) and of a settled rollback (T57)."""

    def git(self, *args: str) -> str:
        return Round5DriftGitTests.git(self, *args)  # type: ignore[arg-type]

    def release(self) -> None:
        """This checkout as a git work tree at the tag TARGET."""

        self.git("init", "-q")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", TARGET)
        self.git("tag", TARGET)

    def test_git_that_cannot_run_answers_nothing(self):
        with mock.patch.object(plan_module.subprocess, "run", side_effect=OSError(8, "Exec format error")):
            self.assertIsNone(plan_module._git(self.root, "status"))
        self.assertIsNone(plan_module._git(self.root, "status"))  # not a work tree: git fails

    def test_a_renamed_entry_is_one_record(self):
        self.assertEqual(plan_module._git_records("R  new.yml\0old.yml\0?? extra.yml\0", renames=True),
                         [("R ", "new.yml"), ("??", "extra.yml")])

    def test_git_that_stops_answering_or_a_top_level_that_cannot_be_resolved_is_no_git(self):
        (self.root / ".git").mkdir()
        top = f"{self.root}\n"
        answers = {"rev-parse": top, "status": None}
        with mock.patch.object(plan_module, "_git", side_effect=lambda root, *args: answers.get(args[0])):
            self.assertIsNone(plan_module._git_drift(self.root, ["docker-compose.yml"], TARGET))
        with mock.patch.object(plan_module, "_git", return_value=top), \
                mock.patch.object(Path, "resolve", side_effect=RuntimeError("Symlink loop")):
            self.assertIsNone(plan_module._git_drift(self.root, ["docker-compose.yml"], TARGET))

    def test_a_deleted_tracked_file_is_reported_as_drift_at_the_target(self):
        self.release()
        (self.root / "docker-compose.yml").unlink()

        found = plan_module._git_drift(self.root, ["docker-compose.yml"], TARGET)

        self.assertEqual(found["changed"], ["docker-compose.yml"])
        self.assertEqual(found["local_changed"], ["docker-compose.yml"])
        self.assertEqual(found["gone"], [])
        reason = plan_module._drift_reason(self.standalone(PREVIOUS), ["docker-compose.yml"], TARGET, None)
        self.assertIn("git checkout v1.52.0 -- docker-compose.yml", reason)

    def test_a_missing_git_hidden_listing_falls_back_to_the_general_diagnosis(self):
        self.release()
        original_git = plan_module._git

        def git_without_listing(root, *args):
            if args and args[0] == "ls-files":
                return None
            return original_git(root, *args)

        with mock.patch.object(plan_module, "_git", side_effect=git_without_listing):
            reason = plan_module._drift_reason(self.standalone(PREVIOUS), ["docker-compose.yml"], TARGET, None)

        self.assertIn("Possible causes: git converts their line endings", reason)
        self.assertIn("git ls-files -v -- docker-compose.yml", reason)
        self.assertNotIn("git does not compare docker-compose.yml", reason)

    def test_without_the_diff_the_changes_git_status_shows_count(self):
        (self.root / ".git").mkdir()

        def answer(root, *args):
            if args[:2] == ("rev-parse", "--show-toplevel"):
                return f"{self.root}\n"
            if args[0] == "status":
                return " M docker-compose.yml\0?? deploy/clickhouse/x.xml\0?? deploy/other/y.xml\0"
            if args[0] == "rev-parse":
                return "c0ffee\n"
            return None  # git diff fails

        with mock.patch.object(plan_module, "_git", side_effect=answer):
            found = plan_module._git_drift(self.root, ["docker-compose.yml", "deploy/clickhouse/"], TARGET)
        # deploy/other is no entry that differs: git listed it, but the digest of these entries does not count it.
        self.assertEqual(found, {"at_target": True, "tag": True, "changed": ["docker-compose.yml"], "gone": [],
                                 "added": [], "extras": ["deploy/clickhouse/x.xml"],
                                 "local_changed": ["docker-compose.yml"]})

    def test_a_file_entry_git_does_not_track_is_named(self):
        self.release()
        self.write("deploy/extra.conf", "mine\n")
        found = plan_module._git_drift(self.root, ["deploy/extra.conf"], TARGET)
        self.assertEqual((found["at_target"], found["changed"], found["extras"]), (True, [], ["deploy/extra.conf"]))

    def test_at_the_target_without_a_change_git_shows_line_endings_are_named(self):
        self.release()
        reason = plan_module._drift_reason(self.standalone(PREVIOUS), ["docker-compose.yml"], TARGET, None)
        # Round 6 (U22, U25): line endings are one possible cause; a file git does not compare is the other.
        self.assertEqual(reason, (
            f"this checkout's deployment files differ from {TARGET}'s (docker-compose.yml). This checkout is at "
            f"{TARGET}, yet git shows no change in them. Possible causes: git converts their line endings "
            "(core.autocrlf, or .gitattributes), or git does not compare a file with the work tree (marked "
            "assume-unchanged or skip-worktree, as a sparse checkout marks the files it leaves out): `git ls-files -v "
            f"-- docker-compose.yml` in {self.root} shows such a file with a lowercase letter or S. Fix the cause, "
            "then check again"))

    def test_at_the_target_with_a_newer_release_waiting_the_changes_were_made_here(self):
        self.release()
        self.write("docker-compose.yml", "services: {}\n")
        reason = plan_module._drift_reason(self.standalone(PREVIOUS), ["docker-compose.yml"], TARGET, "v1.53.0")
        self.assertTrue(reason.endswith(
            f". This checkout is at {TARGET}, so they were made here: docker-compose.yml differs from {TARGET}'s "
            f"(`git checkout {TARGET} -- docker-compose.yml` in {self.root} puts back {TARGET}'s version, discarding "
            "the changes made here)"), reason)

    def test_a_rollback_whose_keys_follow_latest_is_not_settled(self):
        services = [{"service": "app", "component": "standalone", "ref": parse_image_ref("futureagi/standalone:latest")}]
        record = {"env_changes": {"FUTURE_AGI_VERSION": PREVIOUS}}
        self.assertIsNone(plan_module._settled_rollback(record, services, {}, {}, {}))



# ------------------------------------------------------------------ round 6 --

ROUND6_RULE = ("Run it from a shell that does not export any key of deploy/.env.production (Compose prefers your shell "
               "over the env file).")
OVERLAY_STACK_REASON = ("this install runs the production overlay (check-only bootstrap jobs). Use `./bin/update "
                        "--production check`, which uses deploy/.env.production and the overlay as deploy/setup.sh "
                        "does")


class Round6ShellRuleTests(PlanTestCase):
    """H3 (U4, U5, U6): every production Compose command printed to run by hand is followed by one sentence that says
    why (Compose prefers the shell over the env file), and never credits deploy/setup.sh with it."""

    def production(self, version: str = PREVIOUS, **kwargs) -> FakeInstallation:
        return self.distributed(version, mode="production", production=True, **kwargs)

    def test_the_rule_is_one_sentence(self):
        installation = self.production()
        self.assertEqual(plan_module.shell_rule(installation), ROUND6_RULE)
        self.assertEqual(jobs_text(installation),  # type: ignore[arg-type]
                         f"`{PRODUCTION_HINT} run --rm ...` (deploy/README.md › Upgrading a production install. "
                         f"{ROUND6_RULE})")
        self.assertEqual(plan_module.production_procedure(installation),  # type: ignore[arg-type]
                         f"deploy/README.md › Upgrading a production install, with `{PRODUCTION_HINT} ...` for its "
                         f"$COMPOSE commands. {ROUND6_RULE}")

    def test_no_production_pointer_credits_deploy_setup_sh(self):
        plans = {
            "two steps": self.plan(self.production()),
            "staged": self.plan(self.production(TARGET, labels=ALL_PREVIOUS), history=[receipt("1", status=STAGED)]),
            "mixed versions": self.plan(self.production(TARGET, labels={"worker": PREVIOUS})),
            "below the floor": self.plan(self.production("v1.50.0"), manifest=self.manifest(minimum=PREVIOUS)),
        }
        for case, plan in plans.items():
            with self.subTest(case=case):
                printed = plan_text(plan) + plan_json(plan)
                self.assertIn(ROUND6_RULE, plan_text(plan))
                self.assertNotIn("setup.sh", printed)
                self.assertNotIn("take every key", printed)

    def test_a_production_stray_s_removal_carries_the_rule(self):
        installation = self.production()
        installation.profile_services["worker-default"] = WORKER_RAW
        installation.run("worker-default", PREVIOUS, image=f"futureagi/future-agi:{PREVIOUS}")
        reason = self.assertReasonContaining(self.plan(installation), "service worker-default has containers")
        self.assertTrue(reason.endswith(f"`{PRODUCTION_HINT} rm -sf worker-default`. {ROUND6_RULE} Then check again"),
                        reason)
        installation = self.production(TARGET)
        installation.profile_services["worker-default"] = WORKER_RAW
        installation.run("worker-default", PREVIOUS, image=f"futureagi/future-agi:{PREVIOUS}")
        note = [n for n in self.plan(installation)["notes"] if n.startswith("bin/update leaves these containers")][0]
        self.assertTrue(note.endswith(f"`{PRODUCTION_HINT} rm -sf worker-default`. {ROUND6_RULE}"), note)
        # Outside production bin/update refuses exported keys itself: no rule.
        installation = self.distributed(PREVIOUS)
        installation.profile_services["worker-default"] = WORKER_RAW
        installation.run("worker-default", PREVIOUS, image=f"futureagi/future-agi:{PREVIOUS}")
        reason = self.assertReasonContaining(self.plan(installation), "service worker-default has containers")
        self.assertTrue(reason.endswith("`docker compose rm -sf worker-default`, then check again"), reason)


class Round6AnotherReleaseTests(PlanTestCase):
    """H4 (U2, U3, U7, U8, U14, U15, U37): every reason that finishes an update of another release gives, in order,
    its checkout, its initialization jobs (production), the apply with its manifest and the channel its receipt
    records, and the way back."""

    NEWER = "v1.53.0"
    UNDO = f"rollback --receipt {STATE}/receipt-20261019T000001Z-1.json --yes"

    def production(self, version: str = TARGET, **kwargs) -> FakeInstallation:
        return self.distributed(version, mode="production", production=True, **kwargs)

    def assertInOrder(self, text: str, *parts: str) -> None:
        found = [text.find(part) for part in parts]
        self.assertNotIn(-1, found, f"{parts} in {text}")
        self.assertEqual(found, sorted(found), text)

    def test_a_staged_update_is_finished_on_the_channel_it_was_staged_from(self):
        staged = receipt("1", status=STAGED, channel="beta", manifest_source="/srv/beta.json")
        plan = self.plan(self.standalone(TARGET, label=PREVIOUS), manifest=self.manifest(version=self.NEWER),
                         history=[staged])
        reason = self.assertReasonContaining(plan, "is staged")
        self.assertIn("then ./bin/update apply --manifest /srv/beta.json --channel beta) or undo it (./bin/update "
                      f"{self.UNDO}), then check again", reason)
        # A receipt of the channel this install follows needs no option (and one that records none gets none).
        for record in (receipt("1", status=STAGED, channel="stable", manifest_source="/srv/stable.json"),
                       receipt("1", status=STAGED, manifest_source="/srv/stable.json")):
            with self.subTest(record=record):
                plan = self.plan(self.standalone(TARGET, label=PREVIOUS), manifest=self.manifest(version=self.NEWER),
                                 history=[record])
                self.assertNotIn("--channel", self.assertReasonContaining(plan, "is staged"))
        # An install that follows beta finishes a beta staging run without the option.
        plan = self.plan(self.standalone(TARGET, label=PREVIOUS), manifest=self.manifest(version=self.NEWER),
                         history=[staged], settings=self.settings(channel="beta"))
        self.assertIn("then ./bin/update apply --manifest /srv/beta.json) or undo it",
                      self.assertReasonContaining(plan, "is staged"))

    def test_a_stopped_update_of_another_release_is_finished_from_its_checkout(self):
        record = receipt("1", status="failed_during_rollout", channel="beta", manifest_source="/srv/v1.52.0.json")
        reason = self.assertReasonContaining(
            self.plan(self.standalone(TARGET, label=PREVIOUS), manifest=self.manifest(version=self.NEWER),
                      history=[record]), "stopped while containers were starting")
        self.assertIn("Finish it (check out v1.52.0 with `git checkout v1.52.0`, or at least its deployment files "
                      "(your env file stays as it is), then ./bin/update apply --manifest /srv/v1.52.0.json --channel "
                      f"beta) or roll it back (./bin/update {self.UNDO}) first", reason)
        reason = self.assertReasonContaining(
            self.plan(self.production(labels=ALL_PREVIOUS), manifest=self.manifest(version=self.NEWER),
                      history=[record]), "stopped while containers were starting")
        self.assertInOrder(reason, "check out v1.52.0 with `git checkout v1.52.0`",
                           f"run its initialization jobs with {jobs_text(self.production())} if they have not run yet",
                           "then ./bin/update --production apply --manifest /srv/v1.52.0.json --channel beta)",
                           f"or roll it back (./bin/update --production {self.UNDO})")

    def dropped(self, **fields) -> Dict[str, Any]:
        return receipt("1", status="failed_during_rollout", env_changes=dict(EVERY_KEY),
                       manifest_source="/srv/v1.52.0.json", **fields)

    def test_a_dropped_update_of_another_release_is_finished_from_its_checkout(self):
        keys = ", ".join(f"{key}=v1.52.0" for key in sorted(EVERY_KEY))
        for production in (False, True):
            with self.subTest(production=production):
                installation = (self.production(PREVIOUS, labels={"backend": TARGET}) if production
                                else self.distributed(PREVIOUS, labels={"backend": TARGET}))
                env = "deploy/.env.production" if production else ".env"
                command = "./bin/update --production" if production else "./bin/update"
                plan = self.plan(installation, history=[self.dropped(channel="beta")],
                                 manifest=self.manifest(version=self.NEWER))
                reason = self.assertReasonContaining(plan, "Finish that update")
                self.assertInOrder(reason, "Finish that update rather than restarting the stack by hand: check out "
                                           "v1.52.0 with `git checkout v1.52.0`, or at least its deployment files "
                                           "(your env file stays as it is), ",
                                   f"set {keys} in {env} again, ",
                                   *([f"run its initialization jobs with {jobs_text(installation)} if they have not "
                                      "run yet, "] if production else []),
                                   f"then run `{command} apply --manifest /srv/v1.52.0.json --channel beta`, which "
                                   "finishes it",
                                   f"; or undo it with `{command} {self.UNDO}`")
                self.assertTrue(reason.endswith(f"; or undo it with `{command} {self.UNDO}`"), reason)

    def test_a_dropped_update_of_this_release_names_the_way_back_too(self):
        plan = self.plan(self.production(PREVIOUS, labels={"backend": TARGET}), history=[self.dropped()])
        reason = self.assertReasonContaining(plan, "Finish that update")
        self.assertNotIn("git checkout", reason)
        self.assertTrue(reason.endswith("then run `./bin/update --production apply`, which finishes it; or undo it "
                                        f"with `./bin/update --production {self.UNDO}`"), reason)

    def test_a_dropped_staged_update_is_not_said_to_have_started_anything(self):
        staged = receipt("1", status=STAGED, env_changes=dict(EVERY_KEY), manifest_source="/srv/v1.52.0.json")
        plan = self.plan(self.distributed(PREVIOUS, labels={"backend": TARGET}), history=[staged],
                         manifest=self.manifest(version=self.NEWER))
        reason = self.assertReasonContaining(plan, "Finish that update")
        self.assertTrue(reason.startswith(
            f"backend runs v1.52.0, the release of the update staged earlier (receipt {STATE}/receipt-"
            "20261019T000001Z-1.json), but "), reason)
        self.assertNotIn("left backend running", reason)
        self.assertIn("check out v1.52.0 with `git checkout v1.52.0`", reason)


class Round6RollbackNoteTests(PlanTestCase):
    """U12, U16: the note of an abandoned rollback names only the keys put back by hand."""

    def test_only_the_keys_put_back_are_named(self):
        history = [receipt("1", env_changes=dict(EVERY_KEY), previous_values=dict(ALL_PREVIOUS)),
                   receipt("2", kind="rollback", status="failed_during_rollout", target_version=PREVIOUS,
                           rolls_back=f"{STATE}/receipt-20261019T000001Z-1.json", env_changes=dict(ALL_PREVIOUS))]
        plan = self.plan(self.distributed(PREVIOUS, tags={"FUTURE_AGI_VERSION": TARGET}), history=history)
        self.assertIn(f"the rollback to v1.51.0 that stopped while containers were being replaced (receipt "
                      f"{STATE}/receipt-20261019T000002Z-2.json) is abandoned: FUTURE_AGI_VERSION in .env no longer "
                      "selects it and the stack runs what .env selects, so this plan does not finish it", plan["notes"])


class Round6AheadRowTests(PlanTestCase):
    """U13, U17: with nothing to do (`ahead`), an unfinished component's current_source says what its containers do."""

    def source(self, installation: FakeInstallation) -> str:
        plan = self.plan(installation, manifest=self.manifest(version="v1.51.5"), history=[receipt("1", status=STAGED)],
                         newer_release="v1.53.0")
        item = self.component(plan, "standalone")
        self.assertEqual((item["current_version"], item["action"]), (TARGET, "keep"))
        return item["current_source"]

    def test_current_source_says_what_was_found(self):
        cases = {}
        for status in ("paused", "exited"):
            installation = self.standalone(TARGET, label=PREVIOUS)
            installation._containers[0]["status"] = status
            cases[status] = installation
        cases["none"] = self.standalone(TARGET, label=PREVIOUS)
        cases["none"]._containers = [c for c in cases["none"]._containers if c["service"] != "app"]
        cases["unlabelled"] = self.standalone(TARGET, label="")
        cases["running"] = self.standalone(TARGET, label=PREVIOUS)
        selects = "FUTURE_AGI_VERSION in .env (the staged v1.52.0; "
        for case, expected in (("paused", "its paused containers are on v1.51.0"),
                               ("exited", "its stopped containers are on v1.51.0"),
                               ("none", "no container"),
                               ("unlabelled", "its running containers carry no version label"),
                               ("running", "its containers run v1.51.0")):
            with self.subTest(case=case):
                self.assertEqual(self.source(cases[case]), f"{selects}{expected})")


class Round6StrayModelTests(PlanTestCase):
    """U18 (T34): a stray whose service an incomplete service model does not list may be no profile's: it is
    removed by container id, as the rollback words it, never by a service name Compose may reject."""

    def test_a_running_stray_blocks_with_its_container_id(self):
        installation = self.distributed(PREVIOUS)
        installation.run("worker-default", PREVIOUS, image=f"futureagi/future-agi:{PREVIOUS}")
        stray = installation._containers[-1]["id"]
        installation.model_complete = False
        reason = self.assertReasonContaining(self.plan(installation), "service worker-default has containers")
        self.assertEqual(reason, (
            "service worker-default has containers on Future AGI images but is not in the active Compose "
            "configuration, and Compose could not resolve every profile, so bin/update cannot tell whether "
            "worker-default belongs to a profile that is not enabled now or is defined by no compose file any more: "
            "worker-default (running, v1.51.0). The update to v1.52.0 does not replace its containers, so they would "
            f"keep running as they are next to it. Remove its containers with `docker rm -f {stray}`, then check "
            "again"))

    def test_an_idle_stray_is_noted_with_its_container_id(self):
        installation = self.distributed(TARGET)
        installation.run("worker-default", PREVIOUS, image=f"futureagi/future-agi:{PREVIOUS}", status="exited")
        stray = installation._containers[-1]["id"]
        installation.model_complete = False
        plan = self.plan(installation)
        self.assertEqual((plan["decision"], plan["reasons"]), (UP_TO_DATE, []))
        note = [n for n in plan["notes"] if "worker-default" in n]
        self.assertEqual(note, [
            "bin/update leaves these containers of a service outside the active Compose configuration as they are: "
            "worker-default (stopped, v1.51.0). Compose could not resolve every profile, so bin/update cannot tell "
            "whether it belongs to a profile that is not enabled now or is defined by no compose file any more; to "
            f"remove them, run `docker rm -f {stray}`"])

    def test_a_listed_and_an_unlisted_stray_each_get_their_own_removal(self):
        installation = self.distributed(PREVIOUS)
        installation.run("worker-default", PREVIOUS, image=f"futureagi/future-agi:{PREVIOUS}")
        stray = installation._containers[-1]["id"]
        installation.model_complete = False
        installation.profile_services["worker-tasks"] = WORKER_RAW
        installation.run("worker-tasks", PREVIOUS, image=f"futureagi/future-agi:{PREVIOUS}")

        def model(refresh: bool = False) -> Dict[str, Any]:
            # Compose resolved the active services and worker-tasks' profile, not worker-default's.
            return {"services": {**{name: {} for name in installation._config["services"]}, "worker-tasks": {}},
                    "complete": False}

        installation.service_model = model  # type: ignore[assignment]
        reason = self.assertReasonContaining(self.plan(installation), "services worker-default, worker-tasks have")
        self.assertIn("(worker-tasks: a profile that is not enabled now; Compose could not resolve every profile, so "
                      "bin/update cannot tell whether worker-default belongs to one or is defined by no compose file "
                      "any more)", reason)
        self.assertTrue(reason.endswith("or remove it with `docker compose rm -sf worker-tasks`; remove the containers "
                                        f"of worker-default with `docker rm -f {stray}`, then check again"), reason)


class Round6StackReasonTests(PlanTestCase):
    """U40: while check_stack says the containers are another stack's, no missing-keys remedy follows (./bin/install
    would write a Distributed .env, with new secrets, onto a production install)."""

    def test_the_stack_reason_comes_alone(self):
        installation = self.standalone(PREVIOUS, stack_reasons=[OVERLAY_STACK_REASON])
        del installation.env["SECRET_KEY"]
        plan = self.plan(installation)
        self.assertEqual((plan["reasons"], plan["missing_env_keys"]), ([OVERLAY_STACK_REASON], []))
        self.assertNotIn("bin/install", plan_text(plan))
        # Without the stack reason the same .env still gets its remedy.
        installation.stack_reasons = []
        self.assertReasonContaining(self.plan(installation), ".env lacks settings this release's .env.example adds")


class Round6DriftWithoutGitTests(PlanTestCase):
    """U26, U33: without a git work tree git can use, the drift reason prints no git command and never asks to move a
    newer release's own files out of the checkout."""

    DIRECTORY = "futureagi/scripts/property_catalog_oss"

    def test_no_git_command_without_a_work_tree(self):
        manifest = self.manifest()
        self.write("docker-compose.yml", "changed\n")
        for case in ("no repository", "a .git file git cannot read"):
            with self.subTest(case=case):
                if case != "no repository":
                    self.write(".git", "gitdir: /nonexistent/repository\n")
                reason = self.assertReasonContaining(self.plan(self.standalone(PREVIOUS), manifest=manifest),
                                                     "differ from")
                self.assertNotIn("git checkout", reason)
                self.assertNotIn("git fetch", reason)
                self.assertIn(f"git cannot be used in this checkout ({self.root}) to put it back: replace it with "
                              "release v1.52.0's version (your env file stays as it is), then check again", reason)
        if shutil.which("git") is None:
            return
        # A directory of another work tree (no .git of its own): git works there, so its command is printed.
        (self.root / ".git").unlink()
        subprocess.run(["git", "-C", str(self.root), "init", "-q"], check=True, env=_git_env())
        nested = self.root / "nested"
        for relative in DEFAULT_FILES:
            (nested / relative).parent.mkdir(parents=True, exist_ok=True)
            (nested / relative).write_bytes((self.root / relative).read_bytes())
        manifest = make_manifest(nested)
        (nested / "docker-compose.yml").write_text("changed in the nested checkout\n", encoding="utf-8")
        installation = FakeInstallation(nested, services={"app": f"futureagi/standalone:{PREVIOUS}"},
                                        env={"FUTURE_AGI_VERSION": PREVIOUS, "SECRET_KEY": "x"}).run("app", PREVIOUS)
        installation.caller_cwd = self.root  # type: ignore[attr-defined]
        reason = self.assertReasonContaining(self.plan(installation, manifest=manifest), "differ from")
        self.assertIn(f"run `cd {shlex.quote(str(nested))} && git fetch --tags && git checkout v1.52.0` (your env file "
                      "is not tracked", reason)

    def test_a_newer_release_s_files_are_never_moved_out(self):
        self.write(f"{self.DIRECTORY}/schema.sql", "create table t();\n")
        manifest = self.manifest(files=list(DEFAULT_FILES) + [self.DIRECTORY])
        self.write(f"{self.DIRECTORY}/new.sql", "create table n();\n")
        reason = self.assertReasonContaining(
            self.plan(self.standalone(PREVIOUS), manifest=manifest, newer_release="v1.53.0"), "differ from")
        self.assertTrue(reason.endswith("or pass --manifest with the manifest URL of the release this checkout is on"),
                        reason)
        self.assertNotIn("move", reason)


@unittest.skipIf(shutil.which("git") is None, "git is not installed")
class Round6DriftGitTests(PlanTestCase):
    """U22-U25: drift git does not show (assume-unchanged, skip-worktree, a sparse checkout), files added to git's
    index, and files changed here that the release removed are named, each with a command git accepts."""

    DIRECTORY = "futureagi/scripts/property_catalog_oss"

    def git(self, *args: str) -> str:
        return Round5DriftGitTests.git(self, *args)  # type: ignore[arg-type]

    def setUp(self) -> None:
        super().setUp()
        self.files = list(DEFAULT_FILES) + [self.DIRECTORY]
        self.git("init", "-q")
        self.manifests: Dict[str, Dict[str, Any]] = {}
        for version, schema in ((PREVIOUS, "create table t();\n"), (TARGET, "create table t(id int);\n"),
                                ("v1.53.0", "create table t(id bigint);\n")):
            self.write(f"{self.DIRECTORY}/schema.sql", schema)
            if version == PREVIOUS:
                self.write(f"{self.DIRECTORY}/old.sql", "create table old();\n")
            elif (self.root / self.DIRECTORY / "old.sql").exists():
                (self.root / self.DIRECTORY / "old.sql").unlink()
            self.git("add", "-A")
            self.git("commit", "-q", "-m", version)
            self.git("tag", version)
            self.manifests[version] = self.manifest(version=version, files=self.files)
        self.git("checkout", "-q", TARGET)

    def plan_now(self) -> Dict[str, Any]:
        return self.plan(self.standalone(PREVIOUS), manifest=self.manifests[TARGET])

    def reason(self) -> str:
        return self.assertReasonContaining(self.plan_now(), "deployment files differ from")

    def run_printed(self, command: str) -> subprocess.CompletedProcess:
        return subprocess.run(command, shell=True, cwd=str(self.root), env=_git_env(), capture_output=True, text=True,
                              check=False)

    def assertRemedyClears(self, reason: str, marker: str) -> None:
        """Run the command the reason prints after `marker`, then the one the next check prints: no drift is left."""

        command = reason.split(marker, 1)[1].split("`", 1)[0]
        result = self.run_printed(command)
        self.assertEqual(result.returncode, 0, result.stderr)
        again = self.reason()
        path = f"{self.DIRECTORY}/schema.sql"
        self.assertIn(f"{path} differs from v1.52.0's (`git checkout v1.52.0 -- {path}` in {self.root} puts back",
                      again)
        result = self.run_printed(f"git checkout v1.52.0 -- {path}")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.plan_now()["deployment_drift"], [])

    def test_a_skip_worktree_file_edited_at_the_target_is_named(self):
        path = f"{self.DIRECTORY}/schema.sql"
        self.git("update-index", "--skip-worktree", path)
        self.write(path, "edited\n")
        reason = self.reason()
        self.assertEqual(reason, (
            f"this checkout's deployment files differ from v1.52.0's ({self.DIRECTORY}). This checkout is at v1.52.0, "
            f"yet git shows no change in them: git does not compare {path} with the file here (it is marked "
            "assume-unchanged or skip-worktree, as a sparse checkout marks the files it leaves out). Run `git "
            f"update-index --no-assume-unchanged -- {path} && git update-index --no-skip-worktree -- {path}` in "
            f"{self.root} so git compares it again, then check again: it names what differs"))
        self.assertNotIn("autocrlf", reason)
        self.assertRemedyClears(reason, "Run `")

    def test_an_assume_unchanged_file_edited_at_the_target_is_named(self):
        path = f"{self.DIRECTORY}/schema.sql"
        self.git("update-index", "--assume-unchanged", path)
        self.write(path, "edited\n")
        reason = self.reason()
        self.assertIn(f"git does not compare {path} with the file here", reason)
        self.assertRemedyClears(reason, "Run `")

    def test_a_directory_a_sparse_checkout_leaves_out_is_named(self):
        path = f"{self.DIRECTORY}/schema.sql"
        self.git("config", "core.sparseCheckout", "true")
        sparse = Path(self.git("rev-parse", "--git-path", "info/sparse-checkout").strip())
        sparse = sparse if sparse.is_absolute() else self.root / sparse
        sparse.parent.mkdir(parents=True, exist_ok=True)
        sparse.write_text(f"/*\n!/{self.DIRECTORY}/\n", encoding="utf-8")
        self.git("read-tree", "-mu", "HEAD")
        self.assertFalse((self.root / path).exists())
        reason = self.reason()
        self.assertIn(f"yet git shows no change in them: git does not compare {path} with the file here", reason)
        self.assertIn(f"`git update-index --no-assume-unchanged -- {path} && git update-index --no-skip-worktree -- "
                      f"{path}`", reason)

    def test_many_hidden_files_are_cleared_from_gits_own_listing(self):
        for index in range(10):
            self.write(f"{self.DIRECTORY}/extra-{index}.sql", "x\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "extras")
        self.git("tag", "-f", TARGET)
        self.manifests[TARGET] = self.manifest(files=self.files)
        listing = self.git("ls-files", "--", self.DIRECTORY).split()
        self.git("update-index", "--skip-worktree", *listing)
        self.write(f"{self.DIRECTORY}/schema.sql", "edited\n")
        reason = self.reason()
        self.assertIn("and 3 more with the files here (they are marked", reason)
        self.assertIn(f"Run `git ls-files -z -- {self.DIRECTORY} | git update-index -z --no-assume-unchanged --stdin "
                      f"&& git ls-files -z -- {self.DIRECTORY} | git update-index -z --no-skip-worktree --stdin` in "
                      f"{self.root} so git compares them again", reason)
        self.assertRemedyClears(reason, "Run `")

    def test_a_file_added_to_gits_index_is_named_and_taken_out(self):
        for flags in ((), ("-N",)):
            with self.subTest(flags=flags):
                self.git("checkout", "-q", "-f", PREVIOUS)
                path = f"{self.DIRECTORY}/new.sql"
                self.write(path, "create table n();\n")
                self.git("add", *flags, "--", path)
                reason = self.reason()
                self.assertIn(f"First undo the changes made here, which a checkout keeps: {path} is added to git's "
                              f"index here but not committed (`git reset -q -- {path}` in {self.root} takes it out of "
                              "the index): move it out of the checkout. New images", reason)
                self.assertNotIn("not tracked by git", reason)
                # Done as told, the checkout of the release leaves nothing behind.
                result = self.run_printed(f"git reset -q -- {path}")
                self.assertEqual(result.returncode, 0, result.stderr)
                (self.root / path).unlink()
                self.git("checkout", "-q", TARGET)
                self.assertEqual(self.plan_now()["deployment_drift"], [])

    def test_a_file_changed_here_that_the_release_removed_is_named(self):
        self.git("checkout", "-q", PREVIOUS)
        path = f"{self.DIRECTORY}/old.sql"
        self.write(path, "edited\n")
        # git refuses to check out the release over it.
        self.assertNotEqual(self.run_printed("git checkout -q v1.52.0").returncode, 0)
        reason = self.reason()
        self.assertIn(f"First undo the changes made here, which a checkout keeps: {path} was changed here and is not "
                      f"part of v1.52.0 (`git checkout HEAD -- {path}` in {self.root} discards the change; the "
                      "checkout of v1.52.0 then removes it). New images", reason)
        result = self.run_printed(f"git checkout HEAD -- {path}")
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_printed("git checkout -q v1.52.0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.plan_now()["deployment_drift"], [])


class MutationCornerTests(PlanTestCase):
    """Boundary cases for planner helpers and unfinished-update safety rules."""

    def git(self, *args: str) -> str:
        result = subprocess.run(["git", "-c", "commit.gpgsign=false", "-C", str(self.root), *args],
                                capture_output=True, text=True, env=_git_env(), check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_without_manifest_keeps_options_before_the_first_flag(self):
        from deploy.update.plan import _without_manifest

        self.assertEqual(_without_manifest(["--policy", "major", "--manifest", "release.json", "--channel", "beta"]),
                         ["--policy", "major", "--channel", "beta"])

    def test_image_name_preserves_registry_ports_and_removes_tags_or_digests(self):
        from deploy.update.plan import _image_name

        self.assertEqual(_image_name("registry.example:5000/futureagi/backend:v1.52.0"),
                         "registry.example:5000/futureagi/backend")
        self.assertEqual(_image_name("registry.example:5000/futureagi/backend@sha256:" + "a" * 64),
                         "registry.example:5000/futureagi/backend")

    def test_unmergeable_env_scan_continues_after_comments(self):
        self.write(".env.example", ENV_EXAMPLE + "NEW_SETTING=on\n")
        self.write(".env", "# comments are allowed before definitions\nexport SECRET_KEY=abc\n")

        plan = self.plan(self.standalone(PREVIOUS))

        self.assertBlocked(plan)
        self.assertReasonContaining(plan, "Copy them from .env.example into .env by hand")
        self.assertReasonContaining(plan, "written with an export prefix")

    def test_explicit_manifest_source_wins_over_latest_download_setting(self):
        from deploy.update.plan import _manifest_of

        latest = ("https://github.com/future-agi/future-agi/releases/latest/download/"
                  "future-agi-update-manifest.json")
        explicit = "/srv/releases/v1.51.0/manifest.json"
        settings = self.settings(environ={"FUTURE_AGI_UPDATE_MANIFEST_URL": latest})

        self.assertEqual(_manifest_of({"target_version": PREVIOUS, "manifest_source": explicit}, settings), explicit)
        self.assertEqual(_manifest_of({"manifest_source": explicit}, settings), "")

    def test_stray_groups_sort_by_state_then_image_not_input_or_container_id(self):
        from deploy.update.plan import classify_strays

        installation = self.distributed(PREVIOUS)
        installation.profile_services["worker-default"] = WORKER_RAW
        same_id = "sha256:shared-stray-image"
        image = f"futureagi/future-agi:{PREVIOUS}"
        installation.run("worker-default", PREVIOUS, image=image, image_id=same_id, status="paused")
        installation._containers[-1]["id"] = "paused-container"
        installation.run("worker-default", PREVIOUS, image=image, image_id=same_id, status="running")
        installation._containers[-1]["id"] = "running-container"
        installation.run("worker-default", PREVIOUS, image=image, image_id=same_id, status="exited")
        installation._containers[-1]["id"] = "stopped-container"
        entries = classify_strays(installation, installation.containers(), installation.config()["services"])["inactive"]
        self.assertEqual([entry["state"] for entry in entries], ["running", "paused", "stopped"])

        installation = self.distributed(PREVIOUS)
        installation.profile_services["worker-default"] = WORKER_RAW
        for image, container_id in (("futureagi/future-agi:z", "a-container"),
                                    ("futureagi/future-agi:a", "z-container")):
            installation.run("worker-default", PREVIOUS, image=image, image_id="sha256:" + container_id,
                             status="running")
            installation._containers[-1]["id"] = container_id
        entries = classify_strays(installation, installation.containers(), installation.config()["services"])[
            "inactive"]
        self.assertEqual([entry["image"] for entry in entries],
                         ["futureagi/future-agi:a", "futureagi/future-agi:z"])

    def test_stray_state_fallback_uses_the_running_flag(self):
        from deploy.update.plan import stray_states

        self.assertEqual(stray_states([{"service": "worker-default", "running": True, "version": PREVIOUS}]),
                         f"worker-default (running, {PREVIOUS})")

    def test_unknown_inactive_stray_removal_uses_a_twelve_character_id(self):
        installation = self.distributed(PREVIOUS)
        installation.model_complete = False
        installation.run("worker-default", PREVIOUS, image=f"futureagi/future-agi:{PREVIOUS}")
        installation._containers[-1]["id"] = "0123456789abcdef" * 4

        reason = self.assertReasonContaining(self.plan(installation), "service worker-default has containers")

        self.assertIn("docker rm -f 0123456789ab`", reason)

    def test_orphan_removal_uses_a_twelve_character_id(self):
        installation = self.distributed(PREVIOUS)
        installation.run("retired-worker", PREVIOUS, image=f"futureagi/future-agi:{PREVIOUS}")
        installation._containers[-1]["id"] = "0123456789abcdef" * 4

        plan = self.plan(installation)

        note = next(note for note in plan["notes"] if "retired-worker" in note)
        self.assertIn("docker rm -f 0123456789ab`", note)

    def test_resumed_apply_checks_strays_for_every_previously_changed_key(self):
        installation = self.distributed(TARGET)
        installation.profile_services["worker-default"] = WORKER_RAW
        # The frontend is the only active service still on an old image; FUTURE_AGI_VERSION has no active change left,
        # but it is still among the keys the staged apply selected.
        installation.run("frontend", PREVIOUS, image=f"futureagi/frontend:{TARGET}")
        installation.run("worker-default", PREVIOUS, image=f"futureagi/future-agi:{PREVIOUS}")
        record = receipt("1", status="failed_during_rollout", target_version=TARGET,
                         previous_values=dict(ALL_PREVIOUS), env_changes=dict(EVERY_KEY))

        plan = self.plan(installation, history=[record])

        self.assertReasonContaining(plan, "service worker-default has containers")

    def test_resume_without_previous_values_uses_currently_selected_keys(self):
        installation = self.distributed(TARGET)
        installation.model_complete = True
        installation._containers[0]["status"] = "exited"
        installation._containers[0]["exit_code"] = 1
        installation.profile_services["worker-default"] = WORKER_RAW
        installation.run("worker-default", PREVIOUS, image=f"futureagi/future-agi:{PREVIOUS}")
        record = receipt("1", status=STAGED, target_version=TARGET, previous_values={}, env_changes=dict(EVERY_KEY))

        plan = self.plan(installation, history=[record])

        self.assertReasonContaining(plan, "service worker-default has containers")

    def test_service_runs_requires_every_live_container_to_match(self):
        from deploy.update.plan import _service_runs

        containers = [
            {"id": "new", "status": "running", "health": "healthy",
             "image": f"futureagi/standalone:{TARGET}"},
            {"id": "old", "status": "running", "health": "healthy",
             "image": f"futureagi/standalone:{PREVIOUS}"},
        ]
        self.assertIsNone(_service_runs(containers, {"new": TARGET, "old": PREVIOUS}, TARGET))

        exited = [dict(item, status="exited", exit_code=0) for item in containers]
        exited[1]["exit_code"] = 1
        self.assertIsNone(_service_runs(exited, {"new": TARGET, "old": PREVIOUS}, TARGET))

    def test_rollback_with_a_digest_pin_is_not_treated_as_settled(self):
        from deploy.update.plan import _settled_rollback

        ref = parse_image_ref(f"futureagi/standalone:{PREVIOUS}@sha256:{'a' * 64}")
        services = [{"service": "app", "component": "standalone", "ref": ref}]
        running = {"app": [{"id": "container", "status": "running", "health": "healthy",
                             "image": f"futureagi/standalone:{PREVIOUS}"}]}
        record = {"env_changes": {"FUTURE_AGI_VERSION": PREVIOUS}}

        self.assertIsNone(_settled_rollback(record, services, running, {"container": PREVIOUS},
                                            {"FUTURE_AGI_VERSION": PREVIOUS}))

    def test_legacy_staged_receipt_without_kind_is_recognized_as_already_running(self):
        installation = self.standalone(TARGET, label=TARGET)
        record = receipt("1", status=STAGED, target_version=TARGET)
        del record["kind"]

        plan = self.plan(installation, history=[record])

        self.assertEqual((plan["decision"], plan["resume"], plan["staged_version"]), (UP_TO_DATE, False, None))
        self.assertTrue(any("already runs" in note for note in plan["notes"]))

    def test_dropped_update_is_detected_when_any_container_still_runs_its_release(self):
        installation = self.standalone(PREVIOUS, label=TARGET)
        installation.run("app", PREVIOUS)
        record = receipt("1", status="failed_during_rollout", target_version=TARGET,
                         env_changes={"FUTURE_AGI_VERSION": TARGET})

        plan = self.plan(installation, history=[record])

        self.assertReasonContaining(plan, "left app running v1.52.0")

    def test_dropped_update_does_not_trust_an_unlabelled_active_image_tag(self):
        installation = FakeInstallation(self.root, services={"app": f"futureagi/standalone:{PREVIOUS}",
                                                              "postgres": "postgres:16-alpine"},
                                        env={"FUTURE_AGI_VERSION": PREVIOUS, "SECRET_KEY": "x"})
        installation.run("app", "", image=f"futureagi/standalone:{TARGET}", inspectable=False)
        installation.run("postgres", "16.4", image="postgres:16-alpine")
        record = receipt("1", status="failed_during_rollout", target_version=TARGET,
                         env_changes={"FUTURE_AGI_VERSION": TARGET})

        plan = self.plan(installation, history=[record])

        self.assertFalse([reason for reason in plan["reasons"] if "Finish that update rather than" in reason])
        self.assertTrue(any("is abandoned" in note for note in plan["notes"]))

    def test_staged_version_is_not_also_reported_as_a_stopped_version(self):
        plan = self.plan(self.standalone(PREVIOUS, label=PREVIOUS), history=[receipt("1", status=STAGED)])

        self.assertEqual((plan["staged_version"], plan["stopped_version"], plan["stopped_kind"]),
                         (TARGET, None, None))

    def test_unfinished_row_uses_the_oldest_version_still_running(self):
        for status in ("running", "paused"):
            with self.subTest(status=status):
                installation = self.standalone(TARGET, label=TARGET)
                installation._containers[0]["status"] = status
                installation.run("app", PREVIOUS, status=status, image=f"futureagi/standalone:{TARGET}")
                record = receipt("1", status="failed_during_rollout", target_version=TARGET,
                                 env_changes={"FUTURE_AGI_VERSION": TARGET})

                plan = self.plan(installation, history=[record])

                self.assertEqual(self.component(plan, "standalone")["current_version"], PREVIOUS)

    def test_unrelated_version_key_does_not_replace_the_resolved_service_image_in_a_reason(self):
        installation = self.distributed(PREVIOUS)
        installation._config["services"]["manual-service"] = {"image": "example.com/opaque:v1.51.0"}
        installation.raw_images["manual-service"] = "example.com/opaque:${FRONTEND_VERSION:-latest}"
        plan = self.plan(installation)

        reason = self.assertReasonContaining(plan, "service manual-service takes its image from FRONTEND_VERSION")

        self.assertIn("that resolves to example.com/opaque:v1.51.0", reason)

    def test_invalid_shared_key_suggestion_comes_from_a_component_that_uses_it(self):
        installation = self.distributed(PREVIOUS, tags={"AGENTCC_GATEWAY_VERSION": "v1.51.1"},
                                        extra_env={"FUTURE_AGI_VERSION": "${VERSION}"})
        reason = self.assertReasonContaining(self.plan(installation),
                                             "FUTURE_AGI_VERSION in .env is ${VERSION}")

        self.assertIn("Set FUTURE_AGI_VERSION=v1.51.0", reason)
        self.assertNotIn("Set FUTURE_AGI_VERSION=v1.51.1", reason)

    def test_invalid_tag_suggestions_come_from_tags_the_key_actually_selects(self):
        installation = self.standalone(PREVIOUS, extra_env={"FUTURE_AGI_VERSION": "${VERSION}"})
        installation._config["services"]["app-shadow"] = {"image": "futureagi/standalone:v1.51.1"}
        installation.raw_images["app-shadow"] = "futureagi/standalone:${FUTURE_AGI_VERSION:-latest}"
        installation.run("app-shadow", "v1.51.1", image="futureagi/standalone:v1.51.1")
        reason = self.assertReasonContaining(self.plan(installation),
                                             "FUTURE_AGI_VERSION in .env is ${VERSION}")
        self.assertIn("Set FUTURE_AGI_VERSION=v1.51.0", reason)
        self.assertNotIn("Set FUTURE_AGI_VERSION=v1.51.1", reason)

        installation = FakeInstallation(self.root, services={
            "app": f"futureagi/standalone:{PREVIOUS}", "backend": "futureagi/future-agi:v1.51.1"},
            env={"FUTURE_AGI_VERSION": "${VERSION}", "SECRET_KEY": "x"}, mode="distributed")
        installation.run("app", PREVIOUS).run("backend", "v1.51.1")
        reason = self.assertReasonContaining(self.plan(installation),
                                             "FUTURE_AGI_VERSION in .env is ${VERSION}")
        self.assertIn("Set FUTURE_AGI_VERSION=v1.51.1", reason)
        self.assertNotIn("Set FUTURE_AGI_VERSION=v1.51.0", reason)

    def test_handmade_plan_text_keeps_release_options_when_it_builds_the_command(self):
        plan = self.plan(self.standalone(PREVIOUS))
        plan["next_command"] = None
        plan["command_options"] = ["--manifest", "/srv/releases/v1.52.0/manifest.json"]

        self.assertIn("run ./bin/update apply --manifest /srv/releases/v1.52.0/manifest.json", plan_text(plan))

    def test_named_path_limit_is_inclusive(self):
        from deploy.update.plan import _named

        paths = [f"file-{index}" for index in range(8)]
        self.assertEqual(_named(paths), ", ".join(paths))
        self.assertEqual(_named(paths + ["file-8"]), f"{', '.join(paths)} and 1 more")

    @unittest.skipIf(shutil.which("git") is None, "git is not installed")
    def test_eight_changed_files_are_named_without_the_many_file_hint(self):
        directory = "futureagi/eight-files"
        paths = [f"{directory}/file-{index}.txt" for index in range(8)]
        for path in paths:
            self.write(path, "before\n")
        self.git("init", "-q")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", TARGET)
        self.git("tag", TARGET)
        for path in paths:
            self.write(path, "after\n")

        reason = plan_module._drift_reason(self.standalone(PREVIOUS), [directory], TARGET, None)

        for path in paths:
            self.assertIn(path, reason)
        command = reason.split("git checkout v1.52.0 -- ", 1)[1].split("`", 1)[0]
        self.assertEqual(command, " ".join(paths))
        self.assertNotIn("and 0 more", reason)
        self.assertNotIn("git status --untracked-files=all --ignored", reason)

    @unittest.skipIf(shutil.which("git") is None, "git is not installed")
    def test_many_changes_across_directories_name_each_restore_entry(self):
        paths = ["docker-compose.yml"] + [f"deploy/alpha/file-{index}.txt" for index in range(4)] + [
            f"deploy/beta/file-{index}.txt" for index in range(4)]
        self.write("deploy/unrelated.txt", "unchanged\n")
        for path in paths:
            self.write(path, "before\n")
        self.git("init", "-q")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", TARGET)
        self.git("tag", TARGET)
        for path in paths:
            self.write(path, "after\n")

        reason = plan_module._drift_reason(self.standalone(PREVIOUS),
                                           ["docker-compose.yml", "deploy/alpha", "deploy/beta",
                                            "deploy/unrelated.txt"], TARGET, None)

        command = reason.split("git checkout v1.52.0 -- ", 1)[1].split("`", 1)[0]
        self.assertEqual(command, "docker-compose.yml deploy/alpha deploy/beta")

    @unittest.skipIf(shutil.which("git") is None, "git is not installed")
    def test_git_query_ignores_an_exported_git_dir(self):
        self.git("init", "-q")

        with mock.patch.dict(os.environ, {"GIT_DIR": str(self.root / "not-a-repository")}):
            top = plan_module._git(self.root, "rev-parse", "--show-toplevel")

        self.assertEqual(Path(top.strip()).resolve(), self.root.resolve())

    def test_git_record_parser_keeps_short_paths_and_advances_across_diff_records(self):
        self.assertEqual(plan_module._git_records("?? x\0", renames=True), [("??", "x")])
        self.assertEqual(plan_module._git_records("M\0first\0D\0second\0", renames=False),
                         [("M", "first"), ("D", "second")])

    @unittest.skipIf(shutil.which("git") is None, "git is not installed")
    def test_hidden_one_character_file_is_named_when_git_does_not_compare_it(self):
        self.write("x", "before\n")
        self.git("init", "-q")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", TARGET)
        self.git("tag", TARGET)
        self.git("update-index", "--assume-unchanged", "x")
        self.write("x", "after\n")

        reason = plan_module._drift_reason(self.standalone(PREVIOUS), ["x"], TARGET, None)

        self.assertIn("git does not compare x with the file here", reason)
        self.assertIn("git update-index --no-assume-unchanged -- x", reason)


class RestoreExecutionContractTests(unittest.TestCase):
    """Pure authorization/planner boundary; fixtures do not qualify a release."""

    def test_manual_still_uses_published_deployment_without_automatic_authority(self):
        from deploy.update.plan import execution_files
        from deploy.tests.test_update_compatibility import default_v2, synthetic_release
        for manifest in (default_v2(), synthetic_release()[0]):
            self.assertEqual(execution_files(manifest), manifest["deployment"]["files"])

    def test_typed_transition_selects_only_its_exact_retained_files(self):
        from deploy.tests.test_update_compatibility import restore_context
        from deploy.update.compatibility import authorise_transition, plain
        from deploy.update.plan import execution_files
        prepared, obs, controls, backup = restore_context()
        transition = authorise_transition(prepared, obs, controls, backup)
        self.assertEqual(dict(execution_files(plain(prepared.manifest), transition, prepared.raw_manifest)),
                         dict(obs.deployment_files))

    def test_no_untyped_or_other_manifest_can_select_execution_files(self):
        from deploy.tests.test_update_compatibility import restore_context
        from deploy.update.compatibility import CompatibilityError, authorise_transition, plain
        from deploy.update.plan import execution_files
        prepared, obs, controls, backup = restore_context()
        transition = authorise_transition(prepared, obs, controls, backup)
        for authority, raw in ((object(), prepared.raw_manifest), (transition, prepared.raw_manifest + b"\n"),
                               (transition, None)):
            with self.subTest(authority=type(authority).__name__), self.assertRaises(CompatibilityError):
                execution_files(plain(prepared.manifest), authority, raw)


if __name__ == "__main__":
    unittest.main()
