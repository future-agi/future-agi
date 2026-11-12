import { id, integer, object, sameBag } from './schedules';
import { requireTarget, type Target } from './consumer';
import { pollerIdentity, pollerIssues, type ExpectedPoller, type PollerSnapshot } from './pollers';

/** Only a STATUS reader, not a history parser. Public provenance at da4c81cc62:
 * futureagi/tfc/temporal/common/client.py:get_workflow_status_async returns these
 * exact six keys after handle.describe(); exceptions return None. In particular,
 * COMPLETED does NOT prove successful billing: TaskRunnerWorkflow can return a
 * failure result, and an OSS managed-settlement activity can return {} when EE
 * is absent. No workflow-completed => settlement-completed inference is allowed.
 * The GetWorkflowExecutionHistory projection/capture and activity attempt join
 * remain F08-HISTORY owed. No event names/CLI flags/proto fields are invented.
 */
export function parseWorkflowStatus(workflowId: string, runId: string, raw: unknown) {
  id(workflowId); id(runId);
  const value = object(raw);
  if (value.workflow_id !== workflowId || value.run_id !== runId) throw new Error('Temporal workflow/run mismatch');
  const statusName = id(value.status_name);
  if (typeof value.status !== 'string' || !value.status) throw new Error('Temporal workflow status missing');
  const time = (input: unknown) => {
    if (typeof input !== 'string' || !/^\d{4}-\d{2}-\d{2}T.*(?:Z|[+-]\d{2}:\d{2})$/.test(input) || !Number.isFinite(Date.parse(input))) throw new Error('Temporal workflow timestamp missing');
    return Date.parse(input);
  };
  const startedAtMs = time(value.start_time);
  const closedAtMs = value.close_time === null ? null : time(value.close_time);
  if (closedAtMs !== null && closedAtMs < startedAtMs) throw new Error('Temporal workflow timestamp order');
  return { workflowId, runId, statusName, startedAtMs, closedAtMs };
}

/** New F03/F08 native projection request, NOT an existing product wire API.
 * The runner owns process discovery and quiesce/resume. Standalone workers are
 * embedded in the ASGI process (tfc/temporal/embedded.py); stopping a guessed
 * worker service, or a health endpoint, cannot satisfy this contract. Distributed
 * docker-compose.distributed.yml:worker is separate, with optional sized workers.
 * Measure the ACTUAL admitted service/process set, never copy topology counts.
 */
export interface WorkerProcess extends Target {
  pid: number;
  processStartTicks: string;
  pollerIdentity: string; // Native worker-to-DescribeTaskQueue identity join, never guessed from health.
  namespace: string;
  taskQueues: string[];
  state: 'running' | 'quiesced';
}
export interface WorkerLineage {
  serviceId: string;
  predecessorPid: number;
  predecessorStartTicks: string;
  successorPid: number;
  successorStartTicks: string;
}
function requireWorkers(rows: readonly WorkerProcess[], nonempty: boolean): void {
  if (!Array.isArray(rows) || (nonempty && !rows.length) || rows.length > 256 ||
      new Set(rows.map(row => row.serviceId)).size !== rows.length) throw new Error('Temporal worker inventory invalid');
  for (const row of rows) {
    requireTarget(row); id(row.namespace); pollerIdentity(row.pollerIdentity);
    if (integer(row.pid) < 1 || !/^[1-9][0-9]*$/.test(row.processStartTicks) ||
        !['running', 'quiesced'].includes(row.state)) throw new Error('Temporal worker process invalid');
    if (!row.taskQueues.length || new Set(row.taskQueues).size !== row.taskQueues.length) throw new Error('Temporal worker queue inventory invalid');
    row.taskQueues.forEach(id);
  }
}
export function workerTransitionIssues(paused: readonly WorkerProcess[], recovered: readonly WorkerProcess[], links: readonly WorkerLineage[]): string[] {
  requireWorkers(paused, true); requireWorkers(recovered, false);
  if (paused.some(row => row.state !== 'quiesced')) throw new Error('Temporal recovery lacks quiesced precondition');
  const issues: string[] = [];
  if (!sameBag(paused.map(row => row.serviceId), recovered.map(row => row.serviceId))) issues.push('A03:worker-service-set');
  if (!sameBag(paused.map(row => row.serviceId), links.map(row => id(row.serviceId)))) issues.push('A07:worker-lineage-set');
  for (const row of recovered) {
    const before = paused.find(worker => worker.serviceId === row.serviceId);
    if (!before) continue;
    // This consumer supports resume/restart IN PLACE only. An image/container
    // replacement belongs to a separately admitted F03 transition, not L6.
    if (row.containerId !== before.containerId || row.imageId !== before.imageId ||
        row.namespace !== before.namespace || !sameBag(row.taskQueues, before.taskQueues)) issues.push('A07:worker-topology-drift');
    if (row.state !== 'running') issues.push('A03:worker-not-running');
    const link = links.find(item => item.serviceId === row.serviceId);
    if (link && (link.predecessorPid !== before.pid || link.predecessorStartTicks !== before.processStartTicks ||
        link.successorPid !== row.pid || link.successorStartTicks !== row.processStartTicks)) issues.push('A07:worker-lineage-mismatch');
  }
  return issues;
}

