// Look up, pause, trigger, delete and re-register the product's Temporal
// schedules. It runs the Temporal CLI inside the stack's own Temporal container,
// through `bin/e2e compose exec`, which uses DOCKER_HOST from the environment
// (never a Docker context), on the stack bin/e2e manages (E2E_STACK):
// - Standalone (E2E_STACK=standalone): Temporal listens on 127.0.0.1:7233 inside
//   `app` only (deploy/standalone/supervisord.conf [program:temporal]), and the CLI
//   is the one the image ships (deploy/standalone/bin/verify-binaries; the fixture
//   below is its 1.9.1 output). The product's own `manage.py
//   register_temporal_schedules` and the manager's interval change run in that
//   container too, so those commands are Standalone only.
// - Distributed (the default): Temporal is the `temporal` service
//   (docker-compose.distributed.yml, temporalio/auto-setup, which ships the
//   `temporal` CLI with the same `--output json`). Its frontend listens on the
//   container's own address, the one `temporal:7233` resolves to and the backend's
//   TEMPORAL_HOST names, so every CLI command runs there with that address.
//
// Output shapes are the CLI's `--output json` (proto JSON), captured from the
// image into e2e/fixtures/temporal/schedule-describe.json: the action's input is
// one base64 `json/plain` payload, the product's TaskRunnerInput
// (tfc/temporal/schedules/manager.py _build_schedule_for_config); `state.paused`
// is omitted unless true; `info.actionCount` is an int64 string, omitted at 0.
//
// The product's `manage.py register_temporal_schedules` refuses to run in a plain
// `docker compose exec`: the container keeps NO_STARTUP_DB_MUTATIONS=true there
// (deploy/standalone/bin/bootstrap.py:25-27), and model_hub/apps.py ready() raises
// "register_temporal_schedules is disabled during mutation-free startup" unless
// the command is explicitly authorized (explicit_management_mutation_authorized).
// The Standalone bootstrap registers the schedules with NO_STARTUP_DB_MUTATIONS=false
// (bootstrap.py:518, the explicit local mode `./bin/dev manage` runs), so the probe
// runs the product's command in that same mode. `python -c` (the interval change)
// is no management command, so the guard does not apply to it.
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../../', import.meta.url));

/** The stacks bin/e2e manages. */
export type E2EStack = 'standalone' | 'distributed';
/** The container a command runs in, and its working directory there (null: the image's own). */
export interface ExecTarget { service: string; workdir: string | null }

/** Each stack's Temporal container and the frontend address the CLI dials inside it (see the header). */
const TEMPORAL_CLI: Record<E2EStack, ExecTarget & { address: string }> = {
  standalone: { service: 'app', workdir: '/app/backend', address: '127.0.0.1:7233' },
  distributed: { service: 'temporal', workdir: null, address: 'temporal:7233' },
};
/** The product's own commands: the Standalone `app`, in the backend's directory. */
const PRODUCT_TARGET: ExecTarget = { service: 'app', workdir: '/app/backend' };

/** The stack bin/e2e manages: E2E_STACK=standalone, otherwise Distributed (its default). */
const currentStack = (): E2EStack => (process.env.E2E_STACK === 'standalone' ? 'standalone' : 'distributed');

const SCHEDULE_ID = /^[A-Za-z0-9_.:-]{1,200}$/;
const PRODUCT_COMMAND = ['env', 'NO_STARTUP_DB_MUTATIONS=false', 'python', 'manage.py', 'register_temporal_schedules'];
// Ends with os._exit, as register_temporal_schedules does: the SDK's thread aborts
// the interpreter's normal teardown.
const MANAGER_UPDATE = [
  'import asyncio, dataclasses, os, sys', 'import django', 'django.setup()',
  'from tfc.temporal import ALL_SCHEDULES', 'from tfc.temporal.common.client import get_client',
  'from tfc.temporal.schedules.manager import a_create_or_update_schedule',
  'sid, seconds = sys.argv[1], int(sys.argv[2])', 'cfg = next(c for c in ALL_SCHEDULES if c.schedule_id == sid)',
  'async def main():', '    await a_create_or_update_schedule(await get_client(), dataclasses.replace(cfg, interval_seconds=seconds))',
  'asyncio.run(main())', 'print("updated", sid, seconds)', 'sys.stdout.flush()', 'os._exit(0)',
].join('\n');

export interface ScheduleDescription {
  id: string; paused: boolean; intervalSeconds: number | null; taskQueue: string;
  workflowType: string; activityName: string | null; actionCount: number;
}

const b64 = (value: string) => Buffer.from(value, 'base64').toString('utf8');

/** Proto JSON Duration ("60s", "0.5s") in seconds. */
export function durationSeconds(value: unknown): number {
  const match = /^(\d+(?:\.\d+)?)s$/.exec(String(value));
  if (!match) throw new Error(`temporal probe: not a proto duration ${JSON.stringify(value)}`);
  return Number(match[1]);
}

