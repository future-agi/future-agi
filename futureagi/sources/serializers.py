from rest_framework import serializers
from sources.models.source_book import SourceBook
from sources.models.scan_page import ScanPage
from sources.models.target_passage import TargetPassage


class SourceBookSerializer(serializers.ModelSerializer):
    class Meta:
        model = SourceBook
        ref_name = "SourcesSourceBookSerializer"
        fields = [
            "id",
            "title",
            "author",
            "publication_year",
            "gallica_ark",
            "description",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class ScanPageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ScanPage
        ref_name = "SourcesScanPageSerializer"
        fields = [
            "id",
            "book",
            "page_number",
            "page_label",
            "iiif_url",
            "raw_ocr_text",
            "ocr_metadata",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class TargetPassageSerializer(serializers.ModelSerializer):
    class Meta:
        model = TargetPassage
        ref_name = "SourcesTargetPassageSerializer"
        fields = [
            "id",
            "scan_page",
            "text_content",
            "modernized_text",
            "line_range",
            "tags",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class ImportTranskribusSerializer(serializers.Serializer):
    file = serializers.FileField(required=True)
