"""HTTP experiment primitives; no prompt/model fallback and no simulated replies.

The create view must call persist_http_source inside its submit transaction.
The runner supplies selected snapshot-row values keyed by ORIGINAL column IDs
(and original row_id), then calls execute_and_persist_http_row with a stable
attempt ID. The experiment-row lock fences claims/commits against Stop; retries
need a new ID and explicit retry=True. No automatic POST replay is safe.
Output-column setup, snapshot identity binding, evaluation and aggregate status
settlement remain the workflow's responsibility. Header resolution must be
bound to that execution's organization/workspace, never an ambient lookup.
"""

import json
import re
from copy import deepcopy
from time import monotonic
from urllib.parse import quote

from tfc.utils import outbound_destination

VARIABLE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
PATH_PART = re.compile(
    r'''(?:\.([A-Za-z0-9_][A-Za-z0-9_-]*)|\[\s*(-?\d+)\s*\]|\[\s*("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')\s*\])'''
)
MISSING = object()
MAX_RENDERED_BODY_BYTES = 2 * 1024 * 1024
MAX_RENDERED_URL_BYTES = 8192

# An A2A agent is saved as an HTTP source with http.protocol = "a2a". Each row
# sends one JSON-RPC message/send (A2A spec 0.3: Message with kind "message",
# a messageId and text parts with kind "text") and the reply's text is the
# output. The only A2A schema in this repo is the gateway's
# agentcc-gateway/internal/a2a/types.go, which still names parts by "type" and
# writes "input_required"; replies are read in both spellings.
A2A_TEXT_VARIABLE = "text"
A2A_BODY_TEMPLATE = (
    '{"jsonrpc":"2.0","id":"{{row_id}}","method":"message/send","params":{"message":'
    '{"kind":"message","role":"user","messageId":"{{row_id}}",'
    '"parts":[{"kind":"text","text":"{{' + A2A_TEXT_VARIABLE + '}}"}]},'
    '"configuration":{"blocking":true}}}'
)
A2A_WAITING_STATES = {"submitted", "working", "input-required", "input_required",
                      "auth-required", "auth_required"}
A2A_FAILED_STATES = {"failed", "canceled", "cancelled", "rejected"}


def path_tokens(path):
    """The non-evaluating key/index subset shared with the HTTP preview.

    Refuse unsupported filters/wildcards at admission, rather than accepting a
    path whose meaning would change during execution.
    """
    text = path.strip()
    if text.startswith("$"):
        text = text[1:]
    if text and text[0] not in ".[":
        text = "." + text
    tokens = []
    while text:
        match = PATH_PART.match(text)
        if not match:
            raise ValueError("Use a key/index path such as $.reply or $.items[0].text.")
        key, index, quoted = match.groups()
        # faPath.readQuoted removes the escape slash; it does not evaluate
        # Python literals or JSON escape sequences in object-key paths.
        token = key if key is not None else int(index) if index is not None else re.sub(r"\\(.)", r"\1", quoted[1:-1])
        tokens.append(token)
        text = text[match.end():]
    return tokens


def _parsed_json(value):
    if isinstance(value, str) and value.lstrip().startswith(("{", "[")):
        try:
            return json.loads(value)
        except ValueError:
            pass
    return value


def _read_path(value, path):
    tokens = path_tokens(path)
    if not tokens:
        return value
    for token in tokens:
        value = _parsed_json(value)
        if isinstance(value, list) and isinstance(token, str) and re.fullmatch(r"-?\d+", token):
            token = int(token)
        if isinstance(value, dict) and isinstance(token, str):
            value = value.get(token, MISSING)
        elif isinstance(value, list) and isinstance(token, int) and -len(value) <= token < len(value):
            value = value[token]
        elif isinstance(value, list) and token == "length":
            value = len(value)
        else:
            return MISSING
    return _parsed_json(value)


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _text(value):
    return "" if value is None else value if isinstance(value, str) else _json(value)


def _cell(value):
    if isinstance(value, dict) and ("cell_value" in value or "cellValue" in value):
        return value.get("cell_value") if value.get("cell_value") is not None else value.get("cellValue")
    return value


def _variable(name, mapping, values, columns, row_id):
    mapped = mapping.get(name)
    if isinstance(mapped, dict):
        if mapped.get("kind") == "empty":
            return ""
        if mapped.get("kind") == "constant":
            return mapped.get("value", "")
    if isinstance(mapped, str) and mapped:
        column_id, _, path = mapped.partition("::")
        value = _cell(values.get(column_id))
        if path:
            value = _read_path(value, path)
        return "" if value is MISSING or value is None else value
    for column in columns:
        if column["name"] == name:
            value = _cell(values.get(str(column["id"])))
            return "" if value is None else value
    if name == "row_id" and row_id is not None:
        return str(row_id)
    return MISSING


class _RenderBuffer:
    """Account UTF-8 bytes before retaining any rendered fragment."""
    def __init__(self, limit):
        self.remaining = limit
        self.parts = []

    def append(self, fragment):
        # UTF-8 is at least one byte per codepoint. Reject huge inputs before
        # encoding them; encoding a permitted fragment is itself bounded.
        if len(fragment) > self.remaining:
            raise ValueError("Rendered HTTP request exceeds its byte budget.")
        size = len(fragment.encode("utf-8"))
        if size > self.remaining:
            raise ValueError("Rendered HTTP request exceeds its byte budget.")
        self.remaining -= size
        self.parts.append(fragment)

    def finish(self):
        return "".join(self.parts)


def _string_fragments(value):
    # JSONEncoder.iterencode can allocate an entire escaped string at once.
    # Escape small chunks instead, including strings nested in mapped JSON.
    for offset in range(0, len(value), 1024):
        yield json.dumps(value[offset:offset + 1024], ensure_ascii=False)[1:-1]


def _json_fragments(value, depth=0):
    if depth > 64:
        raise ValueError("HTTP mapped JSON is too deeply nested.")
    if isinstance(value, str):
        yield '"'
        yield from _string_fragments(value)
        yield '"'
    elif isinstance(value, (list, tuple)):
        yield "["
        for index, item in enumerate(value):
            if index:
                yield ","
            yield from _json_fragments(item, depth + 1)
        yield "]"
    elif isinstance(value, dict):
        yield "{"
        for index, (key, item) in enumerate(value.items()):
            if index:
                yield ","
            if not isinstance(key, str):
                if key is None or isinstance(key, (bool, int, float)):
                    key = _json(key)
                else:
                    raise TypeError("HTTP JSON keys must be strings or scalars.")
            yield from _json_fragments(key, depth + 1)
            yield ":"
            yield from _json_fragments(item, depth + 1)
        yield "}"
    else:
        yield _json(value)


def _text_fragments(value):
    if value is None:
        yield ""
    elif isinstance(value, str):
        # Do not copy a large mapped string before checking the budget.
        yield value
    else:
        yield from _json_fragments(value)


def _append_value(output, value, *, url=False, json_string=False, json_value=False):
    fragments = _json_fragments(value) if json_value else _text_fragments(value)
    for fragment in fragments:
        if url or json_string:
            # Encoding can expand a value. Bound each encoded chunk before it
            # is appended; never assemble the full expanded value first.
            for offset in range(0, len(fragment), 1024):
                chunk = fragment[offset:offset + 1024]
                output.append(quote(chunk, safe="~()*!.'-") if url else
                              json.dumps(chunk, ensure_ascii=False)[1:-1])
        else:
            output.append(fragment)


