"""Read-only evaluation input previews over a bounded set of saved dataset rows."""

from django.db.models import Q
from rest_framework import serializers
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from model_hub.models.choices import CellStatus, OwnerChoices
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import EvalTemplate, EvalTemplateVersion
from model_hub.services.experiment_eval_pins import PIN_UNSET, resolve_eval_pin
from tfc.utils.api_contracts import validated_request


class EvalMappingPreviewRequest(serializers.Serializer):
    dataset_id = serializers.UUIDField()
    template_id = serializers.UUIDField()
    row_ids = serializers.ListField(
        child=serializers.UUIDField(), min_length=1, max_length=10
    )
    config = serializers.JSONField(default=dict)
    pinned_version_id = serializers.UUIDField(required=False, allow_null=True)

    def validate_config(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("Expected a configuration object.")
        mapping = value.get("mapping", {})
        if not isinstance(mapping, dict) or len(mapping) > 64:
            raise serializers.ValidationError("Provide at most 64 input mappings.")
        for key, refs in mapping.items():
            if not isinstance(key, str) or not key or len(key) > 128:
                raise serializers.ValidationError("Invalid input name.")
            refs = refs if isinstance(refs, list) else [refs]
            if len(refs) > 16 or any(
                ref is not None and (not isinstance(ref, str) or len(ref) > 512)
                for ref in refs
            ):
                raise serializers.ValidationError("Mappings must be column references.")
        pin = value.get("version_pin")
        if pin is not None and (not isinstance(pin, str) or len(pin) > 100):
            raise serializers.ValidationError("Invalid version pin.")
        return value


class EvalMappingPreviewRow(serializers.Serializer):
    row_id = serializers.UUIDField()
    inputs_used = serializers.DictField(child=serializers.JSONField())


class EvalMappingPreviewResult(serializers.Serializer):
    version_number = serializers.IntegerField(allow_null=True)
    rows = EvalMappingPreviewRow(many=True)


class EvalMappingPreviewResponse(serializers.Serializer):
    status = serializers.BooleanField()
    result = EvalMappingPreviewResult()


def _input(column=None, value=None, status="missing", note="Not mapped"):
    return {"column": column, "value": value, "status": status, "note": note}


def _resolve_input(ref, columns, cells, *, key):
    # This is a preview of inputs, never an estimate of a judge's output. The
    # future run's output and prompt chain cannot exist on a saved input row.
    if not ref:
        return _input()
    if key == "call_type":
        return _input("call type", ref, "ok", None)
    if ref.lower() in ("output", "prompt_chain"):
        return _input(
            "run output" if ref.lower() == "output" else "prompt chain",
            status="pending",
            note="Available after the run",
        )
    from model_hub.views.eval_runner import (
        _extract_column_id_and_path,
        _get_cell_value_with_json_path,
    )

    column_id, path = _extract_column_id_and_path(ref)
    column = columns.get(str(column_id))
    if column is None:
        # Missing and foreign identifiers are indistinguishable. Do not fall
        # back to another column, knowledge base, or an evaluator invocation.
        raise NotFound("Mapped column not found.")
    label = column.name + (f".{path}" if path else "")
    cell = cells.get(str(column.id))
    if cell is not None and cell.status == CellStatus.ERROR.value:
        return _input(label, status="error", note="Source cell has an error")
    value = _get_cell_value_with_json_path(cell, path)
    if value is None or (isinstance(value, str) and not value.strip()):
        return _input(label, note="Empty on this row")
    return _input(label, value, "ok", None)


class EvalMappingPreviewView(APIView):
    permission_classes = [IsAuthenticated]

    @validated_request(
        request_serializer=EvalMappingPreviewRequest,
        responses={200: EvalMappingPreviewResponse},
        reject_unknown_fields=True,
    )
    def post(self, request):
        data = request.validated_data
        organization = getattr(request, "organization", None)
        workspace = getattr(request, "workspace", None)
        if organization is None or workspace is None:
            raise NotFound("Dataset not found.")
        dataset = Dataset.no_workspace_objects.filter(
            id=data["dataset_id"],
            organization=organization,
            workspace=workspace,
            deleted=False,
        ).first()
        if dataset is None:
            raise NotFound("Dataset not found.")
        template = EvalTemplate.no_workspace_objects.filter(
            Q(organization=organization, workspace=workspace)
            | Q(organization__isnull=True, owner=OwnerChoices.SYSTEM.value),
            id=data["template_id"],
            deleted=False,
        ).first()
        if template is None:
            raise NotFound("Evaluation not found.")
        try:
            version = resolve_eval_pin(template, data)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        if version is PIN_UNSET or version is None:
            version = EvalTemplateVersion.objects.filter(
                eval_template=template,
                deleted=False,
                is_default=True,
            ).first()
        if (
            template.organization_id is not None
            and version is not None
            and (
                version.organization_id != organization.id
                or version.workspace_id != workspace.id
            )
        ):
            raise NotFound("Evaluation version not found.")
        config = version.config_snapshot if version is not None else template.config
        if template.template_type == "composite":
            raise ValidationError("Composite input previews are not supported.")
        required = (config or {}).get("required_keys", [])
        mappings = data["config"].get("mapping", {})
        keys = list(dict.fromkeys([*required, *mappings]))
        rows = list(
            Row.no_workspace_objects.filter(
                dataset=dataset,
                id__in=data["row_ids"],
                deleted=False,
            )
        )
        if {row.id for row in rows} != set(data["row_ids"]):
            raise NotFound("Sample row not found.")
        # Read only mapped columns, not every cell in a potentially wide row.
        from uuid import UUID

        from model_hub.views.eval_runner import _extract_column_id_and_path

        column_ids = set()
        for key, refs in mappings.items():
            if key == "call_type":
                continue
            for ref in refs if isinstance(refs, list) else [refs]:
                if not ref or ref.lower() in ("output", "prompt_chain"):
                    continue
                base_id, _ = _extract_column_id_and_path(ref)
                try:
                    column_ids.add(UUID(str(base_id)))
                except (ValueError, TypeError) as exc:
                    raise NotFound("Mapped column not found.") from exc
        columns = {
            str(column.id): column
            for column in Column.no_workspace_objects.filter(
                dataset=dataset,
                id__in=column_ids,
                deleted=False,
            )
        }
        cells_by_row = {str(row.id): {} for row in rows}
        for cell in Cell.no_workspace_objects.filter(
            dataset=dataset,
            row__in=rows,
            column_id__in=columns,
            deleted=False,
        ):
            cells_by_row[str(cell.row_id)][str(cell.column_id)] = cell
        result = []
        for row_id in data["row_ids"]:
            inputs = {}
            for key in keys:
                refs = mappings.get(key)
                if isinstance(refs, list):
                    parts = [
                        _resolve_input(ref, columns, cells_by_row[str(row_id)], key=key)
                        for ref in refs
                    ]
                    inputs[key] = _input(
                        ", ".join(part["column"] or "none" for part in parts),
                        [part["value"] for part in parts],
                        "ok"
                        if parts and all(part["status"] == "ok" for part in parts)
                        else "missing",
                        None
                        if parts and all(part["status"] == "ok" for part in parts)
                        else "Some inputs are not available",
                    )
                else:
                    inputs[key] = _resolve_input(
                        refs, columns, cells_by_row[str(row_id)], key=key
                    )
            result.append({"row_id": str(row_id), "inputs_used": inputs})
        return Response(
            {
                "status": True,
                "result": {
                    "version_number": version.version_number if version else None,
                    "rows": result,
                },
            }
        )