export interface WorkerRecoveryRuntime {
  // Accepted F03 port must complete/quiesce before allowing the caller to seed
  // its real F02/F05/F09 cohort. No seeding implementation or synthetic backlog.
  quiesceWorkers(): Promise<WorkerProcess[]>;
  resumeWorkers(paused: readonly WorkerProcess[]): Promise<{
    workers: WorkerProcess[]; lineage: WorkerLineage[]; resumedAtSeconds: number;
    nativeReceiptIds: string[]; rawExitCodes: number[];
  }>;
  readPollers(): Promise<PollerSnapshot>;
}

/** Partial A03/A07 consumer steps only. The caller must additionally assert a
 * nonzero real backlog, real history/consumption and the native process receipt
 * joins. This function never emits complete journey-row annotations or passes.
 * No retries, sleeps, lifecycle commands or new runner are hidden in it.
 */
export async function resumeAndReadPollers(runtime: WorkerRecoveryRuntime, paused: readonly WorkerProcess[], expected: readonly ExpectedPoller[]) {
  requireWorkers(paused, true);
  if (paused.some(row => row.state !== 'quiesced')) throw new Error('Temporal resume requires quiescence');
  const result = await runtime.resumeWorkers(structuredClone(paused));
  if (!result.nativeReceiptIds.length || result.nativeReceiptIds.length !== paused.length ||
      new Set(result.nativeReceiptIds).size !== result.nativeReceiptIds.length ||
      result.rawExitCodes.length !== paused.length || result.rawExitCodes.some(code => code !== 0)) throw new Error('Temporal resume native receipts incomplete');
  result.nativeReceiptIds.forEach(id);
  integer(result.resumedAtSeconds);
  requireWorkers(result.workers, true);
  const expectedKey = (row: ExpectedPoller) => JSON.stringify([row.taskQueue, row.taskQueueType, row.identity]);
  const measured = result.workers.flatMap(worker => worker.taskQueues.flatMap(taskQueue =>
    ([1, 2] as const).map(taskQueueType => ({ taskQueue, taskQueueType, identity: worker.pollerIdentity }))));
  if (!sameBag(expected.map(expectedKey), measured.map(expectedKey))) throw new Error('Temporal expected pollers are not the resumed worker inventory');
  const snapshot = await runtime.readPollers();
  const issues = [...workerTransitionIssues(paused, result.workers, result.lineage),
    ...pollerIssues(expected, snapshot, result.resumedAtSeconds)];
  return { ...result, snapshot, issues };
}
