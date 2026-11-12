import hashlib
import json

import pytest

from simulate.services.canonical_json import canonical_digest
from simulate.services.harness_versioning import hashing
from simulate.services.hosted_harness import HostedHarnessError

# Golden digests computed with ALK ``authoring_runtime_validation._artifact_digest``
# over this exact tree. ``account-status/`` sorts before ``account-status-again/``
# component by component, but after it as a plain string.
TREE = {
    "contract.json": b'{"agent": "support"}\n',
    "handlers/lookup.py": b"def handle(db):\n    return {}\n",
    "scenarios/account-status/scenario.json": b'{"name": "account-status"}\n',
    "scenarios/account-status/checks/looked_up.py": (
        b"def check(world, calls):\n    return True\n"
    ),
    "scenarios/account-status-again/scenario.json": (
        b'{"name": "account-status-again"}\n'
    ),
}
ALK_TREE_DIGEST = (
    "sha256:20cdbb30dc085aa59a54dc6af0748570fbbf07bd3b59f7c5e2223605e2746453"
)
ALK_SCENARIOS_DIGEST = (
    "sha256:d1a74d42848288a5395769e651fad778fab6b32ad0ce77e2a39ccd5226001d84"
)
STRING_SORTED_DIGEST = (
    "sha256:f4f9a24da3f46c767effd60c20fefe2acac44e358d33bee706ae82365efa089b"
)
# Computed with ALK ``outbound.canonical_bytes``.
CANONICAL_VALUE = {"z": [1, 2.5, {"b": "é", "a": None}], "a": True, "名": "値"}
ALK_CANONICAL_DIGEST = (
    "sha256:327018d2dedb65b98703bc138ae79a32393d92172da10e9574085ba9d8889800"
)


def _write(root, files):
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)


def _scenario(**fields):
    return json.dumps({"name": "refund", "instruction": "Ask for a refund", **fields})


class TestTreeDigest:
    def test_matches_alk_artifact_digest(self):
        assert hashing.tree_digest(TREE) == ALK_TREE_DIGEST

    def test_orders_paths_by_component_not_by_string(self):
        assert hashing.tree_digest(TREE) != STRING_SORTED_DIGEST

    def test_reads_a_directory_like_alk(self, tmp_path):
        _write(tmp_path, TREE)

        assert hashing.tree_digest(tmp_path) == ALK_TREE_DIGEST
        assert hashing.tree_digest(tmp_path / "scenarios") == ALK_SCENARIOS_DIGEST

    def test_single_file_hashes_its_bytes(self, tmp_path):
        path = tmp_path / "contract.json"
        path.write_bytes(b"{}")

        assert hashing.tree_digest(path) == (
            "sha256:" + hashlib.sha256(b"{}").hexdigest()
        )

    def test_exclude_skips_paths(self):
        without_contract = {k: v for k, v in TREE.items() if k != "contract.json"}

        assert hashing.tree_digest(
            TREE, exclude=lambda path: path == "contract.json"
        ) == hashing.tree_digest(without_contract)

    def test_normalises_path_spelling(self):
        assert hashing.tree_digest({"./a//b.txt": b"x"}) == hashing.tree_digest(
            {"a/b.txt": b"x"}
        )

    def test_refuses_two_paths_that_name_the_same_file(self):
        with pytest.raises(HostedHarnessError) as error:
            hashing.tree_digest({"a/b.txt": b"x", "./a//b.txt": b"y"})

        assert error.value.code == "archive_member_invalid"

    def test_refuses_a_path_that_is_not_utf8(self):
        name = b"bad-\xff.txt".decode("utf-8", "surrogateescape")

        with pytest.raises(HostedHarnessError) as error:
            hashing.tree_digest({name: b"x"})

        assert error.value.code == "archive_member_invalid"

    def test_canonical_digest_matches_alk_canonical_bytes(self):
        assert canonical_digest(CANONICAL_VALUE) == ALK_CANONICAL_DIGEST


