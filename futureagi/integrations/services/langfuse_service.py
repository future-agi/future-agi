import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import requests

from integrations.services.base import BaseIntegrationService, register_service
from integrations.services.export_failures import validation_failure

logger = logging.getLogger(__name__)


class LangfuseUnsupportedVersion(requests.exceptions.HTTPError):
    """The trace list answered 404 while the project answers 200.

    The keys and the project are fine; the deployment is a Langfuse version
    that does not serve the list endpoint the import reads, such as a
    Langfuse v4 server in its default events_only mode.
    """


@dataclass
class _ObservationsPage:
    """The next page of a trace listing through the observations v2 read.

    fetch_traces returns it as `next_page` and the import loop passes it
    back as `page`. It carries the listing's filters, sent again with every
    cursor, the cursor of the page to read, and the ids of the traces this
    sync has listed, so a trace whose rows span pages is imported once.
    """

    number: int
    params: dict[str, Any]
    cursor: str | None = None
    listed: set[str] = field(default_factory=set)

    def __str__(self) -> str:
        return str(self.number)

    def __add__(self, other: int) -> int:
        """The import loop works out `page + 1` as a fallback on every turn."""
        return self.number + other


class LangfuseService(BaseIntegrationService):
    """Langfuse API client implementation.

    Langfuse uses project-scoped API keys with HTTP Basic Auth:
    - Username = Public Key (pk-lf-...)
    - Password = Secret Key (sk-lf-...)
    """

    TIMEOUT = 30  # seconds

    # API paths
    API_PATH_PROJECTS = "/api/public/projects"
    API_PATH_TRACES = "/api/public/traces"
    # The newer reads, used only when a v1 trace read answers 404: a Langfuse
    # version that does not serve this read, such as Langfuse v4 in its
    # default events_only mode. A Langfuse v3 server keeps the v1 path unchanged.
    API_PATH_OBSERVATIONS_V2 = "/api/public/v2/observations"
    API_PATH_SCORES_V3 = "/api/public/v3/scores"
    V2_PAGE_LIMIT = 1000  # the observations endpoint's maximum
    V2_TRACE_FIELDS = "core,basic,time,io,metadata,model,usage,metrics,trace_context"
    V3_SCORE_LIMIT = 100  # the scores endpoint's maximum
    V3_SCORE_FIELDS = "details,subject"
    # Stops a listing whose server keeps answering with new cursors.
    MAX_CURSOR_PAGES = 10000

    def _get_auth(self, credentials: dict) -> tuple[str, str]:
        return (credentials["public_key"], credentials["secret_key"])

    def _make_request(
        self,
        method: str,
        host_url: str,
        path: str,
        credentials: dict,
        ca_certificate: str | None = None,
        params: dict | None = None,
    ) -> requests.Response:
        url = f"{host_url.rstrip('/')}{path}"
        verify: Any = True
        tmp_path = None

        if ca_certificate and ca_certificate.strip():
            tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False)
            tmp.write(ca_certificate)
            tmp.close()
            tmp_path = tmp.name
            verify = tmp_path

        try:
            response = requests.request(
                method=method,
                url=url,
                auth=self._get_auth(credentials),
                params=params,
                timeout=self.TIMEOUT,
                verify=verify,
            )
            return response
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    def validate_credentials(
        self,
        host_url: str,
        credentials: dict,
        ca_certificate: str | None = None,
    ) -> dict:
        try:
            # Step 1: Validate by fetching project info
            resp = self._make_request(
                "GET",
                host_url,
                self.API_PATH_PROJECTS,
                credentials,
                ca_certificate,
            )

            if resp.status_code == 401:
                return {
                    "valid": False,
                    "error": "Invalid credentials. Please check your public key and secret key.",
                }

            if resp.status_code != 200:
                return {
                    "valid": False,
                    "error": f"Unexpected response from Langfuse (HTTP {resp.status_code}).",
                }

            project_data = resp.json()
            raw_projects = project_data.get("data", [])

            projects = [
                {"id": p.get("id", ""), "name": p.get("name", "Unknown")}
                for p in raw_projects
            ]

            # Step 2: Get aggregate trace count for backfill estimation
            total_traces = 0
            try:
                traces_resp = self._make_request(
                    "GET",
                    host_url,
                    self.API_PATH_TRACES,
                    credentials,
                    ca_certificate,
                    params={"limit": 1},
                )
                if traces_resp.status_code == 200:
                    meta = traces_resp.json().get("meta", {})
                    total_traces = meta.get("totalItems", 0)
            except Exception:
                logger.warning(
                    "Failed to fetch trace count during validation",
                )

            return {
                "valid": True,
                "projects": projects,
                "total_traces": total_traces,
            }

        except requests.exceptions.ConnectionError:
            return {
                "valid": False,
                "error": "Could not reach Langfuse. Verify the host URL.",
            }
        except requests.exceptions.Timeout:
            return {
                "valid": False,
                "error": "Connection timed out. Please try again.",
            }
        except Exception as e:
            return validation_failure("langfuse", e, logger)

    def fetch_traces(
        self,
        host_url: str,
        credentials: dict,
        from_timestamp: str | None = None,
        to_timestamp: str | None = None,
        page: int | _ObservationsPage = 1,
        limit: int = 100,
        ca_certificate: str | None = None,
    ) -> dict:
        """One page of the traces in the window, from the v1 trace list.

        When the v1 list answers 404 on page 1, the listing moves to the
        observations v2 read, one cursor page per call: `next_page` is then
        that listing's next page, which the import loop passes back as
        `page`.
        """
        if isinstance(page, _ObservationsPage):
            return self._next_traces_v2(host_url, credentials, ca_certificate, page)

        params: dict[str, Any] = {"page": page, "limit": limit}
        if from_timestamp:
            params["fromTimestamp"] = from_timestamp
        if to_timestamp:
            params["toTimestamp"] = to_timestamp

        resp = self._make_request(
            "GET",
            host_url,
            self.API_PATH_TRACES,
            credentials,
            ca_certificate,
            params=params,
        )
        if resp.status_code == 404 and page == 1:
            newer = self._list_traces_v2(
                host_url, credentials, from_timestamp, to_timestamp, ca_certificate
            )
            if newer is not None:
                return newer
        if resp.status_code == 404:
            self._raise_list_not_found(resp, host_url, credentials, ca_certificate)
        resp.raise_for_status()

        data = resp.json()
        traces = data.get("data", [])
        meta = data.get("meta", {})
        total_items = meta.get("totalItems", 0)
        total_pages = meta.get("totalPages", 1)
        current_page = meta.get("page", page)

        return {
            "traces": traces,
            "has_more": current_page < total_pages,
            "next_page": current_page + 1,
            "total_items": total_items,
        }

    def _raise_list_not_found(
        self,
        resp: requests.Response,
        host_url: str,
        credentials: dict,
        ca_certificate: str | None,
    ) -> None:
        """Tell a deployment without the list endpoint from a missing project.

        The same keys read the project: when that answers 200, the project
        exists and the list endpoint is what is missing. Any other answer
        keeps the list call's own 404.
        """
        try:
            project = self._make_request(
                "GET",
                host_url,
                self.API_PATH_PROJECTS,
                credentials,
                ca_certificate,
            )
        except requests.exceptions.RequestException:
            project = None
        if project is not None and project.status_code == 200:
            raise LangfuseUnsupportedVersion(
                "Langfuse does not serve the trace list endpoint.",
                response=resp,
            )
        resp.raise_for_status()

    def fetch_trace_detail(
        self,
        host_url: str,
        credentials: dict,
        trace_id: str,
        ca_certificate: str | None = None,
    ) -> dict[str, Any]:
        resp = self._make_request(
            "GET",
            host_url,
            f"{self.API_PATH_TRACES}/{trace_id}",
            credentials,
            ca_certificate,
        )
        if resp.status_code == 404:
            assembled = self._trace_detail_v2(
                host_url, credentials, trace_id, ca_certificate
            )
            if assembled is not None:
                return assembled
        resp.raise_for_status()
        return resp.json()

    def _read_cursor_pages(
        self,
        host_url: str,
        path: str,
        params: dict[str, Any],
        credentials: dict,
        ca_certificate: str | None,
        *,
        missing_ok: bool,
    ) -> list[dict] | None:
        """Every row of a cursor-paginated list, following meta.cursor.

        A page may be short or empty while it still carries a cursor, so the
        listing ends only when the cursor is absent, repeats, or the page cap
        is reached. With `missing_ok`, a 404 on the first page returns None.
        """
        rows: list[dict] = []
        cursor = None
        for number in range(self.MAX_CURSOR_PAGES):
            page_params = dict(params)
            if cursor:
                page_params["cursor"] = cursor
            resp = self._make_request(
                "GET", host_url, path, credentials, ca_certificate, params=page_params
            )
            if missing_ok and number == 0 and resp.status_code == 404:
                return None
            resp.raise_for_status()
            body = resp.json() or {}
            rows.extend(row for row in body.get("data") or [] if isinstance(row, dict))
            next_cursor = (body.get("meta") or {}).get("cursor")
            if not next_cursor or next_cursor == cursor:
                return rows
            cursor = next_cursor
        logger.warning(
            "langfuse_cursor_page_cap_reached %s",
            {"path": path, "pages": self.MAX_CURSOR_PAGES},
        )
        return rows

    def _list_traces_v2(
        self,
        host_url: str,
        credentials: dict,
        from_timestamp: str | None,
        to_timestamp: str | None,
        ca_certificate: str | None,
    ) -> dict | None:
        """The first page of the traces with an observation in the window.

        None when the observations endpoint answers 404 as well.
        """
        params: dict[str, Any] = {"fields": "core", "limit": self.V2_PAGE_LIMIT}
        if from_timestamp:
            params["fromStartTime"] = _utc_z(from_timestamp)
        if to_timestamp:
            params["toStartTime"] = _utc_z(to_timestamp)
        first = self._make_request(
            "GET",
            host_url,
            self.API_PATH_OBSERVATIONS_V2,
            credentials,
            ca_certificate,
            params=params,
        )
        if first.status_code == 404:
            return None
        start = _ObservationsPage(number=1, params=params)
        return self._next_traces_v2(host_url, credentials, ca_certificate, start, first)

    def _next_traces_v2(
        self,
        host_url: str,
        credentials: dict,
        ca_certificate: str | None,
        position: _ObservationsPage,
        resp: requests.Response | None = None,
    ) -> dict:
        """The traces of the next observations page that lists a new trace.

        Reads cursor pages from `position` (the first one may already be in
        `resp`) and returns at the first page that names a trace this sync
        has not listed, newest first, or when the listing ends. So the import
        reads those traces before the next page is fetched, and only the ids
        of listed traces are kept between pages. A page that lists no new
        trace, which the endpoint may send while a cursor follows, is read
        past in the same call.
        """
        number, cursor = position.number, position.cursor
        while True:
            if resp is None:
                params = dict(position.params)
                if cursor:
                    params["cursor"] = cursor
                resp = self._make_request(
                    "GET",
                    host_url,
                    self.API_PATH_OBSERVATIONS_V2,
                    credentials,
                    ca_certificate,
                    params=params,
                )
            resp.raise_for_status()
            body = resp.json() or {}
            resp = None
            trace_ids: list[str] = []
            for row in body.get("data") or []:
                trace_id = row.get("traceId") if isinstance(row, dict) else None
                if trace_id and trace_id not in position.listed:
                    position.listed.add(trace_id)
                    trace_ids.append(trace_id)
            next_cursor = (body.get("meta") or {}).get("cursor")
            has_more = bool(next_cursor) and next_cursor != cursor
            if has_more and number >= self.MAX_CURSOR_PAGES:
                logger.warning(
                    "langfuse_cursor_page_cap_reached %s",
                    {"path": self.API_PATH_OBSERVATIONS_V2, "pages": number},
                )
                has_more = False
            if trace_ids or not has_more:
                following = _ObservationsPage(
                    number + 1, position.params, next_cursor, position.listed
                )
                return {
                    "traces": [{"id": trace_id} for trace_id in trace_ids],
                    "has_more": has_more,
                    "next_page": following if has_more else number + 1,
                    "total_items": len(position.listed),
                }
            number, cursor = number + 1, next_cursor

    def _trace_detail_v2(
        self,
        host_url: str,
        credentials: dict,
        trace_id: str,
        ca_certificate: str | None,
    ) -> dict[str, Any] | None:
        """One trace in the v1 detail shape, from its observations and scores.

        None when the observations endpoint answers 404 or has no row for
        this trace, so the caller raises the v1 read's own 404.
        """
        rows = self._read_cursor_pages(
            host_url,
            self.API_PATH_OBSERVATIONS_V2,
            {
                "traceId": trace_id,
                "fields": self.V2_TRACE_FIELDS,
                "limit": self.V2_PAGE_LIMIT,
            },
            credentials,
            ca_certificate,
            missing_ok=True,
        )
        if not rows:
            return None
        scores = self._read_cursor_pages(
            host_url,
            self.API_PATH_SCORES_V3,
            {
                "traceId": trace_id,
                "fields": self.V3_SCORE_FIELDS,
                "limit": self.V3_SCORE_LIMIT,
            },
            credentials,
            ca_certificate,
            missing_ok=False,
        )
        return assemble_v2_trace(trace_id, rows, scores or [])


