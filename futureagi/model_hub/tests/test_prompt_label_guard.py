"""XP-06 guard: no code outside the one prompt writer writes a prompt label or commits a draft without ``save()``
(webhooks DESIGN.md section 14.3 item 5; WT-21).

Static (AST) and stdlib only, so it also runs without Django. It matches **writes** only:
- ``<x>.labels.add|remove|set|clear(...)`` (annotation models also have a ``labels`` M2M: those sites are listed
  below by file and receiver, with the reason), and the reverse accessor ``<label>.prompt_versions.add|remove|set|
  clear(...)``;
- ``<...>.labels.through.objects...create|bulk_create|get_or_create|update_or_create|delete|update(...)``, also through
  a name the through model was assigned to (``t = PromptVersion.labels.through; t.objects.create(...)``);
- ``.update(is_draft=...)`` and ``bulk_update(..., ["is_draft", ...])`` on any queryset, and ``update(deleted=...)``
  on ``PromptVersion`` / ``PromptTemplate`` querysets, also through a name such a queryset was assigned to (``qs =
  PromptVersion.objects...; qs.update(deleted=True)``): they bypass ``save()``, so the version event or the label
  release would not happen;
- ``PromptVersion(...)`` without ``is_draft=True``: such an instance can only be stored by ``bulk_create``, which
  bypasses ``save()``;
- ``PromptLabelHead`` writes.

Reads of ``labels.through`` (``tracer/utils/create_otel_span.py``, ``tracer/utils/trace_ingestion.py``,
``serializers/prompt_template.py``) are not matched. A planted ``version.labels.add()`` fails the guard (tested below).
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WRITER = "model_hub/services/prompt_label.py"
LABEL_WRITES = {"add", "remove", "set", "clear"}
ORM_WRITES = {
    "create",
    "bulk_create",
    "get_or_create",
    "update_or_create",
    "delete",
    "update",
}
ANNOTATION = "annotation labels (an AnnotationsLabels M2M), not prompt labels"
# (file, receiver source, method) -> why it is not a prompt label write
ALLOWED = {
    ("tracer/views/observation_span.py", "annotation", "add"): ANNOTATION,
    ("model_hub/serializers/develop_annotations.py", "instance", "set"): ANNOTATION,
    ("model_hub/views/develop_annotations.py", "annotation", "set"): ANNOTATION,
    ("model_hub/services/dataset_service.py", "annotation", "remove"): ANNOTATION,
    ("model_hub/views/develop_dataset.py", "annotation", "remove"): ANNOTATION,
    ("accounts/user_onboard.py", "annotation", "set"): ANNOTATION,
    ("model_hub/views/prompt_template.py", "PromptVersion", "update(deleted)"): (
        "prompt delete: PromptTemplate.save(deleted=True) has already released every label through the writer "
        "(template_saving), in perform_destroy, just before this update"
    ),
    ("model_hub/views/prompt_folder.py", "PromptVersion", "update(deleted)"): (
        "folder delete: release_prompt_labels() runs for each prompt in the same transaction, just before"
    ),
    ("model_hub/views/prompt_folder.py", "prompt_templates", "update(deleted)"): (
        "folder delete: release_prompt_labels() runs for each prompt in the same transaction, just before"
    ),
}


def _chain(node: ast.AST) -> list[str]:
    """``a.b.c(...).d`` -> ["a", "b", "c", "d"] (calls are stepped through)."""
    out: list[str] = []
    while True:
        if isinstance(node, ast.Call):
            node = node.func
        elif isinstance(node, ast.Attribute):
            out.append(node.attr)
            node = node.value
        elif isinstance(node, ast.Name):
            out.append(node.id)
            return out[::-1]
        else:
            return out[::-1]


def _kw(call: ast.Call, name: str):
    return next((k.value for k in call.keywords if k.arg == name), None)


def _is_prompt_rows(chain: list[str]) -> bool:
    head = chain[0] if chain else ""
    return head in ("PromptVersion", "PromptTemplate") or head.endswith(
        ("prompt_templates", "prompt_versions")
    )


def _aliases(tree: ast.AST) -> tuple[set[str], set[str]]:
    """Names assigned a prompt version / prompt queryset, and names assigned a ``labels.through`` model, anywhere in
    the file (flow-insensitive: a name reused for another model is reported and can be allowlisted)."""
    rows, through = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            target, value = node.target, node.value
        else:
            continue
        if not isinstance(target, ast.Name):
            continue
        chain = _chain(value)
        if chain[-2:] == ["labels", "through"]:
            through.add(target.id)
        elif _is_prompt_rows(chain):
            rows.add(target.id)
    return rows, through


def findings(rel: str, source: str) -> list[tuple[str, str, str, int]]:
    """Every write this guard matches in one file: (file, receiver, method, line)."""
    out = []
    tree = ast.parse(source)
    rows, through = _aliases(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id == "PromptVersion":
            draft = _kw(node, "is_draft")
            if not (isinstance(draft, ast.Constant) and draft.value is True):
                out.append((rel, "PromptVersion", "construct", node.lineno))
            continue
        if not isinstance(node.func, ast.Attribute):
            continue
        method, chain = node.func.attr, _chain(node.func.value)
        if method in LABEL_WRITES and chain[-1:] in (["labels"], ["prompt_versions"]):
            out.append((rel, ".".join(chain[:-1]), method, node.lineno))
        elif method in ORM_WRITES and (
            ("through" in chain and "labels" in chain)
            or (chain[0] if chain else "") in through
        ):
            out.append((rel, ".".join(chain), method, node.lineno))
        elif method == "update" and _kw(node, "is_draft") is not None:
            out.append(
                (rel, chain[0] if chain else "", "update(is_draft)", node.lineno)
            )
        elif method == "bulk_update" and "is_draft" in ast.unparse(node):
            out.append(
                (rel, chain[0] if chain else "", "bulk_update(is_draft)", node.lineno)
            )
        elif (
            method == "update"
            and _kw(node, "deleted") is not None
            and chain
            and (_is_prompt_rows(chain) or chain[0] in rows)
        ):
            out.append((rel, chain[0], "update(deleted)", node.lineno))
        elif method in ORM_WRITES and chain[:1] == ["PromptLabelHead"]:
            out.append((rel, "PromptLabelHead", method, node.lineno))
    return out


SKIP_DIRS = {"migrations", "tests", "node_modules", "__pycache__", "static", "media"}


def _sources(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS
        )
        for name in sorted(filenames):
            if (
                not name.endswith(".py")
                or name.startswith("test_")
                or name == "conftest.py"
            ):
                continue
            path = Path(dirpath, name)
            rel = path.relative_to(root).as_posix()
            if rel != WRITER:
                yield rel, path.read_text(encoding="utf-8", errors="replace")


def violations(root: Path = ROOT) -> list[tuple[str, str, str, int]]:
    out = []
    for rel, source in _sources(root):
        try:
            found = findings(rel, source)
        except SyntaxError:
            continue
        out.extend(f for f in found if f[:3] not in ALLOWED)
    return out


def test_no_prompt_label_or_draft_write_outside_the_one_writer():
    assert violations() == []


def test_every_allowlisted_site_still_exists():
    """A stale entry would let a new write at the same file and receiver through."""
    seen = {f[:3] for rel, src in _sources(ROOT) for f in findings(rel, src)}
    assert set(ALLOWED) - seen == set()


def test_a_planted_label_write_fails_the_guard(tmp_path):
    pkg = tmp_path / "model_hub" / "views"
    pkg.mkdir(parents=True)
    (pkg / "planted.py").write_text(
        "def f(version, label, template):\n"
        "    version.labels.add(label)\n"
        "    version.labels.through.objects.filter(promptlabel_id=1).delete()\n"
        "    PromptVersion.objects.filter(original_template=template).update(is_draft=False)\n"
        "    PromptVersion.objects.bulk_create([PromptVersion(template_version='v1')])\n"
        "    PromptLabelHead.objects.filter(template=template).update(revision=0)\n"
        "    ids = version.labels.through.objects.filter(promptversion_id=1).values_list('promptlabel_id')\n"
        "    PromptVersion(template_version='v2', is_draft=True)\n"
        "    qs = PromptVersion.objects.filter(original_template=template)\n"
        "    qs.update(deleted=True)\n"
        "    label.prompt_versions.add(version)\n"
        "    label.prompt_versions.clear()\n"
        "    t = PromptVersion.labels.through\n"
        "    t.objects.create(promptversion_id=1, promptlabel_id=2)\n"
        "    t.objects.filter(promptlabel_id=2).values_list('promptversion_id')\n"
        "    return ids\n"
    )
    got = sorted((m, line) for _, _, m, line in violations(tmp_path))
    assert got == [
        ("add", 2),
        ("add", 11),
        ("clear", 12),
        ("construct", 5),
        ("create", 14),
        ("delete", 3),
        ("update", 6),
        ("update(deleted)", 10),
        ("update(is_draft)", 4),
    ]


def test_the_known_reads_of_the_through_table_are_not_matched():
    for rel in (
        "tracer/utils/create_otel_span.py",
        "tracer/utils/trace_ingestion.py",
        "model_hub/serializers/prompt_template.py",
    ):
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert "labels.through.objects" in src
        assert findings(rel, src) == []