class TestEnvironmentHash:
    @pytest.fixture
    def environment(self):
        return {
            "contract.json": json.dumps(
                {
                    "agent": "support",
                    "system_prompt_excerpt": "Be kind.",
                    "chosen_evals": ["tone"],
                }
            ).encode(),
            "sub_goals.json": b"[]",
            "schema.sql": b"create table orders(id int);",
            "world.sqlite": b"SQLite format 3\x00orders: 1",
            "generic-harness/source-model.json": b'{"source_digest": "aaa"}',
            "generic-harness/world-ir.json": b'{"source_model_fingerprint": "aaa"}',
        }

    def test_unchanged_when_source_model_or_world_ir_change(self, environment):
        before = hashing.environment_hash(environment, provider_agent=False)
        environment["generic-harness/source-model.json"] = b'{"source_digest": "bbb"}'
        environment["generic-harness/world-ir.json"] = (
            b'{"source_model_fingerprint": "bbb"}'
        )

        assert hashing.environment_hash(environment, provider_agent=False) == before

    def test_changes_with_world_files(self, environment):
        before = hashing.environment_hash(environment, provider_agent=False)
        environment["schema.sql"] = b"create table refunds(id int);"

        assert hashing.environment_hash(environment, provider_agent=False) != before

    def test_changes_with_world_data(self, environment):
        # world-ir.json is not hashed: a reseal derives it from world.sqlite.
        before = hashing.environment_hash(environment, provider_agent=False)
        environment["world.sqlite"] = b"SQLite format 3\x00orders: 2"
        assert hashing.environment_hash(environment, provider_agent=False) != before

    def test_ignores_chosen_evals(self, environment):
        before = hashing.environment_hash(environment, provider_agent=False)
        contract = json.loads(environment["contract.json"])
        contract["chosen_evals"] = ["tone", "accuracy"]
        environment["contract.json"] = json.dumps(contract).encode()

        assert hashing.environment_hash(environment, provider_agent=False) == before

    def test_prompt_counts_for_code_agents_only(self, environment):
        code_before = hashing.environment_hash(environment, provider_agent=False)
        provider_before = hashing.environment_hash(environment, provider_agent=True)
        contract = json.loads(environment["contract.json"])
        contract["system_prompt_excerpt"] = "Be brief."
        environment["contract.json"] = json.dumps(contract).encode()

        assert (
            hashing.environment_hash(environment, provider_agent=False) != code_before
        )
        assert hashing.environment_hash(environment, provider_agent=True) == (
            provider_before
        )

    def test_contract_key_order_does_not_matter(self, environment):
        before = hashing.environment_hash(environment, provider_agent=False)
        contract = json.loads(environment["contract.json"])
        environment["contract.json"] = json.dumps(
            dict(reversed(list(contract.items())))
        ).encode()

        assert hashing.environment_hash(environment, provider_agent=False) == before

    @pytest.mark.parametrize(
        "path",
        [
            "runtime-validation.json",
            "cost.json",
            "scenarios.json",
            "coverage.json",
            "scenarios/refund/scenario.json",
            "environment-bundle/manifest.json",
            "generic-harness/certified-bundle/manifest.json",
            "generic-harness/setup-checks-passed.json",
            "generic-harness/a-record-alk-adds-later.json",
            ".futureagi-authoring-basis.json",
            "handlers/__pycache__/lookup.cpython-311.pyc",
        ],
    )
    def test_ignores_build_outputs_suite_files_and_noise(self, environment, path):
        before = hashing.environment_hash(environment, provider_agent=False)
        environment[path] = b"anything"

        assert hashing.environment_hash(environment, provider_agent=False) == before

    def test_malformed_contract_does_not_raise(self, environment):
        environment["contract.json"] = b"{not json"

        assert hashing.environment_hash(environment, provider_agent=False)

    def test_a_valid_contract_hashes_as_its_canonical_value(self, environment):
        contract = json.loads(environment["contract.json"])

        assert hashing.environment_hash(
            environment, provider_agent=True
        ) == canonical_digest(
            {
                "contract": hashing.hashed_contract(contract, provider_agent=True),
                "tree": hashing.tree_digest(
                    environment, exclude=hashing.is_environment_unhashed
                ),
            }
        )
        assert hashing.contract_hash_raw(environment) == canonical_digest(contract)

    @pytest.mark.parametrize(
        "first, second",
        [
            (b"{not json", b"{also not json"),
            (b'{"limit": NaN}', b'{"limit": Infinity}'),
        ],
    )
    def test_contracts_without_a_canonical_form_still_differ(
        self, environment, first, second
    ):
        hashes = []
        for body in (first, second):
            environment["contract.json"] = body
            hashes.append(
                (
                    hashing.environment_hash(environment, provider_agent=False),
                    hashing.contract_hash_raw(environment),
                )
            )

        assert hashes[0][0] != hashes[1][0]
        assert hashes[0][1] != hashes[1][1]
        # Stored in 71-character columns.
        assert all(len(raw) == 71 for _, raw in hashes)

    def test_world_ir_hash_covers_only_the_authored_seed(self, environment):
        before = hashing.world_ir_hash(environment)
        environment["generic-harness/world-ir.json"] = b"{}"
        assert hashing.world_ir_hash(environment) == before

        environment["store.json"] = b'{"orders": []}'
        assert hashing.world_ir_hash(environment) != before


