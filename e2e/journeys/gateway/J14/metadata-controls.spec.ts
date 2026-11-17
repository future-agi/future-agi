import { randomUUID } from 'node:crypto';
import { test, type TestInfo } from '@playwright/test';
import { attachSeededIds } from '../_shared/contract';
import type { ApprovalRecord, CredentialRecord } from '../_shared/postgres-mcp';
import type { ToolCallRecord } from '../_shared/postgres-requests';
import {
  assertApprovalAuditExact, assertCredentialMetadataUnchanged,
  assertSharedBearerLifecycle, assertToolCallCohortExact,
  type CredentialLifecycle,
} from './state-assertions';

/** DATA-ONLY ORACLE TESTS. No product, browser, actor fixture, API, database,
 * runner hook, service or environment variable is used here. These are NOT
 * GW-JOURNEY-J14 tests and MUST NOT enter a product invocation inventory.
 * They exercise the partial detectors using synthetic metadata only. Expected
 * red is a real unchanged-oracle assertion failure, not a caught setup error.
 * A future authorized execution can qualify only these detectors; it cannot
 * satisfy any full packet row, semantic forbidden operation or live mutant.
 * Product baseline/negative/red/restored invocations remain NOT_WALKED/owed.
 */
type Phase = 'baseline' | 'red' | 'restored';
const phases: readonly Phase[] = ['baseline', 'red', 'restored'];
const timestamp = '2026-01-01T00:00:00.000Z'; // Synthetic oracle value, not a run timestamp.

function details(row: string, control: string, phase: Phase) {
  return {
    tag: ['@j14-oracle-only'],
    annotation: [{ type: 'journey-subassertion', description: JSON.stringify({
      schema: 'gateway-journey-subassertion/v1', packet: 'J14', row, control, phase,
      scope: 'DATA_ONLY_METADATA_ORACLE', satisfiesPacketRow: false,
      productExecution: false, ...(phase === 'red' ? { expectedFailureRow: row } : {}),
    }) }],
  };
}

async function identities(info: TestInfo) {
  const ids = {
    organizationId: randomUUID(), userId: randomUUID(), alternateUserId: randomUUID(),
    credentialId: randomUUID(), requestId: randomUUID(), runId: randomUUID(),
    keyId: randomUUID(), approvalId: randomUUID(),
  };
  await attachSeededIds(info, 'J14', {
    organizationIds: [ids.organizationId], userIds: [ids.userId, ids.alternateUserId],
    credentialRecordIds: [ids.credentialId], requestIds: [ids.requestId],
    runIds: [ids.runId], keyRecordIds: [ids.keyId], approvalIds: [ids.approvalId],
  });
  return ids;
}

const credentialControls = ['rotate-version', 'revoke-status', 'extra-credential'] as const;
for (const control of credentialControls) {
  for (const phase of phases) {
    test(`J14-ORACLE W07.1 ${control} [${phase}]`, details('W07.1', control, phase), async ({}, info) => {
      const ids = await identities(info);
      const serverId = `server-${randomUUID()}`;
      // Record shape/values: models/mcp_credential.py, services/mcp_credentials.py.
      const created: CredentialRecord = {
        id: ids.credentialId, server_id: serverId, scope: 'shared', user_id: null,
        kind: 'bearer', status: 'active', version: 1, created_by_id: ids.userId,
        rotated_at: null, token_expires_at: null,
      };
      const rotated = { ...created, version: 2, rotated_at: timestamp };
      const snapshots: CredentialLifecycle = {
        created: [created], rotated: [rotated], revoked: [{ ...rotated, status: 'revoked' }],
      };
      // One defect on actual observations only; expected identity and oracle unchanged.
      if (phase === 'red') {
        if (control === 'rotate-version') snapshots.rotated = [{ ...rotated, version: 1 }];
        if (control === 'revoke-status') snapshots.revoked = [{ ...rotated, status: 'active' }];
        if (control === 'extra-credential') snapshots.created = [created, { ...created, id: randomUUID() }];
      }
      await info.attach('oracle-mcp-record-ids', { contentType: 'application/json',
        body: JSON.stringify({ syntheticOracleOnly: true, serverIds: [serverId],
          credentialRecordIds: snapshots.created.map(row => row.id) }) });
      await test.step(`W07.1/oracle/${control}/${phase}`, async () => {
        if (phase === 'red') test.fail();
        assertSharedBearerLifecycle(snapshots, {
          credentialId: ids.credentialId, serverId, creatorId: ids.userId,
        });
      });
    });
  }
}

