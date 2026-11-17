"""Recursive custody discovery must reject additions outside the old file list."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_whole_agentcc_custody_is_explicitly_classified():
    from agentcc.tests.microsoft_custody_census import discover, unclassified

    sites = discover(ROOT)
    assert any(path == "services/mcp_vendor_credentials.py" for path, _, _ in sites)
    assert any(path.startswith("tests/") for path, _, _ in sites)
    assert unclassified(sites) == []


def test_new_nested_aliased_decrypt_module_is_detected(tmp_path):
    from agentcc.tests.microsoft_custody_census import discover, unclassified

    module = tmp_path / "services" / "nested" / "new_custody.py"
    module.parent.mkdir(parents=True)
    module.write_text(
        "from agentcc.services.credential_manager import decrypt_token as unlock\n"
        "def deliver(row):\n"
        "    return unlock(row.encrypted_secret)\n"
    )
    sites = discover(tmp_path)
    assert sites == [("services/nested/new_custody.py", "deliver", 3)]
    assert unclassified(sites) == sites


def test_extra_unrouted_branch_in_known_symbol_is_detected(tmp_path):
    from agentcc.tests.microsoft_custody_census import discover, unclassified

    module = tmp_path / "services" / "connector_oauth_clients.py"
    module.parent.mkdir()
    module.write_text(
        "from agentcc.services.credential_manager import decrypt_token\n"
        "def _org_secret(row):\n"
        "    if row.label:\n"
        "        return decrypt_token(row.encrypted_secret)\n"
        "    deny_if_excluded(row.oauth_client_id)\n"
        "    return decrypt_token(row.encrypted_secret)\n"
    )
    assert unclassified(discover(tmp_path))


def test_module_alias_and_nested_function_are_discovered(tmp_path):
    from agentcc.tests.microsoft_custody_census import discover

    (tmp_path / "module.py").write_text(
        "import agentcc.services.credential_manager as custody\n"
        "def outer():\n"
        "    def inner(row):\n"
        "        return custody.decrypt_token(row.encrypted_secret)\n"
        "    return inner\n"
    )
    assert discover(tmp_path) == [("module.py", "outer.inner", 4)]
