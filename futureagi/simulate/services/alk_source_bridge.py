from __future__ import annotations

import json

from django.conf import settings

from simulate.services.hosted_harness import HostedHarnessError
from simulate.services.hosted_harness_gateway import _alk_source_overlay
from simulate.services.hosted_sandbox import SandboxLaunchSpec, get_sandbox_provider

MAX_RESPONSE_BYTES = 512 * 1024 * 1024
RESPONSE_VERSION = "environment-sources.response.v1"


def _validated_response(response: object, operation: object) -> dict:
    if (
        not isinstance(response, dict)
        or response.get("version") != RESPONSE_VERSION
        or not isinstance(response.get("ok"), bool)
        or response.get("operation") != operation
        or (response["ok"] and not isinstance(response.get("result"), dict))
        or (not response["ok"] and not isinstance(response.get("error"), dict))
    ):
        raise HostedHarnessError(
            "source_processor_invalid",
            "Environment source processing returned an invalid response",
            status_code=502,
        )
    return response


def execute_source_request(payload: dict) -> dict:
    operation = payload.get("operation")
    sandbox_url = str(getattr(settings, "ALK_HARNESS_SANDBOX_URL", "") or "").strip()
    if sandbox_url:
        from simulate.services.harness_sandbox import HarnessSandboxClient

        try:
            return _validated_response(
                HarnessSandboxClient(
                    sandbox_url,
                    getattr(settings, "ALK_HARNESS_SANDBOX_TOKEN", "") or None,
                ).environment_sources(payload),
                operation,
            )
        except HostedHarnessError:
            raise
        except Exception as exc:
            raise HostedHarnessError(
                "source_processor_unavailable",
                "Environment source processing is temporarily unavailable",
                status_code=503,
                retryable=True,
            ) from exc
    request = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    try:
        client = get_sandbox_provider()
    except Exception as exc:
        raise HostedHarnessError(
            "source_processor_unavailable",
            "Environment source processing is temporarily unavailable",
            status_code=503,
            retryable=True,
        ) from exc
    sandbox = None
    try:
        sandbox = client.create(
            SandboxLaunchSpec(
                cpu_units=1,
                memory_mb=1024,
                disk_gb=2,
                ttl_seconds=300,
                os_user=getattr(settings, "ALK_HOSTED_SANDBOX_OS_USER", "svc-control"),
                labels={"futureagi.environment-sources": "1"},
                runtime_name=client.runtime_name,
            ),
            timeout=client.create_timeout_seconds,
        )
        sandbox.fs.upload_file(request, "/work/source-request.json")
        overlay = _alk_source_overlay()
        command = ""
        env = None
        if overlay is not None:
            sandbox.fs.upload_file(overlay, "/work/alk-src.tar.gz")
            command = (
                "mkdir -p /work/alk-src && "
                "tar -xzf /work/alk-src.tar.gz -C /work/alk-src && "
            )
            env = {"PYTHONPATH": "/work/alk-src"}
        run = sandbox.process.exec(
            command + "python -m fi.alk.studio.environment_sources "
            "/work/source-request.json > /work/source-response.json",
            env=env,
            timeout=240,
        )
        body = sandbox.fs.download_file("/work/source-response.json", 30)
        if len(body) > MAX_RESPONSE_BYTES:
            raise ValueError("response too large")
        response = json.loads(body)
        if run.exit_code not in {0, 1} or not isinstance(response, dict):
            raise ValueError("invalid response")
        return _validated_response(response, operation)
    except HostedHarnessError:
        raise
    except Exception as exc:
        raise HostedHarnessError(
            "source_processor_unavailable",
            "Environment source processing is temporarily unavailable",
            status_code=503,
            retryable=True,
        ) from exc
    finally:
        if sandbox is not None:
            try:
                sandbox.delete()
            except Exception:
                pass