def _fill_json(template, resolve, limit):
    output, in_string, index = _RenderBuffer(limit), False, 0
    while index < len(template):
        if template.startswith("{{", index):
            end = template.find("}}", index + 2)
            if end >= 0:
                value = resolve(template[index + 2:end].strip())
                if value is MISSING:
                    output.append(template[index:end + 2])
                else:
                    _append_value(output, value, json_string=in_string, json_value=not in_string)
                index = end + 2
                continue
        char = template[index]
        if char == '"':
            back = index - 1
            while back >= 0 and template[back] == "\\":
                back -= 1
            if (index - back - 1) % 2 == 0:
                in_string = not in_string
        output.append(char)
        index += 1
    return output.finish()


def _fill_text(template, resolve, limit, *, url=False):
    output, index = _RenderBuffer(limit), 0
    for match in VARIABLE.finditer(template):
        # Literal template slices cannot exceed the budget either.
        if match.start() - index > output.remaining:
            raise ValueError("Rendered HTTP request exceeds its byte budget.")
        output.append(template[index:match.start()])
        value = resolve(match.group(1))
        if value is MISSING:
            output.append(match.group(0))
        else:
            _append_value(output, value, url=url)
        index = match.end()
    if len(template) - index > output.remaining:
        raise ValueError("Rendered HTTP request exceeds its byte budget.")
    output.append(template[index:])
    return output.finish()


def build_request(source, *, values, columns=(), row_id=None):
    """Mirror preview semantics with bounded UTF-8 rendering before transport."""
    endpoint = source["http"]
    missing, empty = [], []

    def resolve(name):
        value = _variable(name, source.get("variable_mapping", {}), values, columns, row_id)
        if value is MISSING:
            if name not in missing:
                missing.append(name)
        elif (value is None or isinstance(value, str) and (not value or value.isspace())) and name not in empty:
            empty.append(name)
        return value

    method = endpoint.get("method", "POST").upper()
    url = _fill_text(endpoint["url"].strip(), resolve, MAX_RENDERED_URL_BYTES, url=True)
    template = endpoint.get("body_template", "")
    body = "" if method in ("GET", "HEAD") else (
        _fill_json(template, resolve, MAX_RENDERED_BODY_BYTES) if template.lstrip().startswith(("{", "["))
        else _fill_text(template, resolve, MAX_RENDERED_BODY_BYTES)
    )
    headers = []
    for header in endpoint.get("headers", []):
        ref = header.get("secret") or header.get("secret_ref")
        headers.append({"name": header["name"], **({"secret_ref": ref} if ref else {"value": header.get("value", "")}),
                        **({"scheme": header["scheme"]} if ref and header.get("scheme") else {})})
    return {"method": method, "url": url, "headers": headers, "body": body,
            "missing": missing, "empty": empty}


def _error(code, message, retryable=False):
    return {"code": code, "message": message, "retryable": retryable}


def _a2a_texts(parts):
    return [part["text"] for part in parts if isinstance(part, dict)
            and (part.get("kind") or part.get("type")) == "text" and isinstance(part.get("text"), str)] \
        if isinstance(parts, list) else []


def a2a_reply_text(payload):
    """The text of a message/send reply as (text, None), or (None, row error).

    The result is a Message (its text parts) or a Task. A Task gives the text of
    its artifacts, else of its status message. A Task that is not finished is a
    row error: one message/send per row, no polling and no second send.
    """
    reply = _error("a2a_reply", "The agent's reply is not an A2A message/send result. Check the agent URL, then retry.")
    if not isinstance(payload, dict):
        return None, reply
    if payload.get("error") is not None:
        code = payload["error"].get("code") if isinstance(payload["error"], dict) else None
        code = f" {code}" if isinstance(code, int) and not isinstance(code, bool) else ""
        return None, _error("a2a_error", f"The agent answered with A2A error{code}. Check the agent and its settings, then Retry this row.")
    result = payload.get("result")
    if not isinstance(result, dict):
        return None, reply
    if result.get("kind") == "task" or "status" in result:
        status = result.get("status") if isinstance(result.get("status"), dict) else {}
        state = status.get("state") if isinstance(status.get("state"), str) else None
        if state in A2A_WAITING_STATES:
            return None, _error("a2a_unfinished", f"The agent's task is still {state}. Future AGI sends one message per row and does not wait. Make the agent finish before it answers, then Retry this row.")
        if state in A2A_FAILED_STATES:
            return None, _error("a2a_task_failed", f"The agent's task ended as {state}. Check the agent, then Retry this row.")
        if state != "completed":
            return None, _error("a2a_unfinished", "The agent's task has no finished state. Check the agent, then Retry this row.")
        artifacts = result.get("artifacts")
        if artifacts is not None and not isinstance(artifacts, list):
            # A2A artifacts are a list. Anything else is a malformed reply, not
            # a problem with the row or its settings.
            return None, reply
        texts = [text for artifact in artifacts or [] if isinstance(artifact, dict)
                 for text in _a2a_texts(artifact.get("parts"))]
        message = status.get("message")
        # A2A 0.3 sends a Message here; the gateway's types.go sends the parts list.
        texts = texts or _a2a_texts(message.get("parts") if isinstance(message, dict) else message)
    else:
        texts = _a2a_texts(result.get("parts"))
    if not texts:
        return None, _error("a2a_no_text", "The agent answered without a text part. Make it reply with text, then Retry this row.")
    return "\n".join(texts), None


def execute_http_row(source, *, values, columns=(), row_id=None, resolve_secret=None):
    """One bounded real request, with terminal per-row output/error and latency.

    No automatic retries of POSTs: a timeout may have reached the endpoint.
    Recovery is explicit. Never include transport exception text or resolved
    headers in the persisted result. Production always uses the existing public
    destination transport (deadline, response bound, no redirects).
    """
    started = monotonic()
    output, error, status_code = None, None, None
    secrets = []
    # Sources saved before A2A have no protocol and keep the HTTP wording and path.
    a2a = (source.get("http") or {}).get("protocol") == "a2a"
    who = "The A2A agent" if a2a else "Endpoint"
    try:
        request = build_request(source, values=values, columns=columns, row_id=row_id)
        if request["missing"]:
            raise ValueError("unmapped variables")
        headers = {"Content-Type": "application/json"}
        for header in request["headers"]:
            if "secret_ref" in header:
                try:
                    value = resolve_secret(header["secret_ref"]) if resolve_secret else None
                    if not isinstance(value, str) or not value:
                        raise ValueError("unavailable reference")
                    secrets.append(value)
                except Exception:
                    error = _error("header_reference", "A header reference is unavailable. Check workspace secrets and retry.")
                    break
                if header.get("scheme") == "bearer":
                    value = "Bearer " + value
            else:
                value = header["value"]
            headers[header["name"]] = value
        if error is None:
            response = outbound_destination.request(
                method=request["method"], url=request["url"], headers=headers,
                # The transport's private JSON pipe accepts text and encodes UTF-8.
                data=request["body"] if request["method"] not in ("GET", "HEAD") else None,
                timeout=source["http"].get("timeout_seconds", 30),
            )
            status_code = response.status_code
            if not 200 <= status_code < 300:
                error = _error("http_status", f"{who} returned HTTP {status_code}. Check the {'agent URL and auth' if a2a else 'endpoint'}, then Retry this row.",
                               status_code in (408, 429) or status_code >= 500)
            else:
                try:
                    payload = response.json()
                except ValueError:
                    error = _error("response_json", f"{who} response is not JSON. Check the {'agent URL' if a2a else 'endpoint'} and retry.")
                else:
                    if a2a:
                        value, error = a2a_reply_text(payload)
                    else:
                        value = _read_path(payload, source["http"].get("output_path") or "$.reply")
                        if value is MISSING:
                            error = _error("output_path", "Output path was not found. Check the response mapping and retry.")
                    if error is None:
                        output = _text(redact_secrets(value, secrets))
    except outbound_destination.DestinationError as exc:
        timed_out = str(exc) in {"deadline", "TimeoutError", "ReadTimeout"}
        error = _error("timeout" if timed_out else "destination",
                       f"{who} timed out. Check whether it processed the {'message' if a2a else 'request'} before you Retry this row."
                       if timed_out else f"{who} is unavailable or not a public destination. Check the {'agent URL' if a2a else 'URL'} and retry.", timed_out)
    except (TypeError, ValueError, KeyError):
        error = _error("request_config", "A2A agent settings or row values are invalid. Check the text column, then retry."
                       if a2a else "HTTP request settings or row values are invalid. Correct them and retry.")
    return redact_secrets(
        {"output": output, "error": error, "status_code": status_code,
         "latency_ms": (monotonic() - started) * 1000}, secrets,
    )


