"""The evaluations catalog is the source of a built-in eval's tags.

Contract: api_contracts/harness/eval-catalog.md (P7, P10, P13).
"""

import re
from pathlib import Path

import pytest
import yaml
from django.core.cache import cache

from model_hub.management.commands.seed_system_evals import (
    CATALOG_YAML,
    SYSTEM_EVALS_DIR,
    SYSTEM_EVALS_VERSION,
    _eval_accepts_pdf,
    _load_catalog_tags,
    seed_evals,
)
from model_hub.models.evals_metric import EvalTemplate

assert SYSTEM_EVALS_VERSION > 16, (
    "TH-8043 bumps the seeder version so catalog tags re-seed"
)

SIX = {
    "advice_authority_boundary": {"Insurance"},
    "claim_intake_accuracy": {"Insurance"},
    "lead_qualification_completeness": {"Insurance"},
    "no_misselling": {"Insurance"},
    "intake_field_accuracy": {"Accuracy"},
    "customer_agent_task_completion": set(),
}
BASE = {"Agents", "Conversation", "Voice", "Chatbot behaviors"}


def _one_system_template(name):
    rows = list(
        EvalTemplate.no_workspace_objects.filter(
            name=name, owner="system", deleted=False
        )
    )
    assert len(rows) == 1, f"{name}: expected one system template, found {len(rows)}"
    return rows[0]


def _catalog() -> dict:
    return yaml.safe_load(Path(CATALOG_YAML).read_text()) or {}


def _legacy_names() -> set[str]:
    names = set()
    for subdir in ("function", "agent", "specialty"):
        for path in (Path(SYSTEM_EVALS_DIR) / subdir).glob("*.yaml"):
            data = yaml.safe_load(path.read_text()) or {}
            names.add(data.get("name") or path.stem)
    return names


def test_the_six_have_their_tags():
    catalog = _catalog()
    for name, extra in SIX.items():
        assert name in catalog, name
        assert set(catalog[name]["tags"]) == BASE | extra, name
        assert catalog[name]["required_keys"] == ["agent_prompt", "conversation"], name


def test_the_six_prompts_match_the_legacy_yaml_verbatim():
    catalog = _catalog()
    for name in SIX:
        legacy = yaml.safe_load(
            (Path(SYSTEM_EVALS_DIR) / "agent" / f"{name}.yaml").read_text()
        )
        assert catalog[name]["rule_prompt"] == legacy["config"]["rule_prompt"], name
        assert catalog[name]["description"] == legacy["description"], name


def test_every_catalog_key_has_a_legacy_template():
    # The seeder only copies tags onto templates it seeds from the legacy YAMLs;
    # a catalog key with no legacy file is a tag nobody will ever receive.
    missing = sorted(set(_catalog()) - _legacy_names())
    assert missing == [], missing


def test_every_catalog_entry_declares_required_keys():
    bad = sorted(n for n, e in _catalog().items() if not e.get("required_keys"))
    assert bad == [], bad


@pytest.mark.django_db
def test_catalog_tags_are_copied_onto_the_stored_templates():
    seed_evals(force=True)
    catalog_tags = _load_catalog_tags()
    assert catalog_tags, "catalog has no tagged entries"
    for name, tags in catalog_tags.items():
        template = _one_system_template(name)
        expected = list(tags)
        if _eval_accepts_pdf(template.config or {}) and "PDF" not in expected:
            expected.append("PDF")
        assert template.eval_tags == expected, name


@pytest.mark.django_db
def test_the_six_are_stored_with_their_catalog_tags():
    seed_evals(force=True)
    for name, extra in SIX.items():
        template = _one_system_template(name)
        assert set(template.eval_tags) == BASE | extra, name


@pytest.mark.django_db
def test_the_version_gate_reopens_for_this_change():
    # The cache is process-wide, not rolled back with the test transaction:
    # restore whatever was there so no later test sees this test's value.
    previous = cache.get("system_evals_version")
    cache.set("system_evals_version", 16)
    try:
        seed_evals()
        for name, extra in SIX.items():
            template = _one_system_template(name)
            assert set(template.eval_tags) == BASE | extra, name
    finally:
        if previous is None:
            cache.delete("system_evals_version")
        else:
            cache.set("system_evals_version", previous)


@pytest.mark.django_db
def test_an_unlisted_legacy_eval_keeps_its_own_tags():
    seed_evals(force=True)
    for name, subdir in (
        ("dead_air_detection", "function"),
        ("voicemail_handling", "agent"),
    ):
        legacy = yaml.safe_load(
            (Path(SYSTEM_EVALS_DIR) / subdir / f"{name}.yaml").read_text()
        )
        expected = list(legacy.get("eval_tags") or [])
        template = _one_system_template(name)
        if _eval_accepts_pdf(template.config or {}) and "PDF" not in expected:
            expected.append("PDF")
        assert template.eval_tags == expected, name


def test_a_catalog_entry_with_empty_tags_is_ignored(tmp_path, monkeypatch):
    scratch = tmp_path / "catalog.yaml"
    scratch.write_text(
        "first:\n"
        "  tags: []\n"
        "  description: no tags\n"
        "second:\n"
        "  tags: [X]\n"
        "  description: has tags\n"
    )
    monkeypatch.setattr(
        "model_hub.management.commands.seed_system_evals.CATALOG_YAML", scratch
    )
    assert _load_catalog_tags() == {"second": ["X"]}


def test_the_six_sit_under_their_own_section_header():
    text = Path(CATALOG_YAML).read_text()
    header = (
        "# --- Conversation and insurance agent evals "
        "(added for simulation, TH-8043) ---"
    )
    assert header in text

    header_pos = text.index(header)
    code_evals_match = re.search(r"^# CODE EVALS", text, re.MULTILINE)
    assert code_evals_match, "expected a '# CODE EVALS' section marker"
    code_evals_pos = code_evals_match.start()

    for name in SIX:
        key_match = re.search(rf"^{re.escape(name)}:", text, re.MULTILINE)
        assert key_match, f"{name}: top-level key not found"
        assert key_match.start() > header_pos, (
            f"{name}: expected to appear after the TH-8043 section header"
        )
        assert key_match.start() < code_evals_pos, (
            f"{name}: expected to appear before the '# CODE EVALS' section"
        )


def test_catalog_lists_use_inline_style():
    text = Path(CATALOG_YAML).read_text()
    list_key_re = re.compile(r"^\s*(required_keys|tags):")
    inline_re = re.compile(r"^\s*(required_keys|tags):\s*\[")

    offenders = [
        (lineno, line)
        for lineno, line in enumerate(text.splitlines(), start=1)
        if list_key_re.match(line) and not inline_re.match(line)
    ]
    assert offenders == []