class TestSuiteHashes:
    @pytest.fixture
    def suite(self):
        return {
            "scenarios/refund/scenario.json": _scenario(
                background_noise="cafe",
                max_turns=12,
                persona={"personality": "calm", "age": 40},
            ).encode(),
            "scenarios/refund/setup.py": b"def setup(world):\n    return None\n",
            "scenarios/refund/checks/refunded.py": b"def check(w, c):\n    return 1\n",
            "scenarios.json": b"[]",
            "contract.json": b"{}",
        }

    def test_suite_hash_ignores_checks_and_files_outside_scenarios(self, suite):
        before = hashing.suite_hash(suite)
        suite["scenarios/refund/checks/refunded.py"] = (
            b"def check(w, c):\n    return 0\n"
        )
        suite["scenarios.json"] = b'[{"key": "refund"}]'
        suite["contract.json"] = b'{"agent": "x"}'

        assert hashing.suite_hash(suite) == before

    def test_suite_hash_changes_with_scenario_content(self, suite):
        before = hashing.suite_hash(suite)
        suite["scenarios/refund/setup.py"] = b"def setup(world):\n    world.seed()\n"

        assert hashing.suite_hash(suite) != before

    def test_suite_hash_accepts_the_legacy_scenario_folder(self, suite):
        legacy = {
            path.replace("scenarios/", "scenario/", 1): body
            for path, body in suite.items()
        }

        assert hashing.suite_hash(legacy) == hashing.suite_hash(suite)

    def test_scenarios_folder_wins_over_the_legacy_one(self, suite):
        both = {**suite, "scenario/other/scenario.json": b'{"name": "other"}'}

        assert hashing.suite_hash(both) == hashing.suite_hash(suite)

    def test_hashes_ignore_noise_files(self, suite):
        before = (hashing.suite_hash(suite), hashing.suite_certify_hash(suite))
        suite["scenarios/refund/.DS_Store"] = b"x"
        suite["scenarios/refund/__pycache__/setup.cpython-311.pyc"] = b"x"

        assert (hashing.suite_hash(suite), hashing.suite_certify_hash(suite)) == before

    def test_certify_hash_ignores_checks(self, suite):
        before = hashing.suite_certify_hash(suite)
        suite["scenarios/refund/checks/refunded.py"] = (
            b"def check(w, c):\n    return 0\n"
        )

        assert hashing.suite_certify_hash(suite) == before

    def test_certify_hash_keeps_a_malformed_scenario_as_is(self, suite):
        suite["scenarios/refund/scenario.json"] = b"{not json"

        assert hashing.suite_certify_hash(suite)

    def test_certify_hash_ignores_overlay_values(self, suite):
        before = hashing.suite_certify_hash(suite)
        suite["scenarios/refund/scenario.json"] = _scenario(
            background_noise="street",
            max_turns=30,
            persona={"personality": "angry", "age": 40},
        ).encode()

        assert hashing.suite_certify_hash(suite) == before
        assert hashing.suite_hash(suite) != hashing.suite_hash(
            {**suite, "scenarios/refund/scenario.json": _scenario().encode()}
        )

    def test_certify_hash_changes_with_non_overlay_fields(self, suite):
        before = hashing.suite_certify_hash(suite)
        suite["scenarios/refund/scenario.json"] = _scenario(
            background_noise="cafe",
            max_turns=12,
            persona={"personality": "calm", "age": 70},
        ).encode()

        assert hashing.suite_certify_hash(suite) != before

    @pytest.mark.parametrize(
        "changed",
        [
            {"max_turns": 12, "persona": {"personality": "calm", "age": 40}},
            {
                "background_noise": "cafe",
                "max_turns": 12,
                "persona": {"age": 40},
            },
            {
                "background_noise": "cafe",
                "max_turns": 12,
                "persona": {"personality": "calm", "accent": "scottish", "age": 40},
            },
        ],
        ids=["field-removed", "persona-field-removed", "persona-field-added"],
    )
    def test_certify_hash_changes_when_an_overlay_field_comes_or_goes(
        self, suite, changed
    ):
        # The overlay can change a value but not remove one the bundle was built with.
        before = hashing.suite_certify_hash(suite)
        suite["scenarios/refund/scenario.json"] = _scenario(**changed).encode()

        assert hashing.suite_certify_hash(suite) != before

    def test_certify_hash_survives_a_value_json_cannot_canonicalise(self, suite):
        suite["scenarios/refund/scenario.json"] = b'{"name": "refund", "limit": NaN}'
        first = hashing.suite_certify_hash(suite)
        suite["scenarios/refund/scenario.json"] = (
            b'{"name": "refund", "limit": Infinity}'
        )

        assert hashing.suite_certify_hash(suite) != first

    def test_a_non_finite_overlay_value_is_still_just_a_value(self, suite):
        suite["scenarios/refund/scenario.json"] = (
            b'{"name": "refund", "max_turns": NaN}'
        )
        first = hashing.suite_certify_hash(suite)
        suite["scenarios/refund/scenario.json"] = b'{"name": "refund", "max_turns": 9}'

        assert hashing.suite_certify_hash(suite) == first

    def test_certify_hash_keeps_overlay_when_asked(self, suite):
        before = hashing.suite_certify_hash(suite, keep_overlay=True)
        suite["scenarios/refund/scenario.json"] = _scenario(
            background_noise="street",
            max_turns=12,
            persona={"personality": "calm", "age": 40},
        ).encode()

        assert hashing.suite_certify_hash(suite, keep_overlay=True) != before


