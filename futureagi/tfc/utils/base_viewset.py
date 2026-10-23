import structlog
from django.core.exceptions import FieldDoesNotExist
from django.db import models

logger = structlog.get_logger(__name__)


def active_request_organization(request):
    """The organization a request acts in: ``request.organization`` (set by
    authentication), else the user's active membership. None when neither
    resolves; a request with no organization reaches no org-owned row.

    Same rule as ``accounts.utils.get_request_organization``, which this does
    not call because it needs a request with an authenticated user; a viewset's
    get_queryset also runs without one (schema generation, anonymous access).
    """
    organization = getattr(request, "organization", None)
    if organization:
        return organization
    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    from accounts.utils import get_user_organization

    return get_user_organization(user)


def scope_to_request_organization(queryset, request):
    """Filter an org-owned queryset to the request's organization.

    Fails closed: with no organization the answer is no rows. A missing
    organization is not a wildcard.
    """
    organization = active_request_organization(request)
    if not organization:
        return queryset.none()
    return queryset.filter(organization=organization)


def _forward_relation(model, name):
    """The forward foreign key (or one-to-one) ``name`` of ``model``, else None."""
    try:
        field = model._meta.get_field(name)
    except FieldDoesNotExist:
        return None
    if field.is_relation and field.concrete and (field.many_to_one or field.one_to_one):
        return field
    return None


def organization_lookup_for(model, explicit=None):
    """The ORM lookup from a row of ``model`` to the organization that owns it.

    ``explicit`` (a viewset's ``organization_lookup``) wins. Otherwise the
    model's own ``organization`` foreign key; else, for a model scoped by
    workspace or project only, the organization of its ``workspace``, then of
    its ``project``. None when the model has none of these: the mixins then
    serve no rows.
    """
    if explicit:
        return explicit
    if _forward_relation(model, "organization") is not None:
        return "organization"
    for via in ("workspace", "project"):
        field = _forward_relation(model, via)
        if field is not None and _forward_relation(field.related_model, "organization"):
            return f"{via}__organization"
    return None


def workspace_lookup_for(model):
    """The ORM path from a row of ``model`` to its workspace: its own
    ``workspace``, else its ``project``'s. None for a model scoped by neither."""
    if hasattr(model, "workspace"):
        return "workspace"
    try:
        related_field_names = [
            f.name for f in model._meta.get_fields() if getattr(f, "is_relation", False)
        ]
    except Exception:
        return None
    if "project" in related_field_names:
        return "project__workspace"
    return None


def request_workspace_q(request, workspace_lookup):
    """A filter keeping the rows, reached through ``workspace_lookup``
    (``workspace``, ``project__workspace``, ``alert__workspace``, ...), that
    the request may see by workspace.

    With a workspace: that workspace's rows; in the default workspace also the
    org's other default-workspace rows and rows with no workspace.

    With no workspace (a SAML-scoped session resolves none for a member who
    can access no workspace of the org): the rows the user would see in some
    workspace they can access, decided by who the user is, never by whether a
    row's workspace is NULL. An Owner or Admin, who can access every workspace
    of the org, is not narrowed (rows with no workspace, which org-owned
    models such as gateway rows carry by design, included). Anyone else gets
    the active workspaces they are an active member of, and rows with no
    workspace only when the default workspace is one of them: a member with
    no workspace access gets none. A missing workspace is not a wildcard.
    """
    workspace = getattr(request, "workspace", None)
    if workspace:
        if getattr(workspace, "is_default", False):
            return (
                models.Q(**{workspace_lookup: workspace})
                | models.Q(
                    **{
                        f"{workspace_lookup}__is_default": True,
                        f"{workspace_lookup}__organization": workspace.organization,
                    }
                )
                | models.Q(**{f"{workspace_lookup}__isnull": True})
            )
        return models.Q(**{workspace_lookup: workspace})

    no_rows = models.Q(pk__in=[])
    organization = active_request_organization(request)
    user = getattr(request, "user", None)
    if (
        not organization
        or user is None
        or not getattr(user, "is_authenticated", False)
        or not user.can_access_organization(organization)
    ):
        return no_rows
    if user.has_global_workspace_access(organization):
        return models.Q()

    from accounts.models.workspace import WorkspaceMembership

    workspace_ids, default_is_accessible = [], False
    for workspace_id, is_default in WorkspaceMembership.no_workspace_objects.filter(
        user=user,
        is_active=True,
        workspace__organization=organization,
        workspace__is_active=True,
    ).values_list("workspace_id", "workspace__is_default"):
        workspace_ids.append(workspace_id)
        default_is_accessible = default_is_accessible or is_default
    if not workspace_ids:
        return no_rows
    scope = models.Q(**{f"{workspace_lookup}__in": workspace_ids})
    if default_is_accessible:
        scope |= models.Q(**{f"{workspace_lookup}__isnull": True})
    return scope


