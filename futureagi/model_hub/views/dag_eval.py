"""Decision-tree evals: author, validate, test on a row, run on a dataset, read paths.

Every route resolves the caller's organization and workspace first; trees,
evals, datasets and stored runs of another organization or workspace are
answered with 404. Definitions are validated here, never trusted from the
browser.
"""

from django.db import transaction
from django.http import Http404
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from model_hub.models.choices import OwnerChoices
from model_hub.models.develop_dataset import Row
from model_hub.models.evals_metric import EvalTemplate, EvalTemplateVersion
from model_hub.serializers.contracts import MODEL_HUB_ERROR_RESPONSES
from model_hub.serializers.dag_eval import (
    DagCreateRequestSerializer,
    DagCreateResponseSerializer,
    DagDetailResponseSerializer,
    DagErrorResponseSerializer,
    DagExecuteRequestSerializer,
    DagExecuteResponseSerializer,
    DagPathsQuerySerializer,
    DagPathsResponseSerializer,
    DagRunRequestSerializer,
    DagRunResponseSerializer,
    DagUpdateRequestSerializer,
    DagUpdateResponseSerializer,
)
from model_hub.services import dag_eval_runs as runs
from model_hub.services.dag_eval_definition import (
    DagInvalid,
    first_judge_of,
    outline_of,
    parse,
    summary_of,
    variables_of,
)
from model_hub.utils.eval_validators import validate_eval_name
from model_hub.views.composite_jury import jury_scope
from tfc.utils.api_contracts import validated_request

RESPONSES = {**MODEL_HUB_ERROR_RESPONSES, 400: DagErrorResponseSerializer}


def invalid(errors):
    return Response({"errors": errors, "error": " · ".join(errors)}, status=400)


def scoped_tree(request, template_id, *, lock=False):
    org, workspace = jury_scope(request)
    queryset = runs.tree_queryset(org, workspace)
    if lock:
        queryset = queryset.select_for_update()
    tree = queryset.filter(id=template_id).first()
    if tree is None:
        raise Http404("Decision tree not found.")
    return tree


def definition_of(raw, output_type, tree_model):
    body = dict(raw)
    if output_type:
        body["output_type"] = output_type
    return parse(body, tree_model=tree_model)


def tree_fields(dag, names, threshold):
    pass_fail = dag["output_type"] == "pass_fail"
    return {
        "criteria": outline_of(dag, names),
        "output_type_normalized": "pass_fail" if pass_fail else "percentage",
        "pass_threshold": 0.5 if pass_fail or threshold is None else threshold,
    }


def tree_config(tree_config_value, dag, mapping):
    config = {
        **(tree_config_value or {}),
        "dag": dag,
        "eval_type_id": "DecisionTreeEvaluator",
        "output": "Pass/Fail" if dag["output_type"] == "pass_fail" else "score",
        "required_keys": ["output"] + [v for v in variables_of(dag) if v != "output"],
    }
    if mapping is not None:
        config["mapping"] = mapping
    return config


