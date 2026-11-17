"""Querysets in class bodies must not decide which tenant's rows a request sees.

A queryset written in a class body is built once, when its module is imported,
before any caller is known: it carries no organization or workspace filter,
whatever manager it starts from. This guard fails when a new one is added.

1. ``queryset = Model.<manager>...`` in a class body, unless it ends in
   ``.none()``, when the manager reads the workspace context (``objects``,
   ``all_objects``) or the model belongs to a tenant (it has an organization
   or a workspace, or a project or dataset that has one), whatever the
   manager (``no_workspace_objects`` included). The views in
   ``KNOWN_VIEW_QUERYSETS`` predate the guard and narrow it per request
   themselves; the list may only shrink.
2. A related serializer field (``PrimaryKeyRelatedField``, ``SlugRelatedField``,
   ``HyperlinkedRelatedField``) with ``queryset=`` over a first-party model,
   unless it ends in ``.none()`` or the class's ``__init__`` replaces that
   field's queryset on every instance: a top-level statement of ``__init__``,
   with no ``return`` before it, that assigns ``….fields["<name>"].queryset``
   or ``….fields["<name>"].child_relation.queryset``, calls
   ``set_related_queryset(….fields["<name>"], …)``, or calls
   ``scope_relations(self, {"<name>": …, …})`` (each string key of that dict
   literal names a field it replaces). A replacement that only runs under a
   condition (``if request:``, after an early ``return``) leaves the
   class-level queryset in force otherwise and is flagged unless the field is
   in ``KNOWN_CONDITIONAL_RESCOPING``, which is empty and may not grow.
3. ``….fields["<name>"].queryset = …`` for a ``many=True`` related field without
   ``….fields["<name>"].child_relation.queryset`` in the same function: DRF
   resolves each id through ``child_relation`` and never reads the outer
   attribute (use ``tfc.utils.tenant_scope.set_related_queryset``).

The scan reads source only (no imports of the scanned modules) and resolves
model names through Django's app registry, after importing every first-party
models module so that the result does not depend on import order.
"""

import ast
import importlib
from pathlib import Path

import pytest
from django.apps import apps
from django.db import models

from tfc.utils.base_model import AllObjectsManager, BaseModelManager

ROOT = Path(__file__).resolve().parents[2]
SKIP_DIRS = {
    "tests",
    "migrations",
    ".venv",
    "node_modules",
    "__pycache__",
    ".uv-cache",
}
RELATED_FIELDS = {
    "PrimaryKeyRelatedField",
    "SlugRelatedField",
    "HyperlinkedRelatedField",
}

# Views whose class-level queryset predates this guard. Each one narrows it
# per request (BaseModelViewSetMixin, explicit organization and workspace
# filters, or a get_queryset that does not read it). Do not add to this list:
# build the queryset in get_queryset instead.
KNOWN_VIEW_QUERYSETS = {
    "agentcc/views/analytics.py:AgentccAnalyticsViewSet",
    "agentcc/views/api_key.py:AgentccAPIKeyViewSet",
    "agentcc/views/blocklist.py:AgentccBlocklistViewSet",
    "agentcc/views/custom_property.py:AgentccCustomPropertySchemaViewSet",
    "agentcc/views/email_alert.py:AgentccEmailAlertViewSet",
    "agentcc/views/guardrail_feedback.py:AgentccGuardrailFeedbackViewSet",
    "agentcc/views/guardrail_policy.py:AgentccGuardrailPolicyViewSet",
    "agentcc/views/org_config.py:AgentccOrgConfigViewSet",
    "agentcc/views/prompt_template.py:AgentccPromptTemplateViewSet",
    "agentcc/views/provider_credential.py:AgentccProviderCredentialViewSet",
    "agentcc/views/request_log.py:AgentccRequestLogViewSet",
    "agentcc/views/routing_policy.py:AgentccRoutingPolicyViewSet",
    "agentcc/views/session.py:AgentccSessionViewSet",
    "agentcc/views/shadow_experiments.py:AgentccShadowExperimentViewSet",
    "agentcc/views/shadow_experiments.py:AgentccShadowResultViewSet",
    "agentcc/views/webhook_outbound.py:AgentccWebhookEventViewSet",
    "agentcc/views/webhook_outbound.py:AgentccWebhookViewSet",
    "model_hub/views/annotation_queues.py:AnnotationQueueViewSet",
    "model_hub/views/annotation_queues.py:AutomationRuleViewSet",
    "model_hub/views/annotation_queues.py:QueueItemViewSet",
    "model_hub/views/dataset_optimization.py:DatasetOptimizationViewSet",
    "model_hub/views/develop_optimisation.py:OptimizationDatasetDetailView",
    "model_hub/views/develop_optimisation.py:OptimizationDatasetListView",
    "model_hub/views/develop_optimiser.py:OptimizationDetailView",
    "model_hub/views/experiments.py:ExperimentsTableDetailView",
    "model_hub/views/kb.py:KnowledgeBaseViewSet",
    "model_hub/views/optimize_dataset.py:OptimizeDatasetList",
    "model_hub/views/prompt_template.py:PromptExecutionViewSet",
    "model_hub/views/prompt_template.py:PromptHistoryExecutionViewSet",
    "model_hub/views/prompt_template.py:PromptTemplateViewSet",
    "saml2_auth/views.py:IDPUploadViews",
    "simulate/views/agent_prompt_optimiser.py:AgentPromptOptimiserRunViewSet",
    "tracer/views/shared_link.py:SharedLinkViewSet",
}

