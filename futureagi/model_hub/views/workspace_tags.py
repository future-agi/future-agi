"""The workspace tag registry and an experiment's tags.

Every read and write is scoped to the caller's organization and workspace: a
tag or an experiment outside it answers 404, as one that does not exist.
Writes need workspace write access. An error's sentence is the response's
`result`, which the page shows as it is.
"""

from django.db import IntegrityError, transaction
from django.db.models import Count
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from model_hub.models.experiments import ExperimentsTable
from model_hub.models.workspace_tags import (
    TAG_COLORS,
    ExperimentTag,
    WorkspaceTag,
    tag_name_key,
)
from model_hub.serializers.workspace_tags import (
    ExperimentTagsResponseSerializer,
    ExperimentTagsSerializer,
    TagCreateSerializer,
    TagDeletedResponseSerializer,
    TagListResponseSerializer,
    TagQuerySerializer,
    TagResponseSerializer,
    TagUpdateSerializer,
)
from model_hub.utils.workspace_scope import (
    request_organization,
    request_workspace,
    scoped_dataset_queryset,
)
from tfc.constants.levels import Level
from tfc.permissions.utils import get_effective_workspace_level
from tfc.routers import force_primary
from tfc.utils.api_contracts import validated_request
from tfc.utils.general_methods import GeneralMethods

_gm = GeneralMethods()

NO_WORKSPACE = "Open a workspace to use tags."
NO_ACCESS = "You do not have access to this workspace's tags."
NO_WRITE = "You do not have permission to change tags in this workspace."
TAG_NOT_FOUND = "Tag not found"
EXPERIMENT_NOT_FOUND = "Experiment not found"
UNKNOWN_TAG = "A tag in tag_ids is not one of this workspace's tags."


def _clash_sentence(name):
    return f"A tag named “{name}” already exists."


def _scope(request):
    """(organization, workspace) of the caller, or an error response."""
    organization = request_organization(request)
    workspace = request_workspace(request)
    if (
        organization is None
        or workspace is None
        or workspace.organization_id != organization.id
    ):
        return None, _gm.forbidden_response(NO_WORKSPACE)
    level = get_effective_workspace_level(request.user, workspace.id)
    if level is None or level < Level.VIEWER:
        return None, _gm.forbidden_response(NO_ACCESS)
    return (organization, workspace), None


def _can_write(request, workspace):
    return bool(request.user.can_write_to_workspace(workspace))


def _tags(organization, workspace):
    return WorkspaceTag.objects.filter(organization=organization, workspace=workspace)


def _experiments(request):
    return ExperimentsTable.no_workspace_objects.filter(
        deleted=False, dataset__in=scoped_dataset_queryset(request)
    )


def _live_links(request):
    return ExperimentTag.objects.filter(experiment__in=_experiments(request))


def _result(tag, experiments):
    return {
        "id": str(tag.id),
        "name": tag.name,
        "color": tag.color,
        "description": tag.description,
        # Tags are put on experiments only; datasets and rows carry none.
        "used_on": {"experiments": experiments, "datasets": 0, "rows": 0},
        "created_by": getattr(tag.created_by, "name", None) if tag.created_by_id else None,
        "created_at": tag.created_at,
    }


def _experiment_counts(request, tags):
    rows = (
        _live_links(request)
        .filter(tag__in=tags)
        .values("tag_id")
        .annotate(n=Count("experiment_id", distinct=True))
    )
    return {row["tag_id"]: row["n"] for row in rows}


def _one(request, tag):
    return _result(tag, _experiment_counts(request, [tag]).get(tag.id, 0))


class WorkspaceTagsView(APIView):
    permission_classes = [IsAuthenticated]

    @validated_request(
        query_serializer=TagQuerySerializer,
        responses={200: TagListResponseSerializer},
    )
    def get(self, request):
        scope, refused = _scope(request)
        if refused:
            return refused
        tags = list(_tags(*scope).select_related("created_by"))
        counts = _experiment_counts(request, tags)
        result = {"results": [_result(t, counts.get(t.id, 0)) for t in tags]}
        if request.validated_query_data.get("include") == "experiments":
            experiments = {}
            for experiment_id, tag_id in (
                _live_links(request)
                .filter(tag__in=tags)
                .order_by("created_at", "id")
                .values_list("experiment_id", "tag_id")
            ):
                experiments.setdefault(str(experiment_id), []).append(str(tag_id))
            result["experiments"] = experiments
        return _gm.success_response(result)

    @validated_request(
        request_serializer=TagCreateSerializer,
        responses={200: TagResponseSerializer},
    )
    def post(self, request):
        scope, refused = _scope(request)
        if refused:
            return refused
        organization, workspace = scope
        if not _can_write(request, workspace):
            return _gm.forbidden_response(NO_WRITE)
        data = request.validated_data
        key = tag_name_key(data["name"])
        with force_primary():
            try:
                with transaction.atomic():
                    tags = _tags(organization, workspace)
                    clash = tags.filter(name_key=key).first()
                    if clash is not None:
                        return _gm.bad_request(_clash_sentence(clash.name))
                    color = data.get("color") or TAG_COLORS[tags.count() % len(TAG_COLORS)]
                    tag = WorkspaceTag.objects.create(
                        organization=organization,
                        workspace=workspace,
                        name=data["name"],
                        name_key=key,
                        color=color,
                        description=data.get("description", ""),
                        created_by=request.user,
                    )
            except IntegrityError:
                clash = _tags(organization, workspace).filter(name_key=key).first()
                return _gm.bad_request(_clash_sentence(clash.name if clash else data["name"]))
        return _gm.success_response(_one(request, tag))


class WorkspaceTagDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @validated_request(
        request_serializer=TagUpdateSerializer,
        responses={200: TagResponseSerializer},
    )
    def patch(self, request, tag_id):
        scope, refused = _scope(request)
        if refused:
            return refused
        organization, workspace = scope
        with force_primary():
            tag = _tags(organization, workspace).filter(pk=tag_id).first()
            if tag is None:
                return _gm.not_found(TAG_NOT_FOUND)
            if not _can_write(request, workspace):
                return _gm.forbidden_response(NO_WRITE)
            data = request.validated_data
            try:
                with transaction.atomic():
                    tag = (
                        _tags(organization, workspace)
                        .select_for_update(of=("self",))
                        .filter(pk=tag_id)
                        .first()
                    )
                    if tag is None:
                        return _gm.not_found(TAG_NOT_FOUND)
                    fields = []
                    if "name" in data and data["name"] != tag.name:
                        key = tag_name_key(data["name"])
                        clash = (
                            _tags(organization, workspace)
                            .filter(name_key=key)
                            .exclude(pk=tag.pk)
                            .first()
                        )
                        if clash is not None:
                            return _gm.bad_request(_clash_sentence(clash.name))
                        tag.name, tag.name_key = data["name"], key
                        fields += ["name", "name_key"]
                    for key in ("color", "description"):
                        if key in data and data[key] != getattr(tag, key):
                            setattr(tag, key, data[key])
                            fields.append(key)
                    if fields:
                        tag.save(update_fields=[*fields, "updated_at"])
            except IntegrityError:
                return _gm.bad_request(_clash_sentence(data["name"]))
        return _gm.success_response(_one(request, tag))

    @validated_request(responses={200: TagDeletedResponseSerializer})
    def delete(self, request, tag_id):
        scope, refused = _scope(request)
        if refused:
            return refused
        organization, workspace = scope
        with force_primary():
            tag = _tags(organization, workspace).filter(pk=tag_id).first()
            if tag is None:
                return _gm.not_found(TAG_NOT_FOUND)
            if not _can_write(request, workspace):
                return _gm.forbidden_response(NO_WRITE)
            with transaction.atomic():
                # Removes it from every experiment too (the links cascade).
                deleted, _ = _tags(organization, workspace).filter(pk=tag_id).delete()
            if not deleted:
                return _gm.not_found(TAG_NOT_FOUND)
        return _gm.success_response({"id": str(tag_id)})


class ExperimentTagsView(APIView):
    permission_classes = [IsAuthenticated]

    @validated_request(
        request_serializer=ExperimentTagsSerializer,
        responses={200: ExperimentTagsResponseSerializer},
    )
    def put(self, request, experiment_id):
        scope, refused = _scope(request)
        if refused:
            return refused
        organization, workspace = scope
        with force_primary():
            if not _experiments(request).filter(pk=experiment_id).exists():
                return _gm.not_found(EXPERIMENT_NOT_FOUND)
            if not _can_write(request, workspace):
                return _gm.forbidden_response(NO_WRITE)
            wanted = list(dict.fromkeys(request.validated_data["tag_ids"]))
            with transaction.atomic():
                # One PUT at a time per experiment: the replace below reads
                # then writes its links.
                experiment = (
                    _experiments(request)
                    .select_for_update(of=("self",))
                    .filter(pk=experiment_id)
                    .first()
                )
                if experiment is None:
                    return _gm.not_found(EXPERIMENT_NOT_FOUND)
                # Lock the wanted tags: a tag deleted while this runs waits for
                # it, and its delete then removes these links too, so no link
                # is written to a tag that is gone.
                found = set(
                    _tags(organization, workspace)
                    .select_for_update()
                    .filter(pk__in=wanted)
                    .values_list("pk", flat=True)
                )
                if len(found) != len(wanted):
                    return _gm.bad_request(UNKNOWN_TAG)
                # The PUT replaces this workspace's tags on the experiment and
                # leaves any other link alone.
                links = ExperimentTag.objects.filter(
                    experiment=experiment,
                    tag__organization=organization,
                    tag__workspace=workspace,
                )
                links.exclude(tag_id__in=wanted).delete()
                have = set(links.values_list("tag_id", flat=True))
                ExperimentTag.objects.bulk_create(
                    [
                        ExperimentTag(experiment=experiment, tag_id=tag_id)
                        for tag_id in wanted
                        if tag_id not in have
                    ]
                )
        return _gm.success_response(
            {"experiment_id": str(experiment_id), "tag_ids": [str(t) for t in wanted]}
        )
