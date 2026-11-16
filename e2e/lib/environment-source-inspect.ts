import { execFileSync } from 'node:child_process';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { resolveBackendContainer } from './simulate-seed';

const SCRIPT = String.raw`
import json, os
from types import SimpleNamespace
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tfc.settings.settings")
import django
django.setup()
from simulate.models import HarnessEnvironmentVersion, HostedHarnessJob
from simulate.services import harness_sources
from simulate.services.harness_versioning import archives

job = HostedHarnessJob.no_workspace_objects.get(id=os.environ["ENV_ID"])
version_id = os.environ.get("VERSION_ID") or str(job.environment_version_id)
version = HarnessEnvironmentVersion.no_workspace_objects.get(id=version_id)
run = SimpleNamespace(environment_id=job.id, organization_id=job.organization_id, environment_version=version)
evaluator = harness_sources.resolve_evaluator_sources(run)
files = archives.read_archive(archives.get_archive(version.archive_object_key))
agent = json.loads(files.get("sources/tested-agent.json", b"[]"))
print(json.dumps({"evaluator": evaluator, "agent": agent}))
`;

export function inspectEnvironmentSources(environmentId: string, versionId?: string) {
  const container = resolveBackendContainer();
  const dir = mkdtempSync(join(tmpdir(), 'e2e-t20-inspect-'));
  const script = join(dir, 'inspect.py');
  writeFileSync(script, SCRIPT);
  try {
    execFileSync('docker', ['cp', script, `${container}:/tmp/t20-inspect.py`]);
    const args = ['exec', '-e', `ENV_ID=${environmentId}`];
    if (versionId) args.push('-e', `VERSION_ID=${versionId}`);
    args.push(container, 'sh', '-c', 'cd /app/backend && PYTHONPATH=/app/backend python /tmp/t20-inspect.py');
    const stdout = execFileSync('docker', args, { encoding: 'utf-8' });
    return JSON.parse(stdout.trim().split('\n').pop() ?? '') as { evaluator: unknown; agent: unknown };
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}