def redact_secrets(value, secrets, *, redact_json_scalars=False):
    """Conceal resolved headers; scalar matching is for upstream payloads only.

    Keep the default for envelopes so status, latency and other transport
    metadata retain their numeric/boolean types even if a credential matches.
    """
    if isinstance(value, str):
        for secret in sorted(set(secrets), key=len, reverse=True):
            if secret:
                value = value.replace(secret, "[redacted]")
        return value
    if isinstance(value, dict):
        return {redact_secrets(key, secrets): redact_secrets(
                    item, secrets, redact_json_scalars=redact_json_scalars)
                for key, item in value.items()}
    if isinstance(value, list):
        return [redact_secrets(item, secrets, redact_json_scalars=redact_json_scalars)
                for item in value]
    if redact_json_scalars and (value is None or isinstance(value, (bool, int, float))):
        spelling = json.dumps(value)
        if any(secret and secret in spelling for secret in secrets):
            return "[redacted]"
    return value


def persist_http_source(experiment, entry, *, order=0):
    """Persist an HTTP variant in the existing EDT JSON, never an EPC/EAC.

    Caller must already authorize the experiment and be in the create/update
    submission transaction. The explicit mapping stays in original dataset IDs;
    the runner maps its immutable snapshot values back to those IDs.
    """
    from model_hub.models.develop_dataset import Column
    from model_hub.serializers.experiments import PromptConfigEntrySerializer

    serializer = PromptConfigEntrySerializer(data=entry)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    if data.get("target_type") != "http":
        raise ValueError("An HTTP source is required.")
    wanted = {value.partition("::")[0] for value in data.get("variable_mapping", {}).values()
              if isinstance(value, str) and value}
    available = {str(pk) for pk in Column.objects.filter(
        dataset=experiment.dataset, deleted=False, pk__in=wanted,
    ).values_list("pk", flat=True)}
    if wanted != available:
        raise ValueError("An HTTP mapping column is unavailable in this dataset.")
    return _persist_validated_http_source(experiment, data, order=order)


def _persist_validated_http_source(experiment, data, *, order=0):
    """Materialize a source after serializer and dataset-specific mapping checks.

    Creation validates live columns above; append validates captured columns in
    _validated_http_config. Do not re-resolve snapshot mappings against live data.
    """
    from django.db import transaction
    from model_hub.models.experiments import ExperimentDatasetTable

    stored = {key: deepcopy(data[key]) for key in ("target_type", "http", "variable_mapping") if key in data}
    stored["order"] = order
    with transaction.atomic():
        source = ExperimentDatasetTable.objects.create(
            name=data.get("name") or ("A2A agent" if stored["http"].get("protocol") == "a2a" else "HTTP endpoint"),
            experiment=experiment,
            legacy_prompt_config=stored,
        )
        experiment.experiments_datasets.add(source)
    return source


def public_http_source(source):
    config = source.legacy_prompt_config or {}
    return {"id": str(source.pk), "name": source.name, "target_type": "http",
            "http": deepcopy(config["http"]),
            "variable_mapping": deepcopy(config.get("variable_mapping", {})),
            "order": config.get("order", 0)}


def _active_http_target(source, row, column, attempt_id):
    """Called inside atomic: Stop and output writes lock the same experiment."""
    from model_hub.models.choices import StatusType
    from model_hub.models.develop_dataset import Column, Row
    from model_hub.models.experiments import ExperimentDatasetTable, ExperimentsTable

    experiment = ExperimentsTable.no_workspace_objects.select_for_update().filter(
        pk=source.experiment_id, deleted=False,
    ).first()
    if experiment is None or experiment.status != StatusType.RUNNING.value:
        return None
    live_source = ExperimentDatasetTable.no_workspace_objects.filter(
        pk=source.pk, experiment=experiment, deleted=False,
        legacy_prompt_config__target_type="http",
    ).first()
    execution = ((live_source.legacy_prompt_config or {}).get("_http_execution") or {}) if live_source else {}
    if execution:
        from model_hub.services.experiment_submit import state_of
        state = state_of(experiment)
        if (state.get("state") != "accepted"
                or state.get("admission_id") != execution.get("admission_id")
                or not attempt_id.startswith(execution["admission_id"] + ":")
                or execution.get("phase") in {"finished", "cancelled"}):
            return None
        if "row_ids" in execution and str(row.pk) not in execution["row_ids"]:
            raise ValueError("HTTP row is outside the admitted subset.")
    original, snapshot = experiment.dataset, experiment.snapshot_dataset
    if (original is None or snapshot is None or original.deleted or snapshot.deleted
            or original.organization_id != snapshot.organization_id
            or original.workspace_id != snapshot.workspace_id
            or not ExperimentDatasetTable.no_workspace_objects.filter(
                pk=source.pk, experiment=experiment, deleted=False,
                legacy_prompt_config__target_type="http",
            ).exists()
            or not Row.no_workspace_objects.filter(
                pk=row.pk, dataset_id=snapshot.pk, deleted=False,
            ).exists()
            or not Column.no_workspace_objects.filter(
                pk=column.pk, dataset_id=snapshot.pk, deleted=False,
                experiments_dataset_column__pk=source.pk,
                experiments_dataset_column__deleted=False,
            ).exists()):
        raise ValueError("HTTP result does not belong to this snapshot source.")
    return experiment


def _cell_info(cell):
    value = cell.value_infos
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def claim_http_attempt(*, source, row, column, attempt_id, retry=False):
    """Claim once before sending; never replay a claimed POST on delivery retry.

    IDs must be stable across activity retries. An explicit user retry supplies
    a NEW ID after the prior attempt has settled. A crashed/unknown-outcome
    attempt must be settled by workflow cleanup before an explicit retry.
    """
    from django.db import transaction
    from model_hub.models.choices import CellStatus
    from model_hub.models.develop_dataset import Cell

    if not isinstance(attempt_id, str) or not attempt_id:
        raise ValueError("An HTTP row attempt ID is required.")
    with transaction.atomic():
        experiment = _active_http_target(source, row, column, attempt_id)
        if experiment is None:
            return False
        cells = Cell.no_workspace_objects.filter(
            dataset_id=experiment.snapshot_dataset_id, row_id=row.pk, column_id=column.pk, deleted=False,
        )
        cell = cells.first()
        if cell is not None:
            prior = _cell_info(cell).get("http_attempt_id")
            if prior == attempt_id:
                return False
            if prior and (not retry or cell.status == CellStatus.RUNNING.value):
                return False
            if cell.status != CellStatus.RUNNING.value and not retry:
                return False
        history = deepcopy(_cell_info(cell).get("http_attempt_history", [])) if cell else []
        if cell is not None and _cell_info(cell).get("http_attempt_id"):
            previous_info = deepcopy(_cell_info(cell))
            previous_info.pop("http_attempt_history", None)
            history.append({"value": cell.value, "status": cell.status,
                            "response_time": cell.response_time, "value_infos": previous_info})
        defaults = {
            "value": "", "status": CellStatus.RUNNING.value,
            "value_infos": json.dumps({"http_attempt_id": attempt_id, "http_attempt_history": history}),
            "response_time": None,
        }
        if cell is None:
            Cell.no_workspace_objects.create(
                dataset_id=experiment.snapshot_dataset_id, row_id=row.pk, column_id=column.pk, **defaults,
            )
        else:
            cells.filter(pk=cell.pk).update(**defaults)
        return True


