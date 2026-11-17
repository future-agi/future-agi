import ipaddress
from urllib.parse import urlsplit

from rest_framework import serializers

from integrations.services.credentials import CredentialManager
from tracer.models.bulk_export import BulkExportJob
from tracer.models.export_schedule import ExportSchedule
from tracer.serializers.bulk_export import validate_export_query
from tracer.serializers.filters import FILTER_LIST_MAX_ITEMS, StrictInputSerializer


class S3DestinationSerializer(StrictInputSerializer):
    bucket = serializers.RegexField(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
    prefix = serializers.CharField(required=False, default="", allow_blank=True, max_length=512)
    region = serializers.RegexField(r"^[a-zA-Z0-9-]{1,64}$")
    endpoint_url = serializers.URLField(required=False, max_length=1024)

    def validate_prefix(self, value):
        if any(part in (".", "..") for part in value.split("/")) or any(ord(char) < 32 for char in value):
            raise serializers.ValidationError("Invalid object prefix.")
        return value.strip("/")

    def validate_endpoint_url(self, value):
        try:
            parsed = urlsplit(value)
            if (
                parsed.scheme != "https" or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment or parsed.path not in ("", "/")
                or "\\" in value or any(ord(char) <= 32 or ord(char) == 127 for char in value)
                or parsed.port == 0
            ):
                raise ValueError
            host = parsed.hostname.rstrip(".").lower()
            if "." not in host or host.endswith((".localhost", ".local", ".internal")):
                raise ValueError
            try:
                address = ipaddress.ip_address(host)
            except ValueError:
                address = None
            if address is not None and not address.is_global:
                raise ValueError
        except ValueError:
            raise serializers.ValidationError("Use a public HTTPS S3 endpoint without credentials or query parameters.") from None
        return value.rstrip("/")

    def validate(self, attrs):
        from tracer.services.export_destination import validate_s3_endpoint

        if "endpoint_url" in attrs:
            try:
                attrs["endpoint_url"] = validate_s3_endpoint(attrs["endpoint_url"], attrs.get("region", ""))
            except ValueError as exc:
                raise serializers.ValidationError({"endpoint_url": str(exc)}) from None
        return attrs


class S3CredentialsSerializer(StrictInputSerializer):
    aws_access_key_id = serializers.CharField(max_length=256, trim_whitespace=False)
    aws_secret_access_key = serializers.CharField(max_length=1024, trim_whitespace=False)
    aws_session_token = serializers.CharField(required=False, max_length=16384, trim_whitespace=False)


class ExportScheduleWriteSerializer(StrictInputSerializer):
    project_id = serializers.UUIDField()
    row_type = serializers.ChoiceField(choices=BulkExportJob.ROW_TYPES)
    filters = serializers.JSONField(required=False, default=dict)
    format = serializers.ChoiceField(choices=BulkExportJob.FORMATS, default="csv")
    destination = S3DestinationSerializer()
    credentials = S3CredentialsSerializer(write_only=True)
    interval = serializers.ChoiceField(choices=ExportSchedule.INTERVALS)
    enabled = serializers.BooleanField(required=False, default=True)

    def validate(self, attrs):
        instance = self.instance
        if instance and "project_id" in attrs and attrs["project_id"] != instance.project_id:
            raise serializers.ValidationError({"project_id": "A schedule cannot change projects."})
        row_type = attrs.get("row_type", instance.row_type if instance else None)
        options = attrs.get("filters", instance.filters if instance else {})
        project_id = attrs.get("project_id", instance.project_id if instance else None)
        validated = validate_export_query(row_type, options, project_id)
        # Reserve one canonical filter for the immutable job time window.
        if len(validated["filters"]) >= FILTER_LIST_MAX_ITEMS:
            raise serializers.ValidationError({"filters": "Leave room for the schedule time-window filter."})
        if "destination" in attrs:
            # Nested serializers inherit PATCH's partial flag. A destination
            # replacement is nevertheless complete, not an accidental merge.
            destination = S3DestinationSerializer(data=attrs["destination"])
            destination.is_valid(raise_exception=True)
            attrs["destination"] = destination.validated_data
        if "credentials" in attrs:
            credentials = S3CredentialsSerializer(data=attrs.pop("credentials"))
            credentials.is_valid(raise_exception=True)
            try:
                attrs["encrypted_credentials"] = CredentialManager.encrypt(credentials.validated_data)
            except ValueError:
                raise serializers.ValidationError("Export credential encryption is not configured.") from None
        return attrs

    def create(self, validated_data):
        return ExportSchedule.objects.create(**validated_data)

    def update(self, instance, validated_data):
        for name, value in validated_data.items():
            setattr(instance, name, value)
        instance.save()
        return instance


class ExportSchedulePatchSerializer(ExportScheduleWriteSerializer):
    def validate(self, attrs):
        # The view validates the merged configuration against a locked,
        # project-scoped instance before saving a partial update.
        return attrs


class ExportScheduleReadSerializer(serializers.ModelSerializer):
    destination = S3DestinationSerializer(read_only=True)

    class Meta:
        model = ExportSchedule
        fields = [
            "id", "project_id", "row_type", "filters", "format", "destination",
            "interval", "last_run_at", "enabled", "created_at", "updated_at",
        ]
        read_only_fields = fields


class ExportScheduleListQuerySerializer(StrictInputSerializer):
    project_id = serializers.UUIDField()


class ExportScheduleListSerializer(serializers.Serializer):
    results = ExportScheduleReadSerializer(many=True)
