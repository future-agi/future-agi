"""F32 (New dataset > Upload a file): which reader stores a local-file upload.

The reader is on unless turned off, read at call time. A deployment turns it
off with the env var DSX_PREVIEWED_JSON_UPLOAD_ENABLED (the Django setting of
the same name parses it): only an explicit true keeps it on once the env var
holds a value; a blank one counts as unset. A caller that overrides the setting (override_settings, as tests do)
decides with an explicit True. With neither, it is on.

OFF, nothing changes: create-from-local-file validates every file with
FileProcessor, and the file worker stores it with FileProcessor and column
types inferred from its frame, as before.

ON admits one new reader. A .json or .jsonl file sent to the HTTP route by a
signed-in user from the Upload dialog (source "build", no API key) is read
with services/file_import_json.py, so the dataset stores exactly the rows,
columns, types and values the dialog previewed. The route records that
admission in the new dataset's dataset_config (READER_KEY); only the server
writes it. The worker follows that record, never the file name alone:

- no record (SDK service uploads, API-key and SDK-source uploads, CSV and
  spreadsheets, anything queued while OFF or before F32): FileProcessor,
  even while ON;
- PREVIEWED_JSON while ON: the previewed reader;
- any record while OFF, or an unknown one: refused before the file is
  downloaded or parsed. The upload fails with HELD_MESSAGE, stores no table
  and is not retried.

Turn it on with the env var on the tasks_l workers first, then the API
(each with a restart); turn it off in the reverse order. No rebuild.
"""

import os

from django.conf import UserSettingsHolder, settings

from model_hub.models.choices import DatasetSourceChoices
from model_hub.services import file_import_json

SETTING = "DSX_PREVIEWED_JSON_UPLOAD_ENABLED"
READER_KEY = "file_import_reader"
PREVIEWED_JSON = "previewed_json_v1"
HELD_MESSAGE = "This upload can't be imported right now. Upload the file again."


def _overridden() -> bool:
    """Whether a caller set or removed the setting with override_settings; the
    settings module's own value is never an override."""
    holder = getattr(settings, "_wrapped", None)
    while isinstance(holder, UserSettingsHolder):
        if SETTING in holder.__dict__ or SETTING in holder.__dict__.get("_deleted", ()):
            return True
        holder = holder.default_settings
    return False


def previewed_json_enabled() -> bool:
    if os.environ.get(SETTING, "").strip() or _overridden():
        return getattr(settings, SETTING, False) is True
    return True


def _api_key_request(request) -> bool:
    # APIKeyAuthentication sets org_api_key; this view already reports any
    # X-Api-Key request as an SDK create.
    return (
        getattr(request, "org_api_key", None) is not None
        or request.headers.get("X-Api-Key") is not None
    )


def upload_reader(request, filename, source):
    """The reader admitted for a new HTTP upload: PREVIEWED_JSON, or None for
    FileProcessor."""
    if not previewed_json_enabled():
        return None
    if file_import_json.json_lines_mode(filename) is None:
        return None
    if source != DatasetSourceChoices.BUILD.value or _api_key_request(request):
        return None
    return PREVIEWED_JSON


def worker_reader(dataset_config):
    """(reader, refusal message) for a queued upload, from its admission record."""
    record = (dataset_config or {}).get(READER_KEY)
    if record is None:
        return None, None
    if record == PREVIEWED_JSON and previewed_json_enabled():
        return PREVIEWED_JSON, None
    return None, HELD_MESSAGE


def read_previewed_json(file_obj, filename):
    """(frame, column data types, error message) from the previewed reader.

    Refuses with HELD_MESSAGE, without reading, while OFF or for a name that is
    not .json/.jsonl, so no caller reaches the reader with the switch off.
    """
    lines = file_import_json.json_lines_mode(filename)
    if lines is None or not previewed_json_enabled():
        return None, None, HELD_MESSAGE
    try:
        imported = file_import_json.read_json_import(file_obj, lines)
    except file_import_json.JsonImportError as exc:
        return None, None, str(exc)
    return imported.frame, imported.data_types, None
