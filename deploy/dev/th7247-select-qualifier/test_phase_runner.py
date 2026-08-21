#!/usr/bin/env python3
"""Offline tests for the fail-closed 0816h two-phase runner."""

from __future__ import annotations

import ast
import copy
import hashlib
import importlib.util
import json
import os
import stat
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import phase_runner as runner

PACKAGE_DIR = Path(__file__).resolve().parent
HEX = "a" * 64
HANDOFF = "b" * 64


class ProtocolQualificationFailure(RuntimeError):
    pass


class ProtocolPopulationGap(ProtocolQualificationFailure):
    pass


class ProtocolSafetyViolation(RuntimeError):
    pass


def source_grant_inventory() -> dict[str, object]:
    contract = json.loads(
        (PACKAGE_DIR / "kartik_smoke_0816h_run_contract.json").read_text()
    )
    return contract["database_contract"]["source_grant_inventory"]


def load_wrapper_protocol(fake_q):
    source_path = PACKAGE_DIR / "kartik_smoke_0816h.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"), source_path.name)
    future = next(
        node
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module == "__future__"
    )
    names = {
        "_profile_from_property_candidate",
        "_with_model_value_lookback",
        "key_protocol",
        "metrics_protocol",
    }
    constant_names = {
        "METRIC_CATALOG_FROZEN_PAGE_FUSE",
        "METRIC_CATALOG_DEV_PAGE_FUSE",
        "MODEL_VALUES_FROZEN_LOOKBACK_DAYS",
        "MODEL_VALUES_DEV_LOOKBACK_DAYS",
        "MODEL_VALUES_QUALIFICATION_PAGE_SIZE",
    }
    assignments = [
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id in constant_names
    ]
    functions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    if {node.name for node in functions} != names:
        raise AssertionError("wrapper protocol functions are incomplete")
    if {node.targets[0].id for node in assignments} != constant_names:
        raise AssertionError("wrapper protocol constants are incomplete")
    namespace = {
        "q": fake_q,
        "SafetyViolation": ProtocolSafetyViolation,
    }
    exec(
        compile(
            ast.Module(body=[future, *assignments, *functions], type_ignores=[]),
            source_path.name,
            "exec",
        ),
        namespace,
    )
    return namespace


def load_wrapper_grant_protocol():
    source_path = PACKAGE_DIR / "kartik_smoke_0816h.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"), source_path.name)
    future = next(
        node
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module == "__future__"
    )
    constant_names = {
        "SOURCE_SELECT_GRANT_COLUMNS",
        "SOURCE_DICTGET_GRANT_ATTRIBUTES",
        "SOURCE_PROBES",
        "SOURCE_PROBE_KINDS",
        "FROZEN_SOURCE_GRANT_INVENTORY_SHA256",
        "FROZEN_SOURCE_SHOW_GRANTS_COUNT",
        "FROZEN_SOURCE_SHOW_GRANTS_SHA256",
        "FROZEN_SOURCE_SYSTEM_GRANTS_ROW_COUNT",
        "FROZEN_SOURCE_SYSTEM_GRANTS_SHA256",
    }
    function_names = {
        "_sha256",
        "_source_grant_inventory",
        "_source_grant_inventory_sha256",
        "_source_system_grant_inventory",
        "_is_exact_self_show_grants",
        "_execute_self_show_grants",
        "_source_grant_audit",
    }
    assignments = [
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id in constant_names
    ]
    functions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in function_names
    ]
    if {node.targets[0].id for node in assignments} != constant_names:
        raise AssertionError("wrapper grant constants are incomplete")
    if {node.name for node in functions} != function_names:
        raise AssertionError("wrapper grant functions are incomplete")
    namespace = {
        "Any": object,
        "SafetyViolation": ProtocolSafetyViolation,
        "hashlib": hashlib,
        "json": json,
        "re": __import__("re"),
    }
    exec(
        compile(
            ast.Module(body=[future, *assignments, *functions], type_ignores=[]),
            source_path.name,
            "exec",
        ),
        namespace,
    )
    return namespace


def load_real_qualifier_guard_module():
    qualifier_dir = (
        PACKAGE_DIR.parents[2]
        / "futureagi"
        / "scripts"
        / "th7247_current_select_qualifier"
    )
    safety_name = "_th7247_safety_guard_test"
    qualify_name = "_th7247_qualify_guard_test"
    safety_spec = importlib.util.spec_from_file_location(
        safety_name, qualifier_dir / "safety.py"
    )
    qualify_spec = importlib.util.spec_from_file_location(
        qualify_name, qualifier_dir / "qualify.py"
    )
    if (
        safety_spec is None
        or safety_spec.loader is None
        or qualify_spec is None
        or qualify_spec.loader is None
    ):
        raise AssertionError("qualifier guard modules could not be loaded")
    safety = importlib.util.module_from_spec(safety_spec)
    qualify = importlib.util.module_from_spec(qualify_spec)
    sys.modules[safety_name] = safety
    sys.modules[qualify_name] = qualify
    try:
        safety_spec.loader.exec_module(safety)
        with mock.patch.dict(sys.modules, {"safety": safety}):
            qualify_spec.loader.exec_module(qualify)
    finally:
        sys.modules.pop(safety_name, None)
        sys.modules.pop(qualify_name, None)
    return qualify


def pins() -> dict[str, object]:
    return {
        "base_commit": "c" * 40,
        "bundle_manifest_sha256": "d" * 64,
        "canonical_tenant_binding_sha256": "e" * 64,
        "canonical_trace_project_uuid_sha256": "1" * 64,
        "canonical_voice_project_uuid_sha256": "2" * 64,
        "catalog_activation_sha256": "3" * 64,
        "catalog_activation_source_manifest_sha256": "4" * 64,
        "dockerfile_sha256": "5" * 64,
        "excluded_project_uuid_sha256": "6" * 64,
        "harness_sha256": "7" * 64,
        "image_id": "sha256:" + "8" * 64,
        "local_nonimmutable_tag": "th7247-current-select:test",
        "catalog_database": runner.CATALOG_DATABASE,
        "catalog_epoch": runner.CATALOG_EPOCH,
        "catalog_revision": runner.CATALOG_REVISION,
        "job_template_sha256": "a" * 64,
        "phase_runner_sha256": "f" * 64,
        "qualifier_sha256": "b" * 64,
        "runtime_overlay_sha256": "c" * 64,
        "source_grant_inventory_sha256": runner.SOURCE_GRANT_INVENTORY_SHA256,
        "source_show_grants_normalized_sha256": (
            runner.SOURCE_SHOW_GRANTS_NORMALIZED_SHA256
        ),
        "source_manifest_sha256": "d" * 64,
        "source_system_grants_canonical_sha256": (
            runner.SOURCE_SYSTEM_GRANTS_CANONICAL_SHA256
        ),
        "wrapper_sha256": "e" * 64,
    }


def population(binding: str) -> dict[str, object]:
    return {
        "workspace_admitted": True,
        "project_population_expected": True,
        "live_definition_count": 7,
        "live_value_count": 11,
        "active_catalog_epoch": runner.CATALOG_EPOCH,
        "active_catalog_revision": runner.CATALOG_REVISION,
        "lineage_anchor_revision": 1,
        "projection_version": 1,
        "activation_sequence": 1,
        "activation_source_manifest_sha256": pins()[
            "catalog_activation_source_manifest_sha256"
        ],
        "activation_sha256": pins()["catalog_activation_sha256"],
        "activation_binding_sha256": binding,
        "elapsed_s": 0.2,
    }


def model_values() -> dict[str, object]:
    return {
        "qualified": True,
        "catalog_read_mode": "read",
        "page_size": 1,
        "p1_values": 1,
        "p2_values": 1,
        "continuation_exercised": True,
        "search_proven": True,
        "lookback_frozen_baseline_days": 7,
        "lookback_effective_days": 366,
        "lookback_restored_on_return": True,
    }


def common(phase: str) -> dict[str, object]:
    immutable = pins()
    return {
        "schema": "th7247-kartik-dev-analogue-functional-smoke/0816h/v1",
        "phase": phase,
        "environment": "DEV",
        "evidence_label": "0816h",
        "select_only": True,
        "production_touched": False,
        "release_qualified": False,
        "release_qualification_attempted": False,
        "named_target_matrix_executed": False,
        "functional_smoke_passed": True,
        "coverage_complete": True,
        "coverage_exit_code": 0,
        "exit_code": 0,
        "error": None,
        "route_failures": [],
        "population_gaps": [],
        "required_population_gap_count": 0,
        "run_id": "kartik-0816h-test",
        "frozen_end": "2026-08-16T07:00:00+00:00",
        "source_auth_ipv4_sha256": "f" * 64,
        "canonical_tenant_binding_sha256": immutable["canonical_tenant_binding_sha256"],
        "source_identity": {
            "base_commit": immutable["base_commit"],
            "derived_image_id": immutable["image_id"],
            "local_image_tag": immutable["local_nonimmutable_tag"],
            "base_image_digest": "base.invalid/backend@sha256:" + "0" * 64,
            "source_manifest_sha256": immutable["source_manifest_sha256"],
            "qualifier_sha256": immutable["qualifier_sha256"],
            "verified_runtime_files": 100,
            "verified_runtime_deletions": 0,
            "dirty_file_count": 20,
            "dirty_runtime_file_count": 10,
        },
        "database_identity_audit": {
            "source": {
                "identity_sha256": "1" * 64,
                "probe_count": runner.SOURCE_PROBE_COUNT,
                "probe_kinds": list(runner.SOURCE_PROBE_KINDS),
                "grant_closure": {
                    "grant_inventory_sha256": (
                        immutable["source_grant_inventory_sha256"]
                    ),
                    "show_grants_normalized_count": (
                        runner.SOURCE_SHOW_GRANTS_NORMALIZED_COUNT
                    ),
                    "show_grants_normalized_sha256": (
                        immutable["source_show_grants_normalized_sha256"]
                    ),
                    "active_role_count": 0,
                },
                "caps": {"readonly": 2},
            },
            "catalog": {"identity_sha256": "2" * 64, "caps": {"readonly": 2}},
        },
        "query_kinds": {
            "source_probe_count": runner.SOURCE_PROBE_COUNT,
            "source_probe_kinds": list(runner.SOURCE_PROBE_KINDS),
            "source_grant_inventory": source_grant_inventory(),
            "source_grant_inventory_sha256": (
                immutable["source_grant_inventory_sha256"]
            ),
        },
        "targets": {
            "canonical_voice": {
                "project_id_sha256": immutable["canonical_voice_project_uuid_sha256"],
                "workspace_catalog_admitted": True,
                "surface": "voice",
                "project_catalog_population": population("3" * 64),
                "model_values": model_values(),
            },
            "canonical_trace": {
                "project_id_sha256": immutable["canonical_trace_project_uuid_sha256"],
                "workspace_catalog_admitted": True,
                "surface": "trace",
                "project_catalog_population": population("4" * 64),
                "model_values": model_values(),
            },
        },
        "excluded_target": {
            "selected": False,
            "exclusion_digest_bound": True,
            "uuid_sha256_pin": immutable["excluded_project_uuid_sha256"],
            "target_selection_count": 0,
            "pg_query_count": 0,
            "catalog_query_count": 0,
            "client_count": 0,
            "callback_count": 0,
            "profile_count": 0,
            "matrix_cell_count": 0,
            "target_profile_handoff_entry_count": 0,
            "raw_identity_handoff_entry_count": 0,
        },
    }