class DagCreateView(APIView):
    permission_classes = [IsAuthenticated]

    @validated_request(
        request_serializer=DagCreateRequestSerializer,
        responses={200: DagCreateResponseSerializer, **RESPONSES},
        reject_unknown_fields=True,
    )
    def post(self, request):
        org, workspace = jury_scope(request)
        data = request.validated_data
        try:
            name = validate_eval_name(data["name"])
        except ValueError as exc:
            return invalid([str(exc)])
        taken = (
            EvalTemplate.no_workspace_objects.filter(
                name=name, deleted=False, organization=org
            ).exists()
            or EvalTemplate.no_workspace_objects.filter(
                name=name, deleted=False, owner=OwnerChoices.SYSTEM.value
            ).exists()
        )
        if taken:
            return invalid(["An evaluation with this name already exists."])
        try:
            dag = definition_of(
                data["dag"], data.get("output_type"), data.get("model") or None
            )
            names = runs.eval_names(dag, org, workspace)
        except DagInvalid as exc:
            return invalid(exc.errors)
        model = data.get("model") or first_judge_of(dag)
        with transaction.atomic():
            tree = EvalTemplate.no_workspace_objects.create(
                name=name,
                description=(data.get("description") or "").strip() or summary_of(dag),
                organization=org,
                workspace=workspace,
                owner=OwnerChoices.USER.value,
                eval_type="dag",
                template_type="single",
                config=tree_config({}, dag, data.get("mapping")),
                model=model,
                visible_ui=True,
                **tree_fields(dag, names, data.get("pass_threshold")),
            )
            EvalTemplateVersion.objects.create_version(
                eval_template=tree,
                prompt_messages=[],
                config_snapshot={"dag": dag, "config": {"dag": dag}},
                criteria=tree.criteria,
                model=model,
                user=request.user,
                organization=org,
                workspace=workspace,
            )
        return Response(
            {
                "result": {
                    "id": str(tree.id),
                    "name": tree.name,
                    "eval_type": "dag",
                    "version": runs.version_label(tree),
                }
            }
        )


class DagDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @validated_request(responses={200: DagDetailResponseSerializer, **RESPONSES})
    def get(self, request, template_id):
        tree = scoped_tree(request, template_id)
        org, workspace = jury_scope(request)
        dag = tree.config["dag"]
        try:
            names = runs.eval_names(dag, org, workspace)
        except DagInvalid:
            names = {}
        return Response(
            {
                "result": {
                    "id": str(tree.id),
                    "name": tree.name,
                    "description": tree.description or "",
                    "output_type": dag["output_type"],
                    "pass_threshold": tree.pass_threshold,
                    "dag": dag,
                    "mapping": tree.config.get("mapping"),
                    "instructions": outline_of(dag, names),
                    "model": tree.model,
                    "version": runs.version_label(tree),
                }
            }
        )

    @validated_request(
        request_serializer=DagUpdateRequestSerializer,
        responses={200: DagUpdateResponseSerializer, **RESPONSES},
        reject_unknown_fields=True,
    )
    def patch(self, request, template_id):
        org, workspace = jury_scope(request)
        data = request.validated_data
        with transaction.atomic():
            tree = scoped_tree(request, template_id, lock=True)
            try:
                dag = definition_of(
                    data.get("dag", tree.config["dag"]),
                    data.get("output_type"),
                    tree.model,
                )
                names = runs.eval_names(dag, org, workspace)
            except DagInvalid as exc:
                return invalid(exc.errors)
            threshold = data.get("pass_threshold", tree.pass_threshold)
            for field, value in tree_fields(dag, names, threshold).items():
                setattr(tree, field, value)
            tree.config = tree_config(tree.config, dag, data.get("mapping"))
            tree.model = tree.model or first_judge_of(dag)
            if "description" in data:
                tree.description = data["description"]
            tree.save()
        return Response(
            {
                "result": {
                    "version": runs.version_label(tree),
                    "instructions": tree.criteria,
                }
            }
        )


def adhoc_values(data, org, workspace):
    """Typed values, or one scoped dataset row read through the mapping."""
    if not data.get("dataset_id"):
        return dict(data.get("values") or {})
    dataset = runs.scoped_dataset(data["dataset_id"], org, workspace)
    if dataset is None:
        raise Http404("Dataset not found.")
    row = Row.no_workspace_objects.filter(
        id=data["row_id"], dataset=dataset, deleted=False
    ).first()
    if row is None:
        raise Http404("Row not found.")
    mapping = {k: v for k, v in (data.get("mapping") or {}).items() if v}
    runs.column_ids(dataset, mapping)
    return runs.row_values(dataset, [row], mapping)[str(row.id)]