def persist_http_result(*, source, row, column, result, attempt_id):
    """Commit only the currently running attempt; late responses cannot undo Stop."""
    from django.db import transaction
    from model_hub.models.choices import CellStatus
    from model_hub.models.develop_dataset import Cell

    with transaction.atomic():
        experiment = _active_http_target(source, row, column, attempt_id)
        if experiment is None:
            return None
        cell = Cell.no_workspace_objects.filter(
            dataset_id=experiment.snapshot_dataset_id, row_id=row.pk, column_id=column.pk,
            status=CellStatus.RUNNING.value, deleted=False,
        ).first()
        if cell is None or not attempt_id or _cell_info(cell).get("http_attempt_id") != attempt_id:
            return None
        error = result["error"]
        info = {"metadata": {"response_time": result["latency_ms"]},
                "http_attempt_history": _cell_info(cell).get("http_attempt_history", []),
                "http_attempt_id": attempt_id,
                "http_status": result["status_code"], "error": error,
                "reason": error["message"] if error else None}
        cell.value = error["message"] if error else result["output"]
        cell.status = CellStatus.ERROR.value if error else CellStatus.PASS.value
        cell.value_infos = json.dumps(info)
        cell.response_time = result["latency_ms"]
        cell.save(update_fields=["value", "status", "value_infos", "response_time", "updated_at"])
        return cell


def execute_and_persist_http_row(*, source, row, column, attempt_id, values,
                                columns=(), original_row_id=None, resolve_secret=None,
                                retry=False):
    """Runner entry point. Snapshot-to-original value binding belongs to setup.

    A skipped claim is NOT a successful row; the caller must inspect the saved
    cell or settle an abandoned attempt. No output is fabricated on redelivery.
    """
    if not claim_http_attempt(source=source, row=row, column=column,
                              attempt_id=attempt_id, retry=retry):
        return None
    result = execute_http_row(
        (source.legacy_prompt_config.get("_http_execution") or {}).get("source", source.legacy_prompt_config),
        values=values, columns=columns, row_id=original_row_id, resolve_secret=resolve_secret,
    )
    return persist_http_result(source=source, row=row, column=column,
                               result=result, attempt_id=attempt_id)


# HTTP sources retain classic result columns; immutable identity is shared with
# the snapshot machinery, but no prompt/native provider fallback is involved.
def http_source_ids(experiment):
    from model_hub.models.experiments import ExperimentDatasetTable

    return [str(pk) for pk in ExperimentDatasetTable.no_workspace_objects.filter(
        experiment=experiment, deleted=False, legacy_prompt_config__target_type="http",
    ).order_by("legacy_prompt_config__order", "id").values_list("id", flat=True)]


def create_http_output_column(experiment, source):
    from model_hub.models.choices import StatusType
    from model_hub.services.column_service import create_experiment_column

    if source.experiment_id != experiment.pk or experiment.snapshot_dataset_id is None:
        raise ValueError("HTTP source requires its experiment snapshot.")
    column, _ = create_experiment_column(
        dataset=experiment.snapshot_dataset, source_id=source.pk, name=source.name,
        output_format="string", status=StatusType.NOT_STARTED.value,
    )
    source.columns.add(column)
    return column


def bind_http_submission(experiment, admission_id, *, retry=False):
    """Called under the submit transaction, including explicit whole-run retries."""
    from model_hub.models.experiments import ExperimentDatasetTable
    from model_hub.services.experiment_submit import state_of

    if state_of(experiment).get("admission_id") != admission_id:
        raise ValueError("HTTP submission identity does not match the experiment.")
    plan = state_of(experiment).get("http_plan")
    for source in ExperimentDatasetTable.no_workspace_objects.filter(
        experiment=experiment, deleted=False, legacy_prompt_config__target_type="http",
    ):
        target = plan["targets"].get(str(source.pk)) if plan else None
        if plan and target is None:
            continue  # A subset does not rebind or reset any other source.
        config = deepcopy(source.legacy_prompt_config)
        prior = config.get("_http_execution") or {}
        if prior.get("admission_id") == admission_id:
            continue  # Lost admission acknowledgements never reset completed work.
        if prior:
            config.setdefault("_http_execution_history", {})[prior["admission_id"]] = deepcopy(prior)
        config["_http_execution"] = {
            "admission_id": admission_id, "retry": retry, "phase": "queued",
            **deepcopy(target or {}),
        }
        source.legacy_prompt_config = config
        source.save(update_fields=["legacy_prompt_config"])


def http_sources_finished(experiment):
    from model_hub.models.experiments import ExperimentDatasetTable

    from model_hub.services.experiment_submit import state_of
    plan = state_of(experiment).get("http_plan")
    sources = ExperimentDatasetTable.no_workspace_objects.filter(
        experiment=experiment, deleted=False, legacy_prompt_config__target_type="http",
    )
    if plan:
        sources = sources.filter(pk__in=plan["targets"])
        if sources.count() != len(plan["targets"]):
            return False
    return all((config.get("_http_execution") or {}).get("phase") == "finished"
               for config in sources.values_list("legacy_prompt_config", flat=True))


def http_attempt_id(admission_id, source_id, row_id):
    return f"{admission_id}:{source_id}:{row_id}"


def _http_execution(experiment_id, source_id, admission_id):
    """Read authority from persisted tenant/owner and exact admission, not args."""
    from model_hub.models.choices import SourceChoices, StatusType
    from model_hub.models.experiments import ExperimentDatasetTable
    from model_hub.models.native_experiment_runs import NativeExperimentSnapshot
    from model_hub.services.experiment_submit import state_of
    from model_hub.services.experiment_target_admission import _experiment_context
    from model_hub.services.native_run_binding import validate_snapshot

    experiment, context = _experiment_context(experiment_id)
    state = state_of(experiment)
    if (experiment.status not in {StatusType.RUNNING.value, StatusType.COMPLETED.value} or not admission_id
            or state.get("admission_id") != admission_id
            or state.get("state") != "accepted"):
        raise ValueError("HTTP execution is stopped or no longer current.")
    source = ExperimentDatasetTable.no_workspace_objects.filter(
        pk=source_id, experiment=experiment, deleted=False,
        legacy_prompt_config__target_type="http",
    ).first()
    execution = (source.legacy_prompt_config.get("_http_execution") or {}) if source else {}
    if (execution.get("admission_id") != admission_id
            or (experiment.status == StatusType.COMPLETED.value and execution.get("phase") != "finished")):
        raise ValueError("HTTP source is not part of this submission.")
    snapshot = NativeExperimentSnapshot.objects.select_related(
        "snapshot_dataset", "dataset_version",
    ).filter(experiment=experiment, snapshot_dataset_id=experiment.snapshot_dataset_id).first()
    if snapshot is None:
        raise ValueError("HTTP source has no immutable snapshot identity.")
    validate_snapshot(snapshot, experiment.dataset, experiment)
    from model_hub.models.develop_dataset import Column
    columns = list(Column.no_workspace_objects.filter(
        experiments_dataset_column__id=source.pk,
        dataset_id=experiment.snapshot_dataset_id, deleted=False,
        source=SourceChoices.EXPERIMENT.value, source_id=str(source.pk),
    ))
    if len(columns) != 1:
        raise ValueError("HTTP source output column is unavailable.")
    plan = state.get("http_plan")
    if plan:
        target = plan["targets"].get(str(source.pk))
        if (not target or any(execution.get(key) != target[key] for key in ("row_ids", "source", "column_id"))
                or target["column_id"] != str(columns[0].pk)):
            raise ValueError("HTTP source no longer matches its immutable admission plan.")
    return experiment, context, source, snapshot, columns[0]


