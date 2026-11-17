import type { ExpectedRequest, SettlementFacts } from './settlement-oracle';
import type { RoleIdentity, Topology, TopologyExpectation, TopologyFacts } from './topology-oracle';

/** Pure, explicitly synthetic data for PACKET C04/C05. These functions make NO
 * product requests, credentials, grants, ledger rows, namespace or processes.
 * Their types are test-local projections, not unpinned wire bodies. The nonce
 * is minted per test and attached as a non-secret fixture ID by the spec.
 */
export function settlementFixture(nonce: string) {
  const organizationIds = [`org-a-${nonce}`, `org-b-${nonce}`];
  const requestIds = ['charged-a', 'charged-b', 'void', 'refused'].map(id => `${id}-${nonce}`);
  const expected: ExpectedRequest[] = [
    { organizationId: organizationIds[0], requestId: requestIds[0],
      holdId: `hold-a-${nonce}`, outboxId: `outbox-a-${nonce}`, outcome: 'charged',
      // Deliberately beyond Number.MAX_SAFE_INTEGER; no float tolerance is valid.
      debitMicros: '9007199254740993' },
    { organizationId: organizationIds[1], requestId: requestIds[1],
      holdId: `hold-b-${nonce}`, outboxId: `outbox-b-${nonce}`, outcome: 'charged', debitMicros: '7' },
    { organizationId: organizationIds[0], requestId: requestIds[2],
      holdId: `hold-void-${nonce}`, outboxId: `outbox-void-${nonce}`, outcome: 'void', debitMicros: '0' },
    { organizationId: organizationIds[0], requestId: requestIds[3],
      holdId: null, outboxId: null, outcome: 'refused', debitMicros: '0' },
  ];
  const settled = expected.filter(r => r.outcome !== 'refused');
  const markers = settled.map(r => ({ organizationId: r.organizationId,
    requestId: r.requestId, holdId: r.holdId!, value: '1' }));
  const debits = expected.filter(r => r.outcome === 'charged').map((r, i) => ({
    organizationId: r.organizationId, requestId: r.requestId, outboxId: r.outboxId!,
    entryId: `debit-${i}-${nonce}`, debitMicros: r.debitMicros,
  }));
  const facts: SettlementFacts = {
    markersAfterRecovery: structuredClone(markers), markersReread: structuredClone(markers),
    outboxes: settled.map(r => ({ organizationId: r.organizationId, requestId: r.requestId,
      holdId: r.holdId!, outboxId: r.outboxId! })),
    debitsAfterRecovery: structuredClone(debits), debitsAfterReplay: structuredClone(debits),
  };
  return { expected, facts, ids: { organizationIds, requestIds, runIds: [nonce],
    holdIds: settled.map(r => r.holdId!), settlementIds: settled.map(r => r.outboxId!) } };
}

export function topologyFixture(nonce: string, topology: Topology) {
  // Counts/names are CONTROL-fixture identities only; NOT a purported census of
  // the shipping standalone/distributed graph. That complete live set is owed.
  const kinds = topology === 'standalone'
    ? ['temporal', 'worker'] as const : ['temporal', 'temporal', 'worker'] as const;
  const roles: RoleIdentity[] = kinds.map((kind, i) => ({
    roleId: `${kind}-${i}-${nonce}`, kind,
    imageIdentity: `synthetic-image-${i}-${nonce}`,
    configurationIdentity: `synthetic-config-${i}-${nonce}`,
    serviceId: `synthetic-service-${i}-${nonce}`,
    containerId: `synthetic-container-${i}-${nonce}`,
    processId: `synthetic-process-${i}-${nonce}`,
    namespace: `synthetic-namespace-${nonce}`, taskQueues: [`synthetic-queue-${nonce}`],
  }));
  const expected: TopologyExpectation = { topology, roles: structuredClone(roles) };
  const facts: TopologyFacts = {
    roles: structuredClone(roles),
    disableWitnesses: roles.filter(r => r.kind === 'temporal').map((r, i) => ({
      roleId: r.roleId,
      basis: topology === 'standalone' ? 'standalone-argv' : 'distributed-effective-runtime',
      serverVersionCheckEnabled: false, witnessId: `synthetic-disable-${i}-${nonce}`,
    })),
  };
  return { expected, facts };
}
