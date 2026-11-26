"""The platform's copy of ALK's whose-failure tables (agent-learning-kit ``fi/alk/harness/failure_record.py``),
pinned to ALK's values: a change on either side must change both and this test."""

from simulate.services.harness_versioning import validation

# As agent-learning-kit fi/alk/harness/failure_record.py defines them.
ALK_OWNED_PROCESSES = {"world-db", "tool-proxy", "mcp-toolbox"}
ALK_HARNESS_CODES = {
    "store_statement_failed",
    "resource_capacity_unknown",
    "resource_capacity_unavailable",
}
ALK_OWNED_SEED_DIAGNOSTICS = {"source_model_fingerprint_mismatch"}
ALK_AGENT_OUTPUT = "; last output:"


def test_the_whose_failure_tables_are_alks():
    assert validation._OWNED_PROCESSES == ALK_OWNED_PROCESSES
    assert validation._INFRASTRUCTURE_CODES == ALK_HARNESS_CODES
    assert validation._OWNED_SEED_DIAGNOSTICS == ALK_OWNED_SEED_DIAGNOSTICS
    assert validation._AGENT_OUTPUT == ALK_AGENT_OUTPUT


def test_a_typed_failure_line_is_read_as_alk_writes_it():
    match = validation._TYPED.fullmatch(
        "ProcessRuntimeError: world_build/seed_failed (world-db): could not apply"
    )
    assert match is not None
    assert match.groups() == ("seed_failed", "world-db", "could not apply")
