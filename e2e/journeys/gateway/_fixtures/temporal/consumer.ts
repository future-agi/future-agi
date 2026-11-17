import { SOURCE_REF, scheduleCatalog, type CdcMode } from './catalog';
import { id, integer, parseSchedule, parseScheduleList, sameBag, scheduleIssues, type ScheduleFact } from './schedules';

/** PROPOSED F08 value-only consumer port for the E6 fixtures slot of the pinned
 * CONSUMER-ABI-REQUEST.json (SHA256 b78a171fea942f88d1900ae4e3388e40a11593397ca733441abe4759daf6e803).
 * This is NOT a claim that the shared runner exposes these methods. The later
 * gateway-walk-consumer-abi/v1 descriptor is changes_required with E6 explicitly
 * implemented=false. The runner must implement/admit the binding and
 * reconcile each receipt to originals. No subprocess, Docker, network client,
 * arbitrary command, source read, env read or alternate dispatcher lives here.
 * These receipt fields are a NEW requested fixture projection, not guessed
 * Temporal wire or a substitute for A01/A09 native 18d/certificate joins.
 */
export const REGISTRATION_ARGV = Object.freeze([
  'env', 'NO_STARTUP_DB_MUTATIONS=false', 'python', 'manage.py', 'register_temporal_schedules',
]); // da4c81cc62 e2e/lib/temporal-probe.ts:PRODUCT_COMMAND; command's handle: full ALL_SCHEDULES.
export interface Target { serviceId: string; containerId: string; imageId: string }
export interface TemporalBinding {
  sourceRef: string;
  candidateRef: string;
  sourceContractReceiptId: string;
  runtimeAdmissionId: string;
  containerPolicyId: string;
  exclusiveStackId: string;
  runId: string;
  invocationId: string;
  topology: 'standalone' | 'distributed';
  namespace: string;
  endpoint: string;
  cdcMode: CdcMode;
  registrationTarget: Target;
  temporalTarget: Target;
  // No defaults: main/runtime must fix these BEFORE dispatch, not after a timeout.
  budgets: { registrationMs: number; scheduleInventoryVisibleMs: number; pollIntervalMs: number };
}
export interface RegistrationReceipt extends Target {
  runId: string;
  invocationId: string;
  namespace: string;
  containerPolicyId: string;
  argv: string[]; // Original native command argv, not a synthesized log string.
  cwd: string; // e2e/lib/temporal-probe.ts:PRODUCT_TARGET; entrypoint.sh:189.
  pid: number;
  processStartTicks: string;
  startedAtMs: number;
  endedAtMs: number;
  rawExitCode: number;
  nativeReceiptId: string;
}
export interface ReadReceipt extends Target {
  runId: string;
  invocationId: string;
  namespace: string;
  containerPolicyId: string;
  readId: string;
  nativeReceiptId: string;
  connectionId: string; // Fresh reader connection, never a cached describe.
  startedAtMs: number;
  endedAtMs: number;
}
export interface ScheduleRead {
  receipt: ReadReceipt;
  // Raw CLI shape already captured at source pin; retained original separately.
  listJson: string;
  descriptions: { id: string; json: string }[];
  // Proposed runner pagination accounting; all pages, including empty terminal.
  // Adapter must bind actual pagination exhaustion, not hardcode terminal=true.
  pages: { readId: string; rowCount: number; terminal: boolean }[];
}
export interface TemporalRuntime {
  binding: TemporalBinding;
  registerSchedules(): Promise<RegistrationReceipt>;
  readSchedules(readId: string): Promise<ScheduleRead>;
}
const sha = /^[0-9a-f]{40}$/;
export function requireTarget(target: Target): void {
  id(target.serviceId);
  if (!/^[0-9a-f]{64}$/.test(target.containerId) || !/^sha256:[0-9a-f]{64}$/.test(target.imageId)) throw new Error('Unbound Temporal container/image');
}
export function requireBinding(binding: TemporalBinding): TemporalBinding {
  if (binding.sourceRef !== SOURCE_REF || !sha.test(binding.candidateRef)) throw new Error('Unbound Temporal source/candidate');
  for (const value of [binding.sourceContractReceiptId, binding.runtimeAdmissionId, binding.containerPolicyId,
    binding.exclusiveStackId, binding.runId, binding.invocationId, binding.namespace]) id(value);
  if (!['standalone', 'distributed'].includes(binding.topology)) throw new Error('Unbound Temporal topology');
  // Not the distributed service hostname. Runner must execute the read inside
  // the inspected Temporal container's loopback; no caller-controlled address.
  // Port provenance: deploy/standalone/supervisord.conf and distributed Compose.
  if (binding.endpoint !== '127.0.0.1:7233') throw new Error('Temporal reader requires in-container loopback');
  scheduleCatalog(binding.cdcMode);
  requireTarget(binding.registrationTarget);
  requireTarget(binding.temporalTarget);
  for (const value of [binding.budgets.registrationMs, binding.budgets.scheduleInventoryVisibleMs, binding.budgets.pollIntervalMs]) {
    if (integer(value) < 1 || value > 600000) throw new Error('Unbound Temporal deadline');
  }
  if (binding.budgets.pollIntervalMs > binding.budgets.scheduleInventoryVisibleMs) throw new Error('Temporal poll interval exceeds deadline');
  return structuredClone(binding);
}
function targetEqual(a: Target, b: Target): boolean {
  return a.serviceId === b.serviceId && a.containerId === b.containerId && a.imageId === b.imageId;
}
export function registrationIssues(binding: TemporalBinding, receipt: RegistrationReceipt): string[] {
  requireBinding(binding);
  const issues: string[] = [];
  if (!targetEqual(binding.registrationTarget, receipt)) issues.push('A02:registration-target');
  if (receipt.runId !== binding.runId || receipt.invocationId !== binding.invocationId ||
      receipt.namespace !== binding.namespace || receipt.containerPolicyId !== binding.containerPolicyId) issues.push('A02:registration-binding');
  if (JSON.stringify(receipt.argv) !== JSON.stringify(REGISTRATION_ARGV)) issues.push('A02:registration-argv');
  if (receipt.cwd !== '/app/backend') issues.push('A02:registration-cwd');
  if (!Number.isSafeInteger(receipt.pid) || receipt.pid <= 0 || !/^[1-9][0-9]*$/.test(receipt.processStartTicks)) issues.push('A02:registration-process');
  if (!Number.isSafeInteger(receipt.startedAtMs) || !Number.isSafeInteger(receipt.endedAtMs) || receipt.startedAtMs <= 0 ||
      receipt.endedAtMs < receipt.startedAtMs || receipt.endedAtMs - receipt.startedAtMs > binding.budgets.registrationMs) issues.push('A02:registration-deadline');
  if (receipt.rawExitCode !== 0) issues.push('A02:registration-exit');
  if (typeof receipt.nativeReceiptId !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,254}$/.test(receipt.nativeReceiptId)) issues.push('A02:registration-original');
  return issues;
}

