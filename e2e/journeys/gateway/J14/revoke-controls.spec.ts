import { createHash, randomBytes, randomUUID } from 'node:crypto';
import { test, expect } from '@playwright/test';
import { createCounters, counterDelta } from '../_fixtures/mcp-approvals/counters.ts';
import { createOAuthState } from '../_fixtures/mcp-approvals/oauth-state.ts';
import { attachSeededIds } from '../_shared/contract';

/** SOL-03 fixture-detector qualification, not product/runtime mutation evidence.
 * All three cases use the SAME fresh-family setup and effect assertions. The
 * one-sided control replaces ONLY the fixture revoke transition with its normal
 * response/counter but no family invalidation. Access and refresh assertions must
 * EACH fail for that defect. Restored uses the original state.revoke unchanged.
 * oauth-state.ts owns these synthetic RFC7009 contracts; product binding is OWED.
 * NOT_RUN: no local execution is authorized by authoring these cases.
 */
const details = { tag: ['@j14-fixture-only'], annotation: [{
  type: 'journey-subassertion', description: JSON.stringify({ packet: 'J14',
    scope: 'LOCAL_FIXTURE_DETECTOR_CONTROL', satisfiesPacketRow: false, productExecution: false }),
}] };

for (const phase of ['baseline', 'no-op-revoke', 'restored'] as const) {
  test(`J14-FIXTURE RFC7009 independent effect detector [${phase}]`, details, async ({}, info) => {
    const principal = randomUUID();
    await attachSeededIds(info, 'J14', { userIds: [principal] });
    const counters = createCounters([principal]);
    // Data-only URLs: this state-machine control opens no listener or connection.
    const redirect = 'http://127.0.0.1:12345/callback', resource = 'http://127.0.0.1:12346/mcp';
    const state = createOAuthState(counters, [redirect], () => 0); // No expiry can mask revoke.
    try {
      state.bindResource(resource);
      const registered = state.register({ application_type: 'native', redirect_uris: [redirect] });
      expect(registered.status).toBe(201);
      const clientId = String(registered.body.client_id);
      const verifier = randomBytes(32).toString('base64url');
      const grant = state.validateAuthorization({ client_id: clientId, redirect_uri: redirect,
        response_type: 'code', resource, scope: 'mcp', login_hint: principal,
        state: randomBytes(32).toString('base64url'), code_challenge_method: 'S256',
        code_challenge: createHash('sha256').update(verifier).digest('base64url') });
      expect(grant !== null).toBe(true);
      const code = state.authorize(grant!, true);
      expect(typeof code === 'string').toBe(true);
      const issued = state.token({ grant_type: 'authorization_code', client_id: clientId,
        redirect_uri: redirect, resource, code_verifier: verifier, code: code! });
      expect(issued.status).toBe(200);
      // Prove refresh usability once; revoke its UNUSED descendant, never a
      // replay-invalidated ancestor. This is also the native HTTP test sequence.
      const live = state.token({ grant_type: 'refresh_token', client_id: clientId,
        resource, refresh_token: String(issued.body.refresh_token) });
      expect(live.status).toBe(200);
      expect(live.body.refresh_token !== issued.body.refresh_token).toBe(true);
      const accessToken = String(live.body.access_token), refreshToken = String(live.body.refresh_token);
      expect(state.principal(accessToken)).toEqual({ principalId: principal, valid: true });
      const before = counters.snapshot();
      const revoke = phase === 'no-op-revoke'
        ? (input: Record<string, string>) => {
          expect(input.client_id === clientId && input.token === refreshToken).toBe(true);
          counters.record(principal, 'revoke', 'accepted');
          return { status: 200, body: {} }; // Deliberately omit ONLY family invalidation.
        }
        : state.revoke;
      const result = revoke({ client_id: clientId, token: refreshToken });
      expect(result).toEqual({ status: 200, body: {} });
      expect(counterDelta(before, counters.snapshot())).toEqual([
        { principalId: principal, operation: 'revoke', tool: '', outcome: 'accepted', count: 1 },
      ]);
      const accessAfter = state.principal(accessToken);
      const refreshAfter = state.token({ grant_type: 'refresh_token', client_id: clientId,
        resource, refresh_token: refreshToken });
      // Project ONLY safe status/error before asserting. A failed control must
      // never render the successful response's access or refresh token strings.
      const refreshOutcome = { status: refreshAfter.status, error: refreshAfter.body.error ?? null };
      const assertAccess = () => expect(accessAfter, 'RFC7009 access invalidation')
        .toEqual({ principalId: principal, valid: false });
      const assertRefresh = () => expect(refreshOutcome, 'RFC7009 refresh invalidation')
        .toEqual({ status: 400, error: 'invalid_grant' });
      if (phase === 'no-op-revoke') {
        // Verify the mutant actually left BOTH operations live; setup failures
        // cannot qualify either detector. No broad expected-failure annotation.
        expect(accessAfter).toEqual({ principalId: principal, valid: true });
        expect(refreshOutcome).toEqual({ status: 200, error: null });
        expect(assertAccess).toThrow('RFC7009 access invalidation');
        expect(assertRefresh).toThrow('RFC7009 refresh invalidation');
      } else {
        assertAccess();
        assertRefresh();
      }
    } finally { state.dispose(); }
  });
}