# Related fields ("<path>:<Serializer>.<field>") whose __init__ may replace the
# class-level queryset only under a condition. The list is empty. Do not add
# to it: replace the queryset on every instance instead
# (tracer.serializers.relation_scope.scope_relations, or set_related_queryset
# with scope_to_caller, which resolves the bound context without a request).
KNOWN_CONDITIONAL_RESCOPING = set()


def _source_files(root):
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root)
        if SKIP_DIRS.intersection(rel.parts[:-1]):
            continue
        if rel.parts[:2] == ("ee", "cloud"):
            continue
        if rel.name.startswith("test_") or rel.name == "conftest.py":
            continue
        yield path, rel.as_posix()


def _import_model_modules():
    """Import every first-party models module.

    An app's ``models`` package need not import each of its modules (agentcc
    does not import ``prompt_template``); such a model joins the registry
    only once something imports it. Importing them all first makes the scan
    independent of what the test session happened to import.
    """
    for app in apps.get_app_configs():
        app_path = Path(app.path)
        try:
            rel = app_path.relative_to(ROOT)
        except ValueError:
            continue  # not first-party
        if rel.parts[:2] == ("ee", "cloud"):
            continue
        package = app_path / "models"
        if not package.is_dir():
            continue
        for path in sorted(package.rglob("*.py")):
            parts = path.relative_to(app_path).with_suffix("").parts
            if SKIP_DIRS.intersection(parts) or parts[-1] == "__init__":
                continue
            try:
                importlib.import_module(".".join((app.name, *parts)))
            except Exception:  # an optional module that cannot load here
                continue


def _models_by_name():
    _import_model_modules()
    by_name = {}
    for model in apps.get_models():
        by_name.setdefault(model.__name__, []).append(model)
    return by_name


def _first_party(model, root):
    return (root / model.__module__.split(".")[0]).is_dir()


def _chain_root(node):
    """(model name, manager name, ends in .none()) for ``Model.manager…``."""
    ends_none = (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "none"
    )
    while True:
        if isinstance(node, ast.Call):
            node = node.func
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            return node.value.id, node.attr, ends_none
        elif isinstance(node, ast.Attribute):
            node = node.value
        else:
            return None, None, ends_none


def _manager(models_by_name, model_name, manager_name):
    for model in models_by_name.get(model_name, []):
        manager = getattr(model, manager_name, None)
        if isinstance(manager, models.Manager):
            return model, manager
    return None, None


def _is_tenant_model(model):
    """The model belongs to a tenant: it has an organization or a workspace,
    or a project or dataset that has one."""

    def direct(candidate):
        names = {field.name for field in candidate._meta.get_fields()}
        return bool({"organization", "workspace"} & names)

    if direct(model):
        return True
    for name in ("project", "dataset"):
        try:
            field = model._meta.get_field(name)
        except Exception:
            continue
        related = getattr(field, "related_model", None)
        if getattr(field, "many_to_one", False) and related and direct(related):
            return True
    return False


