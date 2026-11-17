import { requireId } from '../../_shared/identifiers';
import type { ScheduleIdentity } from './catalog';

/** Value-only readers. NO import of TemporalProbe: its default executor shells
 * through bin/e2e and would create a second dispatcher. Wire provenance at
 * da4c81cc623f909ce38f53e01b557ee8374d5adb:
 * e2e/lib/temporal-probe.ts:parseScheduleList/parseScheduleDescription;
 * e2e/fixtures/temporal/schedule-describe.json (Temporal CLI 1.9.1 capture);
 * tfc/temporal/schedules/manager.py:_build_schedule_for_config.
 * The accepted runtime obtains originals inside its container-ID-bound policy.
 * Only allowlisted non-secret identity fields leave these readers. No input,
 * metadata, action args, headers or raw errors are returned or logged.
 */
export interface ScheduleFact extends ScheduleIdentity {
  workflowId: string;
  paused: boolean;
  actionCount: string; // Proto int64: never round a counter via Number().
}
export function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Temporal object missing');
  return value as Record<string, unknown>;
}
export function id(value: unknown): string {
  if (typeof value !== 'string') throw new Error('Temporal identity missing');
  return requireId(value);
}
export function integer(value: unknown): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 0) throw new Error('Temporal integer missing');
  return value;
}
function json(raw: string): unknown {
  if (typeof raw !== 'string' || Buffer.byteLength(raw) > 8 * 1024 * 1024) throw new Error('Temporal JSON exceeds reader bound');
  try { return JSON.parse(raw); } catch { throw new Error('Temporal JSON malformed'); }
}
function plainPayload(value: unknown): string {
  if (typeof value !== 'string' || value.length > 16384 || value.length % 4 !== 0 ||
      !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(value)) {
    throw new Error('Temporal payload is not canonical base64');
  }
  const bytes = Buffer.from(value, 'base64');
  if (bytes.toString('base64') !== value) throw new Error('Temporal base64 roundtrip failed');
  try { return new TextDecoder('utf-8', { fatal: true }).decode(bytes); }
  catch { throw new Error('Temporal payload is not UTF8'); }
}

export function parseScheduleList(raw: string): string[] {
  const rows = json(raw);
  if (!Array.isArray(rows) || rows.length > 1024) throw new Error('Temporal list shape missing');
  // Deliberately retain duplicate and extra identities for the exact-bag oracle.
  return rows.map(row => id(object(row).scheduleId)).sort();
}

export function parseSchedule(scheduleId: string, raw: string): ScheduleFact {
  id(scheduleId);
  const body = object(json(raw));
  const schedule = object(body.schedule);
  const action = object(object(schedule.action).startWorkflow);
  const taskQueue = id(object(action.taskQueue).name);
  const workflowType = id(object(action.workflowType).name);
  const workflowId = id(action.workflowId);
  // Proto JSON omits default false and zero, but the state/info objects must
  // exist in the captured describe shape. Null and wrong types are not defaults.
  const state = object(schedule.state);
  if (state.paused !== undefined && typeof state.paused !== 'boolean') throw new Error('Temporal paused shape missing');
  const rawCount = object(body.info).actionCount;
  const actionCount = rawCount === undefined ? '0' : rawCount;
  if (typeof actionCount !== 'string' || !/^(0|[1-9][0-9]*)$/.test(actionCount) ||
      actionCount.length > 19 || BigInt(actionCount) > 9223372036854775807n) throw new Error('Temporal action count invalid');
  let activityName: string | null = null;
  if (workflowType === 'TaskRunnerWorkflow') {
    const payloads = object(action.input).payloads;
    if (!Array.isArray(payloads) || payloads.length !== 1) throw new Error('Temporal TaskRunner input missing');
    const payload = object(payloads[0]);
    if (plainPayload(object(payload.metadata).encoding) !== 'json/plain') throw new Error('Temporal encoding unsupported');
    const input = object(json(plainPayload(payload.data)));
    activityName = id(input.activity_name);
    if (id(input.queue) !== taskQueue) throw new Error('Temporal input queue differs from action');
  }
  return { id: scheduleId, taskQueue, workflowType, workflowId, activityName,
    paused: state.paused === true, actionCount };
}

export const sameBag = (left: readonly string[], right: readonly string[]) =>
  JSON.stringify([...left].sort()) === JSON.stringify([...right].sort());

/** A02 exact inventory detector. Compare ALL list rows and ALL independently
 * described actions; never prefilter to a gateway subset or deduplicate a list.
 * Paused is deliberately not an A02 assertion: manager preserves operator state.
 * Polling/actionCount cannot substitute for A03's live worker/history/cohort join.
 */
export function scheduleIssues(expected: readonly ScheduleIdentity[], listed: readonly string[], described: readonly ScheduleFact[]): string[] {
  if (!expected.length || new Set(expected.map(r => r.id)).size !== expected.length) throw new Error('Empty or duplicate expected inventory');
  const wanted = expected.map(row => row.id);
  const issues: string[] = [];
  if (!sameBag(wanted, listed)) issues.push('A02:schedule-id-set');
  if (!sameBag(wanted, described.map(row => row.id))) issues.push('A02:description-id-set');
  for (const actual of described) {
    const target = expected.find(row => row.id === actual.id);
    if (!target) continue; // Already an exact-set failure; no unexpected input is accepted.
    if (actual.taskQueue !== target.taskQueue) issues.push(`A02:task-queue:${target.id}`);
    if (actual.workflowType !== target.workflowType || actual.workflowId !== `scheduled-${target.id}`) issues.push(`A02:workflow:${target.id}`);
    if (actual.activityName !== target.activityName) issues.push(`A02:activity:${target.id}`);
  }
  return issues;
}
