"""Atomic folder operations serialized within the authorized workspace."""

from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from accounts.models.workspace import Workspace
from model_hub.models.choices import DatasetSourceChoices
from model_hub.models.dataset_folders import DatasetFolder
from model_hub.models.dataset_stars import DatasetStar
from model_hub.serializers.dataset_folders import FolderPathSerializer
from model_hub.views.develop_dataset import _request_dataset_queryset
from tfc.middleware.workspace_context import get_current_workspace


def scope(request, *, lock=False):
    organization = getattr(request, "organization", None) or request.user.organization
    workspace = getattr(request, "workspace", None) or get_current_workspace()
    if not workspace or workspace.organization_id != organization.id:
        raise PermissionDenied("An authorized workspace is required.")
    if lock:
        # A stable row also serializes creation into a currently empty folder tree.
        get_object_or_404(
            Workspace.no_workspace_objects.select_for_update(),
            pk=workspace.pk,
            organization=organization,
            is_active=True,
        )
    return DatasetFolder.objects.filter(organization=organization, workspace=workspace)


def visible_datasets(request):
    return (
        _request_dataset_queryset(request)
        .filter(
            source__in=[
                DatasetSourceChoices.DEMO.value,
                DatasetSourceChoices.BUILD.value,
                DatasetSourceChoices.OBSERVE.value,
            ]
        )
        .exclude(scenarios__isnull=False)
    )


def subtree(queryset, path):
    return queryset.filter(Q(path=path) | Q(path__startswith=path + "/"))


def item(folder, count=0, total=0):
    return {"id": str(folder.id), "path": folder.path, "count": count, "total": total}


def folder_inventory(request):
    folder_rows = list(scope(request))
    datasets = visible_datasets(request)
    counts = dict(datasets.values_list("folder_id").annotate(n=Count("id")))
    totals = {folder.path: counts.get(folder.id, 0) for folder in folder_rows}
    for folder in folder_rows:
        parts = folder.path.split("/")
        for depth in range(1, len(parts)):
            parent = "/".join(parts[:depth])
            totals[parent] = totals.get(parent, 0) + counts.get(folder.id, 0)
    return {
        "folders": [item(f, counts.get(f.id, 0), totals[f.path]) for f in folder_rows],
        "all_count": sum(counts.values()),
        "no_folder_count": counts.get(None, 0),
        "starred_count": DatasetStar.objects.filter(
            user=request.user, dataset__in=datasets
        ).count(),
    }


def folder_detail(request, folder_id):
    queryset = scope(request)
    folder = get_object_or_404(queryset, pk=folder_id)
    datasets = visible_datasets(request)
    return item(
        folder,
        count=datasets.filter(folder=folder).count(),
        total=datasets.filter(folder__in=subtree(queryset, folder.path)).count(),
    )


def ensure_ancestors(request, queryset, path):
    workspace = getattr(request, "workspace", None) or get_current_workspace()
    parts = path.split("/")
    for depth in range(1, len(parts)):
        queryset.get_or_create(
            path="/".join(parts[:depth]),
            defaults={
                "organization_id": workspace.organization_id,
                "workspace": workspace,
            },
        )
    return workspace


def create_folder(request, path):
    queryset = scope(request, lock=True)
    if queryset.filter(path=path).exists():
        raise ValidationError({"path": "A folder already exists at that path."})
    workspace = ensure_ancestors(request, queryset, path)
    return queryset.create(
        path=path, organization_id=workspace.organization_id, workspace=workspace
    )


def relocate_folder(request, folder_id, path):
    queryset = scope(request, lock=True)
    folder = get_object_or_404(queryset, pk=folder_id)
    if path == folder.path:
        return folder
    if path.startswith(folder.path + "/"):
        raise ValidationError({"path": "A folder cannot move inside itself."})
    moving = list(subtree(queryset, folder.path))
    destinations = {f.id: path + f.path[len(folder.path) :] for f in moving}
    for destination in destinations.values():
        serializer = FolderPathSerializer(data={"path": destination})
        serializer.is_valid(raise_exception=True)
    if (
        queryset.exclude(pk__in=destinations)
        .filter(path__in=destinations.values())
        .exists()
    ):
        raise ValidationError({"path": "A folder already exists at the destination."})
    ensure_ancestors(request, queryset, path)
    for row in moving:
        row.path = destinations[row.id]
        row.updated_at = timezone.now()
    DatasetFolder.objects.bulk_update(moving, ["path", "updated_at"])
    folder.path = path
    return folder


def remove_folder(request, folder_id):
    queryset = scope(request, lock=True)
    folder = get_object_or_404(queryset, pk=folder_id)
    subtree(queryset, folder.path).delete()  # SET_NULL keeps every dataset.


def move_datasets(request, ids, folder_id):
    queryset = scope(request, lock=True)
    folder = get_object_or_404(queryset, pk=folder_id) if folder_id else None
    # Validate all targets before the first write; a mixed-tenant batch is refused whole.
    datasets = visible_datasets(request).filter(pk__in=ids)
    allowed = list(
        datasets.select_for_update(of=("self",)).values_list("pk", flat=True)
    )
    if set(allowed) != set(ids):
        raise NotFound("Dataset not found.")
    datasets.update(folder=folder, updated_at=timezone.now())
    return {
        "dataset_ids": [str(i) for i in ids],
        "folder_id": str(folder.id) if folder else None,
    }


def filter_dataset_folders(queryset, params):
    """Folder filtering is independent of the richer list-query capability."""
    queryset = queryset.select_related("folder")
    if "folder_path" not in params:
        return queryset
    path = params["folder_path"]
    if not path:
        return queryset.filter(folder__isnull=True)
    return queryset.filter(
        Q(folder__path=path) | Q(folder__path__startswith=path + "/")
    )