_warned_without_organization_lookup = set()


class BaseModelViewSetMixin:
    """
    Mixin for ViewSets that scopes the base queryset to the request: its
    organization (always) and, for a model scoped by workspace or project,
    its workspace (``request_workspace_q``).

    Every row is constrained to the request's organization through
    ``organization_lookup_for``: the model's organization, or for a model
    scoped by workspace or project only, that workspace's or project's
    organization. A model whose organization lies further away sets the path
    on the viewset, e.g. ``organization_lookup = "dataset__organization"``.
    With no organization, or no path to one, the queryset is empty. With no
    workspace, the workspace-scoped rows are those of the workspaces the user
    can access: none for a member who can access no workspace.

    Usage:
    class MyViewSet(BaseModelViewSetMixin, viewsets.ModelViewSet):
        def get_queryset(self):
            queryset = super().get_queryset()  # gets workspace + org filtering
            queryset = queryset.filter(some_field='value')
            return queryset
    """

    # The ORM path from the model to its owning organization, for a model
    # that has no organization, workspace or project foreign key of its own.
    organization_lookup = None

    def _base_queryset_and_model(self):
        if hasattr(self, "queryset") and self.queryset is not None:
            return self.queryset.all(), self.queryset.model
        if (
            hasattr(self, "serializer_class")
            and hasattr(self.serializer_class, "Meta")
            and hasattr(self.serializer_class.Meta, "model")
        ):
            model = self.serializer_class.Meta.model
            return model.objects.all(), model
        raise AttributeError(
            f"ViewSet {self.__class__.__name__} must have either a 'queryset' attribute "
            f"or a 'serializer_class' with a 'model' in Meta, or override 'get_queryset'"
        )

    def get_queryset(self):
        """
        Get queryset with workspace and organization filtering applied.
        """
        queryset, model = self._base_queryset_and_model()
        request = getattr(self, "request", None)

        # A request with no organization reaches nothing here, whatever the
        # model is scoped by.
        organization = active_request_organization(request)
        if not organization:
            return queryset.none()

        # Every row is constrained to the request's organization, also when
        # the model is scoped by workspace or project only.
        lookup = organization_lookup_for(model, self.organization_lookup)
        if lookup is None:
            name = type(self).__qualname__
            if name not in _warned_without_organization_lookup:
                _warned_without_organization_lookup.add(name)
                logger.error(
                    "viewset_model_has_no_organization_lookup",
                    viewset=name,
                    model=model._meta.label,
                )
            return queryset.none()

        # Workspace scope: the request's workspace, or with none (a
        # SAML-scoped member who can access no workspace) the workspaces the
        # user can access.
        workspace_lookup = workspace_lookup_for(model)
        if workspace_lookup:
            queryset = queryset.filter(request_workspace_q(request, workspace_lookup))

        queryset = queryset.filter(**{lookup: organization})

        # Apply soft delete filter
        return queryset.filter(deleted=False)


class BaseModelViewSetMixinWithUserOrg(BaseModelViewSetMixin):
    """
    The same scoping as ``BaseModelViewSetMixin``, which it inherits, so the
    organization filter is written in one place.
    """