for (const phase of phases) {
  test(`J14-ORACLE W07.1 unauthorized-write [${phase}]`,
    details('W07.1', 'unauthorized-write', phase), async ({}, info) => {
      const ids = await identities(info);
      const before: CredentialRecord = {
        id: ids.credentialId, server_id: `server-${randomUUID()}`, scope: 'shared',
        user_id: null, kind: 'bearer', status: 'active', version: 1,
        created_by_id: ids.userId, rotated_at: null, token_expires_at: null,
      };
      const after = { ...before, version: phase === 'red' ? 2 : 1 };
      await info.attach('oracle-mcp-record-ids', { contentType: 'application/json',
        body: JSON.stringify({ syntheticOracleOnly: true, serverIds: [before.server_id] }) });
      await test.step(`W07.1/oracle/no-write/${phase}`, async () => {
        if (phase === 'red') test.fail();
        assertCredentialMetadataUnchanged([before], [after]);
      });
    });
}

const approvalControls = ['args-binding', 'decider-binding', 'extra-pending-row'] as const;
for (const control of approvalControls) {
  for (const phase of phases) {
    test(`J14-ORACLE W11.1 ${control} [${phase}]`, details('W11.1', control, phase), async ({}, info) => {
      const ids = await identities(info);
      // Field/status authority: models/mcp_approval.py. This fabricated digest
      // is not a claim about the product's canonical args-hashing algorithm.
      const expected: ApprovalRecord = {
        approval_id: ids.approvalId, virtual_server_id: `virtual-${randomUUID()}`,
        tool: 'delete_file', args_hash: 'a'.repeat(64), agent_run_id: ids.runId,
        api_key_id: ids.keyId, status: 'used', decided_by_id: ids.userId,
        requested_at: timestamp, decided_at: timestamp, expires_at: timestamp,
      };
      const actual: ApprovalRecord[] = [{ ...expected }];
      if (phase === 'red') {
        if (control === 'args-binding') actual[0].args_hash = 'b'.repeat(64);
        if (control === 'decider-binding') actual[0].decided_by_id = ids.alternateUserId;
        if (control === 'extra-pending-row') actual.push({ ...expected,
          approval_id: randomUUID(), status: 'pending', decided_by_id: null, decided_at: null });
      }
      await info.attach('oracle-mcp-record-ids', { contentType: 'application/json',
        body: JSON.stringify({ syntheticOracleOnly: true, virtualServerIds: [expected.virtual_server_id],
          approvalIds: actual.map(row => row.approval_id) }) });
      await test.step(`W11.1/oracle/${control}/${phase}`, async () => {
        if (phase === 'red') test.fail();
        assertApprovalAuditExact(actual, [expected]);
      });
    });
  }
}

const toolControls = ['duplicate-call', 'wrong-via', 'missing-call'] as const;
for (const control of toolControls) {
  for (const phase of phases) {
    test(`J14-ORACLE W07.2 ${control} [${phase}]`, details('W07.2', control, phase), async ({}, info) => {
      const ids = await identities(info);
      const event = randomUUID();
      // Model fields/defaults: models/mcp_tool_call.py. In particular via=''
      // is the model default, NOT a claimed direct-call wire value. The red
      // value below is a synthetic mismatch, not an accepted MCP protocol enum.
      const expected: ToolCallRecord = {
        event_id: event, tool_call_id: event, request_id: ids.requestId,
        server_id: `server-${randomUUID()}`, virtual_server_id: `virtual-${randomUUID()}`,
        tool: 'echo', via: '', user_id: ids.userId, agent_run_id: ids.runId,
        outcome: 'success', error_code: '', approval_id: '', dispatched: true,
        billing_mode: 'byok', upstream_cost_micros: '0', billed_cost_micros: '0',
        markup_micros: '0', content_mode: '',
      };
      const actual: ToolCallRecord[] = [{ ...expected }];
      if (phase === 'red') {
        if (control === 'duplicate-call') actual.push({ ...expected, event_id: randomUUID() });
        if (control === 'wrong-via') actual[0].via = 'synthetic-wrong-via';
        if (control === 'missing-call') actual.splice(0, 1);
      }
      await info.attach('oracle-mcp-record-ids', { contentType: 'application/json',
        body: JSON.stringify({ syntheticOracleOnly: true, serverIds: [expected.server_id],
          virtualServerIds: [expected.virtual_server_id], expectedEventId: event,
          actualEventIds: actual.map(row => row.event_id) }) });
      await test.step(`W07.2/oracle/${control}/${phase}`, async () => {
        if (phase === 'red') test.fail();
        assertToolCallCohortExact(actual, [expected]);
      });
    });
  }
}