def _http_rows(snapshot, source=None):
    from model_hub.services.native_run_binding import snapshot_examples

    from types import SimpleNamespace
    from model_hub.models.develop_dataset import Dataset, Row

    # The original native identity never changes. HTTP-only additions carry
    # separate append-only captures, not fabricated DatasetVersion membership.
    links = list(snapshot_examples(snapshot))
    config = Dataset.no_workspace_objects.get(pk=snapshot.snapshot_dataset_id, deleted=False).dataset_config or {}
    captures = config.get(HTTP_APPEND_ROWS_KEY, {})
    copied = {str(row.pk): row for row in Row.no_workspace_objects.filter(
        dataset_id=snapshot.snapshot_dataset_id, pk__in=captures, deleted=False,
    )}
    if set(copied) != set(captures):
        raise ValueError("Appended HTTP snapshot rows are unavailable.")
    for row_id, capture in captures.items():
        if capture["column_map"] != snapshot.column_map or set(capture["values"]) - set(snapshot.column_map):
            raise ValueError("Appended HTTP columns do not match their captured identity.")
        links.append(SimpleNamespace(
            snapshot_row_id=copied[row_id].pk, snapshot_row=copied[row_id],
            source_row_id=capture["source_row_id"],
            version_item=SimpleNamespace(input={"values": deepcopy(capture["values"])}),
        ))
    if len(links) > 10000 or len({str(link.source_row_id) for link in links}) != len(links):
        raise ValueError("HTTP snapshot has invalid or duplicate row identities.")
    if source is not None:
        selected = (source.legacy_prompt_config.get("_http_execution") or {}).get("row_ids")
        if selected is not None:
            if len(selected) != len(set(selected)) or not set(selected) <= {str(link.snapshot_row_id) for link in links}:
                raise ValueError("HTTP execution rows are outside this immutable snapshot.")
            links = [link for link in links if str(link.snapshot_row_id) in set(selected)]
    return links


def setup_http_source(experiment_id, source_id, admission_id):
    from django.db import transaction
    from model_hub.models.choices import StatusType
    from model_hub.models.experiments import ExperimentsTable

    with transaction.atomic():
        ExperimentsTable.no_workspace_objects.select_for_update().get(pk=experiment_id)
        experiment, context, source, snapshot, column = _http_execution(experiment_id, source_id, admission_id)
        links = _http_rows(snapshot, source)
        if not links:
            raise ValueError("HTTP execution has no selected rows.")
        execution = source.legacy_prompt_config["_http_execution"]
        if execution["phase"] != "finished":
            execution["phase"] = "running"
            source.status = StatusType.RUNNING.value
            source.save(update_fields=["legacy_prompt_config", "status"])
            column.status = StatusType.RUNNING.value
            column.save(update_fields=["status"])
        return {
            "experiment_id": str(experiment.pk), "source_id": str(source.pk),
            "admission_id": admission_id, "dataset_id": str(experiment.snapshot_dataset_id),
            "column_id": str(column.pk), "row_ids": [str(link.snapshot_row_id) for link in links],
            "status": "READY",
        }


def _http_row(experiment_id, source_id, row_id, admission_id):
    from model_hub.models.develop_dataset import Column

    experiment, context, source, snapshot, column = _http_execution(experiment_id, source_id, admission_id)
    link = next((link for link in _http_rows(snapshot, source)
                 if str(link.snapshot_row_id) == str(row_id)), None)
    if link is None:
        raise ValueError("HTTP row is not a selected snapshot row.")
    # Values were captured from the copied snapshot cells, keyed by ORIGINAL
    # column IDs. Do not substitute snapshot IDs, row order, or live cell values.
    values = deepcopy(link.version_item.input.get("values", {}))
    mapping = snapshot.column_map
    columns = list(Column.no_workspace_objects.filter(
        pk__in=mapping.values(), dataset_id=experiment.snapshot_dataset_id, deleted=False,
    ))
    reverse = {str(new): str(old) for old, new in mapping.items()}
    if len(columns) != len(mapping) or set(values) - set(mapping):
        raise ValueError("HTTP input columns no longer match the snapshot identity.")
    descriptions = [{"id": reverse[str(item.pk)], "name": item.name} for item in columns]
    return experiment, context, source, column, link, values, descriptions


def run_http_snapshot_row(experiment_id, source_id, row_id, admission_id):
    from model_hub.models.api_key import SecretModel
    from model_hub.models.choices import CellStatus
    from model_hub.views.http_endpoint_test import _header_value
    from tfc.utils.tenant_scope import scope_to_caller

    experiment, context, source, column, link, values, columns = _http_row(
        experiment_id, source_id, row_id, admission_id,
    )

    def resolve_secret(name):
        matches = list(scope_to_caller(
            SecretModel.no_workspace_objects.filter(deleted=False, name=name), context,
        )[:2])
        if len(matches) != 1:
            raise ValueError("Header reference is unavailable in the execution workspace.")
        return _header_value(matches[0].actual_key)

    cell = execute_and_persist_http_row(
        source=source, row=link.snapshot_row, column=column,
        attempt_id=http_attempt_id(admission_id, source.pk, link.snapshot_row_id),
        values=values, columns=columns, original_row_id=link.source_row_id,
        resolve_secret=resolve_secret, retry=source.legacy_prompt_config["_http_execution"]["retry"],
    )
    return {"row_id": str(row_id), "column_id": str(column.pk),
            "status": "BLOCKED" if cell is None else
                      "COMPLETED" if cell.status == CellStatus.PASS.value else "FAILED"}


def settle_http_row(experiment_id, source_id, row_id, admission_id):
    """Only read/settle durable evidence; NEVER send an endpoint request here."""
    from django.db import transaction
    from model_hub.models.choices import CellStatus
    from model_hub.models.develop_dataset import Cell
    from model_hub.models.experiments import ExperimentsTable

    with transaction.atomic():
        ExperimentsTable.no_workspace_objects.select_for_update().get(pk=experiment_id)
        experiment, context, source, column, link, values, columns = _http_row(
            experiment_id, source_id, row_id, admission_id,
        )
        attempt = http_attempt_id(admission_id, source.pk, row_id)
        cell = Cell.no_workspace_objects.filter(
            dataset_id=experiment.snapshot_dataset_id, row_id=row_id, column=column, deleted=False,
        ).first()
        if cell is not None and _cell_info(cell).get("http_attempt_id") == attempt:
            if cell.status in {CellStatus.PASS.value, CellStatus.ERROR.value}:
                return {"row_id": str(row_id), "status": "COMPLETED" if cell.status == CellStatus.PASS.value else "FAILED"}
        elif not claim_http_attempt(
            source=source, row=link.snapshot_row, column=column, attempt_id=attempt,
            retry=source.legacy_prompt_config["_http_execution"]["retry"],
        ):
            return {"row_id": str(row_id), "status": "BLOCKED"}
        cell = persist_http_result(
            source=source, row=link.snapshot_row, column=column, attempt_id=attempt,
            result={"output": None, "status_code": None, "latency_ms": None,
                    "error": _error("unknown_outcome", "The HTTP attempt did not finish. Check whether the endpoint processed it before explicitly rerunning.")},
        )
        return {"row_id": str(row_id), "status": "FAILED" if cell is not None else "BLOCKED"}