def _call_name(call):
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _keyword(call, name):
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _fields_subscript_name(node):
    """``"x"`` for ``fields["x"]`` or ``<anything>.fields["x"]``, else None."""
    if not (
        isinstance(node, ast.Subscript)
        and isinstance(node.slice, ast.Constant)
        and isinstance(node.slice.value, str)
    ):
        return None
    holder = node.value
    if (isinstance(holder, ast.Attribute) and holder.attr == "fields") or (
        isinstance(holder, ast.Name) and holder.id == "fields"
    ):
        return node.slice.value
    return None


def _rescoped_field(statement):
    """``"x"`` when ``statement`` replaces ``fields["x"]``'s queryset, else None.

    That is ``….fields["x"].queryset = …``,
    ``….fields["x"].child_relation.queryset = …`` or
    ``set_related_queryset(….fields["x"], …)``.
    """
    if isinstance(statement, ast.Assign):
        for target in statement.targets:
            if not (isinstance(target, ast.Attribute) and target.attr == "queryset"):
                continue
            holder = target.value
            if isinstance(holder, ast.Attribute) and holder.attr == "child_relation":
                holder = holder.value
            name = _fields_subscript_name(holder)
            if name:
                return name
        return None
    if (
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Call)
        and _call_name(statement.value) == "set_related_queryset"
        and statement.value.args
    ):
        return _fields_subscript_name(statement.value.args[0])
    return None


def _scope_relations_keys(statement, self_name):
    """The string keys of ``scope_relations(<self_name>, {"x": …, …})``.

    ``tracer.serializers.relation_scope.scope_relations`` replaces the
    queryset of every field named by a key of its second argument. Any other
    statement, a first argument other than ``self_name`` or a second argument
    that is not a dict literal gives an empty set.
    """
    if not (
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Call)
        and _call_name(statement.value) == "scope_relations"
        and len(statement.value.args) >= 2
    ):
        return set()
    serializer, relations = statement.value.args[:2]
    if not (isinstance(serializer, ast.Name) and serializer.id == self_name):
        return set()
    if not isinstance(relations, ast.Dict):
        return set()
    return {
        key.value
        for key in relations.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }


def _rescoped_names(statement, self_name):
    """Fields whose queryset ``statement`` replaces, in either form above."""
    name = _rescoped_field(statement)
    names = {name} if name else set()
    return names | _scope_relations_keys(statement, self_name)


def _rescoped_in_init(class_node):
    """(always, conditionally): fields whose queryset ``__init__`` replaces.

    *always*: a top-level statement of ``__init__`` with no ``return`` before
    it. *conditionally*: any other statement of ``__init__``.
    """
    always, conditionally = set(), set()
    for item in class_node.body:
        if not (isinstance(item, ast.FunctionDef) and item.name == "__init__"):
            continue
        params = item.args.posonlyargs + item.args.args
        self_name = params[0].arg if params else None
        returned = False
        for statement in item.body:
            names = _rescoped_names(statement, self_name)
            (conditionally if returned else always).update(names)
            for node in ast.walk(statement):
                if node is statement:
                    continue
                if isinstance(node, ast.Return):
                    returned = True
                if isinstance(node, ast.stmt):
                    conditionally.update(_rescoped_names(node, self_name))
            if isinstance(statement, ast.Return):
                returned = True
    return always, conditionally - always


