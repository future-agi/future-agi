import { id, integer, object, sameBag } from './schedules';

/** Source-pinned DescribeTaskQueue projection, NOT guessed Temporal CLI JSON.
 * da4c81cc623f909ce38f53e01b557ee8374d5adb e2e/lib/managed-mock-background.py:
 * describe_worker calls WorkflowServiceStub.DescribeTaskQueue with namespace,
 * TaskQueue(name), task_queue_type 1 (WORKFLOW) and 2 (ACTIVITY). Its response
 * exposes pollers[*].identity, last_access_time.seconds. validate_pollers pins
 * -5 <= age <= 120 seconds. The accepted native adapter returns those fields
 * only and owns grpc/channel timeout/closure. We do NOT import that script:
 * its unrelated paths inspect environment/private source, outside this lane.
 */
export interface ExpectedPoller { taskQueue: string; taskQueueType: 1 | 2; identity: string }
export interface PollerFact extends ExpectedPoller { lastAccessSeconds: number }
export interface PollerSnapshot { observedAtSeconds: number; rows: PollerFact[] }
export function pollerIdentity(value: unknown): string {
  // The source observer derives <pid>@<container-hostname>. Embedded workers
  // may use SDK-owned identities; the runner supplies/correlates the actual ID.
  if (typeof value !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,254}$/.test(value)) throw new Error('Temporal poller identity missing');
  return value;
}
export function parsePollers(taskQueue: string, taskQueueType: 1 | 2, raw: unknown): PollerFact[] {
  id(taskQueue);
  if (taskQueueType !== 1 && taskQueueType !== 2) throw new Error('Temporal queue type missing');
  const rows = object(raw).pollers;
  if (!Array.isArray(rows) || rows.length > 256) throw new Error('Temporal pollers missing');
  return rows.map(row => {
    const value = object(row);
    return { taskQueue, taskQueueType, identity: pollerIdentity(value.identity),
      lastAccessSeconds: integer(object(value.last_access_time).seconds) };
  });
}
const key = (row: ExpectedPoller) => JSON.stringify([id(row.taskQueue), row.taskQueueType, pollerIdentity(row.identity)]);
export function pollerIssues(expected: readonly ExpectedPoller[], snapshot: PollerSnapshot, resumedAtSeconds: number): string[] {
  if (!expected.length || expected.length > 256 || new Set(expected.map(key)).size !== expected.length ||
      expected.some(row => row.taskQueueType !== 1 && row.taskQueueType !== 2)) throw new Error('Temporal expected pollers invalid');
  // Both workflow AND activity polling must be declared for every queue.
  for (const queue of new Set(expected.map(row => row.taskQueue))) {
    if (!expected.some(row => row.taskQueue === queue && row.taskQueueType === 1) ||
        !expected.some(row => row.taskQueue === queue && row.taskQueueType === 2)) throw new Error('Temporal queue missing a poll type');
  }
  integer(snapshot.observedAtSeconds); integer(resumedAtSeconds);
  if (!Array.isArray(snapshot.rows) || snapshot.rows.length > 256 || snapshot.observedAtSeconds < resumedAtSeconds) throw new Error('Temporal poll observation invalid');
  const issues: string[] = [];
  if (!sameBag(expected.map(key), snapshot.rows.map(key))) issues.push('A03:poller-identity-set');
  for (const row of snapshot.rows) {
    integer(row.lastAccessSeconds);
    const age = snapshot.observedAtSeconds - row.lastAccessSeconds;
    if (age < -5 || age > 120) issues.push('A03:poller-stale-or-future');
    // The pinned projection has only seconds: equality cannot distinguish a
    // pre-resume cached poll from a later poll within that same second. Require
    // a strictly later second, even when the worker/poller identity is unchanged.
    if (row.lastAccessSeconds <= resumedAtSeconds) issues.push('A03:poller-before-resume');
  }
  return issues;
}