def finish_http_source(experiment_id, source_id, admission_id):
    from django.db import transaction
    from model_hub.models.choices import CellStatus, StatusType
    from model_hub.models.develop_dataset import Cell
    from model_hub.models.experiments import ExperimentsTable

    with transaction.atomic():
        ExperimentsTable.no_workspace_objects.select_for_update().get(pk=experiment_id)
        experiment, context, source, snapshot, column = _http_execution(experiment_id, source_id, admission_id)
        row_ids = [str(link.snapshot_row_id) for link in _http_rows(snapshot, source)]
        cells = {str(cell.row_id): cell for cell in Cell.no_workspace_objects.filter(
            dataset_id=experiment.snapshot_dataset_id, column=column,
            row_id__in=row_ids, deleted=False,
        )}
        outcomes = []
        for row_id in row_ids:
            cell = cells.get(row_id)
            if (cell is not None and cell.status in {CellStatus.PASS.value, CellStatus.ERROR.value}
                    and _cell_info(cell).get("http_attempt_id") == http_attempt_id(admission_id, source.pk, row_id)):
                outcomes.append({"status": "COMPLETED" if cell.status == CellStatus.PASS.value else "FAILED"})
            else:
                outcomes.append(settle_http_row(experiment_id, source_id, row_id, admission_id))
        if any(result["status"] == "BLOCKED" for result in outcomes):
            raise ValueError("HTTP attempts are still fenced; completion is unavailable.")
        source.legacy_prompt_config["_http_execution"]["phase"] = "finished"
        source.status = StatusType.COMPLETED.value
        source.save(update_fields=["legacy_prompt_config", "status"])
        column.status = StatusType.COMPLETED.value
        column.save(update_fields=["status"])
        return {"completed_rows": sum(result["status"] == "COMPLETED" for result in outcomes),
                "failed_rows": sum(result["status"] == "FAILED" for result in outcomes),
                "status": "COMPLETED"}


def prepare_http_experiment(runner, admission_id):
    """Serialize setup with Stop; a delayed activity cannot revive another run."""
    from django.db import transaction
    from model_hub.models.choices import StatusType
    from model_hub.models.experiments import ExperimentsTable
    from model_hub.services.experiment_submit import state_of

    if not http_source_ids(runner.experiment):
        return False
    with transaction.atomic():
        experiment = ExperimentsTable.no_workspace_objects.select_for_update().get(pk=runner.experiment.pk)
        state = state_of(experiment)
        if (not admission_id or state.get("admission_id") != admission_id
                or state.get("state") != "accepted"
                or experiment.status not in {StatusType.QUEUED.value, StatusType.RUNNING.value}):
            raise ValueError("HTTP submission is stopped or no longer current.")
        experiment.status = StatusType.RUNNING.value
        experiment.save(update_fields=["status"])
        runner.experiment = experiment
        if state.get("http_plan"):
            from model_hub.models.develop_dataset import Dataset
            snapshot = Dataset.no_workspace_objects.select_for_update().get(pk=experiment.snapshot_dataset_id)
            config = deepcopy(snapshot.dataset_config or {})
            if config.get("_http_prepared_admission") != admission_id:
                # Precreate only the selected sources, without blanking other
                # sources or rows. Evaluator activities receive the same row fence.
                runner.precreate_eval_columns_for_edts(list(state["http_plan"]["targets"]))
                _reset_http_subset_evals(experiment, state["http_plan"])
                config["_http_prepared_admission"] = admission_id
                snapshot.dataset_config = config
                snapshot.save(update_fields=["dataset_config"])
        else:
            runner.empty_or_create_evals_column()
    return True


def http_admission_ready(experiment_id, admission_id):
    """Wait for the request-side acknowledgement before setup or HTTP dispatch."""
    from django.db import transaction
    from model_hub.models.choices import StatusType
    from model_hub.services.experiment_submit import _locked_native_admission, SubmissionCancelled
    from tfc.routers import force_primary

    with force_primary(), transaction.atomic():
        experiment, snapshot, state = _locked_native_admission(experiment_id)
        # V2 memo exists for classic admissions as well. Their path is unchanged.
        if not http_source_ids(experiment):
            return True
        if (not admission_id or state.get("admission_id") != admission_id
                or state.get("state") == "cancelled"
                or experiment.status == StatusType.CANCELLED.value):
            raise SubmissionCancelled("HTTP admission was stopped or superseded.")
        if state.get("state") not in {"pending", "accepted"}:
            raise ValueError("HTTP admission acknowledgement is unavailable.")
        if state["state"] == "accepted" and not state.get("workflow_id"):
            raise ValueError("HTTP admission has no confirmed workflow.")
        return state["state"] == "accepted"


def cancel_pending_http_cells(experiment, admission_id):
    """Under submission locks, settle only cells claimed by this pending run.

    New parents cannot dispatch while pending. Older parents may already have
    claimed a cell before a lost start acknowledgement; settle that evidence
    without replaying the request or overwriting earlier terminal receipts.
    """
    from model_hub.models.choices import CellStatus, SourceChoices, StatusType
    from model_hub.models.develop_dataset import Cell
    from model_hub.models.experiments import ExperimentDatasetTable

    for source in ExperimentDatasetTable.no_workspace_objects.filter(
        experiment=experiment, deleted=False, legacy_prompt_config__target_type="http",
    ):
        execution = (source.legacy_prompt_config or {}).get("_http_execution") or {}
        if execution.get("admission_id") != admission_id:
            continue
        for cell in Cell.no_workspace_objects.filter(
            dataset_id=experiment.snapshot_dataset_id,
            column__experiments_dataset_column=source,
            status=CellStatus.RUNNING.value, deleted=False,
        ).select_related("column").distinct():
            if "row_ids" in execution and str(cell.row_id) not in execution["row_ids"]:
                continue
            info = _cell_info(cell)
            if (cell.column.source == SourceChoices.EXPERIMENT.value
                    and info.get("http_attempt_id") != http_attempt_id(admission_id, source.pk, cell.row_id)):
                continue
            # Older pending-enabled workers may also have started evaluation
            # placeholders. They belong to this stopped source, but have no
            # HTTP request attempt token. Terminal receipts are left untouched.
            message = "HTTP execution was stopped. Check whether the endpoint processed the request before rerunning."
            info.update(error=_error("cancelled", message), reason=message)
            cell.status = CellStatus.ERROR.value
            cell.value = message
            cell.value_infos = json.dumps(info)
            cell.save(update_fields=["status", "value", "value_infos", "updated_at"])
        execution["phase"] = "cancelled"
        source.status = StatusType.FAILED.value
        source.save(update_fields=["legacy_prompt_config", "status"])
        source.columns.filter(dataset_id=experiment.snapshot_dataset_id, deleted=False).update(
            status=StatusType.FAILED.value,
        )


HTTP_SIDE_EFFECT_WARNING = (
    "A prior HTTP attempt may have had side effects, even if it timed out or failed. "
    "Check the endpoint before sending it again."
)
HTTP_APPEND_ROWS_KEY = "_http_appended_rows"


def _http_snapshot(experiment):
    from model_hub.models.native_experiment_runs import NativeExperimentSnapshot
    from model_hub.services.native_run_binding import validate_snapshot

    snapshot = NativeExperimentSnapshot.objects.select_related(
        "dataset_version", "snapshot_dataset",
    ).get(experiment=experiment, snapshot_dataset_id=experiment.snapshot_dataset_id)
    validate_snapshot(snapshot, experiment.dataset, experiment)
    return snapshot


def _http_idle(experiment):
    from model_hub.models.choices import StatusType
    from model_hub.services.experiment_submit import ensure_native_idle, state_of

    if (state_of(experiment).get("state") == "pending"
            or experiment.status in {StatusType.QUEUED.value, StatusType.RUNNING.value}):
        raise ValueError("Wait for or stop the active execution before changing HTTP sources.")
    ensure_native_idle(experiment)


def _http_public_config(config):
    return {key: deepcopy(config[key]) for key in ("target_type", "http", "variable_mapping") if key in config}