def _class_body_nodes(class_node):
    """Nodes evaluated at class creation: the body, minus function bodies."""
    stack = list(class_node.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        if isinstance(node, ast.ClassDef):
            continue  # scanned as its own class
        yield node
        stack.extend(ast.iter_child_nodes(node))


def _assigned_name(statement):
    if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
        target = statement.targets[0]
        if isinstance(target, ast.Name):
            return target.id, statement.value
    if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
        return statement.target.id, statement.value
    return None, None


def _is_many(call):
    many = _keyword(call, "many")
    return isinstance(many, ast.Constant) and many.value is True


def scan(root=ROOT):
    """Return (violations, known entries found, many-field names).

    The known entries found are those of ``KNOWN_VIEW_QUERYSETS`` and
    ``KNOWN_CONDITIONAL_RESCOPING`` the scan met.
    """
    models_by_name = _models_by_name()
    violations, known_found, known_conditional = [], set(), set()
    many_fields, trees = set(), []

    for path, rel in _source_files(root):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError):
            continue
        trees.append((rel, tree))
        for class_node in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
            where = f"{rel}:{class_node.name}"
            rescoped = None
            for statement in class_node.body:
                name, value = _assigned_name(statement)
                if name == "queryset" and value is not None:
                    model_name, manager_name, ends_none = _chain_root(value)
                    model, manager = _manager(models_by_name, model_name, manager_name)
                    reads_context = isinstance(
                        manager, (BaseModelManager, AllObjectsManager)
                    )
                    tenant = (
                        model is not None
                        and _first_party(model, root)
                        and _is_tenant_model(model)
                    )
                    if (reads_context or tenant) and not ends_none:
                        if where in KNOWN_VIEW_QUERYSETS:
                            known_found.add(where)
                        else:
                            violations.append(
                                f"{where}: class-level queryset over "
                                f"{model_name}.{manager_name} "
                                f"(line {statement.lineno})"
                            )
            for node in _class_body_nodes(class_node):
                if not isinstance(node, ast.Call):
                    continue
                if _call_name(node) not in RELATED_FIELDS:
                    continue
                queryset = _keyword(node, "queryset")
                if queryset is None:
                    continue
                model_name, manager_name, ends_none = _chain_root(queryset)
                model, _ = _manager(models_by_name, model_name, manager_name)
                if model is None or not _first_party(model, root):
                    continue
                field_name = next(
                    (
                        assigned
                        for statement in class_node.body
                        for assigned, value in [_assigned_name(statement)]
                        if value is node
                    ),
                    None,
                )
                if field_name and _is_many(node):
                    many_fields.add(field_name)
                if ends_none:
                    continue
                if rescoped is None:
                    rescoped = _rescoped_in_init(class_node)
                always, conditionally = rescoped
                if field_name and field_name in always:
                    continue
                field_where = f"{where}.{field_name or '<unnamed>'}"
                if field_name and field_name in conditionally:
                    if field_where in KNOWN_CONDITIONAL_RESCOPING:
                        known_conditional.add(field_where)
                        continue
                    violations.append(
                        f"{field_where}: related field over "
                        f"{model_name}.{manager_name} is re-scoped only under a "
                        f"condition in __init__ (line {node.lineno})"
                    )
                    continue
                violations.append(
                    f"{field_where}: related field over "
                    f"{model_name}.{manager_name} is not re-scoped per instance "
                    f"(line {node.lineno})"
                )

    for rel, tree in trees:
        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            outer, child = {}, set()
            for node in ast.walk(function):
                if not isinstance(node, ast.Assign):
                    continue
                for target in node.targets:
                    if not (
                        isinstance(target, ast.Attribute) and target.attr == "queryset"
                    ):
                        continue
                    holder = target.value
                    if isinstance(holder, ast.Attribute) and (
                        holder.attr == "child_relation"
                    ):
                        name = _fields_subscript_name(holder.value)
                        if name:
                            child.add(name)
                        continue
                    name = _fields_subscript_name(holder)
                    if name:
                        outer.setdefault(name, node.lineno)
            for name, lineno in outer.items():
                if name in many_fields and name not in child:
                    violations.append(
                        f"{rel}:{function.name}: fields[{name!r}].queryset is set "
                        f"without child_relation.queryset (line {lineno})"
                    )

    return violations, known_found | known_conditional, many_fields


@pytest.mark.unit
def test_no_unscoped_class_level_tenant_querysets():
    violations, known_found, _ = scan()

    assert not violations, (
        "Querysets built at import time carry no tenant filter. Build them per "
        "request (get_queryset, or the serializer's __init__ with "
        "tfc.utils.tenant_scope), or default to .none():\n"
        + "\n".join(sorted(violations))
    )
    known = KNOWN_VIEW_QUERYSETS | KNOWN_CONDITIONAL_RESCOPING
    assert known_found == known, (
        "KNOWN_VIEW_QUERYSETS / KNOWN_CONDITIONAL_RESCOPING list entries the "
        "guard no longer meets; remove them: " + ", ".join(sorted(known - known_found))
    )


