import structlog
from django.core.exceptions import FieldDoesNotExist
from django.db import models

from tfc.middleware.workspace_context import workspace_scope_is_empty
from tfc.utils.workspace_scope import model_has_workspace_data

logger = structlog.get_logger(__name__)

_UNSET = object()


def active_request_organization(request):
    """The organization a request acts in.

    - A request that authentication has seen: ``request.organization`` as
      authentication set it, ``None`` included. Nothing else can widen it
      (the rule of ``tfc.utils.tenant_scope.caller_organization``).
    - A request it has not seen (a test's ``force_authenticate``, a request
      handled outside DRF): the signed-in user's active membership.
    - No user, or an anonymous one: None.

    None is the case for a user an org admin removed from every org:
    ``APIKeyAuthentication`` still authenticates them, with no organization.
    A viewset's get_queryset also runs without a signed-in user (schema
    generation, anonymous access). It never falls back to the legacy
    ``user.organization`` FK, which member removal leaves set, nor to the
    bound workspace context.
    """
    organization = getattr(request, "organization", _UNSET)
    if organization is not _UNSET:
        return organization or None
    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    from accounts.utils import get_user_organization

    return get_user_organization(user)


def scope_to_request_organization(queryset, request):
    """Filter an org-owned queryset to the request's organization.

    Fails closed: with no organization the answer is no rows, never every
    org's rows. A missing organization is not a wildcard.
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
    serve no rows rather than every org's.
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


def required_organization_lookup_for(model):
    """The ORM lookup to the organization through the relation every row of
    ``model`` has, preferring it to one that may be empty.

    The model's own ``organization`` foreign key; else, among its
    ``workspace`` and ``project`` (forward relations to a model with an
    organization), a non-nullable one first, then the workspace. A saved view
    always has a workspace but not always a project. None when the model has
    none of these. ``BaseModelViewSetMixin`` applies it as well as
    ``organization_lookup_for`` when the two differ, so a row is held to the
    request's organization through both paths.
    """
    if _forward_relation(model, "organization") is not None:
        return "organization"
    candidates = []
    for via in ("workspace", "project"):
        field = _forward_relation(model, via)
        if field is not None and _forward_relation(field.related_model, "organization"):
            candidates.append((bool(field.null), via))
    if not candidates:
        return None
    # sorted() is stable: among equally required relations the workspace wins.
    _, via = sorted(candidates, key=lambda candidate: candidate[0])[0]
    return f"{via}__organization"


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


def _no_workspace_q(workspace_lookup, workspace, model=None):
    """Rows with no workspace, which a default workspace also covers.

    A model with an organization of its own is narrowed by the organization
    filter. A model reached through its project has none, so the project's
    organization must be the workspace's.
    """
    scope = models.Q(**{f"{workspace_lookup}__isnull": True})
    if (
        model is not None
        and workspace_lookup == "project__workspace"
        and _forward_relation(model, "organization") is None
    ):
        scope &= models.Q(project__organization_id=workspace.organization_id)
    return scope


def request_workspace_q(request, workspace_lookup, model=None):
    """A filter keeping the rows, reached through ``workspace_lookup``
    (``workspace``, ``project__workspace``, ``alert__workspace``, ...), that
    the request may see by workspace.

    With a workspace: that workspace's rows; in the default workspace also the
    org's other default-workspace rows and rows with no workspace (for a
    ``model`` reached through its project only, those of the workspace's
    organization's projects).

    An authenticated empty scope, or a bound session with no workspace,
    returns no rows. Only outside that state, with no workspace, return the
    rows the user would see in some
    workspace they can access, decided by who the user is, never by whether a
    row's workspace is NULL. An Owner or Admin, who can access every workspace
    of the org, is not narrowed (rows with no workspace, which org-owned
    models such as gateway rows carry by design, included). Anyone else gets
    the active workspaces they are an active member of, and rows with no
    workspace only when the default workspace is one of them: a member with
    no workspace access gets none. A missing workspace is not a wildcard.
    """
    # The authenticated-empty sentinel dominates both explicit attributes and
    # legacy membership-union fallback, even for global org roles.
    workspace = getattr(request, "workspace", None)
    if workspace_scope_is_empty() or (
        workspace is None and getattr(request, "auth_scope", None) is not None
    ):
        return models.Q(pk__in=[])
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
                | _no_workspace_q(workspace_lookup, workspace, model)
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

        # Prebuilt querysets predate authentication; their managers' current
        # guard has not run. Include FK-owned data, not authorization metadata.
        if workspace_scope_is_empty() and model_has_workspace_data(model):
            return queryset.none()

        # A request with no organization (a user removed from every org still
        # authenticates) reaches nothing here, whatever the model is scoped
        # by: a skipped filter must not read as "every org's rows".
        organization = active_request_organization(request)
        if not organization:
            return queryset.none()

        # Every row is constrained to the request's organization, also when
        # the model is scoped by workspace or project only. The workspace
        # filter below does not do that on its own: in a default workspace it
        # admits rows whose workspace is NULL, which for a project-scoped
        # model means projects of any org.
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

        # Workspace scope: the request's workspace, or outside an authenticated
        # empty scope the membership-confined fallback. Skipping it when there
        # is no workspace gave a restricted
        # member every workspace of the org, each of them refused by name.
        workspace_lookup = workspace_lookup_for(model)
        if workspace_lookup:
            queryset = queryset.filter(
                request_workspace_q(request, workspace_lookup, model)
            )

        queryset = queryset.filter(**{lookup: organization})
        # Also through the relation every row has, when that is another path
        # (a model whose workspace may be empty but whose project may not).
        required = required_organization_lookup_for(model)
        if required is not None and required != lookup:
            queryset = queryset.filter(**{required: organization})

        # Apply soft delete filter
        return queryset.filter(deleted=False)


class BaseModelViewSetMixinWithUserOrg(BaseModelViewSetMixin):
    """
    The same scoping as ``BaseModelViewSetMixin``. It used to repeat that
    mixin's body, and the organization filter, applied by hand in both copies,
    could drift; it now inherits it.
    """