def _validated_http_config(experiment, entry):
    from model_hub.serializers.experiments import PromptConfigEntrySerializer

    serializer = PromptConfigEntrySerializer(data=entry)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    if data.get("target_type") != "http":
        raise ValueError("Edit HTTP sources separately from prompt and agent configurations.")
    snapshot = _http_snapshot(experiment)
    wanted = {value.partition("::")[0] for value in data.get("variable_mapping", {}).values()
              if isinstance(value, str) and value}
    if not wanted <= set(snapshot.column_map):
        raise ValueError("HTTP mappings must reference original columns captured in this experiment snapshot.")
    # Validate copied columns as well as original IDs; no live-column fallback.
    from model_hub.models.develop_dataset import Column
    available = set(map(str, Column.no_workspace_objects.filter(
        dataset_id=experiment.snapshot_dataset_id, deleted=False,
        pk__in=[snapshot.column_map[value] for value in wanted],
    ).values_list("pk", flat=True)))
    if available != {snapshot.column_map[value] for value in wanted}:
        raise ValueError("HTTP snapshot mapping columns are unavailable.")
    return data


def update_http_sources(experiment, entries):
    """Save settings in place. Saving never sends an HTTP request or resets cells."""
    from model_hub.models.experiments import ExperimentDatasetTable
    from model_hub.services.experiment_submit import invalidate_accepted_replay

    _http_idle(experiment)
    sources = {str(source.pk): source for source in ExperimentDatasetTable.no_workspace_objects.filter(
        experiment=experiment, deleted=False, legacy_prompt_config__target_type="http",
    )}
    prepared, seen = [], set()
    for entry in entries:
        data = _validated_http_config(experiment, entry)
        source_id = str(data.get("id") or "")
        if source_id not in sources or source_id in seen:
            raise ValueError("Edit requires a unique existing HTTP source ID in this experiment.")
        seen.add(source_id)
        prepared.append((sources[source_id], data))
    for source, data in prepared:
        config = deepcopy(source.legacy_prompt_config)
        public = _http_public_config(data)
        name = data.get("name", source.name) or "HTTP endpoint"
        if _http_public_config(config) == public and source.name == name:
            continue
        config.setdefault("_http_config_history", []).append({
            "name": source.name, **_http_public_config(config),
        })
        config.update(public)
        source.name = name
        source.legacy_prompt_config = config
        source.save(update_fields=["name", "legacy_prompt_config"])
        # The output column's ID and all its receipts survive a rename.
        source.columns.filter(dataset_id=experiment.snapshot_dataset_id,
                              source_id=str(source.pk), deleted=False).update(name=name)
    invalidate_accepted_replay(experiment)


def _http_append_candidates(experiment, source_row_ids, *, identity=None):
    """Read the exact candidate-minus-captured set shared by preview and append."""
    from model_hub.models.develop_dataset import Row

    identity = identity or _http_snapshot(experiment)
    links = _http_rows(identity)
    prior_ids = {str(link.source_row_id) for link in links}
    requested = set(map(str, source_row_ids))
    rows = list(Row.no_workspace_objects.filter(
        dataset_id=experiment.dataset_id, pk__in=requested, deleted=False,
    ).order_by("order", "id"))
    if {str(row.pk) for row in rows} != requested:
        raise ValueError("Append rows must belong to the source dataset.")
    return links, [row for row in rows if str(row.pk) not in prior_ids]


def preview_http_append_rows(experiment, source_row_ids):
    """Read only: no captures, submission, source changes or HTTP requests."""
    _http_idle(experiment)
    sources = http_source_ids(experiment)
    if not sources:
        raise ValueError("Select an experiment with an HTTP source to preview rows.")
    links, rows = _http_append_candidates(experiment, source_row_ids)
    return {
        "preview": True,
        "new_row_ids": [str(row.pk) for row in rows],
        "request_count": len(rows) * len(sources),
        "held_count": len(links),
        "limit": 10000,
    }


def _append_http_rows(experiment, source_row_ids):
    """Append immutable HTTP-only row captures without rewriting native bindings.

    NativeExperimentSnapshot and DatasetVersion items remain immutable. Additional
    HTTP rows live in an append-only snapshot manifest, keyed by copied row ID;
    prompt/agent bindings continue to enumerate their original version members.
    """
    from model_hub.models.develop_dataset import Cell, Dataset, Row

    identity = _http_snapshot(experiment)
    snapshot = Dataset.no_workspace_objects.select_for_update().get(pk=experiment.snapshot_dataset_id)
    links, rows = _http_append_candidates(experiment, source_row_ids, identity=identity)
    if len(rows) + len(links) > 10000:
        raise ValueError("HTTP experiment snapshots support at most 10000 rows.")
    config = deepcopy(snapshot.dataset_config or {})
    captures = config.setdefault(HTTP_APPEND_ROWS_KEY, {})
    next_order = max((link.snapshot_row.order for link in links), default=-1) + 1
    appended = []
    for offset, original in enumerate(rows):
        copied = Row.no_workspace_objects.create(
            dataset=snapshot, order=next_order + offset, metadata=deepcopy(original.metadata),
        )
        values = {}
        for cell in Cell.no_workspace_objects.filter(
            dataset_id=experiment.dataset_id, row=original,
            column_id__in=identity.column_map, deleted=False,
        ):
            values[str(cell.column_id)] = cell.value
            Cell.no_workspace_objects.create(
                dataset=snapshot, row=copied, column_id=identity.column_map[str(cell.column_id)],
                value=cell.value, status=cell.status, value_infos=deepcopy(cell.value_infos),
            )
        captures[str(copied.pk)] = {
            "source_row_id": str(original.pk), "values": values,
            "column_map": deepcopy(identity.column_map),
        }
        appended.append(str(copied.pk))
    snapshot.dataset_config = config
    snapshot.save(update_fields=["dataset_config"])
    return appended


def _http_output_column(experiment, source):
    from model_hub.models.choices import SourceChoices
    from model_hub.models.develop_dataset import Column

    columns = list(Column.no_workspace_objects.filter(
        dataset_id=experiment.snapshot_dataset_id, deleted=False,
        experiments_dataset_column=source, source=SourceChoices.EXPERIMENT.value,
        source_id=str(source.pk),
    ))
    if len(columns) != 1:
        raise ValueError("HTTP source output column is unavailable.")
    return columns[0]


def select_http_retry(experiment, data):
    """Resolve a strict per-source row set; None preserves non-HTTP retry paths."""
    from model_hub.models.choices import CellStatus
    from model_hub.models.develop_dataset import Cell, Column
    from model_hub.models.experiments import ExperimentDatasetTable

    sources = {str(source.pk): source for source in ExperimentDatasetTable.no_workspace_objects.filter(
        experiment=experiment, deleted=False, legacy_prompt_config__target_type="http",
    )}
    explicit = set(map(str, data.get("source_ids", [])))
    requested_columns = {str(cell["column_id"]) for cell in data.get("cells", [])}
    output = {str(_http_output_column(experiment, source).pk): source_id for source_id, source in sources.items()}
    if not (explicit & sources.keys() or requested_columns & output.keys()):
        return None
    if data.get("user_eval_metric_ids") or explicit - sources.keys() or requested_columns - output.keys():
        raise ValueError("Retry HTTP output cells separately from prompt, agent, or evaluator cells.")
    # Do not accept a foreign column even if its source_id names a local source.
    if Column.no_workspace_objects.filter(pk__in=requested_columns, dataset_id=experiment.snapshot_dataset_id,
                                          deleted=False).count() != len(requested_columns):
        raise ValueError("Columns not found in experiment snapshot.")
    rows = {str(link.snapshot_row_id) for link in _http_rows(_http_snapshot(experiment))}
    selected = {source_id: set(rows) for source_id in explicit}
    for cell in data.get("cells", []):
        row_id = str(cell["row_id"])
        if row_id not in rows:
            raise ValueError("Rows not found in immutable HTTP snapshot.")
        selected.setdefault(output[str(cell["column_id"])], set()).add(row_id)
    if data.get("failed_only"):
        for source_id in selected:
            selected[source_id] &= set(map(str, Cell.no_workspace_objects.filter(
                dataset_id=experiment.snapshot_dataset_id, column=_http_output_column(experiment, sources[source_id]),
                row_id__in=selected[source_id], status=CellStatus.ERROR.value, deleted=False,
            ).values_list("row_id", flat=True)))
    return {source_id: sorted(row_ids) for source_id, row_ids in selected.items() if row_ids}


