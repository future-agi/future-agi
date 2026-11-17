"""Dataset row reads and recorded row sources are on unless turned off.

DSX_DATASET_ROW_SOURCE_ENABLED is on when its environment variable is unset
or blank. An explicit value uses the parsed setting, and only True keeps it
on. Environment changes need a restart of the API and dataset import workers.

When explicitly off, the row route raises the resolver's own 404 before DRF
and is omitted from the generated schema. Its slashless path does not redirect.
Existing row and table reads attach no source and run no row-source query, and
span exports do not read trace_session_id.

The migration and import writer are independent of this switch. The writer
records nothing without a source context, and an import passes one only while
this switch is on.
"""

from django.urls import Resolver404

from tfc.utils.default_on import on_unless_turned_off

SETTING = "DSX_DATASET_ROW_SOURCE_ENABLED"


def row_source_enabled() -> bool:
    return on_unless_turned_off(SETTING)


def ensure_row_read_route_enabled(request):
    """OFF: the unknown-route 404, raised outside DRF's exception handling."""

    if not row_source_enabled():
        raise Resolver404({"path": request.path_info})


class _AppendSlashWhileEnabled:
    """The row read callback's ``should_append_slash``: truthy only while ON.

    With APPEND_SLASH, CommonMiddleware 301s a slashless request whose
    slash-terminated path resolves unless the callback's should_append_slash
    is falsy (as Django's no_append_slash sets it). It is evaluated on every
    request, so OFF the slashless path keeps the resolver's 404.
    """

    def __bool__(self):
        return row_source_enabled()


def gate_append_slash(view):
    """OFF: no APPEND_SLASH redirect to ``view``'s route."""

    view.should_append_slash = _AppendSlashWhileEnabled()
    return view