export function requireRead(binding: TemporalBinding, receipt: ReadReceipt, readId: string, notBeforeMs: number): void {
  if (!targetEqual(binding.temporalTarget, receipt) || receipt.runId !== binding.runId ||
      receipt.invocationId !== binding.invocationId || receipt.namespace !== binding.namespace ||
      receipt.containerPolicyId !== binding.containerPolicyId || receipt.readId !== readId) throw new Error('Temporal read identity mismatch');
  id(receipt.connectionId); id(receipt.nativeReceiptId);
  integer(receipt.startedAtMs); integer(receipt.endedAtMs);
  if (receipt.startedAtMs < notBeforeMs || receipt.endedAtMs < receipt.startedAtMs) throw new Error('Temporal read is stale');
}
export interface InventoryObservation { receipt: ReadReceipt; ids: string[]; schedules: ScheduleFact[]; issues: string[] }
export function inventoryObservation(binding: TemporalBinding, read: ScheduleRead, readId: string, notBeforeMs: number): InventoryObservation {
  requireBinding(binding);
  requireRead(binding, read.receipt, readId, notBeforeMs);
  const ids = parseScheduleList(read.listJson);
  if (!Array.isArray(read.pages) || !read.pages.length || read.pages.length > 1024 ||
      new Set(read.pages.map(p => id(p.readId))).size !== read.pages.length) throw new Error('Temporal pagination accounting missing');
  let rows = 0;
  read.pages.forEach((page, index) => {
    rows += integer(page.rowCount);
    if (page.terminal !== (index === read.pages.length - 1)) throw new Error('Temporal pagination incomplete');
  });
  if (rows !== ids.length) throw new Error('Temporal pagination row count differs');
  if (!Array.isArray(read.descriptions) || read.descriptions.length > 1024 ||
      !sameBag(ids, read.descriptions.map(row => id(row.id)))) throw new Error('Temporal descriptions do not cover full list');
  const schedules = read.descriptions.map(row => parseSchedule(row.id, row.json));
  return { receipt: read.receipt, ids, schedules,
    issues: scheduleIssues(scheduleCatalog(binding.cdcMode), ids, schedules) };
}
