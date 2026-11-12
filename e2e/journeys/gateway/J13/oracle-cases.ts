import type { AssertionRow } from '../_shared/packet-rows';
import {
  assertActivationTransition, assertCapNoDispatch, assertCappedMoney,
  assertDailyCap, assertLiveTtl, assertMemberNoWrite, assertMessagesPreservation,
  assertMixedModeRefusal, assertRefusalBound, assertRefusedEffects,
  assertSameKey, assertScopeSample, assertSettledTtl, assertTenantIsolation,
  type AttemptMoney,
} from './oracle-assertions';

export interface OracleSeeds {
  keyRecordId: string;
  otherKeyRecordId: string;
  scopeId: string;
  otherScopeId: string;
}
export interface OracleCase {
  id: string;
  family: string;
  row: AssertionRow<'J13'>;
  perturbation: string;
  check: (red: boolean, seeds: OracleSeeds) => void;
}

/** Synthetic detector fixtures; not mock provider responses, Redis snapshots,
 * ledger records or accepted runtime outputs. Provenance: PACKET.json
 * control_inventory controls CTRL-W03-BOUND/SCOPE/LEDGER and CTRL-W04-CAPS/
 * REVISION-ROLES. Each check builds a fresh value and changes ONE observation.
 * Actual baseline/red/restored product executions remain owed; no source
 * assertion is replaced in a red phase, and no product state is reseeded here.
 */
function money(): AttemptMoney[] {
  return [
    { rated: 13n, bound: 10n, debit: 10n, waived: 3n, upstreamCost: 13n, recordedUpstreamCost: 13n },
    { rated: 4n, bound: 8n, debit: 4n, waived: null, upstreamCost: 4n, recordedUpstreamCost: 4n },
  ];
}
function cap(scopeId: string) {
  // Fixed synthetic midday; neither a wall-clock stamp nor rollover evidence.
  return { includesByok: true, dayBoundary: 'UTC', period: 'daily',
    observedAtMs: Date.UTC(2000, 0, 2, 12), periodStartMs: Date.UTC(2000, 0, 2), scopeId };
}
function activation() {
  return { beforeAtMs: 1_000, afterAtMs: 1_000, beforeRevision: 4n, afterRevision: 5n,
    gatewayCopyEqualsActiveConfig: true, accountingBefore: [13n, 2n], accountingAfter: [13n, 2n] };
}