class DagExecuteAdhocView(APIView):
    """Test a definition on one row; nothing is stored."""

    permission_classes = [IsAuthenticated]

    @validated_request(
        request_serializer=DagExecuteRequestSerializer,
        responses={200: DagExecuteResponseSerializer, **RESPONSES},
        reject_unknown_fields=True,
    )
    def post(self, request):
        org, workspace = jury_scope(request)
        data = request.validated_data
        tree = (
            scoped_tree(request, data["template_id"])
            if data.get("template_id")
            else None
        )
        tree_model = tree.model if tree else None
        try:
            dag = definition_of(data["dag"], None, tree_model)
            runs.eval_names(dag, org, workspace)
            values = adhoc_values(data, org, workspace)
            values.pop("output", None)
            runs.require_inputs(dag, values)
        except DagInvalid as exc:
            return invalid(exc.errors)
        threshold = data.get("pass_threshold")
        calls = runs.TreeCalls(
            org,
            workspace,
            tree.name if tree else "decision-tree-test",
            tree_model or first_judge_of(dag),
        )
        result = calls.execute(
            dag,
            output=data["output"],
            values=values,
            threshold=0.5 if threshold is None else threshold,
        )
        return Response({"result": result})


class DagRunView(APIView):
    """Run the saved tree on a dataset's first rows and store every path."""

    permission_classes = [IsAuthenticated]

    @validated_request(
        request_serializer=DagRunRequestSerializer,
        responses={200: DagRunResponseSerializer, **RESPONSES},
        reject_unknown_fields=True,
    )
    def post(self, request, template_id):
        org, workspace = jury_scope(request)
        tree = scoped_tree(request, template_id)
        data = request.validated_data
        dataset = runs.scoped_dataset(data["dataset_id"], org, workspace)
        if dataset is None:
            raise Http404("Dataset not found.")
        dag = tree.config["dag"]
        mapping = data["mapping"]
        try:
            runs.eval_names(dag, org, workspace)
            runs.column_ids(dataset, mapping)
            runs.require_inputs(dag, dict.fromkeys(mapping, ""))
        except DagInvalid as exc:
            return invalid(exc.errors)
        rows = runs.scoped_rows(dataset, limit=data.get("limit", runs.MAX_RUN_ROWS))
        if not rows:
            return invalid(["This dataset has no rows"])
        values = runs.row_values(dataset, rows, mapping)
        calls = runs.TreeCalls(org, workspace, tree.name, tree.model)
        threshold = tree.pass_threshold if tree.pass_threshold is not None else 0.5
        results = []
        for row in rows:
            row_values = dict(values[str(row.id)])
            output = row_values.pop("output", "")
            results.append(
                (
                    str(row.id),
                    calls.execute(
                        dag, output=output, values=row_values, threshold=threshold
                    ),
                )
            )
        run, label = runs.save_run(
            tree=tree,
            user=request.user,
            org=org,
            workspace=workspace,
            dataset=dataset,
            mapping=mapping,
            results=results,
        )
        return Response(
            {
                "result": {
                    "run_id": str(run.id),
                    "run_label": label,
                    "n": len(results),
                    "rows": [
                        {
                            "row_id": row_id,
                            "value": result["value"],
                            "passed": result["passed"],
                            "skipped": result["skipped"],
                            "path_key": result["path_key"],
                            "path_label": result["path_label"],
                        }
                        for row_id, result in results
                    ],
                }
            }
        )


class DagPathsView(APIView):
    """The paths one stored run took; reads stored results only."""

    permission_classes = [IsAuthenticated]

    @validated_request(
        query_serializer=DagPathsQuerySerializer,
        responses={200: DagPathsResponseSerializer, **RESPONSES},
    )
    def get(self, request, template_id):
        org, workspace = jury_scope(request)
        tree = scoped_tree(request, template_id)
        report = runs.paths_report(
            tree, org, workspace, request.validated_query_data.get("run_id")
        )
        if report is None:
            raise Http404("Run not found.")
        return Response({"result": report})
