"""Authorized published context references, not inferred runtime adoption."""

from rest_framework import serializers as s

from model_hub.serializers.skills_hub_v11 import Integer
from model_hub.serializers.skills_hub_v12 import ContextSerializer, PageQuery, Selector


class DependentsQuery(PageQuery):
    view = s.ChoiceField(choices=["active", "history"], default="active")
    candidate_version = Integer(min_value=1, required=False)


class ContextDependent(ContextSerializer):
    context_id = s.UUIDField()
    name = s.CharField()
    context_version = Integer(min_value=1)
    labels = s.ListField(child=s.CharField())
    requested = Selector()
    locked_version = Integer(min_value=1)
    update_state = s.ChoiceField(
        choices=["pinned_as_intended", "unavailable", "update_available", "current"]
    )


class ContextDependentsPage(ContextSerializer):
    as_of = s.DateTimeField()
    scope = s.ChoiceField(choices=["published_context_references"])
    results = ContextDependent(many=True)
    count = Integer(min_value=0)
    page = Integer(min_value=1)
    page_size = Integer(min_value=1)
    total_pages = Integer(min_value=0)
