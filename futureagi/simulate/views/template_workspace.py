from uuid import UUID

from simulate.services.harness_templates import system_templates


_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def shared_template(identifier):
    """The system template at this id, or None; ids that are not UUIDs name no template."""
    try:
        identifier = UUID(str(identifier))
    except (TypeError, ValueError):
        return None
    return system_templates().filter(id=identifier).first()


class TemplateWorkspaceMixin:
    """Let anyone read a shared template by id; writes still resolve only the caller's own.

    A template is changed by nobody: editing or running one starts with ``copy``, which gives
    the caller's organization its own environment to act on.
    """

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        identifier = kwargs.get("pk")
        if identifier and request.method in _SAFE_METHODS:
            request.template_workspace = shared_template(identifier)
