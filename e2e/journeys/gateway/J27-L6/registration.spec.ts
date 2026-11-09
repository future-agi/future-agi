import { test, expect } from '../_fixtures/temporal/fixtures';
import { registrationIssues, type InventoryObservation } from '../_fixtures/temporal/consumer';
import { assertionRow, attachSeededIds, journeyAnnotations, journeyTitle, selection } from '../_shared/contract';

/** A02 source implementation. It is NOT WALKED and remains runtime-OWED until
 * F01 discovery + F08-ABI native fixtures + F17/F19 admission are bound. This
 * test uses no browser/actor/keys. The runner must prevent bootstrap/embedded
 * auto-registration racing the isolated namespace and keep unrelated periodic
 * jobs inert/local. It executes the EXISTING product registration command;
 * no direct schedule insert, reduced --model-hub-only scope or cleanup flag.
 * C01 red/restored must additionally prove an unexecuted one-shot and an empty
 * isolated namespace before this full A02 path; a transport error is not red.
 */
test(journeyTitle('J27-L6', 'registration.full-inventory', 'baseline'), {
  tag: [selection('J27-L6').tag, '@service-only'],
  annotation: journeyAnnotations('J27-L6', ['A02'], 'baseline'),
}, async ({ temporal }, testInfo) => {
  const binding = temporal.binding;
  const SCHEDULE_INVENTORY_VISIBLE = {
    timeout: binding.budgets.scheduleInventoryVisibleMs,
    intervals: [binding.budgets.pollIntervalMs],
  };
  // All budgets come from the pre-admitted descriptor. This timeout is fixed
  // BEFORE any native action, not widened after recovery. Fixture setup is
  // value-only; native operations enforce the same deadlines inside the runner.
  test.setTimeout(binding.budgets.registrationMs + SCHEDULE_INVENTORY_VISIBLE.timeout + 10000);
  await attachSeededIds(testInfo, 'J27-L6', { runIds: [binding.runId] });
  await testInfo.attach('temporal-binding', { contentType: 'application/json', body: JSON.stringify({
    sourceRef: binding.sourceRef, candidateRef: binding.candidateRef,
    sourceContractReceiptId: binding.sourceContractReceiptId, runtimeAdmissionId: binding.runtimeAdmissionId,
    containerPolicyId: binding.containerPolicyId, exclusiveStackId: binding.exclusiveStackId,
    runId: binding.runId, invocationId: binding.invocationId, namespace: binding.namespace,
    topology: binding.topology, cdcMode: binding.cdcMode, budgets: binding.budgets,
    productAccepted: false,
  }) });
  await assertionRow(testInfo, 'J27-L6', 'A02', 'baseline', async () => {
    const receipt = await temporal.register();
    expect(registrationIssues(binding, receipt)).toEqual([]);
    // Safe projection, not an unchecked serialized runtime object/full Env.
    await testInfo.attach('registration-process', { contentType: 'application/json', body: JSON.stringify({
      serviceId: receipt.serviceId, containerId: receipt.containerId, imageId: receipt.imageId,
      pid: receipt.pid, processStartTicks: receipt.processStartTicks, argv: receipt.argv, cwd: receipt.cwd,
      namespace: receipt.namespace, runId: receipt.runId, invocationId: receipt.invocationId,
      startedAtMs: receipt.startedAtMs, endedAtMs: receipt.endedAtMs, rawExitCode: receipt.rawExitCode,
      nativeReceiptId: receipt.nativeReceiptId,
    }) });
    let final: InventoryObservation | undefined;
    // Don't let expect.poll hide a transport/parser/receipt failure as eventual
    // consistency. Only an authoritative, complete inventory mismatch is polled.
    let readError: unknown;
    await expect.poll(async () => {
      if (readError) throw readError;
      try { final = await temporal.observe(receipt.endedAtMs); }
      catch (error) { readError = error; throw error; }
      return final.issues;
    }, SCHEDULE_INVENTORY_VISIBLE).toEqual([]);
    if (!final || readError) throw new Error('Temporal inventory did not produce a fresh complete observation');
    await testInfo.attach('registration-schedules', { contentType: 'application/json', body: JSON.stringify({
      readId: final.receipt.readId, nativeReceiptId: final.receipt.nativeReceiptId,
      connectionId: final.receipt.connectionId, namespace: final.receipt.namespace,
      serviceId: final.receipt.serviceId, containerId: final.receipt.containerId, imageId: final.receipt.imageId,
      startedAtMs: final.receipt.startedAtMs, endedAtMs: final.receipt.endedAtMs,
      ids: final.ids, schedules: final.schedules,
    }) });
  });
});