def _utc_z(value: str) -> str:
    """An ISO 8601 time as UTC with millisecond precision and a Z suffix."""
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return value
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    moment = moment.astimezone(UTC)
    return f"{moment:%Y-%m-%dT%H:%M:%S}.{moment.microsecond // 1000:03d}Z"


def _parsed_io(value: Any) -> Any:
    """The v2 reads return input and output as raw strings; v1 parsed JSON."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _seconds_between(start: Any, end: Any) -> float | None:
    try:
        began = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        ended = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (ended - began).total_seconds()


def _v1_observation(row: dict) -> dict[str, Any]:
    """A v2 observation row in the v1 trace detail's observation shape."""
    usage = row.get("usageDetails")
    if not usage:
        usage = {
            key: row[field]
            for key, field in (
                ("input", "inputUsage"),
                ("output", "outputUsage"),
                ("total", "totalUsage"),
            )
            if row.get(field) is not None
        }
    latency = row.get("latency")
    if latency is None and row.get("startTime") and row.get("endTime"):
        latency = _seconds_between(row["startTime"], row["endTime"])
    return {
        "id": row.get("id"),
        "traceId": row.get("traceId"),
        "type": row.get("type"),
        "name": row.get("name"),
        "parentObservationId": row.get("parentObservationId"),
        "startTime": row.get("startTime"),
        "endTime": row.get("endTime"),
        "completionStartTime": row.get("completionStartTime"),
        "level": row.get("level"),
        "statusMessage": row.get("statusMessage"),
        "version": row.get("version"),
        "environment": row.get("environment"),
        "model": row.get("model"),
        "modelParameters": _parsed_io(row.get("modelParameters")),
        "input": _parsed_io(row.get("input")),
        "output": _parsed_io(row.get("output")),
        "metadata": row.get("metadata") or {},
        "usageDetails": usage or {},
        "costDetails": row.get("costDetails") or {},
        "calculatedTotalCost": row.get("totalCost"),
        "latency": latency,
    }