def green_timing() -> dict[str, object]:
    return {
        "under_9_8s": True,
        "max_s": 0.9,
        "callbacks": 1,
        "statuses": {"200": 1},
    }


def registry_payload() -> dict[str, object]:
    payload = common("registry")
    payload.update(
        {
            "registry": {
                "executed": True,
                "passed": True,
                "handoff_created": True,
                "handoff_sha256": HANDOFF,
            },
            "matrix": {"executed": False, "expected_cell_count": 108},
            "timings_by_route": {
                route: green_timing()
                for route in ("property_keys", "filter_values", "metrics")
            },
        }
    )
    return payload


def matrix_payload() -> dict[str, object]:
    payload = common("matrix")
    cells = [
        {
            "target": target,
            "window": window,
            "kind": kind,
            "profile": profile,
            "passed": True,
            "positive": True,
        }
        for target, window, kind, profile in sorted(runner._expected_cell_identities())
    ]
    payload.update(
        {
            "analogue_matrix_executed": True,
            "registry": {
                "executed": False,
                "prerequisite_verified": True,
                "handoff_loaded": True,
                "handoff_sha256": HANDOFF,
            },
            "matrix": {
                "executed": True,
                "windows": list(runner.WINDOWS),
                "shapes": {
                    "canonical_voice": {
                        "kinds": ["voice"],
                        "profiles": list(runner.PROFILES),
                    },
                    "canonical_trace": {
                        "kinds": ["trace", "span"],
                        "profiles": list(runner.PROFILES),
                    },
                },
                "expected_cell_count": 108,
                "executed_cell_count": 108,
                "passed_cell_count": 108,
                "positive_cell_count": 108,
                "continuation_cell_count": 12,
                "cells": cells,
            },
            "timings_by_route": {
                route: green_timing()
                for route in ("trace_list", "span_list", "voice_list")
            },
        }
    )
    return payload


def capture(phase: str, payload: dict[str, object]) -> runner.PhaseCapture:
    stdout = runner._canonical_json(payload)
    return runner.PhaseCapture(
        phase=phase,
        returncode=0,
        stdout=stdout,
        stderr=b"",
        stdout_sha256=hashlib.sha256(stdout).hexdigest(),
        payload=payload,
    )


def minimal_contract() -> dict[str, object]:
    return {
        "pins": pins(),
        "database_contract": {
            "source_grant_inventory": source_grant_inventory(),
        },
        "minimal_environment": {"fixed_pin_values": {}},
    }


def image_binding(_contract: dict[str, object]) -> dict[str, object]:
    immutable = pins()
    return {
        "image_id": immutable["image_id"],
        "local_image_tag": immutable["local_nonimmutable_tag"],
        "repo_digests": [],
        "pull_policy": "never",
    }


def snapshot(content: str = "first") -> runner.EnvSnapshot:
    return runner.EnvSnapshot(
        device=1,
        inode=2,
        size=len(content),
        mode=0o600,
        uid=os.geteuid(),
        content_sha256=hashlib.sha256(content.encode()).hexdigest(),
        values={},
    )


