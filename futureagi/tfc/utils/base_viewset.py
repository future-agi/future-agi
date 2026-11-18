from django.db import models


def _default_workspace_q(workspace, workspace_lookup, model):
    """Default-workspace filter. Null-workspace rows are included only when the
    model can constrain them to this workspace's organization. A workspace-only
    model must not see every null-workspace row.
    """
    q = models.Q(**{workspace_lookup: workspace}) | models.Q(
        **{
            f"{workspace_lookup}__is_default": True,
            f"{workspace_lookup}__organization": workspace.organization,
        }
    )
    if workspace_lookup == "workspace":
        org_lookup = "organization_id"
        has_org = any(
            getattr(field, "attname", None) == "organization_id"
            for field in model._meta.get_fields()
        )
    else:
        org_lookup = f"{workspace_lookup.removesuffix('__workspace')}__organization_id"
        has_org = True
        probe = model
        for part in workspace_lookup.split("__")[:-1]:
            try:
                probe = probe._meta.get_field(part).related_model
            except Exception:
                has_org = False
                break
        else:
            has_org = any(
                getattr(field, "attname", None) == "organization_id"
                for field in probe._meta.get_fields()
            )
    if has_org:
        q |= models.Q(
            **{
                f"{workspace_lookup}__isnull": True,
                org_lookup: workspace.organization_id,
            }
        )
    return q


class BaseModelViewSetMixin:
    """
    Mixin for ViewSets that automatically applies workspace and organization
    filtering using the per-request ``request.workspace`` / ``request.organization``
    attributes set by authentication.

    Usage:
    class MyViewSet(BaseModelViewSetMixin, viewsets.ModelViewSet):
        def get_queryset(self):
            queryset = super().get_queryset()  # gets workspace + org filtering
            queryset = queryset.filter(some_field='value')
            return queryset
    """

    def get_queryset(self):
        """
        Get queryset with workspace and organization filtering applied.
        """
        # Get the model class
        if hasattr(self, "queryset") and self.queryset is not None:
            model = self.queryset.model
            queryset = self.queryset.all()
        elif (
            hasattr(self, "serializer_class")
            and hasattr(self.serializer_class, "Meta")
            and hasattr(self.serializer_class.Meta, "model")
        ):
            model = self.serializer_class.Meta.model
            queryset = model.objects.all()
        else:
            raise AttributeError(
                f"ViewSet {self.__class__.__name__} must have either a 'queryset' attribute "
                f"or a 'serializer_class' with a 'model' in Meta, or override 'get_queryset'"
            )

        # Apply workspace filtering from the per-request attribute
        workspace = getattr(getattr(self, "request", None), "workspace", None)
        if workspace:
            workspace_lookup = None
            if hasattr(model, "workspace"):
                workspace_lookup = "workspace"
            else:
                try:
                    related_field_names = [
                        f.name
                        for f in model._meta.get_fields()
                        if getattr(f, "is_relation", False)
                    ]
                    if "project" in related_field_names:
                        workspace_lookup = "project__workspace"
                except Exception:
                    pass

            if workspace_lookup:
                if getattr(workspace, "is_default", False):
                    queryset = queryset.filter(
                        _default_workspace_q(workspace, workspace_lookup, model)
                    )
                else:
                    queryset = queryset.filter(**{workspace_lookup: workspace})

        # Apply organization filtering (request.organization first, membership fallback)
        if hasattr(model, "organization"):
            organization = getattr(getattr(self, "request", None), "organization", None)
            if (
                not organization
                and hasattr(self, "request")
                and hasattr(self.request, "user")
            ):
                from accounts.utils import get_user_organization

                organization = get_user_organization(self.request.user)
            if organization:
                queryset = queryset.filter(organization=organization)

        # Apply soft delete filter
        return queryset.filter(deleted=False)


class BaseModelViewSetMixinWithUserOrg(BaseModelViewSetMixin):
    """
    Enhanced mixin that also automatically filters by the current user's organization
    when the model has an organization field.
    """

    def get_queryset(self):
        """
        Get queryset with workspace filtering and user organization filtering applied.
        """
        if hasattr(self, "queryset") and self.queryset is not None:
            queryset = self.queryset.all()
        elif (
            hasattr(self, "serializer_class")
            and hasattr(self.serializer_class, "Meta")
            and hasattr(self.serializer_class.Meta, "model")
        ):
            model = self.serializer_class.Meta.model
            queryset = model.objects.all()
        else:
            raise AttributeError(
                f"ViewSet {self.__class__.__name__} must have either a 'queryset' attribute "
                f"or a 'serializer_class' with a 'model' in Meta, or override 'get_queryset'"
            )

        # Get the model class from the queryset
        if hasattr(self, "queryset") and self.queryset is not None:
            model = self.queryset.model
        else:
            model = self.serializer_class.Meta.model

        # Apply workspace filtering from the per-request attribute
        workspace = getattr(getattr(self, "request", None), "workspace", None)
        if workspace:
            workspace_lookup = None
            if hasattr(model, "workspace"):
                workspace_lookup = "workspace"
            else:
                try:
                    related_field_names = [
                        f.name
                        for f in model._meta.get_fields()
                        if getattr(f, "is_relation", False)
                    ]
                    if "project" in related_field_names:
                        workspace_lookup = "project__workspace"
                except Exception:
                    pass

            if workspace_lookup:
                if getattr(workspace, "is_default", False):
                    queryset = queryset.filter(
                        _default_workspace_q(workspace, workspace_lookup, model)
                    )
                else:
                    queryset = queryset.filter(**{workspace_lookup: workspace})

        # Apply soft delete filter
        queryset = queryset.filter(deleted=False)

        # Apply organization filtering (request.organization first, membership fallback)
        if hasattr(model, "organization"):
            organization = getattr(getattr(self, "request", None), "organization", None)
            if (
                not organization
                and hasattr(self, "request")
                and hasattr(self.request, "user")
            ):
                from accounts.utils import get_user_organization

                organization = get_user_organization(self.request.user)
            if organization:
                queryset = queryset.filter(organization=organization)

        return queryset
