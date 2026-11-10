import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { copyFileSync, readFileSync, realpathSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { request } from '@playwright/test';
import type { TestInfo } from '@playwright/test';
import { expect, ScopeActors } from './scope-actors';
import type { StateProbe } from './state-probe';
import { POLL } from './state-probe';
import { E2E } from './env';
import { sendTrace } from './otlp';

const root = fileURLToPath(new URL('../../', import.meta.url));
const driver = `${root}e2e/drivers/error_feed_driver.py`;
const digest = (data: string | Buffer) => createHash('sha256').update(data).digest('hex');
interface Profile { project: string; service: string; apiUrl: string; env: Record<string, string> }

/** Separate entitlement/clock safety contract; never weaken managed-mock. */
export function validateErrorFeedProfile(profile: Profile) {
  const endpoint = new URL(profile.apiUrl);
  if (profile.project !== 'futureagi-th8613-e2e' || profile.service !== 'backend'
      || endpoint.hostname !== 'localhost' || endpoint.port !== '28113'
      || profile.env.ENV_TYPE !== 'local' || profile.env.E2E_ERROR_FEED_PROFILE !== 'th8613'
      || profile.env.PG_HOST !== 'postgres' || profile.env.TEMPORAL_HOST !== 'temporal:7233'
      || profile.env.ERROR_FEED_ESCALATION_MODE !== 'disabled'
      || profile.env.ERROR_FEED_GROUPING_ENABLED !== 'false') {
    throw new Error('STOP: Error Feed profile is not the dedicated local test stack');
  }
}

export interface FixtureTrace {
  trace_id: string; root_span_id: string; occurred_at: string; findings: number;
}
export interface FixtureProject { id: string; name: string; traces: FixtureTrace[] }
export interface IssueReceipt {
  id: string; cluster_id: string; status: string; status_origin: string;
  status_revision: number; state: Record<string, unknown>;
}
export interface PublicationReceipt {
  issue?: IssueReceipt; created_issue_ids: Record<string, string>;
  source_reports: { report_id: string; occurrence_ids: string[] }[];
  rejected?: string; reason?: string;
}
export interface ErrorFeedContainer {
  Id: string; Config: { Labels: Record<string, string>; Env: string[] };
  Mounts: { Type: string; Source: string; Destination: string; RW: boolean }[];
}

export function managedErrorFeedBackend(): ErrorFeedContainer {
  const ids = execFileSync('docker', ['ps', '-q', '--filter', 'label=com.docker.compose.project=futureagi-th8613-e2e',
    '--filter', 'label=com.docker.compose.service=backend'], { encoding: 'utf8' }).trim().split('\n').filter(Boolean);
  if (ids.length !== 1) throw new Error('STOP: Error Feed profile needs exactly one running managed backend');
  const container: ErrorFeedContainer = JSON.parse(execFileSync('docker', ['inspect', ids[0]], { encoding: 'utf8' }))[0];
  const env = Object.fromEntries(container.Config.Env.map(item => {
    const separator = item.indexOf('='); return [item.slice(0, separator), item.slice(separator + 1)];
  }));
  validateErrorFeedProfile({ project: container.Config.Labels['com.docker.compose.project'],
    service: container.Config.Labels['com.docker.compose.service'], apiUrl: E2E.apiUrl, env });
  return container;
}

export class ErrorFeedHarness {
  readonly base = new Date(Math.floor(Date.now() / 300_000) * 300_000);
  private readonly container: ErrorFeedContainer;
  readonly receipts: unknown[] = [];

  constructor(readonly scopes: ScopeActors, readonly probe: StateProbe, readonly info: TestInfo) {
    this.container = managedErrorFeedBackend();
    const mounted = this.container.Mounts.find(item => item.Destination === '/opt/e2e/error_feed_driver.py');
    if (!mounted || mounted.Type !== 'bind' || mounted.RW) throw new Error('STOP: Error Feed profile driver must be read-only');
    const stage = realpathSync(fileURLToPath(new URL('../..', `file://${mounted.Source}`)));
    if (stage !== realpathSync(root)) {
      const marker = JSON.parse(readFileSync(`${stage}/th8613-worktree.json`, 'utf8'));
      if (marker.profile !== 'th8613' || realpathSync(marker.source_root) !== realpathSync(root)) {
        throw new Error('STOP: Error Feed profile mount belongs to a different checkout');
      }
    }
    // Colima intentionally shares only /private/tmp: refresh this test-only
    // mounted script from the reviewed worktree, not any production module.
    if (realpathSync(driver) !== realpathSync(mounted.Source)) copyFileSync(driver, mounted.Source);
    const copied = execFileSync('docker', ['exec', this.container.Id, 'sha256sum', '/opt/e2e/error_feed_driver.py'], { encoding: 'utf8' }).split(/\s/)[0];
    if (copied !== digest(readFileSync(driver))) throw new Error('STOP: Error Feed profile driver digest differs');
    for (const module of ['tracer/services/feed_escalation.py', 'tracer/services/feed_escalation_policy.py',
      'tracer/services/feed_escalation_inputs.py', 'tracer/services/feed_escalation_publication.py']) {
      const actual = execFileSync('docker', ['exec', this.container.Id, 'sha256sum', `/app/backend/${module}`], { encoding: 'utf8' }).split(/\s/)[0];
      if (actual !== digest(readFileSync(`${root}futureagi/${module}`))) throw new Error('STOP: Error Feed profile backend is stale');
    }
  }

  at(minutes: number) { return new Date(this.base.getTime() + minutes * 60_000).toISOString(); }

  private async run<T>(project: FixtureProject, operation: string, minutes: number, values: Record<string, unknown> = {}): Promise<T> {
    console.info(`[Error Feed fixture] ${operation} at minute ${minutes}: ${project.name}`);
    const actor = this.scopes.ownerA;
    const input = { ...values, prefix: this.scopes.prefix, operation, now: this.at(minutes),
      project_id: project.id, workspace_id: actor.workspaceId, organization_id: actor.organizationId };
    const stdout = execFileSync('docker', ['exec', '-i', this.container.Id, 'python', '/opt/e2e/error_feed_driver.py'],
      { input: JSON.stringify(input), encoding: 'utf8', timeout: 180_000, maxBuffer: 8 * 1024 * 1024 });
    const lines = stdout.split('\n').filter(line => line.startsWith('TH8613_RECEIPT='));
    if (lines.length !== 1) throw new Error('Private source driver did not return exactly one publication receipt');
    const receipt = JSON.parse(lines[0].slice('TH8613_RECEIPT='.length)) as T;
    this.receipts.push({ operation, now: input.now, project_id: project.id, receipt });
    await this.info.attach(`driver-${operation}-${this.receipts.length}`, { contentType: 'application/json',
      body: JSON.stringify(this.receipts.at(-1)) });
    return receipt;
  }

  async ingest(projectName: string, findings: number, occurredAt: string, sessionId?: string): Promise<FixtureTrace> {
    const req = await request.newContext();
    try {
      const owner = this.scopes.owners[0];
      const end = new Date(occurredAt).getTime();
      const seed = await sendTrace(req, { collectorUrl: E2E.collectorUrl, apiKey: owner.apiKey,
        secretKey: owner.secretKey, projectName,
        startTimeUnixNano: BigInt(end - 50) * 1_000_000n, endTimeUnixNano: BigInt(end) * 1_000_000n,
        rootAttributes: { 'fi.span.output': 'requested=100 executed=10', ...(sessionId ? { 'session.id': sessionId } : {}) },
        ...(sessionId ? { childAttributes: { 'session.id': sessionId }, resourceAttributes: { project_type: 'observe' } } : {}) });
      await expect.poll(async () => {
        const rows = await this.probe.ch<{ n: string }>(
          'SELECT count() AS n FROM spans FINAL WHERE trace_id = {trace:String}', { trace: seed.traceId });
        return Number(rows[0].n);
      }, POLL.SPAN_VISIBLE).toBe(seed.spanIds.length);
      const fixture = { trace_id: seed.traceId, root_span_id: seed.spanIds[0], occurred_at: occurredAt, findings };
      await this.info.attach(`collector-${seed.traceId}`, { contentType: 'application/json', body: JSON.stringify(fixture) });
      return fixture;
    } finally { await req.dispose(); }
  }

  async project(label: string, findings = 20, offset = -20, sessionId?: string): Promise<FixtureProject> {
    const name = `${this.scopes.prefix}-${label}`;
    const trace = await this.ingest(name, findings, this.at(offset), sessionId);
    const rows = await this.probe.pg<{ id: string; workspace_id: string }>(
      'SELECT id, workspace_id FROM tracer_project WHERE name=$1 AND organization_id=$2',
      [name, this.scopes.ownerA.organizationId]);
    expect(rows).toHaveLength(1);
    expect(rows[0].workspace_id).toBe(this.scopes.ownerA.workspaceId);
    return { id: rows[0].id, name, traces: [trace] };
  }

  publish(project: FixtureProject, options: { target_issue_id?: string; traces?: FixtureTrace[];
    now?: number; title?: string; allow_rejection?: boolean; prepare_only?: boolean } = {}) {
    return this.run<PublicationReceipt>(project, 'publish', options.now ?? 0,
      { ...options, traces: options.traces ?? project.traces,
        title: options.title ?? `Refund amount mismatch ${project.name}` });
  }

  evaluate(project: FixtureProject, minutes: number) {
    return this.run<{ workflow_id: string; result: { status: string;
      result: { status: string; result: Record<string, number> } } }>(project, 'evaluate', minutes);
  }

  qualifySession(project: FixtureProject, sessionId: string) {
    return this.run<{ occurred_at: string; provenance: string; traces_count: number }>(
      project, 'qualify_session', 10, { session_id: sessionId });
  }

  publishLegacy(project: FixtureProject, source: 'scanner' | 'eval', traces = project.traces) {
    return this.run<{ issue: IssueReceipt; source_ids: string[] }>(project, 'publish_legacy', 0,
      { source, traces, title: `Refund amount mismatch ${project.name}` });
  }

  reconcile(project: FixtureProject, kind: 'merge' | 'split' | 'remove', issueIds: string[], minutes: number) {
    return this.run<{ issues: IssueReceipt[]; removed_ids: string[] }>(project, 'reconcile', minutes,
      { kind, issue_ids: issueIds });
  }
}
