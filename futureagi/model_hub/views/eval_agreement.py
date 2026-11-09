"""Read agreement between stored evaluation results and human labels."""

from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from model_hub.services.eval_agreement import agreement_report
from tfc.utils.api_contracts import validated_request


class EvalAgreementQuery(serializers.Serializer):
    source = serializers.CharField(required=False, max_length=100)
    dataset_id = serializers.UUIDField(required=False)
    label_id = serializers.UUIDField(required=False)
    include = serializers.ChoiceField(choices=["pairs"], required=False)


class EvalAgreementSource(serializers.Serializer):
    key = serializers.CharField()
    dataset_name = serializers.CharField()
    binding_name = serializers.CharField()
    label_name = serializers.CharField()


class EvalAgreementLabelStatistics(serializers.Serializer):
    label = serializers.CharField()
    precision = serializers.FloatField(allow_null=True)
    recall = serializers.FloatField(allow_null=True)
    f1 = serializers.FloatField()
    support = serializers.IntegerField()


class EvalAgreementPair(serializers.Serializer):
    row_id = serializers.UUIDField()
    human = serializers.JSONField()
    judge = serializers.JSONField()


class EvalAgreementReport(EvalAgreementSource):
    n = serializers.IntegerField()
    binding_id = serializers.UUIDField()
    dataset_id = serializers.UUIDField()
    label_id = serializers.UUIDField()
    label_type = serializers.CharField()
    numeric = serializers.BooleanField()
    accuracy = serializers.FloatField(allow_null=True)
    kappa = serializers.FloatField(allow_null=True)
    mad = serializers.FloatField(allow_null=True)
    spearman = serializers.FloatField(allow_null=True)
    labels = serializers.ListField(child=serializers.CharField())
    matrix = serializers.ListField(
        child=serializers.ListField(child=serializers.IntegerField())
    )
    per_label = EvalAgreementLabelStatistics(many=True)
    excluded = serializers.IntegerField()
    disagreements = EvalAgreementPair(many=True)


class EvalAgreementResult(serializers.Serializer):
    sources = EvalAgreementSource(many=True)
    report = EvalAgreementReport(allow_null=True)


class EvalAgreementResponse(serializers.Serializer):
    status = serializers.BooleanField()
    result = EvalAgreementResult()


class EvalAgreementView(APIView):
    permission_classes = [IsAuthenticated]

    @validated_request(
        query_serializer=EvalAgreementQuery,
        responses={200: EvalAgreementResponse},
        reject_unknown_fields=True,
        strict_response_validation=True,
    )
    def get(self, request, template_id):
        result = agreement_report(
            getattr(request, "organization", None),
            getattr(request, "workspace", None),
            template_id,
            request.validated_query_data,
        )
        return Response({"status": True, "result": result})
