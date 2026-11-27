"""Run the normal ALK scenario writer against a frozen copy of the suite."""

import json
import logging

from django.conf import settings
from httpx import HTTPError

from simulate.services import hosted_harness_gateway as gateway
from simulate.services.harness_versioning import archives
from simulate.services.hosted_harness import HostedHarnessError

logger = logging.getLogger(__name__)


MISSING_SNAPSHOT = "source_world_snapshot_missing"


def missing_snapshot_error():
    return HostedHarnessError(
        MISSING_SNAPSHOT,
        "Rebuild this environment to create its world snapshot. Discard this draft first, then add the source again after the rebuild.",
        status_code=409,
    )


def author_additions(owner, files, pin, cases):
    if "manifest.json" not in files:
        raise missing_snapshot_error()
    provider = gateway.HostedHarnessGateway().client
    material = gateway._platform_simulator_material()
    env = {
        k: material[k]
        for k in (
            "AGENTCC_BASE_URL",
            "AGENTCC_API_KEY",
            "ALK_HARNESS",
            "ALK_HARNESS_MODEL",
            "ALK_CLAUDE_GATEWAY_URL",
            "ALK_CLAUDE_GATEWAY_API_KEY",
            "CLAUDE_CODE_USE_VERTEX",
        )
        if k in material
    }
    domains = gateway._authoring_egress_domains(env, "", provider_profile=False)
    gateway._validate_resolved_egress_domains(
        domains, max_domains=provider.max_egress_domains
    )
    ttl = gateway._authoring_ttl_seconds(provider.name)
    runtime = owner.payload["runtime"]
    spec = gateway.SandboxLaunchSpec(
        cpu_units=runtime["cpu_units"],
        memory_mb=runtime["memory_mb"],
        disk_gb=10,
        ttl_seconds=ttl,
        os_user=getattr(settings, "ALK_HOSTED_SANDBOX_OS_USER", "svc-control"),
        labels={
            "futureagi.job": str(owner.pk),
            "futureagi.authoring": "reviewed-append",
        },
        allowed_domains=tuple(sorted(domains)),
    )
    sandbox = provider.create(spec, timeout=provider.create_timeout_seconds)
    try:
        sandbox.fs.upload_file(archives.pack_archive(files), "/work/current-suite.tgz")
        sandbox.fs.upload_file(
            json.dumps(
                {
                    "pin": pin,
                    "evidence": {
                        "documents": [],
                        "records": [],
                        "cases": [
                            {k: v for k, v in case.items() if k != "proof_digest"}
                            for case in cases
                        ],
                    },
                }
            ).encode(),
            "/work/approved-cases.json",
        )
        overlay = gateway._alk_source_overlay()
        prepare = ""
        if overlay:
            sandbox.fs.upload_file(overlay, "/work/alk-src.tar.gz")
            prepare = "mkdir -p /work/alk-src && tar xzf /work/alk-src.tar.gz -C /work/alk-src && export PYTHONPATH=/work/alk-src && "
        result = sandbox.process.exec(
            prepare
            + "mkdir -p /work/current /work/additions && tar xzf /work/current-suite.tgz -C /work/current && "
            "python -m fi.alk.harness.reviewed_append /work/current /work/approved-cases.json /work/additions && "
            "tar czf /work/additions.tgz -C /work/additions .",
            env=env,
            timeout=ttl - 15,
        )
        if result.exit_code:
            raise HostedHarnessError(
                "source_writer_failed",
                "The ALK scenario writer could not prove the additions.",
                status_code=422,
            )
        return archives.read_archive(
            sandbox.fs.download_file("/work/additions.tgz", 300)
        )
    finally:
        # A provider cleanup outage must preserve both a downloaded result and
        # any original writer exception. The sandbox also has a bounded TTL.
        try:
            sandbox.delete()
        except (RuntimeError, OSError, HTTPError) as error:
            logger.warning(
                "Reviewed writer sandbox cleanup failed for job %s (%s)",
                owner.pk,
                type(error).__name__,
            )
