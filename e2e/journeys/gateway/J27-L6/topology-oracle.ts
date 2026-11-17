import { requireId } from '../_shared/identifiers';

/** Isolated C05 fixture model, NOT the shared runtime's wire/certificate schema.
 * Provenance: J27/L6/PACKET.json A07/A08/C05. Must be supplied: the complete actual
 * role set for the selected topology; no standalone -> distributed inheritance.
 * Source-built image/config digests and native process/namespace/poller witnesses
 * cannot be minted by this detector. Runtime readers and their byte bindings are
 * owed (FOUNDATION F03/F08/F17/F19). No Docker, subprocess or configuration edit.
 */
export type Topology = 'standalone' | 'distributed';
export interface RoleIdentity {
  roleId: string;
  kind: 'temporal' | 'worker';
  imageIdentity: string;
  configurationIdentity: string;
  serviceId: string;
  containerId: string;
  processId: string;
  namespace: string;
  taskQueues: string[];
}
export interface DisableWitness {
  roleId: string;
  basis: 'standalone-argv' | 'distributed-effective-runtime';
  serverVersionCheckEnabled: boolean;
  witnessId: string;
}
export interface TopologyExpectation {
  topology: Topology;
  roles: RoleIdentity[];
}
export interface TopologyFacts {
  roles: RoleIdentity[];
  disableWitnesses: DisableWitness[];
}

const exact = (left: string[], right: string[]) =>
  JSON.stringify([...left].sort()) === JSON.stringify([...right].sort());

function roleKey(role: RoleIdentity): string {
  const fields = [role.roleId, role.imageIdentity, role.configurationIdentity,
    role.serviceId, role.containerId, role.processId, role.namespace];
  fields.forEach(requireId);
  if (!['temporal', 'worker'].includes(role.kind) || !role.taskQueues.length ||
      new Set(role.taskQueues).size !== role.taskQueues.length) {
    throw new Error('Oracle role needs a kind and distinct task queues');
  }
  role.taskQueues.forEach(requireId);
  return JSON.stringify([role.kind, ...fields, [...role.taskQueues].sort()]);
}

function validateExpected(expected: TopologyExpectation): void {
  if (!['standalone', 'distributed'].includes(expected.topology) ||
      !expected.roles.some(r => r.kind === 'worker') ||
      !expected.roles.some(r => r.kind === 'temporal') ||
      new Set(expected.roles.map(r => r.roleId)).size !== expected.roles.length) {
    throw new Error('Oracle fixture requires distinct worker and Temporal roles');
  }
  expected.roles.forEach(roleKey);
}

/** Exact multiset: no Set-based deduplication of observed roles. This proves
 * detector fallibility only; a typed fixture cannot attest an actual process.
 */
export function identityIssues(expected: TopologyExpectation, facts: TopologyFacts): string[] {
  validateExpected(expected);
  return exact(expected.roles.map(roleKey), facts.roles.map(roleKey))
    ? [] : ['A07:role-identity-set'];
}

/** The packet's documented standalone argv setting is
 * frontend.enableServerVersionCheck=false (PACKET.temporal_prerequisite).
 * This normalized boolean is NOT an invented distributed configuration key.
 * The real distributed effective-runtime projection is deliberately unbound.
 * Zero DNS/connect/delivery proof (A08) and C06 MUST use the accepted base, not
 * this fixture checker. Never turn on a real updater as a negative control.
 */
export function disableIssues(expected: TopologyExpectation, facts: TopologyFacts): string[] {
  validateExpected(expected);
  const issues: string[] = [];
  const roleIds = expected.roles.filter(r => r.kind === 'temporal').map(r => r.roleId);
  const actualIds = facts.disableWitnesses.map(r => requireId(r.roleId));
  if (!exact(roleIds, actualIds)) issues.push('A08:disable-witness-set');
  const wantedBasis = expected.topology === 'standalone'
    ? 'standalone-argv' : 'distributed-effective-runtime';
  for (const witness of facts.disableWitnesses) {
    requireId(witness.witnessId);
    if (witness.basis !== wantedBasis) issues.push('A08:wrong-topology-disable-witness');
    if (witness.serverVersionCheckEnabled !== false) issues.push('A08:version-check-not-disabled');
  }
  return issues;
}
