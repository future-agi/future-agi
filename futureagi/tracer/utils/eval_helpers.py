"""Lightweight eval utility functions — no heavy imports at module level."""


_EVAL_FIELD_DENY = frozenset({"deleted", "deleted_at"})


def evalable_field_names(model) -> frozenset:
    """Concrete DB columns on ``model`` usable as eval mapping targets.

    FKs expose the bare relation name (``session``, ``project``) — the
    resolver maps it to the underlying ``*_id`` UUID via
    ``evalable_fk_remap``. Reverse relations and ``_EVAL_FIELD_DENY`` names
    are excluded.
    """
    from django.db.models import ForeignKey, OneToOneField

    names = set()
    for f in model._meta.get_fields():
        if f.is_relation and not (f.many_to_one or f.one_to_one):
            continue
        if isinstance(f, (ForeignKey, OneToOneField)):
            names.add(f.name)
            continue
        if not getattr(f, "concrete", False):
            continue
        if f.name in _EVAL_FIELD_DENY:
            continue
        names.add(f.name)
    return frozenset(names)


def evalable_fk_remap(model) -> dict:
    """Picker-name → ``getattr`` target for FK columns on ``model``.

    ``getattr(trace, "session")`` would return a TraceSession instance —
    not JSON-serialisable. Resolvers translate FK picker names to their
    ``*_id`` attname so they read the UUID instead.
    """
    from django.db.models import ForeignKey, OneToOneField

    return {
        f.name: f.attname
        for f in model._meta.get_fields()
        if isinstance(f, (ForeignKey, OneToOneField))
    }


def resolve_eval_template_id(eval_id, organization_id=None):
    """Resolve eval template name to UUID, scoped by organization.

    If ``eval_id`` is already a valid UUID string it is returned as-is.
    Otherwise it is treated as a human-readable name and looked up in the
    ``EvalTemplate`` table.
    """
    import uuid as _uuid

    try:
        _uuid.UUID(eval_id)
        return eval_id  # already a UUID
    except (ValueError, AttributeError):
        from model_hub.models.evals_metric import EvalTemplate

        qs = EvalTemplate.objects.filter(name=eval_id, deleted=False)
        # Always scope by organization to prevent cross-tenant name resolution
        if organization_id:
            qs = qs.filter(organization_id=organization_id)
        else:
            # No org context — refuse to search across all orgs
            return eval_id
        tid = qs.values_list("id", flat=True).first()
        return str(tid) if tid else eval_id


def resolve_eval_config_id(eval_config_id, project_ids=None):
    """Resolve eval config name to UUID, scoped by project_ids if available.

    If ``eval_config_id`` is already a valid UUID string it is returned as-is.
    Otherwise it is treated as a human-readable name and looked up in the
    ``CustomEvalConfig`` table, optionally scoped by ``project_ids`` to
    prevent cross-tenant leakage.
    """
    import uuid as _uuid

    try:
        _uuid.UUID(eval_config_id)
        return eval_config_id  # already a UUID
    except (ValueError, AttributeError):
        from tracer.models.custom_eval_config import CustomEvalConfig

        qs = CustomEvalConfig.objects.filter(name=eval_config_id)
        # Always scope by project to prevent cross-tenant name resolution
        if project_ids:
            qs = qs.filter(project_id__in=project_ids)
        else:
            # No project context — refuse to search across all tenants
            return eval_config_id
        cfg = qs.values_list("id", flat=True).first()
        return str(cfg) if cfg else eval_config_id
