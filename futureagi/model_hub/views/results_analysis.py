"""Tenant-scoped read-only C-ANALYSIS endpoint; URL registration belongs to P02."""
from django.db import DatabaseError
from django.db.models import Q
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from model_hub.models.experiments import ExperimentRun
from model_hub.serializers.contracts import MODEL_HUB_ERROR_RESPONSES
from model_hub.serializers.results_analysis import (
    ResultsAnalysisQuerySerializer, ResultsAnalysisResponseSerializer,
    ResultsAnalysisResultSerializer,
)
from model_hub.services.dataset_runs import get_baseline
from model_hub.services.experiment_run_writes import RunWriteError
from model_hub.services.results_analysis import AnalysisUnavailable, MAX_ITEMS, build_analysis
from model_hub.utils.workspace_scope import scoped_dataset_queryset
from tfc.routers import primary_required
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_errors import build_error_envelope
from tfc.utils.general_methods import GeneralMethods

_gm = GeneralMethods()


def _error(message, status):
    return Response(build_error_envelope(message, status_code=status), status=status)


def _population(dataset, runs, query):
    # P03 composition contract, explicitly documented in ANALYSIS-CONTRACT.json.
    # Do not silently substitute a local filter dialect or a paginated R3 read.
    try:
        from model_hub.services.results_selection import select_analysis_population
    except ImportError as exc:
        raise AnalysisUnavailable("The shared results selection adapter is unavailable.") from exc
    return select_analysis_population(
        dataset=dataset, runs=runs, filters=query.get("filters", []),
        baseline_id=query.get("baseline"), max_items=MAX_ITEMS,
    )


class ResultsAnalysisView(APIView):
    permission_classes = [IsAuthenticated]

    @validated_request(
        query_serializer=ResultsAnalysisQuerySerializer,
        responses={200: ResultsAnalysisResponseSerializer, **MODEL_HUB_ERROR_RESPONSES},
        reject_unknown_fields=True,
        operation_id="dataset_results_analysis",
        operation_description=(
            "Read full filtered immutable run evidence. Runs, baseline and candidate "
            "must all be visible in the authenticated dataset/workspace. Strict "
            "version, plan and evaluator snapshots are preserved. Comparisons "
            "are descriptive paired trial means, not statistical significance or "
            "causal diagnosis. Unsupported group_by is 400; incomplete population "
            "or work-bound excess is 503, never a truncated success."
        ),
    )
    @primary_required
    def get(self, request, dataset_id):
        if getattr(request, "organization", None) is None:
            return _gm.not_found("Dataset not found")
        query = dict(request.validated_query_data)
        try:
            dataset = scoped_dataset_queryset(request).filter(id=dataset_id).first()
            if dataset is None:
                return _gm.not_found("Dataset not found")
            referenced = set(query["runs"])
            for field in ("baseline", "cand"):
                value = query.get(field)
                if value is not None and value != "none":
                    referenced.add(str(value))
            visible = ExperimentRun.no_workspace_objects.filter(
                id__in=referenced, deleted=False, dataset_version__deleted=False,
                dataset_version__dataset=dataset,
            ).filter(
                Q(experiment__isnull=True) |
                Q(experiment__dataset=dataset, experiment__deleted=False)
            ).select_related("dataset_version")
            by_id = {str(run.id): run for run in visible}
            # Authorize EVERY reference before any count, selection, label or membership error.
            if set(by_id) != referenced:
                return _gm.not_found("Experiment run not found")
            for field in ("baseline", "cand"):
                value = query.get(field)
                if value is not None and value != "none" and str(value) not in query["runs"]:
                    raise ValidationError({field: "Run must be in the selected run set."})
            ordered = [by_id[run_id] for run_id in query["runs"]]
            if "baseline" not in query:
                pin = get_baseline(dataset)
                query["baseline"] = (
                    str(pin.run_id) if pin and str(pin.run_id) in query["runs"]
                    else query["runs"][0] if len(ordered) > 1 else "none"
                )
            selection = _population(dataset, ordered, query)
            payload = build_analysis(dataset, ordered, selection, query)
            return _gm.success_response(ResultsAnalysisResultSerializer(payload).data)
        except ValidationError:
            raise
        except RunWriteError:
            return _error("Immutable run evidence is incomplete or incompatible with analysis.", 409)
        except AnalysisUnavailable as exc:
            return _error(str(exc), 503)
        except DatabaseError:
            return _error("Analysis evidence is temporarily unavailable. Retry later.", 503)