def stage_http_mutation(experiment, data, workspace, execution_key, *, operation):
    """Caller holds source -> experiment locks. Admission commits before dispatch."""
    from model_hub.models.experiments import ExperimentDatasetTable
    from model_hub.services.experiment_submit import (
        keyed_submission, stage_submission, validate_execution_key,
    )

    if operation not in {"append", "retry"}:
        raise ValueError("Unknown HTTP mutation.")
    if operation == "retry" and data.get("acknowledge_http_side_effects") is not True:
        raise ValueError(HTTP_SIDE_EFFECT_WARNING + " Confirm acknowledgement before retrying.")
    execution_key = validate_execution_key(execution_key)
    if not execution_key:
        raise ValueError("HTTP execution requires an Idempotency-Key. Reuse it when recovering the same request.")
    previous = keyed_submission(experiment, data, "http_" + operation, execution_key)
    if previous is not None:
        return previous
    _http_idle(experiment)
    if operation == "retry":
        selected = select_http_retry(experiment, data)
        if selected is None:
            raise ValueError("Select an HTTP source or HTTP output cell.")
    else:
        if any(key in data for key in ("column_id", "user_eval_metrics")):
            raise ValueError("Append cannot change existing columns or evaluations. Save those edits separately.")
        entries = [_validated_http_config(experiment, entry) for entry in data.get("prompt_config", [])]
        if any(entry.get("id") for entry in entries):
            raise ValueError("Append accepts new HTTP sources without IDs; use Edit for existing sources.")
        existing_ids = http_source_ids(experiment)
        appended = _append_http_rows(experiment, data.get("row_ids", [])) if data.get("row_ids") else []
        selected = {source_id: appended for source_id in existing_ids} if appended else {}
        all_rows = [str(link.snapshot_row_id) for link in _http_rows(_http_snapshot(experiment))]
        order = ExperimentDatasetTable.no_workspace_objects.filter(experiment=experiment, deleted=False).count()
        for offset, entry in enumerate(entries):
            # Entries passed _validated_http_config against this snapshot above.
            source = _persist_validated_http_source(experiment, entry, order=order + offset)
            create_http_output_column(experiment, source)
            selected[str(source.pk)] = all_rows
    if not selected:
        raise ValueError("No new HTTP rows or sources to append." if operation == "append" else "No selected HTTP rows match this retry.")
    sources = {str(source.pk): source for source in ExperimentDatasetTable.no_workspace_objects.filter(
        experiment=experiment, pk__in=selected, deleted=False, legacy_prompt_config__target_type="http",
    )}
    if set(sources) != set(selected):
        raise ValueError("HTTP sources are outside this experiment.")
    targets = {source_id: {
        "row_ids": list(rows), "column_id": str(_http_output_column(experiment, sources[source_id]).pk),
        "source": _http_public_config(sources[source_id].legacy_prompt_config),
    } for source_id, rows in selected.items()}
    state = stage_submission(
        experiment, data, "http_" + operation, authorized_workspace=workspace,
        execution_key=execution_key, http_plan={"operation": operation, "targets": targets},
        http_only=True, max_concurrent_rows=min(data.get("max_concurrent_rows", 10), 32),
    )
    bind_http_submission(experiment, state["admission_id"], retry=operation == "retry")
    return state


def cleanup_http_mutation(experiment, state, *, stopped=False):
    """Settle only this HTTP selection; never erase another source's receipts."""
    from model_hub.models.choices import CellStatus, StatusType
    from model_hub.models.develop_dataset import Cell
    from model_hub.models.experiments import ExperimentDatasetTable

    count = 0
    for source_id, target in state["http_plan"]["targets"].items():
        source = ExperimentDatasetTable.no_workspace_objects.get(
            pk=source_id, experiment=experiment, deleted=False,
            legacy_prompt_config__target_type="http",
        )
        execution = source.legacy_prompt_config.get("_http_execution") or {}
        if execution.get("admission_id") != state["admission_id"]:
            continue
        message = ("HTTP execution was stopped. " if stopped else "The HTTP attempt did not finish. ") + HTTP_SIDE_EFFECT_WARNING
        for cell in Cell.no_workspace_objects.filter(
            dataset_id=experiment.snapshot_dataset_id, column__experiments_dataset_column=source,
            row_id__in=target["row_ids"], deleted=False, status=CellStatus.RUNNING.value,
        ).distinct():
            info = deepcopy(_cell_info(cell))
            info.update(error=_error("cancelled" if stopped else "unknown_outcome", message), reason=message)
            cell.status, cell.value, cell.value_infos = CellStatus.ERROR.value, message, json.dumps(info)
            cell.save(update_fields=["status", "value", "value_infos", "updated_at"])
            count += 1
        if stopped:
            execution["phase"] = "cancelled"
            source.status = StatusType.FAILED.value
            source.save(update_fields=["legacy_prompt_config", "status"])
            source.columns.filter(dataset_id=experiment.snapshot_dataset_id, deleted=False).update(status=StatusType.FAILED.value)
    if stopped:
        experiment.status = StatusType.CANCELLED.value
        experiment.save(update_fields=["status"])
    return {"experiment_id": str(experiment.pk), "cells_cleaned": count,
            "status": "CANCELLED" if stopped else "COMPLETED"}


def ensure_http_mutation_admitted(experiment_id, admission_id):
    """HTTP-only client gate never falls through to the prompt/agent selector."""
    from model_hub.services.experiment_submit import state_of
    from model_hub.services.experiment_target_admission import _experiment_context

    experiment, _ = _experiment_context(experiment_id)
    state = state_of(experiment)
    if (not admission_id or state.get("admission_id") != admission_id
            or state.get("state") not in {"pending", "accepted"}
            or not state.get("http_plan", {}).get("targets")):
        raise ValueError("HTTP mutation requires its committed admission plan.")
    return state


def _reset_http_subset_evals(experiment, plan):
    from model_hub.models.choices import CellStatus, SourceChoices, StatusType
    from model_hub.models.develop_dataset import Cell, Column

    for source_id, target in plan["targets"].items():
        columns = Column.no_workspace_objects.filter(
            dataset_id=experiment.snapshot_dataset_id, experiments_dataset_column__id=source_id,
            deleted=False,
        ).exclude(source=SourceChoices.EXPERIMENT.value)
        ids = list(columns.values_list("id", flat=True))
        columns.update(status=StatusType.RUNNING.value)
        for cell in Cell.no_workspace_objects.filter(
            dataset_id=experiment.snapshot_dataset_id, column_id__in=ids,
            row_id__in=target["row_ids"], deleted=False,
        ):
            info = deepcopy(_cell_info(cell))
            history = info.pop("http_eval_history", [])
            history.append({"value": cell.value, "status": cell.status, "value_infos": info})
            cell.value_infos = json.dumps({"http_eval_history": history})
            cell.value = ""
            cell.status = CellStatus.RUNNING.value
            cell.save(update_fields=["value_infos", "value", "status", "updated_at"])
