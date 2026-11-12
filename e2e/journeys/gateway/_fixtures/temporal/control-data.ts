import { SOURCE_REF } from './catalog';
import { REGISTRATION_ARGV, type TemporalBinding, type RegistrationReceipt } from './consumer';

/** Explicitly synthetic fixture-contract inputs ONLY. Not admission evidence,
 * not runtime receipts and never exported from the live fixture entry point.
 * Source IDs/command are pinned elsewhere; container identities below are fake
 * data to qualify detectors. The named spec attaches the minted fixture nonce.
 */
export function syntheticBinding(runId: string, topology: 'standalone' | 'distributed'): TemporalBinding {
  return {
    sourceRef: SOURCE_REF, candidateRef: SOURCE_REF, sourceContractReceiptId: 'synthetic-source-contract',
    runtimeAdmissionId: 'synthetic-admission', containerPolicyId: 'synthetic-policy', exclusiveStackId: `synthetic-stack-${runId}`,
    runId, invocationId: `synthetic-invocation-${runId}`, topology, namespace: `synthetic-${runId}`,
    endpoint: '127.0.0.1:7233', cdcMode: 'peerdb',
    registrationTarget: { serviceId: 'synthetic-registration', containerId: 'a'.repeat(64), imageId: 'sha256:' + 'a'.repeat(64) },
    temporalTarget: { serviceId: 'synthetic-temporal', containerId: 'd'.repeat(64), imageId: 'sha256:' + 'd'.repeat(64) },
    budgets: { registrationMs: 60000, scheduleInventoryVisibleMs: 60000, pollIntervalMs: 1000 },
  };
}
export function syntheticReceipt(binding: TemporalBinding): RegistrationReceipt {
  return { ...binding.registrationTarget, runId: binding.runId, invocationId: binding.invocationId,
    namespace: binding.namespace, containerPolicyId: binding.containerPolicyId, argv: [...REGISTRATION_ARGV],
    cwd: '/app/backend', pid: 42, processStartTicks: '500', startedAtMs: 1000000, endedAtMs: 1001000, rawExitCode: 0,
    nativeReceiptId: `synthetic-native-${binding.runId}` };
}