/** `temporal schedule describe --output json` -> the fields a flow asserts. */
export function parseScheduleDescription(id: string, raw: string): ScheduleDescription {
  const d = JSON.parse(raw);
  const action = d?.schedule?.action?.startWorkflow;
  if (!action) throw new Error(`temporal probe: ${id} has no start-workflow action`);
  const intervals: { interval: string }[] = d.schedule.spec?.interval ?? [];
  if (intervals.length > 1) throw new Error(`temporal probe: ${id} has ${intervals.length} intervals`);
  const payloads: { metadata?: { encoding?: string }; data?: string }[] = action.input?.payloads ?? [];
  let activityName: string | null = null;
  if (action.workflowType?.name === 'TaskRunnerWorkflow') {
    // One TaskRunnerInput, encoded json/plain by the Python SDK's default converter.
    if (payloads.length !== 1 || b64(payloads[0].metadata?.encoding ?? '') !== 'json/plain') {
      throw new Error(`temporal probe: ${id}'s TaskRunnerWorkflow input is not one json/plain payload`);
    }
    activityName = JSON.parse(b64(payloads[0].data ?? '')).activity_name;
    if (typeof activityName !== 'string' || !activityName) throw new Error(`temporal probe: ${id} names no activity`);
  }
  return {
    id, paused: d.schedule.state?.paused === true,
    intervalSeconds: intervals.length ? durationSeconds(intervals[0].interval) : null,
    taskQueue: action.taskQueue?.name ?? '', workflowType: action.workflowType?.name ?? '', activityName,
    actionCount: Number(d.info?.actionCount ?? 0),
  };
}

/** `temporal schedule list --output json` -> the schedule ids, sorted. */
export function parseScheduleList(raw: string): string[] {
  const rows: { scheduleId?: unknown }[] = JSON.parse(raw);
  if (!Array.isArray(rows) || rows.some((r) => typeof r.scheduleId !== 'string')) {
    throw new Error('temporal probe: unexpected schedule list');
  }
  return rows.map((r) => r.scheduleId as string).sort();
}

/** Runs a command in one container of the stack bin/e2e manages and returns its stdout. */
export type StackExec = (argv: string[], target: ExecTarget) => string;

export const composeExec: StackExec = (argv, { service, workdir }) => {
  const env = { ...process.env };
  delete env.DOCKER_CONTEXT; // DOCKER_HOST from the environment only, never a context
  return execFileSync(`${root}bin/e2e`, ['compose', 'exec', '-T', ...(workdir === null ? [] : ['-w', workdir]), service, ...argv],
    { encoding: 'utf8', env, timeout: 120_000, maxBuffer: 8 * 1024 * 1024, stdio: ['ignore', 'pipe', 'pipe'] });
};

export class TemporalProbe {
  private readonly cli: ExecTarget & { address: string };

  /** `stack`: whose containers it runs in; by default the stack bin/e2e manages. */
  constructor(private readonly exec: StackExec = composeExec, private readonly stack: E2EStack = currentStack()) {
    this.cli = TEMPORAL_CLI[stack];
  }

  private temporal(sub: string[], id?: string): string {
    if (id !== undefined && !SCHEDULE_ID.test(id)) throw new Error(`temporal probe: bad schedule id ${JSON.stringify(id)}`);
    const { service, workdir, address } = this.cli;
    return this.exec(['temporal', 'schedule', ...sub, ...(id === undefined ? [] : ['--schedule-id', id]),
      '--address', address, '--namespace', 'default'], { service, workdir });
  }

  /** The product's own commands run in the Standalone `app` only (see the header). */
  private product(argv: string[]): string {
    if (this.stack !== 'standalone') {
      throw new Error('temporal probe: the product\'s schedule commands run on the Standalone stack only (E2E_STACK=standalone)');
    }
    return this.exec(argv, PRODUCT_TARGET);
  }

  /** Every schedule id, sorted (Temporal's visibility list: a new schedule can take a moment to appear). */
  async listSchedules(): Promise<string[]> { return parseScheduleList(this.temporal(['list', '--output', 'json'])); }

  /** Throws when the schedule does not exist (the CLI exits 1, "schedule not found"). */
  async describeSchedule(id: string): Promise<ScheduleDescription> {
    return parseScheduleDescription(id, this.temporal(['describe', '--output', 'json', '--time-format', 'iso'], id));
  }

  async triggerSchedule(id: string): Promise<void> { this.temporal(['trigger'], id); }
  async pauseSchedule(id: string): Promise<void> { this.temporal(['toggle', '--pause', '--reason', 'e2e'], id); }
  async unpauseSchedule(id: string): Promise<void> { this.temporal(['toggle', '--unpause', '--reason', 'e2e'], id); }
  async deleteSchedule(id: string): Promise<void> { this.temporal(['delete'], id); }

  /** The product's own registration of every ALL_SCHEDULES entry (no flag). */
  async registerSchedules(): Promise<string> {
    return this.product(PRODUCT_COMMAND);
  }

  /** Re-registers one ALL_SCHEDULES entry with another interval through the
   *  product's own manager (a_create_or_update_schedule, which updates an existing
   *  schedule and keeps its state), as a deploy that changed the config would. */
  async setIntervalViaManager(id: string, seconds: number): Promise<void> {
    if (!SCHEDULE_ID.test(id) || !(Number.isInteger(seconds) && seconds > 0)) throw new Error('temporal probe: bad interval change');
    const out = this.product(['python', '-c', MANAGER_UPDATE, id, String(seconds)]);
    if (!out.includes(`updated ${id} ${seconds}`)) throw new Error(`temporal probe: interval change not confirmed for ${id}`);
  }

  /** The product's own `--list`: the schedule ids it prints, sorted. */
  async productList(): Promise<string[]> {
    const out = this.product([...PRODUCT_COMMAND, '--list']);
    return out.split('\n').map((l) => /^ {2}- (\S+)$/.exec(l)?.[1]).filter((x): x is string => !!x).sort();
  }
}
