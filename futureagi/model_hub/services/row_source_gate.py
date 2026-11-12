"""D8-PGRID server switch: F54 row sources and the F82 row read (default OFF).

DSX_DATASET_ROW_SOURCE_ENABLED comes from the env var of the same name and
defaults to False. It is read on every call, so turning it on is an env change
plus a restart of the API and the dataset import workers, never a rebuild.
Only an explicit True opens the unit's server paths; OFF, each is the base:

- GET /model-hub/datasets/{dataset_id}/rows/{row_id}/ raises the URL
  resolver's own 404 before DRF dispatch, so it answers exactly as an unknown
  route does (no authentication, lookup or query), and the generated schema
  omits the route. The slashless path gets no APPEND_SLASH redirect to it, so
  it answers as an unknown slashless path does.
- get-row-data and get-dataset-table attach no ``row_meta.source`` and run no
  row-source query.
- The span export behind an Observe import never reads trace_session_id, so
  it selects the columns it selected before the unit.

Not gated, by design: the 0126 migration (one reversible CreateModel of a new,
empty table) and the import worker's writer, which records nothing without a
source context; no caller passes one.
"""

from django.conf import settings
from django.urls import Resolver404

SETTING = "DSX_DATASET_ROW_SOURCE_ENABLED"


def row_source_enabled() -> bool:
    return getattr(settings, SETTING, False) is True


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