export const ORACLE_CASES: readonly OracleCase[] = [
  {
    id: 'bound-dispatch', family: 'CTRL-W03-BOUND', row: 'W03-12',
    perturbation: 'One new dispatch witness on a refused attempt; retain one legitimate earlier ledger row.',
    check: (red) => assertRefusedEffects('bound-dispatch', {
      upstreamCalls: red ? 1 : 0, holdDelta: 0n, accountHeldDelta: 0n,
      newSettledMarkers: 0, newLedgerRows: 0, earlierLedgerRowsBefore: 1, earlierLedgerRowsAfter: 1,
    }),
  },
  {
    id: 'bound-metadata', family: 'CTRL-W03-BOUND', row: 'W03-02',
    perturbation: 'Report B_i instead of C+B_i as required micros.',
    check: (red) => assertRefusalBound('bound-metadata', {
      max: 10n, admitted: 7n, attempt: 5n, required: red ? 5n : 12n,
      reportedMax: 10n, reportedAdmitted: 7n, reportedAttempt: 5n,
    }),
  },
  {
    id: 'scope-spent-held', family: 'CTRL-W03-SCOPE', row: 'W03-05',
    perturbation: 'Spent plus held exceeds the fixed scope total by one micro.',
    check: (red) => assertScopeSample('scope-spent-held', {
      expectedMax: 10n, max: 10n, spent: 4n, held: red ? 7n : 6n,
      outstanding: 3, expectedOutstanding: 3,
    }),
  },
  {
    id: 'scope-live-pttl', family: 'CTRL-W03-SCOPE', row: 'W03-07',
    perturbation: 'An outstanding scope acquires a positive expiry.',
    check: (red) => assertLiveTtl('scope-live-pttl', 1, red ? 1_000 : -1),
  },
  {
    id: 'scope-terminal-pttl', family: 'CTRL-W03-SCOPE', row: 'W03-07',
    perturbation: 'The final settled scope keeps an unbounded lifetime.',
    check: (red) => assertSettledTtl('scope-terminal-pttl', 0n, 0, red ? -1 : 86_400_000),
  },
  {
    id: 'scope-same-key', family: 'CTRL-W03-SCOPE', row: 'W03-08',
    perturbation: 'A different key record performs the first close.',
    check: (red, seeds) => assertSameKey('scope-same-key', seeds.keyRecordId, [
      seeds.keyRecordId, seeds.keyRecordId, red ? seeds.otherKeyRecordId : seeds.keyRecordId, seeds.keyRecordId,
    ]),
  },
  {
    id: 'ledger-capped-debit', family: 'CTRL-W03-LEDGER', row: 'W03-11',
    perturbation: 'An overrun debits rated cost instead of the attempt bound.',
    check: (red) => {
      const attempts = money();
      if (red) attempts[0].debit = 13n;
      assertCappedMoney('ledger-capped-debit', attempts, 14n);
    },
  },
  {
    id: 'ledger-waived-anomaly', family: 'CTRL-W03-LEDGER', row: 'W03-11',
    perturbation: 'The nonzero waived overrun observation disappears.',
    check: (red) => {
      const attempts = money();
      if (red) attempts[0].waived = null;
      assertCappedMoney('ledger-waived-anomaly', attempts, 14n);
    },
  },
  {
    id: 'ledger-upstream-cost', family: 'CTRL-W03-LEDGER', row: 'W03-11',
    perturbation: 'The recorded upstream cost is incorrectly replaced by the capped debit.',
    check: (red) => {
      const attempts = money();
      if (red) attempts[0].recordedUpstreamCost = 10n;
      assertCappedMoney('ledger-upstream-cost', attempts, 14n);
    },
  },
  {
    id: 'messages-metadata', family: 'CTRL-W03-LEDGER', row: 'W03-10',
    perturbation: 'Messages translation loses complete bound metadata.',
    check: (red) => assertMessagesPreservation('messages-metadata', {
      status: 402, code: 'max_charge_exceeded', parameterPreserved: true, metadataPreserved: !red,
    }),
  },
  {
    id: 'tenant-result', family: 'CTRL-W03-LEDGER', row: 'W03-13',
    perturbation: 'Org B gains an unexpected A-only scope observation.',
    check: (red, seeds) => assertTenantIsolation('tenant-result', {
      status: 404, code: 'max_charge_scope_not_found', before: [seeds.otherScopeId],
      after: red ? [seeds.otherScopeId, seeds.scopeId] : [seeds.otherScopeId],
    }),
  },
  {
    id: 'mixed-refusal', family: 'CTRL-W03-LEDGER', row: 'W03-14',
    perturbation: 'Mixed funding is accepted rather than refused before dispatch.',
    check: (red) => assertMixedModeRefusal('mixed-refusal', !red, 0),
  },
  {
    id: 'caps-dispatch', family: 'CTRL-W04-CAPS', row: 'W04-05',
    perturbation: 'One upstream dispatch is attributed to the cap-refused request.',
    check: (red) => assertCapNoDispatch('caps-dispatch', red ? 1 : 0),
  },
  {
    id: 'caps-byok', family: 'CTRL-W04-CAPS', row: 'W04-06',
    perturbation: 'The cap accounting observation excludes BYOK.',
    check: (red, seeds) => {
      const value = cap(seeds.scopeId);
      if (red) value.includesByok = false;
      assertDailyCap('caps-byok', value, seeds.scopeId);
    },
  },
  {
    id: 'caps-scope', family: 'CTRL-W04-CAPS', row: 'W04-04',
    perturbation: 'The exhausted cap identifies a different minted scope.',
    check: (red, seeds) => {
      const value = cap(seeds.scopeId);
      if (red) value.scopeId = seeds.otherScopeId;
      assertDailyCap('caps-scope', value, seeds.scopeId);
    },
  },
  {
    id: 'caps-utc', family: 'CTRL-W04-CAPS', row: 'W04-06',
    perturbation: 'The daily period starts one hour after UTC midnight.',
    check: (red, seeds) => {
      const value = cap(seeds.scopeId);
      if (red) value.periodStartMs += 3_600_000;
      assertDailyCap('caps-utc', value, seeds.scopeId);
    },
  },
  {
    id: 'revision-activation', family: 'CTRL-W04-REVISION-ROLES', row: 'W04-03',
    perturbation: 'Editing or rollback resets the prior activation timestamp.',
    check: (red) => {
      const value = activation();
      if (red) value.afterAtMs = 2_000;
      assertActivationTransition('revision-activation', value);
    },
  },
  {
    id: 'revision-increase', family: 'CTRL-W04-REVISION-ROLES', row: 'W04-03',
    perturbation: 'A subsequent activation reuses the prior policy revision.',
    check: (red) => {
      const value = activation();
      if (red) value.afterRevision = value.beforeRevision;
      assertActivationTransition('revision-increase', value);
    },
  },
  {
    id: 'revision-copy', family: 'CTRL-W04-REVISION-ROLES', row: 'W04-03',
    perturbation: 'The gateway copy differs from the active configuration.',
    check: (red) => {
      const value = activation();
      if (red) value.gatewayCopyEqualsActiveConfig = false;
      assertActivationTransition('revision-copy', value);
    },
  },
  {
    id: 'member-version', family: 'CTRL-W04-REVISION-ROLES', row: 'W04-07',
    perturbation: 'Member save returns 403 but nevertheless adds a config version.',
    check: (red) => assertMemberNoWrite('member-version', {
      status: 403, configVersionDelta: red ? 1 : 0, revisionDelta: 0n, activationMutationCount: 0,
    }),
  },
];