def _v1_score(score: dict, trace_id: str) -> dict[str, Any] | None:
    """A v3 score in the v1 shape: numeric value plus stringValue.

    A boolean reads 1 or 0 with "True" or "False", and a category reads 0
    with its label, as the v1 reads return them when no score config is
    linked. Session and experiment scores are not trace scores.
    """
    subject = score.get("subject") or {}
    kind = subject.get("kind")
    if kind not in (None, "trace", "observation"):
        return None
    data_type = (score.get("dataType") or "").upper()
    value = score.get("value")
    string_value = None
    if data_type == "BOOLEAN":
        truth = value is True or value == 1 or str(value).lower() == "true"
        value, string_value = (1 if truth else 0), ("True" if truth else "False")
    elif data_type == "CATEGORICAL":
        value, string_value = 0, ("" if value is None else str(value))
    elif data_type in ("TEXT", "CORRECTION"):
        value, string_value = None, ("" if value is None else str(value))
    return {
        "id": score.get("id"),
        "traceId": trace_id,
        "observationId": (
            subject.get("id") if kind == "observation" else score.get("observationId")
        ),
        "name": score.get("name"),
        "value": value,
        "stringValue": string_value,
        "dataType": data_type or None,
        "source": score.get("source"),
        "comment": score.get("comment"),
        "configId": score.get("configId"),
        "metadata": score.get("metadata"),
        "environment": score.get("environment"),
        "timestamp": score.get("timestamp"),
    }


