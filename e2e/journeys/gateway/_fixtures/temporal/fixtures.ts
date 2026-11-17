import { randomUUID } from 'node:crypto';
import { test as base } from '@playwright/test';
import { requireBinding, inventoryObservation, type TemporalRuntime, type TemporalBinding,
  type RegistrationReceipt, type InventoryObservation } from './consumer';

/** E6 consumer-extension fixture. Runner request F08-ABI must bind the option
 * through its accepted fixtures/config factory at worker construction. It must
 * supply an actual ID-policy adapter, NOT TemporalProbe/composeExec or a mock.
 * The option has NO default executor, process lookup, environment-based module
 * loader, filesystem receipt trust or global registration side effect.
 * CONSUMER-ABI.json now declares E6 implemented=false: this is an additive port
 * request, not an executable-runner acceptance claim. Missing binding is a hard
 * failure (never skip, zero-test success, W20 fallback or a boolean pass).
 */
export interface TemporalFixture {
  binding: TemporalBinding;
  register(): Promise<RegistrationReceipt>;
  observe(notBeforeMs: number): Promise<InventoryObservation>;
}
export function temporalFixture(runtime: TemporalRuntime | undefined): TemporalFixture {
  if (!runtime || typeof runtime.registerSchedules !== 'function' || typeof runtime.readSchedules !== 'function') {
    throw new Error('F08-ABI OWED: accepted container-ID Temporal runtime fixture not supplied');
  }
  const binding = requireBinding(runtime.binding);
  const serialized = JSON.stringify(binding);
  const unchanged = () => {
    if (JSON.stringify(requireBinding(runtime.binding)) !== serialized) throw new Error('Temporal runtime binding changed');
  };
  let registered = false;
  const connections = new Set<string>();
  const originals = new Set<string>();
  return {
    binding: structuredClone(binding),
    async register() {
      unchanged();
      if (registered) throw new Error('Temporal registration is one-shot per fixture');
      registered = true; // A failed call is not retried; keep its native outcome.
      const result = await runtime.registerSchedules();
      unchanged();
      return result;
    },
    async observe(notBeforeMs) {
      unchanged();
      const readId = randomUUID();
      const read = await runtime.readSchedules(readId);
      unchanged();
      const observation = inventoryObservation(binding, read, readId, notBeforeMs);
      if (connections.has(read.receipt.connectionId) || originals.has(read.receipt.nativeReceiptId)) throw new Error('Temporal read reused a connection/original');
      connections.add(read.receipt.connectionId); originals.add(read.receipt.nativeReceiptId);
      return observation;
    },
  };
}

export const test = base.extend<{ temporalRuntime: TemporalRuntime | undefined; temporal: TemporalFixture }>({
  temporalRuntime: [undefined, { option: true }],
  temporal: async ({ temporalRuntime }, use) => { await use(temporalFixture(temporalRuntime)); },
});
export { expect } from '@playwright/test';
