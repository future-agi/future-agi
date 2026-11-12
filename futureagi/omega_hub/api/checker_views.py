"""The checker's internal endpoints (SPEC §4.1, §9.4). One shared secret; nothing else."""

from __future__ import annotations

import json

from rest_framework.response import Response

from omega_hub.api.base import CheckerView, body, bytes_response
from omega_hub.errors import HubError
from omega_hub.services import checks

MAX_PART = 8 * 1024 * 1024


class ClaimView(CheckerView):
    def post(self, request):
        job = checks.claim(self.checker_id(request))
        if job is None:
            return Response(status=204)
        return Response(job)


class JobBundleView(CheckerView):
    previous = False

    def get(self, request, job_id):
        data = checks.job_bundle(job_id, self.checker_id(request), previous=self.previous)
        return bytes_response(data, "application/octet-stream", {"Cache-Control": "no-store"})


class JobPreviousBundleView(JobBundleView):
    previous = True


class JobContextView(CheckerView):
    def post(self, request, job_id):
        data = body(request)
        return Response(checks.context(job_id, self.checker_id(request), name=data.get("name"),
                                       version=data.get("version"), remix_of=data.get("remixOf")))


class JobHeartbeatView(CheckerView):
    def post(self, request, job_id):
        return Response(checks.heartbeat(job_id, self.checker_id(request)))


def _part(request, name: str) -> bytes | None:
    upload = request.FILES.get(name)
    if upload is not None:
        if upload.size > MAX_PART:
            raise HubError("OMEGA_HUB_REPORT_INVALID", 400, f"{name} is too large")
        return upload.read()
    value = request.data.get(name)
    return value.encode("utf-8") if isinstance(value, str) else None


class JobResultView(CheckerView):
    def post(self, request, job_id):
        raw = _part(request, "report")
        try:
            report = json.loads(raw.decode("utf-8")) if raw else None
        except (ValueError, UnicodeDecodeError) as exc:
            raise HubError("OMEGA_HUB_REPORT_INVALID", 400) from exc
        result = checks.result(job_id, self.checker_id(request), report=report, bundle=_part(request, "bundle"),
                               log=_part(request, "log"), cast=_part(request, "cast"))
        return Response(result)