class RunnerTests(unittest.TestCase):
    def temp_root(self):
        return tempfile.TemporaryDirectory(dir=PACKAGE_DIR)

    def write_bound_contract_fixture(self, root: Path):
        for name in ("bundle", "run", "evidence"):
            (root / name).mkdir(mode=0o700)
        contract = json.loads(
            (PACKAGE_DIR / "kartik_smoke_0816h_run_contract.json").read_text()
        )
        with mock.patch.object(runner, "RUN_ROOT", root):
            expected = runner._expected_paths()
        wrapper_path = expected["wrapper"]
        runner_path = expected["runner"]
        contract_path = expected["contract"]
        wrapper_path.write_bytes((PACKAGE_DIR / "kartik_smoke_0816h.py").read_bytes())
        runner_path.write_bytes(Path(runner.__file__).read_bytes())
        wrapper_path.chmod(0o600)
        runner_path.chmod(0o600)
        wrapper_hash = hashlib.sha256(wrapper_path.read_bytes()).hexdigest()
        runner_hash = hashlib.sha256(runner_path.read_bytes()).hexdigest()
        image_id = "sha256:" + "a" * 64
        contract["pins"]["image_id"] = image_id
        contract["pins"]["wrapper_sha256"] = wrapper_hash
        contract["pins"]["phase_runner_sha256"] = runner_hash
        contract["minimal_environment"]["fixed_pin_values"]["EXPECTED_IMAGE_ID"] = (
            image_id
        )
        contract["minimal_environment"]["fixed_pin_values"][
            "EXPECTED_KARTIK_SMOKE_0816H_SHA256"
        ] = wrapper_hash
        contract["binding_state"] = {
            "state": "BOUND_AUDITED_DEV_GO",
            "placeholder_count_remaining": 0,
            "independent_static_and_runtime_preflight_audit_passed": True,
            "human_DEV_approval_recorded": True,
        }
        contract["status"] = "BOUND_AUDITED_DEV_GO"
        contract["execution_authorized"] = True
        contract["execution"]["approval_required"] = False
        contract["minimal_environment"]["host_env_file"] = str(expected["env"])
        contract["output_capture"]["env_key_attestation_path"] = str(
            expected["attestation"]
        )
        keys = tuple(contract["minimal_environment"]["exact_keys"])
        for phase in ("registry", "matrix"):
            section = contract["phase_execution"][phase]
            section["stdout_path"] = str(expected[f"{phase}.stdout"])
            section["stderr_path"] = str(expected[f"{phase}.stderr"])
            section["exit_code_path"] = str(expected[f"{phase}.exit"])
            section["argv_record_path"] = str(expected[f"{phase}.argv"])
            with mock.patch.object(runner, "RUN_ROOT", root):
                section["exact_docker_exec_argv"] = runner._expected_phase_argv(
                    keys, phase
                )
        contract["cleanup_plan"]["retain_mode_0600"] = [
            str(path)
            for name, path in sorted(expected.items())
            if name
            in {
                "contract",
                "wrapper",
                "runner",
                "attestation",
                "registry.stdout",
                "registry.stderr",
                "registry.exit",
                "registry.argv",
                "matrix.stdout",
                "matrix.stderr",
                "matrix.exit",
                "matrix.argv",
            }
        ]
        contract_path.write_bytes(runner._canonical_json(contract))
        contract_path.chmod(0o600)
        return contract, expected

    def test_env_snapshot_binds_exact_keys_inode_and_confidential_content(self):
        with self.temp_root() as raw:
            path = Path(raw) / "smoke.env"
            keys = tuple(f"KEY{index:03d}" for index in range(68))
            wire = "".join(
                f"{key}=value-{index:03d}\n" for index, key in enumerate(keys)
            )
            path.write_text(wire, encoding="utf-8")
            path.chmod(0o600)
            initial = runner._env_snapshot(path, keys)
            self.assertEqual(len(initial.values), 68)
            mutated = wire.replace("value-000", "value-999", 1)
            self.assertEqual(len(mutated), len(wire))
            path.write_text(mutated, encoding="utf-8")
            path.chmod(0o600)
            with self.assertRaisesRegex(runner.RunnerViolation, "confidential content"):
                runner._assert_same_env(initial, runner._env_snapshot(path, keys))

    def test_exclusive_capture_uses_shell_false_and_mode_0600(self):
        with self.temp_root() as raw:
            root = Path(raw)
            for name in ("bundle", "run", "evidence"):
                (root / name).mkdir(mode=0o700)
            expected = {
                "stdout_path": str(
                    root / "evidence/kartik-smoke-0816h.registry.stdout.json"
                ),
                "stderr_path": str(
                    root / "evidence/kartik-smoke-0816h.registry.stderr.bin"
                ),
                "exit_code_path": str(
                    root / "evidence/kartik-smoke-0816h.registry.exit-code"
                ),
                "argv_record_path": str(
                    root / "evidence/kartik-smoke-0816h.registry.argv.json"
                ),
                "exact_docker_exec_argv": ["docker", "exec", "fake"],
            }
            contract = {"phase_execution": {"registry": expected}}
            observed: dict[str, object] = {}

            class FakeProcess:
                def __init__(self, argv, **kwargs):
                    observed["argv"] = argv
                    observed.update(kwargs)
                    os.write(kwargs["stdout"], b"{}\n")

                def wait(self):
                    return 0

            with mock.patch.object(runner, "RUN_ROOT", root):
                result = runner._execute_phase(
                    contract, "registry", popen_factory=FakeProcess
                )
                self.assertEqual(result.returncode, 0)
                self.assertIs(observed["shell"], False)
                for key in ("stdout", "stderr", "exit", "argv"):
                    path = runner._expected_paths()[f"registry.{key}"]
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                with self.assertRaisesRegex(runner.RunnerViolation, "overwrite"):
                    runner._execute_phase(
                        contract, "registry", popen_factory=FakeProcess
                    )

    def test_provisional_contract_refuses_before_any_execution(self):
        with self.temp_root() as raw:
            root = Path(raw)
            for name in ("bundle", "run", "evidence"):
                (root / name).mkdir(mode=0o700)
            contract_path = root / "bundle/kartik-smoke-0816h-run-contract.json"
            wrapper_path = root / "bundle/kartik-smoke-0816h.py"
            provisional = json.loads(
                (PACKAGE_DIR / "kartik_smoke_0816h_run_contract.json").read_text()
            )
            provisional["pins"]["image_id"] = "__PENDING_0816H_IMAGE_ID__"
            contract_path.write_bytes(runner._canonical_json(provisional))
            wrapper_path.write_bytes(
                (PACKAGE_DIR / "kartik_smoke_0816h.py").read_bytes()
            )
            contract_path.chmod(0o600)
            wrapper_path.chmod(0o600)
            with mock.patch.object(runner, "RUN_ROOT", root):
                with self.assertRaisesRegex(runner.RunnerViolation, "placeholders"):
                    runner._validate_contract(contract_path)

    def test_recovered_contract_phase_argv_is_exactly_reconstructible(self):
        contract = json.loads(
            (PACKAGE_DIR / "kartik_smoke_0816h_run_contract.json").read_text()
        )
        keys = tuple(contract["minimal_environment"]["exact_keys"])
        with mock.patch.object(
            runner,
            "RUN_ROOT",
            Path("/home/ubuntu/th7247-dev-qualifier-current-0816h"),
        ):
            for phase in ("registry", "matrix"):
                self.assertEqual(
                    contract["phase_execution"][phase]["exact_docker_exec_argv"],
                    runner._expected_phase_argv(keys, phase),
                )

    def test_local_image_binding_accepts_container_created_from_exact_id(self):
        contract = minimal_contract()
        image_id = contract["pins"]["image_id"]
        tag = contract["pins"]["local_nonimmutable_tag"]
        with mock.patch.object(
            runner,
            "_inspect_scalar",
            side_effect=[image_id, "[]", image_id, image_id],
        ):
            binding = runner._inspect_local_image_binding(contract)
        self.assertEqual(binding["image_id"], image_id)
        self.assertEqual(binding["repo_digests"], [])
        self.assertEqual(binding["pull_policy"], "never")
        with mock.patch.object(
            runner,
            "_inspect_scalar",
            side_effect=[image_id, "[]", image_id, tag],
        ):
            self.assertEqual(
                runner._inspect_local_image_binding(contract)["image_id"], image_id
            )
        for repo_digests, container_reference in (
            ("[]", "unexpected:tag"),
            ('["unexpected@sha256:' + "1" * 64 + '"]', image_id),
        ):
            with (
                mock.patch.object(
                    runner,
                    "_inspect_scalar",
                    side_effect=[
                        image_id,
                        repo_digests,
                        image_id,
                        container_reference,
                    ],
                ),
                self.assertRaisesRegex(
                    runner.RunnerViolation, "exact local-only image"
                ),
            ):
                runner._inspect_local_image_binding(contract)

    def test_wrapper_disables_executable_discovery_before_django_preload(self):
        source = (PACKAGE_DIR / "kartik_smoke_0816h.py").read_text()
        path_guard = 'os.environ["PATH"] = ""'
        django_preload = "q._bootstrap_reviewed_django_runtime(django.setup)"
        self.assertEqual(source.count(path_guard), 1)
        self.assertLess(source.index(path_guard), source.index(django_preload))

    def test_wrapper_bridges_cursor_state_only_through_private_tmpfs_cache(self):
        source = (PACKAGE_DIR / "kartik_smoke_0816h.py").read_text()
        install = source.index("def _install_attribute_cursor_cache(")
        bootstrap = source.index("q._bootstrap_reviewed_django_runtime(django.setup)")
        binding = source.index("attribute_cursor_state.cache = cache")
        cleanup = source.index("def _cleanup_attribute_cursor_cache(")
        install_call = source.index(
            "cursor_cache = _install_attribute_cursor_cache(controlled_phase())"
        )
        restore = source.index(
            "attribute_cursor_state.cache = _attribute_cursor_cache_original"
        )
        main_cleanup = source.rindex("_cleanup_attribute_cursor_cache()")
        self.assertLess(install, binding)
        self.assertLess(binding, cleanup)
        self.assertLess(cleanup, restore)
        self.assertLess(bootstrap, install_call)
        self.assertLess(install_call, main_cleanup)
        self.assertLess(restore, main_cleanup)
        for fragment in (
            'ATTRIBUTE_CURSOR_CACHE_TMPFS = Path("/tmp")',
            'tmp_mount[1] != "tmpfs"',
            '"nosuid",',
            '"nodev",',
            '"noexec",',
            "tempfile.mkdtemp(",
            "stat.S_IMODE(path_info.st_mode) != 0o700",
            'isinstance(caches["default"], LocMemCache)',
            "attribute_cursor_state.cache is not default_cache_proxy",
            '"MAX_ENTRIES": ATTRIBUTE_CURSOR_CACHE_MAX_ENTRIES',
            'multiprocessing.get_context("fork").Process(target=write_sentinel)',
            "cache.get(sentinel_key) != sentinel",
            "_attribute_cursor_cache.clear()",
            "_attribute_cursor_cache_path.rmdir()",
            '"lane": "cursor_cache_cleanup"',
        ):
            self.assertIn(fragment, source)
        self.assertNotIn('caches["default"] =', source)
        self.assertEqual(source.count("attribute_cursor_state.cache = cache"), 1)
        self.assertEqual(
            source.count(
                "attribute_cursor_state.cache = _attribute_cursor_cache_original"
            ),
            1,
        )

    def test_key_profile_reuses_enumerated_candidate_without_exact_q_lookup(self):
        proof = {
            "qualified": True,
            "p1_count": 25,
            "p2_count": 7,
            "continuation_exercised": True,
        }

        def require_status(response, lane):
            self.assertTrue(lane.endswith(".values.p1"))
            if response.status_code != 200:
                raise ProtocolQualificationFailure("unexpected status")
            return response.data

        exact_q_lookup = mock.Mock(side_effect=AssertionError("exact-q lookup used"))
        metrics_lookup = mock.Mock()
        fake_q = types.SimpleNamespace(
            DirectDRFClient=object,
            QualificationFailure=ProtocolQualificationFailure,
            PopulationGap=ProtocolPopulationGap,
            _qualify_key_read_more=mock.Mock(return_value=proof),
            _property_key_page=mock.Mock(
                return_value=([{"key": "custom.score", "type": "number"}], {})
            ),
            _discover_property_profile=exact_q_lookup,
            _qualify_metrics_catalog=metrics_lookup,
            _require_status=require_status,
            _digest=lambda value, length=16: f"digest-{length}",
            METRIC_CATALOG_QUALIFICATION_MAX_PAGES=8,
        )
        metrics_lookup.side_effect = lambda *_args, **_kwargs: (
            self.assertEqual(fake_q.METRIC_CATALOG_QUALIFICATION_MAX_PAGES, 64)
            or {"search_proven": True}
        )
        protocol = load_wrapper_protocol(fake_q)

        class Client:
            project = types.SimpleNamespace(id="project-1")

            def __init__(self):
                self.calls = []

            def call(self, endpoint, *, lane, query):
                self.calls.append((endpoint, lane, query))
                return types.SimpleNamespace(
                    status_code=200,
                    data={"values": [{"value": 0, "type": "number"}]},
                )

        client = Client()
        key, value, value_type, evidence = protocol["key_protocol"](
            client, "canonical_voice.property_keys"
        )
        self.assertEqual((key, value, value_type), ("custom.score", 0, "number"))
        self.assertEqual(
            client.calls,
            [
                (
                    "filter_values",
                    "canonical_voice.property_keys.profile.digest-16.values.p1",
                    {
                        "metric_name": "custom.score",
                        "metric_type": "custom_attribute",
                        "source": "traces",
                        "project_ids": "project-1",
                        "page_size": 10,
                        "attribute_type": "number",
                    },
                )
            ],
        )
        fake_q._property_key_page.assert_called_once_with(
            client,
            lane="canonical_voice.property_keys.profile_candidates",
            page_size=25,
        )
        exact_q_lookup.assert_not_called()
        self.assertTrue(evidence["continuation_exercised"])
        self.assertTrue(evidence["candidate_page_proven"])
        self.assertTrue(evidence["filter_value_binding_proven"])
        self.assertNotIn("search_proven", evidence)
        self.assertEqual(
            protocol["metrics_protocol"](
                client, "canonical_voice.metrics", "custom.score"
            ),
            {
                "search_proven": True,
                "page_fuse_frozen_baseline": 8,
                "page_fuse_effective": 64,
                "page_fuse_restored_on_return": True,
            },
        )
        self.assertEqual(fake_q.METRIC_CATALOG_QUALIFICATION_MAX_PAGES, 8)
        metrics_lookup.assert_called_once_with(
            client,
            lane="canonical_voice.metrics",
            expected_property_ids=(
                "system_attribute:traces:model",
                "custom_attribute:custom.score",
            ),
        )

    def test_metrics_page_fuse_restores_after_helper_failure(self):
        helper_error = ProtocolQualificationFailure("catalog failed")
        fake_q = types.SimpleNamespace(
            DirectDRFClient=object,
            METRIC_CATALOG_QUALIFICATION_MAX_PAGES=8,
            _qualify_metrics_catalog=mock.Mock(side_effect=helper_error),
        )
        metrics = load_wrapper_protocol(fake_q)["metrics_protocol"]
        with self.assertRaisesRegex(ProtocolQualificationFailure, "catalog failed"):
            metrics(object(), "metrics", "custom.key")
        self.assertEqual(fake_q.METRIC_CATALOG_QUALIFICATION_MAX_PAGES, 8)

    def test_metrics_page_fuse_rejects_baseline_and_lifecycle_drift(self):
        helper = mock.Mock(return_value={"qualified": True})
        fake_q = types.SimpleNamespace(
            DirectDRFClient=object,
            METRIC_CATALOG_QUALIFICATION_MAX_PAGES=9,
            _qualify_metrics_catalog=helper,
        )
        metrics = load_wrapper_protocol(fake_q)["metrics_protocol"]
        with self.assertRaisesRegex(ProtocolSafetyViolation, "baseline drifted"):
            metrics(object(), "metrics", "custom.key")
        helper.assert_not_called()
        self.assertEqual(fake_q.METRIC_CATALOG_QUALIFICATION_MAX_PAGES, 9)

        fake_q.METRIC_CATALOG_QUALIFICATION_MAX_PAGES = 8

        def drift(*_args, **_kwargs):
            fake_q.METRIC_CATALOG_QUALIFICATION_MAX_PAGES = 63
            return {"qualified": True}

        helper.side_effect = drift
        with self.assertRaisesRegex(ProtocolSafetyViolation, "lifecycle drifted"):
            metrics(object(), "metrics", "custom.key")
        self.assertEqual(fake_q.METRIC_CATALOG_QUALIFICATION_MAX_PAGES, 8)

    def test_registry_scopes_model_values_and_trace_profile_and_restores_lookback(self):
        protocol = load_wrapper_protocol(types.SimpleNamespace())
        frozen = protocol["MODEL_VALUES_FROZEN_LOOKBACK_DAYS"]
        effective = protocol["MODEL_VALUES_DEV_LOOKBACK_DAYS"]
        page_size = protocol["MODEL_VALUES_QUALIFICATION_PAGE_SIZE"]
        self.assertEqual((frozen, effective), (7, 366))
        self.assertEqual(page_size, 1)
        settings = types.SimpleNamespace()
        dashboard_view = types.SimpleNamespace(
            FILTER_VALUES_DEFAULT_LOOKBACK_DAYS=frozen
        )
        django = types.ModuleType("django")
        django_conf = types.ModuleType("django.conf")
        django_conf.settings = settings
        django.conf = django_conf
        tracer = types.ModuleType("tracer")
        tracer_views = types.ModuleType("tracer.views")
        dashboard = types.ModuleType("tracer.views.dashboard")
        dashboard.DashboardViewSet = dashboard_view
        tracer.views = tracer_views
        tracer_views.dashboard = dashboard
        modules = {
            "django": django,
            "django.conf": django_conf,
            "tracer": tracer,
            "tracer.views": tracer_views,
            "tracer.views.dashboard": dashboard,
        }
        scope = protocol["_with_model_value_lookback"]
        observed = []

        def baseline_callback(name):
            self.assertEqual(
                getattr(
                    settings,
                    "FILTER_VALUES_DEFAULT_LOOKBACK_DAYS",
                    dashboard_view.FILTER_VALUES_DEFAULT_LOOKBACK_DAYS,
                ),
                frozen,
            )
            observed.append(name)

        baseline_callback("voice_profile")

        def three_scoped_model_callbacks():
            for name in ("voice_values", "trace_values", "trace_profile"):
                self.assertEqual(
                    settings.FILTER_VALUES_DEFAULT_LOOKBACK_DAYS, effective
                )
                observed.append(name)
            return tuple(observed[-3:])

        with mock.patch.dict(sys.modules, modules):
            value, evidence = scope(three_scoped_model_callbacks)
        self.assertEqual(value, ("voice_values", "trace_values", "trace_profile"))
        self.assertEqual(len(observed), 4)
        self.assertFalse(hasattr(settings, "FILTER_VALUES_DEFAULT_LOOKBACK_DAYS"))
        self.assertEqual(
            evidence,
            {
                "lookback_frozen_baseline_days": frozen,
                "lookback_effective_days": effective,
                "lookback_restored_on_return": True,
            },
        )
        self.assertEqual(dashboard_view.FILTER_VALUES_DEFAULT_LOOKBACK_DAYS, frozen)

        def non_model_callback():
            return getattr(
                settings,
                "FILTER_VALUES_DEFAULT_LOOKBACK_DAYS",
                dashboard_view.FILTER_VALUES_DEFAULT_LOOKBACK_DAYS,
            )

        self.assertEqual(non_model_callback(), frozen)

        source = (PACKAGE_DIR / "kartik_smoke_0816h.py").read_text()
        registry = source[
            source.index("def registry_phase(") : source.index("def matrix_phase(")
        ]
        scoped = registry[
            registry.index("    def qualify_model_registry():") : registry.index(
                ") = _with_model_value_lookback(qualify_model_registry)"
            )
        ]
        before_scoped = registry[: registry.index("    def qualify_model_registry():")]
        self.assertEqual(before_scoped.count("q._qualify_model_values("), 0)
        self.assertEqual(before_scoped.count("q._discover_system_model("), 1)
        self.assertEqual(scoped.count("q._qualify_model_values("), 2)
        self.assertEqual(scoped.count("q._discover_system_model("), 1)
        self.assertEqual(
            scoped.count("page_size=MODEL_VALUES_QUALIFICATION_PAGE_SIZE"),
            2,
        )
        self.assertEqual(registry.count("q._qualify_model_values("), 2)
        self.assertEqual(registry.count("q._discover_system_model("), 2)

    def test_real_registry_orchestration_observes_7_366_366_366_7(self):
        protocol = load_wrapper_protocol(types.SimpleNamespace())
        frozen = protocol["MODEL_VALUES_FROZEN_LOOKBACK_DAYS"]
        effective = protocol["MODEL_VALUES_DEV_LOOKBACK_DAYS"]
        page_size = protocol["MODEL_VALUES_QUALIFICATION_PAGE_SIZE"]
        settings = types.SimpleNamespace()
        dashboard_view = types.SimpleNamespace(
            FILTER_VALUES_DEFAULT_LOOKBACK_DAYS=frozen
        )
        django = types.ModuleType("django")
        django_conf = types.ModuleType("django.conf")
        django_conf.settings = settings
        django.conf = django_conf
        tracer = types.ModuleType("tracer")
        tracer_views = types.ModuleType("tracer.views")
        dashboard = types.ModuleType("tracer.views.dashboard")
        dashboard.DashboardViewSet = dashboard_view
        tracer.views = tracer_views
        tracer_views.dashboard = dashboard
        modules = {
            "django": django,
            "django.conf": django_conf,
            "tracer": tracer,
            "tracer.views": tracer_views,
            "tracer.views.dashboard": dashboard,
        }

        def current_lookback():
            return getattr(
                settings,
                "FILTER_VALUES_DEFAULT_LOOKBACK_DAYS",
                dashboard_view.FILTER_VALUES_DEFAULT_LOOKBACK_DAYS,
            )

        sequence = []

        def qualify_model_values(_client, *, lane, page_size):
            sequence.append((f"{lane}.qualify", current_lookback(), page_size))
            return {"continuation_exercised": True, "page_size": page_size}

        def discover_system_model(_client, *, lane):
            sequence.append((f"{lane}.discover", current_lookback(), None))
            return "model", "string"

        fake_q = types.SimpleNamespace(
            PopulationGap=ProtocolPopulationGap,
            _qualify_model_values=qualify_model_values,
            _discover_system_model=discover_system_model,
            _system_model_filter=lambda *_args: {},
        )

        class StopAfterModels(RuntimeError):
            pass

        def stop_after_models():
            sequence.append(("post", current_lookback(), None))
            raise StopAfterModels

        source_path = PACKAGE_DIR / "kartik_smoke_0816h.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"), source_path.name)
        future = next(
            node
            for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.module == "__future__"
        )
        registry = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "registry_phase"
        )
        namespace = {
            "q": fake_q,
            "SafetyViolation": ProtocolSafetyViolation,
            "required": lambda _scope, operation: operation(),
            "key_protocol": lambda _client, _lane: (
                "custom.key",
                "value",
                "string",
                {"continuation_exercised": True},
            ),
            "metrics_protocol": lambda *_args: {"continuation_exercised": True},
            "gap": lambda *_args: self.fail("unexpected registry gap"),
            "_with_model_value_lookback": protocol["_with_model_value_lookback"],
            "MODEL_VALUES_QUALIFICATION_PAGE_SIZE": page_size,
            "guard_and_timings": stop_after_models,
        }
        exec(
            compile(
                ast.Module(body=[future, registry], type_ignores=[]),
                source_path.name,
                "exec",
            ),
            namespace,
        )
        context = {
            "voice_client": types.SimpleNamespace(name="voice"),
            "trace_client": types.SimpleNamespace(name="trace"),
        }
        with mock.patch.dict(sys.modules, modules):
            with self.assertRaises(StopAfterModels):
                namespace["registry_phase"](context)
        self.assertEqual(
            sequence,
            [
                ("canonical_voice.model_profile.discover", frozen, None),
                ("canonical_voice.model_values.qualify", effective, 1),
                ("canonical_trace.model_values.qualify", effective, 1),
                ("canonical_trace.model_profile.discover", effective, None),
                ("post", frozen, None),
            ],
        )
        self.assertFalse(hasattr(settings, "FILTER_VALUES_DEFAULT_LOOKBACK_DAYS"))

    def test_registry_keeps_terminal_page_size_one_as_a_required_gap(self):
        protocol = load_wrapper_protocol(types.SimpleNamespace())
        frozen = protocol["MODEL_VALUES_FROZEN_LOOKBACK_DAYS"]
        page_size = protocol["MODEL_VALUES_QUALIFICATION_PAGE_SIZE"]
        settings = types.SimpleNamespace()
        dashboard_view = types.SimpleNamespace(
            FILTER_VALUES_DEFAULT_LOOKBACK_DAYS=frozen
        )
        django = types.ModuleType("django")
        django_conf = types.ModuleType("django.conf")
        django_conf.settings = settings
        django.conf = django_conf
        tracer = types.ModuleType("tracer")
        tracer_views = types.ModuleType("tracer.views")
        dashboard = types.ModuleType("tracer.views.dashboard")
        dashboard.DashboardViewSet = dashboard_view
        tracer.views = tracer_views
        tracer_views.dashboard = dashboard
        modules = {
            "django": django,
            "django.conf": django_conf,
            "tracer": tracer,
            "tracer.views": tracer_views,
            "tracer.views.dashboard": dashboard,
        }

        def qualify_model_values(_client, *, lane, page_size):
            return {
                "continuation_exercised": not lane.startswith("canonical_voice"),
                "page_size": page_size,
            }

        fake_q = types.SimpleNamespace(
            PopulationGap=ProtocolPopulationGap,
            _qualify_model_values=qualify_model_values,
            _discover_system_model=lambda *_args, **_kwargs: ("model", "string"),
            _system_model_filter=lambda *_args: {},
            _snapshot_counts=lambda: {},
            MAX_REQUESTS=600,
            MAX_CH_READS=4096,
            QUALIFIER_WALL_SECONDS=5280,
        )

        gaps = []
        seal_handoff = mock.Mock()
        write_handoff = mock.Mock()

        def record_gap(lane, reason_code, required, _exc=None):
            gaps.append(
                {
                    "lane": lane,
                    "reason_code": reason_code,
                    "required": required,
                }
            )

        source_path = PACKAGE_DIR / "kartik_smoke_0816h.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"), source_path.name)
        future = next(
            node
            for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.module == "__future__"
        )
        registry = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "registry_phase"
        )
        namespace = {
            "q": fake_q,
            "SafetyViolation": ProtocolSafetyViolation,
            "required": lambda _scope, operation: operation(),
            "key_protocol": lambda _client, _lane: (
                "custom.key",
                "value",
                "string",
                {"continuation_exercised": True},
            ),
            "metrics_protocol": lambda *_args: {"continuation_exercised": True},
            "gap": record_gap,
            "gaps": gaps,
            "failures": [],
            "_with_model_value_lookback": protocol["_with_model_value_lookback"],
            "MODEL_VALUES_QUALIFICATION_PAGE_SIZE": page_size,
            "guard_and_timings": lambda: (
                {
                    "property_keys": {},
                    "filter_values": {},
                    "metrics": {},
                },
                [],
            ),
            "common_evidence": lambda _context: {
                "targets": {
                    "canonical_voice": {},
                    "canonical_trace": {},
                }
            },
            "metrics_output": lambda evidence: evidence,
            "query_evidence": lambda *_args: {},
            "seal_handoff": seal_handoff,
            "write_handoff": write_handoff,
        }
        exec(
            compile(
                ast.Module(body=[future, registry], type_ignores=[]),
                source_path.name,
                "exec",
            ),
            namespace,
        )
        context = {
            "voice_client": types.SimpleNamespace(name="voice"),
            "trace_client": types.SimpleNamespace(name="trace"),
        }
        with mock.patch.dict(sys.modules, modules):
            result = namespace["registry_phase"](context)
        self.assertEqual(
            gaps,
            [
                {
                    "lane": "canonical_voice.model_values",
                    "reason_code": "TERMINAL_MODEL_PAGE",
                    "required": True,
                }
            ],
        )
        self.assertFalse(result["coverage_complete"])
        self.assertEqual(result["coverage_exit_code"], 1)
        self.assertEqual(result["required_population_gap_count"], 1)
        self.assertEqual(
            result["registry"],
            {
                "executed": True,
                "passed": False,
                "handoff_created": False,
                "handoff_sha256": None,
            },
        )
        seal_handoff.assert_not_called()
        write_handoff.assert_not_called()
        self.assertFalse(hasattr(settings, "FILTER_VALUES_DEFAULT_LOOKBACK_DAYS"))

    def test_model_lookback_restores_on_baseexception_and_fails_on_drift(self):
        protocol = load_wrapper_protocol(types.SimpleNamespace())
        frozen = protocol["MODEL_VALUES_FROZEN_LOOKBACK_DAYS"]
        effective = protocol["MODEL_VALUES_DEV_LOOKBACK_DAYS"]
        settings = types.SimpleNamespace()
        dashboard_view = types.SimpleNamespace(
            FILTER_VALUES_DEFAULT_LOOKBACK_DAYS=frozen
        )
        django = types.ModuleType("django")
        django_conf = types.ModuleType("django.conf")
        django_conf.settings = settings
        django.conf = django_conf
        tracer = types.ModuleType("tracer")
        tracer_views = types.ModuleType("tracer.views")
        dashboard = types.ModuleType("tracer.views.dashboard")
        dashboard.DashboardViewSet = dashboard_view
        tracer.views = tracer_views
        tracer_views.dashboard = dashboard
        modules = {
            "django": django,
            "django.conf": django_conf,
            "tracer": tracer,
            "tracer.views": tracer_views,
            "tracer.views.dashboard": dashboard,
        }
        scope = protocol["_with_model_value_lookback"]
        with mock.patch.dict(sys.modules, modules):
            with self.assertRaises(KeyboardInterrupt):
                scope(lambda: (_ for _ in ()).throw(KeyboardInterrupt()))
        self.assertFalse(hasattr(settings, "FILTER_VALUES_DEFAULT_LOOKBACK_DAYS"))

        dashboard_view.FILTER_VALUES_DEFAULT_LOOKBACK_DAYS = frozen + 1
        operation = mock.Mock()
        with mock.patch.dict(sys.modules, modules):
            with self.assertRaisesRegex(ProtocolSafetyViolation, "baseline drifted"):
                scope(operation)
        operation.assert_not_called()

        dashboard_view.FILTER_VALUES_DEFAULT_LOOKBACK_DAYS = float(frozen)
        with mock.patch.dict(sys.modules, modules):
            with self.assertRaisesRegex(ProtocolSafetyViolation, "baseline drifted"):
                scope(operation)
        operation.assert_not_called()

        dashboard_view.FILTER_VALUES_DEFAULT_LOOKBACK_DAYS = frozen

        settings.FILTER_VALUES_DEFAULT_LOOKBACK_DAYS = frozen

        def present_setting_operation():
            self.assertEqual(settings.FILTER_VALUES_DEFAULT_LOOKBACK_DAYS, effective)
            return "restored"

        with mock.patch.dict(sys.modules, modules):
            value, _evidence = scope(present_setting_operation)
        self.assertEqual(value, "restored")
        self.assertTrue(hasattr(settings, "FILTER_VALUES_DEFAULT_LOOKBACK_DAYS"))
        self.assertEqual(settings.FILTER_VALUES_DEFAULT_LOOKBACK_DAYS, frozen)
        del settings.FILTER_VALUES_DEFAULT_LOOKBACK_DAYS

        def drift():
            settings.FILTER_VALUES_DEFAULT_LOOKBACK_DAYS = effective - 2
            return "unsafe"

        with mock.patch.dict(sys.modules, modules):
            with self.assertRaisesRegex(ProtocolSafetyViolation, "lifecycle drifted"):
                scope(drift)
        self.assertFalse(hasattr(settings, "FILTER_VALUES_DEFAULT_LOOKBACK_DAYS"))
        self.assertEqual(dashboard_view.FILTER_VALUES_DEFAULT_LOOKBACK_DAYS, frozen)

    def test_key_profile_skips_invalid_and_valueless_candidates_boundedly(self):
        fake_q = types.SimpleNamespace(
            DirectDRFClient=object,
            QualificationFailure=ProtocolQualificationFailure,
            PopulationGap=ProtocolPopulationGap,
            _qualify_key_read_more=mock.Mock(
                return_value={"qualified": True, "continuation_exercised": True}
            ),
            _property_key_page=mock.Mock(
                return_value=(
                    [
                        {"key": ["unsafe"], "type": "string"},
                        {"key": "custom.empty", "type": "string"},
                        {"key": "custom.ready", "type": "boolean"},
                    ],
                    {},
                )
            ),
            _require_status=lambda response, _lane: response.data,
            _digest=lambda value, length=16: f"{value}-{length}",
        )
        protocol = load_wrapper_protocol(fake_q)

        class Client:
            project = types.SimpleNamespace(id="project-2")

            def __init__(self):
                self.calls = []

            def call(self, endpoint, *, lane, query):
                self.calls.append((endpoint, lane, query))
                values = [] if len(self.calls) == 1 else [{"value": False}]
                return types.SimpleNamespace(status_code=200, data={"values": values})

        client = Client()
        result = protocol["key_protocol"](client, "canonical_trace.property_keys")
        self.assertEqual(result[:3], ("custom.ready", False, "boolean"))
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(client.calls[0][2]["metric_name"], "custom.empty")
        self.assertEqual(client.calls[1][2]["metric_name"], "custom.ready")

    def test_candidate_profile_fails_closed_on_malformed_value_payload(self):
        fake_q = types.SimpleNamespace(
            DirectDRFClient=object,
            QualificationFailure=ProtocolQualificationFailure,
            PopulationGap=ProtocolPopulationGap,
            _require_status=lambda response, _lane: response.data,
        )
        profile = load_wrapper_protocol(fake_q)["_profile_from_property_candidate"]

        class Client:
            project = types.SimpleNamespace(id="project-3")

            def __init__(self):
                self.calls = 0

            def call(self, _endpoint, *, lane, query):
                self.calls += 1
                return types.SimpleNamespace(status_code=200, data={"values": {}})

        client = Client()
        with self.assertRaisesRegex(ProtocolQualificationFailure, "omitted values"):
            profile(
                client,
                {"key": "custom.bad", "type": "string"},
                lane="profile",
            )
        self.assertEqual(client.calls, 1)
        with self.assertRaisesRegex(
            ProtocolQualificationFailure, "not a scalar custom key"
        ):
            profile(
                client,
                {"key": "custom.bad", "type": "object"},
                lane="profile",
            )
        self.assertEqual(client.calls, 1)

    def test_catalog_population_ctes_use_explicit_analyzer_aliases(self):
        source = (PACKAGE_DIR / "kartik_smoke_0816h.py").read_text()
        for fragment in (
            "FROM project_definition_rows AS binding",
            "max(tuple(binding.catalog_revision, binding.source_version))",
            "FROM latest_binding_rows AS binding",
            "GROUP BY binding.visibility_id, binding.binding_id",
        ):
            self.assertIn(fragment, source)
        self.assertEqual(source.count("rows.visibility_id AS visibility_id"), 2)

    def test_source_grant_protocol_binds_self_show_grants_and_static_union(self):
        protocol = load_wrapper_grant_protocol()
        self.assertTrue(protocol["_is_exact_self_show_grants"]("SHOW GRANTS"))
        for query in (
            " SHOW GRANTS",
            "SHOW\nGRANTS",
            "SHOW GRANTS ",
            "SHOW GRANTS FOR source_reader",
            "SHOW USERS",
            "SHOW GRANTS; SELECT 1",
            "SHOW GRANTS -- comment",
            "SHOW GRANTS /* comment */",
            "/* comment */ SHOW GRANTS",
            "show grants",
        ):
            self.assertFalse(protocol["_is_exact_self_show_grants"](query))

        guard_calls = []

        def original_guard(query):
            guard_calls.append(query)
            if not query.startswith(("SELECT", "WITH")):
                raise ProtocolSafetyViolation("ClickHouse non-read statement blocked")

        request_count = {"value": 0}
        fake_q = types.SimpleNamespace(
            assert_ch_read=original_guard,
            _snapshot_counts=lambda: {"requests": request_count["value"]},
            _request_records=[],
        )
        protocol["q"] = fake_q
        normalized = [
            "GRANT SELECT ON futureagi.spans TO <SOURCE_ROLE>",
            "GRANT SELECT(_peerdb_is_deleted, _peerdb_version, created_at, "
            "custom_eval_config_id, deleted, error, id, observation_span_id, "
            "output_bool, output_float, output_str, output_str_list, "
            "skipped_reason, status, trace_id) ON futureagi.tracer_eval_logger "
            "TO <SOURCE_ROLE>",
            "GRANT SELECT(_peerdb_is_deleted, _peerdb_version, created_at, "
            "deleted, id, label_id, observation_span_id, trace_id, value) ON "
            "futureagi.model_hub_score TO <SOURCE_ROLE>",
            "GRANT SELECT(_version, id, is_deleted, project_id, tags) ON "
            "futureagi.traces TO <SOURCE_ROLE>",
            "GRANT SELECT(active, database, min_time, `table`) ON system.parts "
            "TO <SOURCE_ROLE>",
            "GRANT SELECT(end_user_id, is_deleted, project_id, user_id, version) "
            "ON futureagi.end_users TO <SOURCE_ROLE>",
            "GRANT SELECT(new_id, old_id) ON futureagi.end_user_id_remap "
            "TO <SOURCE_ROLE>",
            "GRANT dictGet ON futureagi.end_users_dict TO <SOURCE_ROLE>",
        ]
        wire = "".join(f"{line}\n" for line in sorted(normalized)).encode("ascii")
        self.assertEqual(
            hashlib.sha256(wire).hexdigest(),
            runner.SOURCE_SHOW_GRANTS_NORMALIZED_SHA256,
        )
        principal = "source_reader"
        grants = [
            (line.replace("<SOURCE_ROLE>", principal),) for line in reversed(normalized)
        ]
        grants[3] = (grants[3][0][: -len(principal)] + f"`{principal}`",)

        class Client:
            def __init__(self, roles=None, rows=None):
                self.roles = [] if roles is None else roles
                self.rows = grants if rows is None else rows

            def execute(self, sql):
                fake_q.assert_ch_read(sql)
                if sql == "SELECT currentRoles()":
                    return [(self.roles,)]
                if sql == "SHOW GRANTS":
                    return self.rows
                raise AssertionError(f"unexpected SQL: {sql}")

        evidence = protocol["_source_grant_audit"](Client(), principal, principal)
        self.assertIs(fake_q.assert_ch_read, original_guard)
        self.assertEqual(guard_calls, ["SELECT currentRoles()"])
        self.assertEqual(
            evidence,
            {
                "grant_inventory_sha256": runner.SOURCE_GRANT_INVENTORY_SHA256,
                "show_grants_normalized_count": 8,
                "show_grants_normalized_sha256": (
                    runner.SOURCE_SHOW_GRANTS_NORMALIZED_SHA256
                ),
                "active_role_count": 0,
            },
        )
        system_inventory = protocol["_source_system_grant_inventory"]()
        system_wire = (
            json.dumps(
                system_inventory,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            )
            + "\n"
        ).encode("ascii")
        self.assertEqual(len(system_inventory), 42)
        self.assertEqual(
            hashlib.sha256(system_wire).hexdigest(),
            runner.SOURCE_SYSTEM_GRANTS_CANONICAL_SHA256,
        )
        contract = json.loads(
            (PACKAGE_DIR / "kartik_smoke_0816h_run_contract.json").read_text()
        )
        database_contract = contract["database_contract"]
        self.assertEqual(
            database_contract["source_grant_inventory"],
            protocol["_source_grant_inventory"](),
        )
        self.assertEqual(
            database_contract["source_grant_inventory_sha256"],
            protocol["_source_grant_inventory_sha256"](),
        )
        self.assertEqual(
            database_contract["source_probes"], list(protocol["SOURCE_PROBES"])
        )
        self.assertEqual(database_contract["source_probes"], list(runner.SOURCE_PROBES))
        self.assertEqual(
            database_contract["source_probe_kinds"],
            list(protocol["SOURCE_PROBE_KINDS"]),
        )
        self.assertEqual(
            database_contract["source_probe_count"],
            len(protocol["SOURCE_PROBES"]),
        )

        invalid_cases = (
            (Client(roles=["unexpected"]), principal, "active role"),
            (
                Client(
                    rows=grants + [(f"GRANT SELECT ON futureagi.extra TO {principal}",)]
                ),
                principal,
                "SHOW GRANTS inventory",
            ),
            (
                Client(rows=[*grants[:-1], (grants[-1][0] + " WITH GRANT OPTION",)]),
                principal,
                "SHOW GRANTS content",
            ),
            (Client(), "different_reader", "principal shape"),
        )
        for client, expected, message in invalid_cases:
            with (
                self.subTest(message=message),
                self.assertRaisesRegex(ProtocolSafetyViolation, message),
            ):
                protocol["_source_grant_audit"](client, principal, expected)

        request_count["value"] = 1
        with self.assertRaisesRegex(ProtocolSafetyViolation, "pre-callback scope"):
            protocol["_execute_self_show_grants"](Client())
        request_count["value"] = 0
        fake_q._request_records.append({"completed": True})
        with self.assertRaisesRegex(ProtocolSafetyViolation, "pre-callback scope"):
            protocol["_execute_self_show_grants"](Client())
        fake_q._request_records.clear()

        class FailingShowClient:
            def execute(self, sql):
                fake_q.assert_ch_read(sql)
                raise ProtocolSafetyViolation("offline SHOW failure")

        with self.assertRaisesRegex(ProtocolSafetyViolation, "offline SHOW failure"):
            protocol["_execute_self_show_grants"](FailingShowClient())
        self.assertIs(fake_q.assert_ch_read, original_guard)

        class InterruptingShowClient:
            def execute(self, sql):
                fake_q.assert_ch_read(sql)
                raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            protocol["_execute_self_show_grants"](InterruptingShowClient())
        self.assertIs(fake_q.assert_ch_read, original_guard)

        class BindingDriftClient:
            def execute(self, sql):
                fake_q.assert_ch_read(sql)
                fake_q.assert_ch_read = lambda _query: None
                return []

        with self.assertRaisesRegex(ProtocolSafetyViolation, "guard scope drifted"):
            protocol["_execute_self_show_grants"](BindingDriftClient())
        self.assertIs(fake_q.assert_ch_read, original_guard)

        class RequestDriftClient:
            def execute(self, sql):
                fake_q.assert_ch_read(sql)
                request_count["value"] += 1
                return []

        with self.assertRaisesRegex(ProtocolSafetyViolation, "guard scope drifted"):
            protocol["_execute_self_show_grants"](RequestDriftClient())
        self.assertIs(fake_q.assert_ch_read, original_guard)
        request_count["value"] = 0

        class CallbackDriftClient:
            def execute(self, sql):
                fake_q.assert_ch_read(sql)
                fake_q._request_records.append({"unexpected": True})
                return []

        with self.assertRaisesRegex(ProtocolSafetyViolation, "guard scope drifted"):
            protocol["_execute_self_show_grants"](CallbackDriftClient())
        self.assertIs(fake_q.assert_ch_read, original_guard)
        fake_q._request_records.clear()

    def test_self_show_grants_scope_uses_and_restores_real_installed_guard(self):
        protocol = load_wrapper_grant_protocol()
        qualify = load_real_qualifier_guard_module()
        native_calls = []

        class NativeClient:
            def __init__(self):
                self.interrupt = False

            def execute(self, query, *args, **kwargs):
                native_calls.append((query, args, kwargs))
                if self.interrupt:
                    raise KeyboardInterrupt
                return query

            def execute_iter(self, _query, *_args, **_kwargs):
                return iter(())

        class HTTPClient:
            def query(self, _query, *_args, **_kwargs):
                return None

            def command(self, _query, *_args, **_kwargs):
                return None

            def raw_query(self, _query, *_args, **_kwargs):
                return None

        native_module = types.ModuleType("clickhouse_driver")
        native_module.Client = NativeClient
        connect_module = types.ModuleType("clickhouse_connect")
        connect_module.__path__ = []
        connect_driver_module = types.ModuleType("clickhouse_connect.driver")
        connect_driver_module.__path__ = []
        connect_client_module = types.ModuleType("clickhouse_connect.driver.client")
        connect_client_module.Client = HTTPClient
        connect_module.driver = connect_driver_module
        connect_driver_module.client = connect_client_module
        fake_modules = {
            "clickhouse_driver": native_module,
            "clickhouse_connect": connect_module,
            "clickhouse_connect.driver": connect_driver_module,
            "clickhouse_connect.driver.client": connect_client_module,
        }
        with qualify._lock:
            qualify._counts["requests"] = 0
            qualify._counts["ch_read"] = 0
            qualify._counts["ch_blocked"] = 0
        qualify._request_records.clear()
        original_guard = qualify.assert_ch_read
        protocol["q"] = qualify
        with mock.patch.dict(sys.modules, fake_modules):
            qualify._install_ch_guard()
            client = NativeClient()
            with self.assertRaises(qualify.SafetyViolation):
                client.execute("SHOW GRANTS")
            self.assertEqual(
                protocol["_execute_self_show_grants"](client), "SHOW GRANTS"
            )
            self.assertIs(qualify.assert_ch_read, original_guard)
            self.assertEqual([call[0] for call in native_calls], ["SHOW GRANTS"])

            for query in (
                "SHOW GRANTS FOR source_reader",
                "SHOW\nGRANTS",
                "SHOW GRANTS; SELECT 1",
                "GRANT SELECT ON futureagi.spans TO source_reader",
                "REVOKE SELECT ON futureagi.spans FROM source_reader",
            ):
                with (
                    self.subTest(query=query),
                    self.assertRaises(qualify.SafetyViolation),
                ):
                    client.execute(query)

            client.interrupt = True
            with self.assertRaises(KeyboardInterrupt):
                protocol["_execute_self_show_grants"](client)
            self.assertIs(qualify.assert_ch_read, original_guard)
            with self.assertRaises(qualify.SafetyViolation):
                client.execute("SHOW GRANTS")

    def test_fully_bound_contract_reaches_green_validator(self):
        with self.temp_root() as raw:
            root = Path(raw)
            for name in ("bundle", "run", "evidence"):
                (root / name).mkdir(mode=0o700)
            source = (PACKAGE_DIR / "kartik_smoke_0816h_run_contract.json").read_text()
            contract = json.loads(source)
            wrapper_path = root / "bundle/kartik-smoke-0816h.py"
            runner_path = root / "bundle/phase-runner-0816h.py"
            contract_path = root / "bundle/kartik-smoke-0816h-run-contract.json"
            wrapper_path.write_bytes(
                b"#!/usr/bin/env python3\n# bound wrapper fixture\n"
            )
            runner_path.write_bytes(Path(runner.__file__).read_bytes())
            wrapper_path.chmod(0o600)
            runner_path.chmod(0o600)
            wrapper_hash = hashlib.sha256(wrapper_path.read_bytes()).hexdigest()
            runner_hash = hashlib.sha256(runner_path.read_bytes()).hexdigest()
            image_id = "sha256:" + "a" * 64
            contract["pins"]["image_id"] = image_id
            contract["pins"]["wrapper_sha256"] = wrapper_hash
            contract["pins"]["phase_runner_sha256"] = runner_hash
            contract["minimal_environment"]["fixed_pin_values"]["EXPECTED_IMAGE_ID"] = (
                image_id
            )
            contract["minimal_environment"]["fixed_pin_values"][
                "EXPECTED_KARTIK_SMOKE_0816H_SHA256"
            ] = wrapper_hash
            contract["binding_state"] = {
                "state": "BOUND_AUDITED_DEV_GO",
                "placeholder_count_remaining": 0,
                "independent_static_and_runtime_preflight_audit_passed": True,
                "human_DEV_approval_recorded": True,
            }
            contract["status"] = "BOUND_AUDITED_DEV_GO"
            contract["execution_authorized"] = True
            contract["execution"]["approval_required"] = False
            with mock.patch.object(runner, "RUN_ROOT", root):
                expected = runner._expected_paths()
                contract["minimal_environment"]["host_env_file"] = str(expected["env"])
                contract["output_capture"]["env_key_attestation_path"] = str(
                    expected["attestation"]
                )
                keys = tuple(contract["minimal_environment"]["exact_keys"])
                for phase in ("registry", "matrix"):
                    section = contract["phase_execution"][phase]
                    section["stdout_path"] = str(expected[f"{phase}.stdout"])
                    section["stderr_path"] = str(expected[f"{phase}.stderr"])
                    section["exit_code_path"] = str(expected[f"{phase}.exit"])
                    section["argv_record_path"] = str(expected[f"{phase}.argv"])
                    section["exact_docker_exec_argv"] = runner._expected_phase_argv(
                        keys, phase
                    )
                contract["cleanup_plan"]["retain_mode_0600"] = [
                    str(path)
                    for name, path in sorted(expected.items())
                    if name
                    in {
                        "contract",
                        "wrapper",
                        "runner",
                        "attestation",
                        "registry.stdout",
                        "registry.stderr",
                        "registry.exit",
                        "registry.argv",
                        "matrix.stdout",
                        "matrix.stderr",
                        "matrix.exit",
                        "matrix.argv",
                    }
                ]
                contract_path.write_bytes(runner._canonical_json(contract))
                contract_path.chmod(0o600)
                with mock.patch.object(runner, "__file__", str(runner_path)):
                    loaded, loaded_keys, env_path = runner._validate_contract(
                        contract_path
                    )
            self.assertTrue(loaded["execution_authorized"])
            self.assertEqual(len(loaded_keys), 68)
            self.assertEqual(env_path, root / "run/kartik-smoke-0816h.env")

    def test_frozen_contract_hash_pins_and_source_closure_are_exact(self):
        contract_path = PACKAGE_DIR / "kartik_smoke_0816h_run_contract.json"
        contract = runner._decode_json(contract_path.read_bytes())
        immutable = contract["pins"]
        wrapper_sha256 = hashlib.sha256(
            (PACKAGE_DIR / "kartik_smoke_0816h.py").read_bytes()
        ).hexdigest()
        runner_sha256 = hashlib.sha256(
            (PACKAGE_DIR / "phase_runner.py").read_bytes()
        ).hexdigest()
        self.assertEqual(immutable["wrapper_sha256"], wrapper_sha256)
        self.assertEqual(immutable["phase_runner_sha256"], runner_sha256)
        self.assertEqual(
            contract["wrapper"]["runtime_sha256_must_equal"], wrapper_sha256
        )
        self.assertEqual(
            contract["minimal_environment"]["fixed_pin_values"][
                "EXPECTED_KARTIK_SMOKE_0816H_SHA256"
            ],
            wrapper_sha256,
        )
        self.assertEqual(
            immutable["source_grant_inventory_sha256"],
            runner.SOURCE_GRANT_INVENTORY_SHA256,
        )
        self.assertEqual(
            immutable["source_show_grants_normalized_sha256"],
            runner.SOURCE_SHOW_GRANTS_NORMALIZED_SHA256,
        )
        self.assertEqual(
            immutable["source_system_grants_canonical_sha256"],
            runner.SOURCE_SYSTEM_GRANTS_CANONICAL_SHA256,
        )
        self.assertNotIn(b"__PENDING_0816H_", contract_path.read_bytes())

    def test_contract_rejects_each_wrapper_runner_and_fixed_env_hash_drift(self):
        cases = (
            ("wrapper", "host wrapper hash"),
            ("runner", "phase runner hash"),
            ("fixed_env", "fixed environment pins"),
        )
        for case, message in cases:
            with self.subTest(case=case), self.temp_root() as raw:
                root = Path(raw)
                contract, expected = self.write_bound_contract_fixture(root)
                if case == "wrapper":
                    expected["wrapper"].write_bytes(
                        expected["wrapper"].read_bytes() + b"\n# wrapper drift\n"
                    )
                    expected["wrapper"].chmod(0o600)
                elif case == "runner":
                    expected["runner"].write_bytes(
                        expected["runner"].read_bytes() + b"\n# runner drift\n"
                    )
                    expected["runner"].chmod(0o600)
                else:
                    contract["minimal_environment"]["fixed_pin_values"][
                        "EXPECTED_KARTIK_SMOKE_0816H_SHA256"
                    ] = "0" * 64
                    expected["contract"].write_bytes(runner._canonical_json(contract))
                    expected["contract"].chmod(0o600)
                with (
                    mock.patch.object(runner, "RUN_ROOT", root),
                    mock.patch.object(runner, "__file__", str(expected["runner"])),
                    self.assertRaisesRegex(runner.RunnerViolation, message),
                ):
                    runner._validate_contract(expected["contract"])

    def test_contract_rejects_source_role_closure_drift(self):
        cases = (
            ("pin", "closure hash pin"),
            ("inventory", "closure contract"),
            ("probe_count_bool", "closure contract"),
            ("probes_replaced", "closure contract"),
            ("probes_reordered", "closure contract"),
            ("probes_duplicated", "closure contract"),
            ("probe_kind_order", "closure contract"),
            ("show_count_bool", "closure contract"),
            ("admin_system_count", "closure contract"),
            ("admin_role_count_bool", "closure contract"),
        )
        for case, message in cases:
            with self.subTest(case=case), self.temp_root() as raw:
                root = Path(raw)
                contract, expected = self.write_bound_contract_fixture(root)
                database_contract = contract["database_contract"]
                if case == "pin":
                    contract["pins"]["source_show_grants_normalized_sha256"] = "0" * 64
                elif case == "inventory":
                    database_contract["source_grant_inventory"]["select"][0][
                        "columns"
                    ].append("unexpected")
                elif case == "probe_count_bool":
                    database_contract["source_probe_count"] = True
                elif case == "probes_replaced":
                    database_contract["source_probes"] = ["SELECT 1"]
                elif case == "probes_reordered":
                    database_contract["source_probes"].reverse()
                elif case == "probes_duplicated":
                    database_contract["source_probes"].append(
                        database_contract["source_probes"][0]
                    )
                elif case == "probe_kind_order":
                    database_contract["source_probe_kinds"].reverse()
                elif case == "show_count_bool":
                    database_contract["source_show_grants_normalized_count"] = True
                elif case == "admin_system_count":
                    database_contract["source_admin_grant_preflight"][
                        "system_grants_canonical_row_count"
                    ] = 41
                else:
                    database_contract["source_admin_grant_preflight"][
                        "role_grants_count"
                    ] = False
                expected["contract"].write_bytes(runner._canonical_json(contract))
                expected["contract"].chmod(0o600)
                with (
                    mock.patch.object(runner, "RUN_ROOT", root),
                    mock.patch.object(runner, "__file__", str(expected["runner"])),
                    self.assertRaisesRegex(runner.RunnerViolation, message),
                ):
                    runner._validate_contract(expected["contract"])

    def test_contract_rejects_bool_for_int_authorization_fields(self):
        cases = ("placeholder_count_remaining", "catalog_revision")
        for case in cases:
            with self.subTest(case=case), self.temp_root() as raw:
                root = Path(raw)
                contract, expected = self.write_bound_contract_fixture(root)
                if case == "placeholder_count_remaining":
                    contract["binding_state"]["placeholder_count_remaining"] = False
                    message = "authorization"
                else:
                    contract["pins"]["catalog_revision"] = True
                    message = "catalog database or activation revision"
                expected["contract"].write_bytes(runner._canonical_json(contract))
                expected["contract"].chmod(0o600)
                with (
                    mock.patch.object(runner, "RUN_ROOT", root),
                    mock.patch.object(runner, "__file__", str(expected["runner"])),
                    self.assertRaisesRegex(runner.RunnerViolation, message),
                ):
                    runner._validate_contract(expected["contract"])

    def test_common_validator_rejects_source_role_closure_evidence_drift(self):
        cases = (
            ("probe_count", True),
            ("probe_kinds", list(reversed(runner.SOURCE_PROBE_KINDS))),
            ("grant_inventory_sha256", "0" * 64),
            ("show_grants_normalized_count", True),
            ("show_grants_normalized_sha256", "0" * 64),
            ("active_role_count", 1),
            ("query_probe_count", 7),
            ("query_probe_kinds", [*runner.SOURCE_PROBE_KINDS, "unexpected"]),
            ("query_inventory", {"select": [], "dictGet": []}),
            ("query_inventory_sha256", "0" * 64),
        )
        for phase in ("registry", "matrix"):
            valid = registry_payload() if phase == "registry" else matrix_payload()
            for field, value in cases:
                with self.subTest(phase=phase, field=field):
                    invalid = copy.deepcopy(valid)
                    source = invalid["database_identity_audit"]["source"]
                    closure = source["grant_closure"]
                    queries = invalid["query_kinds"]
                    if field == "probe_count":
                        source["probe_count"] = value
                    elif field == "probe_kinds":
                        source["probe_kinds"] = value
                    elif field.startswith("query_"):
                        query_field = {
                            "query_probe_count": "source_probe_count",
                            "query_probe_kinds": "source_probe_kinds",
                            "query_inventory": "source_grant_inventory",
                            "query_inventory_sha256": ("source_grant_inventory_sha256"),
                        }[field]
                        queries[query_field] = value
                    else:
                        closure[field] = value
                    with self.assertRaisesRegex(
                        runner.RunnerViolation, "source-role closure evidence"
                    ):
                        runner._validate_common(invalid, phase, minimal_contract())

    def test_source_probe_contract_drift_blocks_all_phase_execution(self):
        for case in ("replaced", "reordered", "duplicated"):
            with self.subTest(case=case), self.temp_root() as raw:
                root = Path(raw)
                contract, expected = self.write_bound_contract_fixture(root)
                probes = contract["database_contract"]["source_probes"]
                if case == "replaced":
                    contract["database_contract"]["source_probes"] = ["SELECT 1"]
                elif case == "reordered":
                    probes.reverse()
                else:
                    probes.append(probes[0])
                expected["contract"].write_bytes(runner._canonical_json(contract))
                expected["contract"].chmod(0o600)
                calls = []

                def execute(_contract, phase):
                    calls.append(phase)
                    return capture(phase, registry_payload())

                with (
                    mock.patch.object(runner, "RUN_ROOT", root),
                    mock.patch.object(runner, "__file__", str(expected["runner"])),
                    self.assertRaisesRegex(
                        runner.RunnerViolation, "source-role closure contract"
                    ),
                ):
                    runner.run_contract(
                        expected["contract"],
                        execute_phase=execute,
                        inspect_image_binding=image_binding,
                    )
                self.assertEqual(calls, [])

    def test_registry_validator_requires_scoped_model_continuation_proof(self):
        contract = minimal_contract()
        valid = registry_payload()
        self.assertEqual(
            runner._validate_registry(capture("registry", valid), contract),
            HANDOFF,
        )
        cases = (
            ("page_size", True),
            ("page_size", 10),
            ("p1_values", 0),
            ("p2_values", 0),
            ("continuation_exercised", False),
            ("lookback_effective_days", 7),
            ("lookback_restored_on_return", False),
        )
        for key, value in cases:
            with self.subTest(key=key, value=value):
                invalid = copy.deepcopy(valid)
                invalid["targets"]["canonical_voice"]["model_values"][key] = value
                with self.assertRaisesRegex(
                    runner.RunnerViolation,
                    "registry result did not satisfy the matrix start gate",
                ):
                    runner._validate_registry(
                        capture("registry", invalid),
                        contract,
                    )

    def test_matrix_validator_requires_exact_108_unique_identities(self):
        contract = minimal_contract()
        valid = matrix_payload()
        self.assertEqual(
            runner._validate_matrix(capture("matrix", valid), contract), HANDOFF
        )
        duplicate = copy.deepcopy(valid)
        duplicate["matrix"]["cells"][-1] = copy.deepcopy(
            duplicate["matrix"]["cells"][0]
        )
        with self.assertRaisesRegex(runner.RunnerViolation, "108-cell"):
            runner._validate_matrix(capture("matrix", duplicate), contract)
        slow = copy.deepcopy(valid)
        slow["timings_by_route"]["trace_list"]["max_s"] = 9.8
        with self.assertRaisesRegex(runner.RunnerViolation, "108-cell"):
            runner._validate_matrix(capture("matrix", slow), contract)

    def test_registry_failure_makes_matrix_command_unreachable(self):
        with self.temp_root() as raw:
            root = Path(raw)
            for name in ("bundle", "run", "evidence"):
                (root / name).mkdir(mode=0o700)
            bad = registry_payload()
            bad["registry"]["passed"] = False
            calls: list[str] = []

            def execute(_contract, phase):
                calls.append(phase)
                return capture(phase, bad if phase == "registry" else matrix_payload())

            with (
                mock.patch.object(runner, "RUN_ROOT", root),
                mock.patch.object(
                    runner,
                    "_validate_contract",
                    return_value=(minimal_contract(), tuple(), root / "run/env"),
                ),
                mock.patch.object(runner, "_env_snapshot", return_value=snapshot()),
            ):
                with self.assertRaisesRegex(runner.RunnerViolation, "start gate"):
                    runner.run_contract(
                        root / "ignored",
                        execute_phase=execute,
                        inspect_image_binding=image_binding,
                    )
            self.assertEqual(calls, ["registry"])

            calls.clear()
            bad_closure = registry_payload()
            bad_closure["database_identity_audit"]["source"]["grant_closure"][
                "show_grants_normalized_sha256"
            ] = "0" * 64

            def execute_bad_closure(_contract, phase):
                calls.append(phase)
                return capture(
                    phase,
                    bad_closure if phase == "registry" else matrix_payload(),
                )

            with (
                mock.patch.object(runner, "RUN_ROOT", root),
                mock.patch.object(
                    runner,
                    "_validate_contract",
                    return_value=(minimal_contract(), tuple(), root / "run/env"),
                ),
                mock.patch.object(runner, "_env_snapshot", return_value=snapshot()),
            ):
                with self.assertRaisesRegex(
                    runner.RunnerViolation, "source-role closure evidence"
                ):
                    runner.run_contract(
                        root / "ignored",
                        execute_phase=execute_bad_closure,
                        inspect_image_binding=image_binding,
                    )
            self.assertEqual(calls, ["registry"])

    def test_registry_bool_for_int_drift_cannot_reach_matrix(self):
        cases = (
            "returncode",
            "coverage_exit_code",
            "exit_code",
            "required_population_gap_count",
            "excluded_target_selection_count",
            "excluded_matrix_cell_count",
            "active_catalog_revision",
            "timing_status_count",
        )
        with self.temp_root() as raw:
            root = Path(raw)
            for name in ("bundle", "run", "evidence"):
                (root / name).mkdir(mode=0o700)
            for case in cases:
                with self.subTest(case=case):
                    payload = registry_payload()
                    if case == "coverage_exit_code":
                        payload["coverage_exit_code"] = False
                    elif case == "exit_code":
                        payload["exit_code"] = False
                    elif case == "required_population_gap_count":
                        payload["required_population_gap_count"] = False
                    elif case == "excluded_target_selection_count":
                        payload["excluded_target"]["target_selection_count"] = False
                    elif case == "excluded_matrix_cell_count":
                        payload["excluded_target"]["matrix_cell_count"] = False
                    elif case == "active_catalog_revision":
                        payload["targets"]["canonical_voice"][
                            "project_catalog_population"
                        ]["active_catalog_revision"] = True
                    elif case == "timing_status_count":
                        payload["timings_by_route"]["metrics"]["statuses"]["200"] = True
                    registry_capture = capture("registry", payload)
                    if case == "returncode":
                        registry_capture = runner.PhaseCapture(
                            phase=registry_capture.phase,
                            returncode=False,
                            stdout=registry_capture.stdout,
                            stderr=registry_capture.stderr,
                            stdout_sha256=registry_capture.stdout_sha256,
                            payload=registry_capture.payload,
                        )
                    calls: list[str] = []

                    def execute(_contract, phase):
                        calls.append(phase)
                        return (
                            registry_capture
                            if phase == "registry"
                            else capture("matrix", matrix_payload())
                        )

                    with (
                        mock.patch.object(runner, "RUN_ROOT", root),
                        mock.patch.object(
                            runner,
                            "_validate_contract",
                            return_value=(
                                minimal_contract(),
                                tuple(),
                                root / "run/env",
                            ),
                        ),
                        mock.patch.object(
                            runner, "_env_snapshot", return_value=snapshot()
                        ),
                        self.assertRaises(runner.RunnerViolation),
                    ):
                        runner.run_contract(
                            root / "ignored",
                            execute_phase=execute,
                            inspect_image_binding=image_binding,
                        )
                    self.assertEqual(calls, ["registry"])

    def test_env_change_or_handoff_mismatch_blocks_matrix(self):
        with self.temp_root() as raw:
            root = Path(raw)
            for name in ("bundle", "run", "evidence"):
                (root / name).mkdir(mode=0o700)
            calls: list[str] = []

            def execute(_contract, phase):
                calls.append(phase)
                return capture(phase, registry_payload())

            with (
                mock.patch.object(runner, "RUN_ROOT", root),
                mock.patch.object(
                    runner,
                    "_validate_contract",
                    return_value=(minimal_contract(), tuple(), root / "run/env"),
                ),
                mock.patch.object(
                    runner, "_env_snapshot", side_effect=[snapshot(), snapshot("other")]
                ),
            ):
                with self.assertRaisesRegex(
                    runner.RunnerViolation, "confidential content"
                ):
                    runner.run_contract(
                        root / "ignored",
                        execute_phase=execute,
                        inspect_image_binding=image_binding,
                    )
            self.assertEqual(calls, ["registry"])

            calls.clear()
            with (
                mock.patch.object(runner, "RUN_ROOT", root),
                mock.patch.object(
                    runner,
                    "_validate_contract",
                    return_value=(minimal_contract(), tuple(), root / "run/env"),
                ),
                mock.patch.object(runner, "_env_snapshot", return_value=snapshot()),
            ):
                with self.assertRaisesRegex(runner.RunnerViolation, "digest differ"):
                    runner.run_contract(
                        root / "ignored",
                        execute_phase=execute,
                        probe_handoff=lambda **_kwargs: "0" * 64,
                        inspect_image_binding=image_binding,
                    )
            self.assertEqual(calls, ["registry"])

    def test_green_run_cross_binds_results_and_destroys_handoff(self):
        with self.temp_root() as raw:
            root = Path(raw)
            for name in ("bundle", "run", "evidence"):
                (root / name).mkdir(mode=0o700)
            calls: list[str] = []

            def execute(_contract, phase):
                calls.append(phase)
                payload = (
                    registry_payload() if phase == "registry" else matrix_payload()
                )
                return capture(phase, payload)

            def probe(*, expect_present):
                return HANDOFF if expect_present else None

            with (
                mock.patch.object(runner, "RUN_ROOT", root),
                mock.patch.object(
                    runner,
                    "_validate_contract",
                    return_value=(minimal_contract(), tuple(), root / "run/env"),
                ),
                mock.patch.object(runner, "_env_snapshot", return_value=snapshot()),
            ):
                result = runner.run_contract(
                    root / "ignored",
                    execute_phase=execute,
                    probe_handoff=probe,
                    inspect_image_binding=image_binding,
                )
                self.assertTrue(result["accepted"])
                self.assertEqual(result["matrix_cell_count"], 108)
                attestation = runner._expected_paths()["attestation"]
                self.assertEqual(stat.S_IMODE(attestation.stat().st_mode), 0o600)
                saved = json.loads(attestation.read_text())
                self.assertFalse(
                    saved["env_file"]["confidential_content_sha256_recorded"]
                )
                self.assertNotIn("content_sha256", saved["env_file"])
            self.assertEqual(calls, ["registry", "matrix"])

    def test_cross_binding_mismatch_and_retained_handoff_fail(self):
        with self.temp_root() as raw:
            root = Path(raw)
            for name in ("bundle", "run", "evidence"):
                (root / name).mkdir(mode=0o700)
            changed = matrix_payload()
            changed["run_id"] = "different-run"

            def execute(_contract, phase):
                payload = registry_payload() if phase == "registry" else changed
                return capture(phase, payload)

            with (
                mock.patch.object(runner, "RUN_ROOT", root),
                mock.patch.object(
                    runner,
                    "_validate_contract",
                    return_value=(minimal_contract(), tuple(), root / "run/env"),
                ),
                mock.patch.object(runner, "_env_snapshot", return_value=snapshot()),
            ):
                with self.assertRaisesRegex(runner.RunnerViolation, "bindings differ"):
                    runner.run_contract(
                        root / "ignored",
                        execute_phase=execute,
                        probe_handoff=lambda *, expect_present: (
                            HANDOFF if expect_present else None
                        ),
                        inspect_image_binding=image_binding,
                    )

            # A fresh evidence directory is necessary because captures are O_EXCL.
            (root / "evidence/kartik-smoke-0816h.env-keys.json").unlink()
            with (
                mock.patch.object(runner, "RUN_ROOT", root),
                mock.patch.object(
                    runner,
                    "_validate_contract",
                    return_value=(minimal_contract(), tuple(), root / "run/env"),
                ),
                mock.patch.object(runner, "_env_snapshot", return_value=snapshot()),
            ):
                with self.assertRaisesRegex(runner.RunnerViolation, "destroy"):
                    runner.run_contract(
                        root / "ignored",
                        execute_phase=lambda _contract, phase: capture(
                            phase,
                            registry_payload()
                            if phase == "registry"
                            else matrix_payload(),
                        ),
                        probe_handoff=lambda *, expect_present: HANDOFF,
                        inspect_image_binding=image_binding,
                    )


if __name__ == "__main__":
    unittest.main()
