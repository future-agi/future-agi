"""All shipped starter skills are complete, portable, and safely instantiated."""

import re
from pathlib import Path

import pytest
import yaml

from model_hub.services.skills_hub.checks import parse_frontmatter, run_checks
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.templates_registry import (
    get_template_files,
    list_templates,
)

IDS = {
    "blank",
    "support-policy",
    "code-review",
    "incident-triage",
    "research-brief",
    "trace-debugging",
    "eval-writer",
}


@pytest.mark.parametrize("template_id", sorted(IDS))
def test_every_template_is_complete_and_has_no_errors_or_warnings(template_id):
    template = next(item for item in list_templates() if item["id"] == template_id)
    files = get_template_files(
        template_id, name=template_id, description=template["description"]
    )
    assert "SKILL.md" in {file["path"] for file in files}
    assert all(
        "{{name}}" not in file["content"] and "{{description}}" not in file["content"]
        for file in files
    )
    result = run_checks(files, expected_name=template_id)
    assert result["summary"]["errors"] == 0, result["items"]
    assert result["summary"]["warnings"] == 0, result["items"]
    assert result["frontmatter"]["description"] == template["description"]
    assert template["files"] == [
        {"path": file["path"], "size_bytes": len(file["content"].encode("utf-8"))}
        for file in files
    ]


def test_registry_exact_shape_and_known_templates():
    templates = list_templates()
    assert {template["id"] for template in templates} == IDS
    assert templates[0]["id"] == "blank"
    assert all(
        set(template) == {"id", "name", "title", "description", "tags", "files"}
        for template in templates
    )


@pytest.mark.parametrize(
    "description",
    [
        'Use when a customer says "refund: please" and a precise answer is needed.',
        "Use when quoted data includes:\nhooks: danger\n---\nnew: field",
        "Use when 日本語 and café text need clear, verifiable instructions.",
        "Use when the literal {{name}} and {{description}} markers occur in source text.",
    ],
)
def test_substitutions_are_quoted_scalars_not_yaml_injection(description):
    root = get_template_files("blank", name="new-name", description=description)[0]
    frontmatter, body, items = parse_frontmatter(root["content"])
    assert items == []
    assert frontmatter == {"name": "new-name", "description": description}
    assert "# Task guide" in body


def test_template_calls_return_fresh_files_and_do_not_mutate_sources():
    first = get_template_files("blank", name="first", description="First description")
    first[0]["content"] = "mutated"
    second = get_template_files(
        "blank", name="second", description="Second description"
    )
    assert parse_frontmatter(second[0]["content"])[0]["name"] == "second"


@pytest.mark.parametrize("template_id", ["unknown", "../blank", "blank/SKILL.md", ""])
def test_unknown_template_has_domain_error(template_id):
    with pytest.raises(SkillsHubError) as exc:
        get_template_files(template_id, name="example", description="text")
    assert (exc.value.code, exc.value.status) == ("template_not_found", 404)


def test_required_supporting_files_are_shipped():
    expected = {
        "support-policy": {"references/policy.md", "references/examples.md"},
        "code-review": {"references/checklist.md"},
        "incident-triage": {
            "references/severity-matrix.md",
            "scripts/collect-context.sh",
        },
        "research-brief": {"templates/brief.md"},
    }
    for template_id, paths in expected.items():
        files = get_template_files(template_id, name=template_id, description="text")
        assert paths <= {file["path"] for file in files}


def test_trace_and_eval_templates_name_real_mcp_tools():
    backend_root = Path(__file__).resolve().parents[3]
    catalog = yaml.safe_load(
        (backend_root / "mcp_server/catalog/tools.yaml").read_text()
    )
    tool_names = {tool["name"] for tool in catalog["tools"]}
    for template_id in ("trace-debugging", "eval-writer"):
        files = get_template_files(template_id, name=template_id, description="text")
        assert files
        content = "\n".join(file["content"] for file in files)
        referenced_tools = set(re.findall(r"`([a-z]+_[a-z_]+)`", content))
        assert referenced_tools & tool_names
        assert "Future AGI MCP server" in content
        # Only actual tool identifiers use this standalone backtick spelling.
        assert referenced_tools <= tool_names
