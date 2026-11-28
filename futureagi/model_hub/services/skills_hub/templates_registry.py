"""Original starter skills shipped alongside the backend source."""

from __future__ import annotations

import json
import re
from pathlib import Path

from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.types import FileInput

_ROOT = Path(__file__).with_name("templates")
_TEMPLATES = (
    (
        "blank",
        "Blank skill",
        "Use when a task needs repeatable instructions, clear inputs, and a verifiable result.",
        ["starter"],
    ),
    (
        "support-policy",
        "Support policy",
        "Use when a customer asks about returns or refunds and a policy-grounded response is needed.",
        ["support", "policy"],
    ),
    (
        "code-review",
        "Code review",
        "Use when reviewing a code change for correctness, security, and evidence-backed regression risks.",
        ["engineering", "review"],
    ),
    (
        "incident-triage",
        "Incident triage",
        "Use when a service incident needs a severity assessment, evidence collection, and a safe next step.",
        ["operations", "incident"],
    ),
    (
        "research-brief",
        "Research brief",
        "Use when a decision needs a concise research brief with cited evidence, uncertainty, and alternatives.",
        ["research"],
    ),
    (
        "trace-debugging",
        "Trace debugging",
        "Use when an agent fails and Future AGI traces and evaluations can help isolate the first incorrect step.",
        ["observability", "debugging"],
    ),
    (
        "eval-writer",
        "Evaluation writer",
        "Use when turning a product requirement into measurable Future AGI evaluations and representative test cases.",
        ["evaluation", "testing"],
    ),
)


def get_template_files(
    template_id: str, *, name: str, description: str
) -> list[FileInput]:
    """Return fresh text files, safely quoting frontmatter substitutions as YAML."""
    if template_id not in {template[0] for template in _TEMPLATES}:
        raise SkillsHubError(
            "template_not_found", "This skill template was not found.", status=404
        )
    root = _ROOT / template_id
    files: list[FileInput] = []
    values = {"name": name, "description": description}
    for file in sorted(root.rglob("*")):
        if not file.is_file():
            continue
        path = file.relative_to(root).as_posix()
        content = file.read_bytes().decode("utf-8")
        if path == "SKILL.md":
            # Replace only the frontmatter. JSON strings are valid YAML scalars;
            # colons, newlines and quotes in user input cannot add YAML fields.
            header, separator, body = content.partition("\n---\n")
            header = re.sub(
                r"\{\{(name|description)\}\}",
                lambda match: json.dumps(values[match.group(1)], ensure_ascii=False),
                header,
            )
            content = header + separator + body
        files.append({"path": path, "content": content})
    return files


def list_templates() -> list[dict]:
    """List starter metadata and the byte sizes of default rendered files."""
    return [
        {
            "id": template_id,
            "name": template_id,
            "title": title,
            "description": description,
            "tags": list(tags),
            "files": [
                {
                    "path": file["path"],
                    "size_bytes": len(file["content"].encode("utf-8")),
                }
                for file in get_template_files(
                    template_id, name=template_id, description=description
                )
            ],
        }
        for template_id, title, description, tags in _TEMPLATES
    ]
