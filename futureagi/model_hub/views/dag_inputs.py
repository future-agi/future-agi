"""Resolve a draft decision tree's inputs without executing it."""

from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from model_hub.serializers.dag_eval import DagDefinitionField
from model_hub.services.dag_eval_definition import DagInvalid, normalize, variables_of
from model_hub.services.dag_eval_runs import eval_names
from model_hub.views.composite_jury import jury_scope
from model_hub.views.dag_eval import RESPONSES, invalid
from tfc.utils.api_contracts import validated_request


class DagInputsRequestSerializer(serializers.Serializer):
    dag = DagDefinitionField()


class DagInputsSerializer(serializers.Serializer):
    required_keys = serializers.ListField(child=serializers.CharField())


class DagInputsResponseSerializer(serializers.Serializer):
    result = DagInputsSerializer()


class DagInputsView(APIView):
    permission_classes = [IsAuthenticated]

    @validated_request(
        request_serializer=DagInputsRequestSerializer,
        responses={200: DagInputsResponseSerializer, **RESPONSES},
        reject_unknown_fields=True,
    )
    def post(self, request):
        org, workspace = jury_scope(request)
        try:
            # An editor draft may have unconnected edges, but referenced evals
            # must already be valid and visible in this workspace.
            dag = normalize(request.validated_data["dag"])
            eval_names(dag, org, workspace)
        except DagInvalid as exc:
            return invalid(exc.errors)
        keys = list(dict.fromkeys(["output", *variables_of(dag)]))
        return Response({"result": {"required_keys": keys}})
