// Look up, pause, trigger, delete and re-register the product's Temporal
// schedules on the Standalone stack, where Temporal listens on 127.0.0.1:7233
// inside `app` only (deploy/standalone/supervisord.conf [program:temporal]). It
// runs the Temporal CLI the image ships (deploy/standalone/bin/verify-binaries;
// 1.9.1 when this was written) and the product's own `manage.py
// register_temporal_schedules` in that container, through `bin/e2e compose exec`,
// which uses DOCKER_HOST from the environment (never a Docker context).
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
const TEMPORAL = ['--address', '127.0.0.1:7233', '--namespace', 'default'];
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

/** Runs a command in the Standalone `app` container and returns its stdout. */
export type AppExec = (argv: string[]) => string;

export const composeExec: AppExec = (argv) => {
  if (process.env.E2E_STACK !== 'standalone') throw new Error('temporal probe: Standalone stack only (E2E_STACK=standalone)');
  const env = { ...process.env };
  delete env.DOCKER_CONTEXT; // DOCKER_HOST from the environment only, never a context
  return execFileSync(`${root}bin/e2e`, ['compose', 'exec', '-T', '-w', '/app/backend', 'app', ...argv],
    { encoding: 'utf8', env, timeout: 120_000, maxBuffer: 8 * 1024 * 1024, stdio: ['ignore', 'pipe', 'pipe'] });
};

export class TemporalProbe {
  constructor(private readonly exec: AppExec = composeExec) {}

  private temporal(sub: string[], id?: string): string {
    if (id !== undefined && !SCHEDULE_ID.test(id)) throw new Error(`temporal probe: bad schedule id ${JSON.stringify(id)}`);
    return this.exec(['temporal', 'schedule', ...sub, ...(id === undefined ? [] : ['--schedule-id', id]), ...TEMPORAL]);
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
    return this.exec(PRODUCT_COMMAND);
  }

  /** Re-registers one ALL_SCHEDULES entry with another interval through the
   *  product's own manager (a_create_or_update_schedule, which updates an existing
   *  schedule and keeps its state), as a deploy that changed the config would. */
  async setIntervalViaManager(id: string, seconds: number): Promise<void> {
    if (!SCHEDULE_ID.test(id) || !(Number.isInteger(seconds) && seconds > 0)) throw new Error('temporal probe: bad interval change');
    const out = this.exec(['python', '-c', MANAGER_UPDATE, id, String(seconds)]);
    if (!out.includes(`updated ${id} ${seconds}`)) throw new Error(`temporal probe: interval change not confirmed for ${id}`);
  }

  /** The product's own `--list`: the schedule ids it prints, sorted. */
  async productList(): Promise<string[]> {
    const out = this.exec([...PRODUCT_COMMAND, '--list']);
    return out.split('\n').map((l) => /^ {2}- (\S+)$/.exec(l)?.[1]).filter((x): x is string => !!x).sort();
  }
}