class TestSubGoalDigests:
    def test_keyed_by_name_and_tracks_the_definition(self):
        goal = {
            "name": "refunded",
            "what": "Refund issued",
            "check": "c",
            "judged": False,
        }

        before = hashing.sub_goal_digests([goal])
        after = hashing.sub_goal_digests([{**goal, "what": "Refund issued in full"}])

        assert set(before) == {"refunded"}
        assert before["refunded"] != after["refunded"]


class TestAgentHashes:
    def _identity(self, **overrides):
        fields = {
            "source": "git:abc123",
            "connector": "github",
            "credential_fingerprints": {
                "STRIPE_KEY": hashing.credential_fingerprint("org-1", "sk_live_1")
            },
        }
        fields.update(overrides)
        return hashing.AgentIdentity(**fields)

    def test_rotated_value_changes_agent_hash_but_not_cache_hash(self):
        before = self._identity()
        rotated = self._identity(
            credential_fingerprints={
                "STRIPE_KEY": hashing.credential_fingerprint("org-1", "sk_live_2")
            }
        )

        assert hashing.agent_hash(rotated) != hashing.agent_hash(before)
        assert hashing.agent_cache_hash(rotated) == hashing.agent_cache_hash(before)

    def test_added_variable_changes_both_hashes(self):
        before = self._identity()
        added = self._identity(
            credential_fingerprints={
                **before.credential_fingerprints,
                "ENABLE_REFUNDS": hashing.credential_fingerprint("org-1", "true"),
            }
        )

        assert hashing.agent_hash(added) != hashing.agent_hash(before)
        assert hashing.agent_cache_hash(added) != hashing.agent_cache_hash(before)

    def test_provider_key_change_changes_agent_hash_but_not_cache_hash(self):
        before = self._identity(
            connector="vapi",
            source="assistant:vapi:asst_1",
            provider_alias_names=("VAPI_API_KEY",),
            provider_credentials_fingerprint=hashing.provider_credentials_fingerprint(
                "org-1", "key-1", "asst_1"
            ),
        )
        rotated = self._identity(
            connector="vapi",
            source="assistant:vapi:asst_1",
            provider_alias_names=("VAPI_API_KEY",),
            provider_credentials_fingerprint=hashing.provider_credentials_fingerprint(
                "org-1", "key-2", "asst_1"
            ),
        )

        assert hashing.agent_hash(rotated) != hashing.agent_hash(before)
        assert hashing.agent_cache_hash(rotated) == hashing.agent_cache_hash(before)

    def test_source_change_changes_both_hashes(self):
        before = self._identity()
        pushed = self._identity(source="git:def456")

        assert hashing.agent_hash(pushed) != hashing.agent_hash(before)
        assert hashing.agent_cache_hash(pushed) != hashing.agent_cache_hash(before)

    def test_credential_set_hash_ignores_provider_aliases(self):
        runtime = {"STRIPE_KEY": "f1"}

        assert hashing.credential_set_hash(
            {**runtime, "VAPI_API_KEY": "f2"}
        ) == hashing.credential_set_hash(runtime)
        assert hashing.credential_names_hash(
            ["STRIPE_KEY", "VAPI_API_KEY"]
        ) == hashing.credential_names_hash(["STRIPE_KEY"])

    def test_fingerprints_are_keyed_per_organization(self):
        assert hashing.credential_fingerprint(
            "org-1", "sk_live_1"
        ) != hashing.credential_fingerprint("org-2", "sk_live_1")
        assert hashing.credential_fingerprint(
            "org-1", "sk_live_1"
        ) == hashing.credential_fingerprint("org-1", b"sk_live_1")

    def test_assembly_key_depends_on_all_three_pieces(self):
        key = hashing.assembly_key("sha256:a", "sha256:e", "sha256:s")

        assert key != hashing.assembly_key("sha256:a", "sha256:e2", "sha256:s")
        assert key != hashing.assembly_key("sha256:a2", "sha256:e", "sha256:s")
        assert key != hashing.assembly_key("sha256:a", "sha256:e", "sha256:s2")
