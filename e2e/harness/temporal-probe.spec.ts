import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { parseScheduleDescription, parseScheduleList, TemporalProbe } from '../lib/temporal-probe';

// TemporalProbe's own tests. Offline: the CLI's real `--output json` (captured from the
// Standalone image's temporal 1.9.1 into fixtures/temporal/schedule-describe.json)
// and a recorded exec. Live (E2E_STACK=standalone): the product's schedules.

const describeJson = readFileSync(new URL('../fixtures/temporal/schedule-describe.json', import.meta.url), 'utf8');

test('temporal probe offline: reads interval, queue, workflow, activity, pause and action count from the CLI JSON', () => {
  expect(parseScheduleDescription('gw-check-a', describeJson)).toEqual({ id: 'gw-check-a', paused: false,
    intervalSeconds: 60, taskQueue: 'default', workflowType: 'TaskRunnerWorkflow',
    activityName: 'purge_expired_agentcc_request_logs_activity', actionCount: 0 });
  const paused = JSON.parse(describeJson);
  paused.schedule.state = { notes: 'e2e', paused: true };
  paused.info.actionCount = '3';
  expect(parseScheduleDescription('gw-check-a', JSON.stringify(paused))).toMatchObject({ paused: true, actionCount: 3 });
});

test('temporal probe offline: an input that is not one json/plain TaskRunnerInput throws', () => {
  for (const mutate of [
    (d: any) => { d.schedule.action.startWorkflow.input.payloads = []; },
    (d: any) => { d.schedule.action.startWorkflow.input.payloads[0].metadata.encoding = Buffer.from('binary/null').toString('base64'); },
    (d: any) => { d.schedule.action.startWorkflow.input.payloads[0].data = Buffer.from('{"args":[]}').toString('base64'); },
    (d: any) => { d.schedule.spec.interval.push({ interval: '30s' }); },
    (d: any) => { delete d.schedule.action; },
  ]) {
    const d = JSON.parse(describeJson); mutate(d);
    expect(() => parseScheduleDescription('gw-check-a', JSON.stringify(d))).toThrow('temporal probe:');
  }
});

test('temporal probe offline: list ids, sorted; the exact CLI and product commands, never another address', async () => {
  expect(parseScheduleList('[\n{"scheduleId":"b","info":{}},{"scheduleId":"a"}\n]')).toEqual(['a', 'b']);
  expect(parseScheduleList('[\n]')).toEqual([]);
  const calls: string[][] = [];
  const probe = new TemporalProbe((argv) => {
    calls.push(argv);
    if (argv[2] === 'list') return '[{"scheduleId":"x"}]';
    if (argv[2] === 'describe') return describeJson;
    if (argv.includes('--list')) return 'Listing all Temporal schedules...\n\nFound 2 schedules:\n  - b\n  - a\n';
    if (argv[1] === '-c') return `updated ${argv[3]} ${argv[4]}\n`;
    return '';
  });
  await probe.listSchedules();
  await probe.describeSchedule('agentcc-log-retention');
  await probe.pauseSchedule('s1');
  await probe.unpauseSchedule('s1');
  await probe.triggerSchedule('s1');
  await probe.deleteSchedule('s1');
  await probe.registerSchedules();
  expect(await probe.productList()).toEqual(['a', 'b']);
  await probe.setIntervalViaManager('s1', 120);
  const at = ['--address', '127.0.0.1:7233', '--namespace', 'default'];
  expect(calls.slice(0, 6)).toEqual([
    ['temporal', 'schedule', 'list', '--output', 'json', ...at],
    ['temporal', 'schedule', 'describe', '--output', 'json', '--time-format', 'iso', '--schedule-id', 'agentcc-log-retention', ...at],
    ['temporal', 'schedule', 'toggle', '--pause', '--reason', 'e2e', '--schedule-id', 's1', ...at],
    ['temporal', 'schedule', 'toggle', '--unpause', '--reason', 'e2e', '--schedule-id', 's1', ...at],
    ['temporal', 'schedule', 'trigger', '--schedule-id', 's1', ...at],
    ['temporal', 'schedule', 'delete', '--schedule-id', 's1', ...at],
  ]);
  expect(calls[6]).toEqual(['env', 'NO_STARTUP_DB_MUTATIONS=false', 'python', 'manage.py', 'register_temporal_schedules']);
  expect(calls[7]).toEqual(['env', 'NO_STARTUP_DB_MUTATIONS=false', 'python', 'manage.py', 'register_temporal_schedules', '--list']);
  expect([calls[8][0], calls[8][1], calls[8][3], calls[8][4]]).toEqual(['python', '-c', 's1', '120']);
  await expect(probe.describeSchedule('bad id; rm')).rejects.toThrow('bad schedule id');
});

// tfc/temporal/schedules/agentcc_retention.py: a schedule no gateway flow uses, so
// pausing, deleting and re-registering it disturbs no flow.
const RETENTION = { id: 'agentcc-log-retention', intervalSeconds: 86400, taskQueue: 'default',
  workflowType: 'TaskRunnerWorkflow', activityName: 'purge_expired_agentcc_request_logs_activity' };

test('temporal probe live: the product\'s schedules, pause, delete, re-register and an interval change', async () => {
  test.skip(process.env.E2E_STACK !== 'standalone', 'the probe reaches Temporal inside the Standalone app');
  test.setTimeout(240_000);
  const probe = new TemporalProbe();
  const listed = await probe.listSchedules();
  expect(listed).toContain(RETENTION.id);
  expect(listed).not.toContain(`e2e-probe-absent-${Date.now().toString(36)}`);
  expect(listed).toEqual(await probe.productList());
  expect(await probe.describeSchedule(RETENTION.id)).toMatchObject({ ...RETENTION, paused: false });

  await probe.pauseSchedule(RETENTION.id);
  expect((await probe.describeSchedule(RETENTION.id)).paused).toBe(true);
  await probe.unpauseSchedule(RETENTION.id);
  expect((await probe.describeSchedule(RETENTION.id)).paused).toBe(false);

  await probe.deleteSchedule(RETENTION.id);
  await expect(probe.describeSchedule(RETENTION.id)).rejects.toThrow();
  await expect.poll(() => probe.listSchedules(), { timeout: 30_000 }).not.toContain(RETENTION.id);
  await probe.registerSchedules();
  expect(await probe.describeSchedule(RETENTION.id)).toMatchObject({ ...RETENTION, paused: false });

  // A changed config through the product's manager, then registration again: it
  // updates an existing schedule (manager.py a_create_or_update_schedule), so the
  // config's interval comes back. GW-E2E-013 relies on this.
  await probe.setIntervalViaManager(RETENTION.id, 43200);
  expect((await probe.describeSchedule(RETENTION.id)).intervalSeconds).toBe(43200);
  await probe.registerSchedules();
  expect((await probe.describeSchedule(RETENTION.id)).intervalSeconds).toBe(RETENTION.intervalSeconds);
});