PLANTED = """
from rest_framework import generics, serializers

from tfc.utils.tenant_scope import set_related_queryset
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.project import Project
from tracer.serializers.relation_scope import scope_relations


class PlantedListView(generics.ListAPIView):
    queryset = Project.objects.all()


class PlantedEmptyView(generics.ListAPIView):
    queryset = Project.objects.none()


class PlantedNoWorkspaceManagerView(generics.ListAPIView):
    queryset = Project.no_workspace_objects.all()


class PlantedOtherAttributeInitSerializer(serializers.Serializer):
    project = serializers.PrimaryKeyRelatedField(queryset=Project.objects.all())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["project"].required = False


class PlantedConditionalInitSerializer(serializers.Serializer):
    project = serializers.PrimaryKeyRelatedField(queryset=Project.objects.all())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        if request is not None:
            self.fields["project"].queryset = Project.no_workspace_objects.none()


class PlantedEarlyReturnInitSerializer(serializers.Serializer):
    project = serializers.PrimaryKeyRelatedField(queryset=Project.objects.all())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.context.get("request"):
            return
        set_related_queryset(
            self.fields["project"], Project.no_workspace_objects.none()
        )


class PlantedRescopedInitSerializer(serializers.Serializer):
    project = serializers.PrimaryKeyRelatedField(queryset=Project.objects.all())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        set_related_queryset(
            self.fields["project"], Project.no_workspace_objects.none()
        )


class PlantedScopeRelationsSerializer(serializers.Serializer):
    project = serializers.PrimaryKeyRelatedField(queryset=Project.objects.all())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        scope_relations(self, {"project": ("organization", "workspace")})


class PlantedConditionalScopeRelationsSerializer(serializers.Serializer):
    project = serializers.PrimaryKeyRelatedField(queryset=Project.objects.all())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        if request:
            scope_relations(self, {"project": ("organization", "workspace")})


class PlantedPartialScopeRelationsSerializer(serializers.Serializer):
    project = serializers.PrimaryKeyRelatedField(queryset=Project.objects.all())
    eval_config = serializers.PrimaryKeyRelatedField(
        queryset=CustomEvalConfig.no_workspace_objects.all()
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        scope_relations(self, {"project": ("organization", "workspace")})


class PlantedSerializer(serializers.Serializer):
    project = serializers.PrimaryKeyRelatedField(queryset=Project.objects.all())
    evals = serializers.PrimaryKeyRelatedField(
        queryset=CustomEvalConfig.no_workspace_objects.all(), many=True
    )


class PlantedScopedSerializer(serializers.Serializer):
    project = serializers.PrimaryKeyRelatedField(
        queryset=Project.no_workspace_objects.none()
    )
    evals = serializers.PrimaryKeyRelatedField(
        queryset=CustomEvalConfig.no_workspace_objects.all(), many=True
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["evals"].child_relation.queryset = (
            CustomEvalConfig.no_workspace_objects.none()
        )


def planted_view_get_serializer(serializer):
    fields = serializer.fields
    fields["evals"].queryset = CustomEvalConfig.no_workspace_objects.none()
"""


@pytest.mark.unit
def test_guard_flags_planted_violations(tmp_path, monkeypatch):
    (tmp_path / "tracer").mkdir()
    (tmp_path / "tracer" / "planted.py").write_text(PLANTED, encoding="utf-8")

    violations, _, _ = scan(tmp_path)

    flagged = sorted(violation.split(" ")[0] for violation in violations)
    assert flagged == [
        "tracer/planted.py:PlantedConditionalInitSerializer.project:",
        "tracer/planted.py:PlantedConditionalScopeRelationsSerializer.project:",
        "tracer/planted.py:PlantedEarlyReturnInitSerializer.project:",
        "tracer/planted.py:PlantedListView:",
        "tracer/planted.py:PlantedNoWorkspaceManagerView:",
        "tracer/planted.py:PlantedOtherAttributeInitSerializer.project:",
        "tracer/planted.py:PlantedPartialScopeRelationsSerializer.eval_config:",
        "tracer/planted.py:PlantedSerializer.evals:",
        "tracer/planted.py:PlantedSerializer.project:",
        "tracer/planted.py:planted_view_get_serializer:",
    ], violations

    # A conditional scope_relations call passes once its field is listed.
    listed = "tracer/planted.py:PlantedConditionalScopeRelationsSerializer.project"
    monkeypatch.setitem(globals(), "KNOWN_CONDITIONAL_RESCOPING", {listed})
    violations, known_found, _ = scan(tmp_path)
    assert listed + ":" not in {violation.split(" ")[0] for violation in violations}
    assert known_found == {listed}