def assemble_v2_trace(
    trace_id: str, rows: list[dict], scores: list[dict]
) -> dict[str, Any]:
    """The v1 trace detail for one trace, built from v2 rows and v3 scores.

    Trace name, tags and release come from the rows' trace context, user and
    session from the rows, and input and output from the root observation:
    the newer reads have no trace-level input, output or metadata.
    """
    rows = sorted(rows, key=lambda row: str(row.get("startTime") or ""))
    # The root is the row Langfuse marks as root (an SDK may mark one that
    # keeps a parent from another service), else a row without a parent,
    # else the earliest row.
    marked = [row for row in rows if row.get("isRootObservation") is True]
    parentless = [row for row in rows if not row.get("parentObservationId")]
    root = (marked or parentless or rows)[0]

    def first(field: str) -> Any:
        for row in (root, *rows):
            value = row.get(field)
            if value not in (None, "", []):
                return value
        return None

    converted = (_v1_score(score, trace_id) for score in scores)
    return {
        "id": trace_id,
        "name": first("traceName"),
        "timestamp": root.get("startTime"),
        "userId": first("userId"),
        "sessionId": first("sessionId"),
        "release": first("release"),
        "environment": root.get("environment"),
        "tags": list(first("tags") or []),
        "input": _parsed_io(root.get("input")),
        "output": _parsed_io(root.get("output")),
        "metadata": {},
        "observations": [_v1_observation(row) for row in rows],
        "scores": [score for score in converted if score is not None],
    }


# Self-register on module import
_langfuse_service = LangfuseService()
register_service("langfuse", _langfuse_service)
