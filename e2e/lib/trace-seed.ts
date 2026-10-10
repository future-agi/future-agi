import { randomBytes } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { resolveBackendContainer } from './simulate-seed';

/**
 * Registers OTLP-seeded traces as Postgres `tracer_trace` rows.
 *
 * The trace tag endpoint (`PATCH /tracer/trace/{id}/tags/`, `TraceView.update_tags`)
 * reads and writes `tracer_trace`. The collector this harness sends OTLP through
 * writes ClickHouse only, so a collector-seeded trace has no row there. The
 * Django ingestion path does create one (`tracer/utils/create_otel_span.py`,
 * `Trace.objects.get_or_create(id=..., defaults={"project": ...})`) and then
 * mirrors it into ClickHouse `traces` with
 * `trace_writer.mirror_traces_to_clickhouse`, the table the trace list reads
 * tags from. This does the same two steps through the real Django code inside
 * the running backend container, the way `simulate-seed.ts` seeds call
 * executions, and fails if the stack has that mirror switched off.
 *
 * Scoped per call to the caller's own project and organization.
 */
export interface PgTraceSeed { id: string; name: string; tags: string[] }

const SEED_SCRIPT = `
import json
import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tfc.settings.settings")
import django
django.setup()

from tracer.models.project import Project
from tracer.models.trace import Trace
from tracer.services.clickhouse.v2.trace_writer import (
    dual_write_enabled,
    mirror_traces_to_clickhouse,
)


def main():
    if not dual_write_enabled():
        raise SystemExit("trace mirror to ClickHouse is off in this stack (CH25_TRACE_DUAL_WRITE / CH25_DROP_LEGACY_CDC_CHAIN)")
    spec = json.loads(os.environ["SEED_TRACES"])
    project = Project.objects.get(
        id=spec["projectId"], organization_id=spec["organizationId"]
    )
    out = []
    for item in spec["traces"]:
        trace, created = Trace.objects.get_or_create(
            id=item["id"],
            defaults={"project": project, "name": item["name"], "tags": item["tags"]},
        )
        if not created:
            raise SystemExit(f"trace {item['id']} already had a Postgres row")
        out.append({"id": str(trace.id), "tags": trace.tags})
    mirror_traces_to_clickhouse([item["id"] for item in out])
    print(json.dumps(out))


main()
`;

export function seedPgTraces(opts: {
  organizationId: string;
  projectId: string;
  traces: PgTraceSeed[];
}): { id: string; tags: string[] }[] {
  const container = resolveBackendContainer();
  const suffix = `${Date.now().toString(36)}-${randomBytes(3).toString('hex')}`;
  const dir = mkdtempSync(join(tmpdir(), 'e2e-trace-seed-'));
  const scriptPath = join(dir, `trace-seed-${suffix}.py`);
  writeFileSync(scriptPath, SEED_SCRIPT);
  try {
    execFileSync('docker', ['cp', scriptPath, `${container}:/tmp/trace-seed-${suffix}.py`]);
    const stdout = execFileSync('docker', ['exec',
      '-e', `SEED_TRACES=${JSON.stringify({
        organizationId: opts.organizationId, projectId: opts.projectId, traces: opts.traces })}`,
      container, 'sh', '-c',
      `cd /app/backend && PYTHONPATH=/app/backend python /tmp/trace-seed-${suffix}.py`,
    ], { encoding: 'utf-8' });
    const lastLine = stdout.trim().split('\n').pop() ?? '';
    return JSON.parse(lastLine) as { id: string; tags: string[] }[];
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}
