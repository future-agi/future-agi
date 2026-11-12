import type { Phase } from '../_shared/contract';
import type { Debt, Row } from './obligations';

export interface OwedControlCase {
  caseId: string;
  control: string;
  phase: Exclude<Phase, 'baseline'>;
  rows: readonly Row[];
  debts: readonly Debt[];
  requiredOutcome: string;
}

// Provenance: J27/L5/PACKET.json negative_and_restored_controls.controls C01-C06.
// These are individually selectable, UNBOUND control steps, not red/green receipts.
// Even a red MUST hard-fail its admission guard: test.fail() would incorrectly
// credit an absent fixture/setup failure as a demonstrated detector failure.
// Each eventual control runs with its own raw output root and disposable project.
export const controlCases: readonly OwedControlCase[] = [
  {
    caseId: 'C01-WRONG-DIGEST', control: 'L5-C01-pair-and-schedule', phase: 'negative',
    rows: ['L5-A02'], debts: ['pair', 'mutation'],
    requiredOutcome: 'The actual pair admission path refuses a wrong image digest at its precise gate, before any mutation.',
  },
  {
    caseId: 'C01-NONADJACENT', control: 'L5-C01-pair-and-schedule', phase: 'negative',
    rows: ['L5-A02'], debts: ['pair', 'mutation'],
    requiredOutcome: 'The actual pair admission path refuses a nonadjacent eligible version entry before any mutation.',
  },
  {
    caseId: 'C01-MISSING-RESERVATION', control: 'L5-C01-pair-and-schedule', phase: 'negative',
    rows: ['L5-A10'], debts: ['schedule', 'mutation'],
    requiredOutcome: 'The same scheduling path refuses a missing reservation before any mutation.',
  },
  {
    caseId: 'C01-OVERLAP', control: 'L5-C01-pair-and-schedule', phase: 'negative',
    rows: ['L5-A10'], debts: ['schedule', 'mutation'],
    requiredOutcome: 'The same scheduling path refuses overlap with actual hosted walks before any mutation.',
  },
  {
    caseId: 'C01-RESTORED', control: 'L5-C01-pair-and-schedule', phase: 'restored',
    rows: ['L5-A02', 'L5-A10'], debts: ['pair', 'schedule', 'mutation'],
    requiredOutcome: 'Restore the accepted adjacent pair and exclusive reservation; the same gates accept with no bypass.',
  },
  {
    caseId: 'C02-IDENTITY', control: 'L5-C02-redis-identity', phase: 'red',
    rows: ['L5-A03'], debts: ['redis', 'pair', 'mutation'],
    requiredOutcome: 'Replace Redis only in a control project without flush; unchanged endpoint/image tag must not hide the changed instance. The identity detector fails A03.',
  },
  {
    caseId: 'C02-RESTORED', control: 'L5-C02-redis-identity', phase: 'restored',
    rows: ['L5-A03'], debts: ['redis', 'pair', 'mutation'],
    requiredOutcome: 'Fresh intact control fixture, genuine gateway-only rollback and unchanged Redis pass A03; do not repair/relabel the red fixture.',
  },
  {
    caseId: 'C03-HOLD', control: 'L5-C03-state-fallibility', phase: 'red',
    rows: ['L5-A04'], debts: ['redis', 'barrier', 'mutation'],
    requiredOutcome: 'In its own fixture remove one real synthetic outstanding hold with the reviewed scoped hook. Authoritative observation fails A04.',
  },
  {
    caseId: 'C03-HOLD-RESTORED', control: 'L5-C03-state-fallibility', phase: 'restored',
    rows: ['L5-A04'], debts: ['redis', 'barrier', 'pair', 'mutation'],
    requiredOutcome: 'Fresh genuine product admission with no injected defect passes A04 across the qualified pair; no reseeding within the measured transition.',
  },
  {
    caseId: 'C03-BUDGET', control: 'L5-C03-state-fallibility', phase: 'red',
    rows: ['L5-A05'], debts: ['redis', 'barrier', 'mutation'],
    requiredOutcome: 'In a separate fixture reset a consumed/reserved scope-budget field with the reviewed scoped hook. Authoritative observation fails A05.',
  },
  {
    caseId: 'C03-BUDGET-RESTORED', control: 'L5-C03-state-fallibility', phase: 'restored',
    rows: ['L5-A05'], debts: ['redis', 'barrier', 'pair', 'admission', 'ledger', 'mutation'],
    requiredOutcome: 'Fresh genuine admitted budget fixture without the defect passes A05, retaining exact conservation and real denial/positive witnesses.',
  },
  {
    caseId: 'C03-EPOCH', control: 'L5-C03-state-fallibility', phase: 'red',
    rows: ['L5-A06'], debts: ['redis', 'identity', 'mutation'],
    requiredOutcome: 'In a third separate fixture regress a synthetic credential epoch/tombstone. Authoritative observation fails A06; no real credential is touched.',
  },
  {
    caseId: 'C03-EPOCH-RESTORED', control: 'L5-C03-state-fallibility', phase: 'restored',
    rows: ['L5-A06'], debts: ['redis', 'identity', 'pair', 'admission', 'mutation'],
    requiredOutcome: 'Fresh product-created credential fixture without the defect passes A06, including stale/revoked/cross-tenant refusal and valid target-side delivery.',
  },
  {
    caseId: 'C04-DUPLICATE-DEBIT', control: 'L5-C04-financial-oracle', phase: 'red',
    rows: ['L5-A08'], debts: ['ledger', 'mutation'],
    requiredOutcome: 'Reviewed observation-fixture injection supplies a duplicate settlement/debit. The unchanged exactly-once reconciler fails A08; this is detector evidence only.',
  },
  {
    caseId: 'C04-RESTORED', control: 'L5-C04-financial-oracle', phase: 'restored',
    rows: ['L5-A08'], debts: ['ledger', 'barrier', 'redis', 'pair', 'mutation'],
    requiredOutcome: 'Remove injected observation, use actual worker redelivery/request replay and the real ledger; the same reconciler passes exactly-once with no orphan.',
  },
  {
    caseId: 'C05-PREVIOUS-DENIALS', control: 'L5-C05-real-denial-and-positive', phase: 'negative',
    rows: ['L5-A05', 'L5-A06', 'L5-A07', 'L5-A08'],
    debts: ['pair', 'identity', 'admission', 'barrier', 'redis', 'ledger'],
    requiredOutcome: 'On the actual previous image separately try stale, revoked, cross-tenant, wrong-scope, over-budget and not-ready requests. Each precise refusal has zero target delivery, reservation and debit.',
  },
  {
    caseId: 'C05-PREVIOUS-POSITIVE', control: 'L5-C05-real-denial-and-positive', phase: 'restored',
    rows: ['L5-A05', 'L5-A06', 'L5-A07', 'L5-A08'],
    debts: ['pair', 'identity', 'admission', 'barrier', 'redis', 'ledger'],
    requiredOutcome: 'After previous-image readiness, a valid current-epoch in-scope within-budget credential yields real local mock delivery and exactly one correct final settlement.',
  },
  {
    caseId: 'C05-CURRENT-DENIALS', control: 'L5-C05-real-denial-and-positive', phase: 'negative',
    rows: ['L5-A05', 'L5-A06', 'L5-A07', 'L5-A08', 'L5-A09'],
    debts: ['pair', 'identity', 'admission', 'barrier', 'redis', 'ledger'],
    requiredOutcome: 'Repeat all six distinct denials across roll-forward to the original current image, including its not-ready interval; same Redis/data, fresh request IDs, zero forbidden effects.',
  },
  {
    caseId: 'C05-CURRENT-POSITIVE', control: 'L5-C05-real-denial-and-positive', phase: 'restored',
    rows: ['L5-A05', 'L5-A06', 'L5-A07', 'L5-A08', 'L5-A09'],
    debts: ['pair', 'identity', 'admission', 'barrier', 'redis', 'ledger'],
    requiredOutcome: 'After original-current-image readiness, repeat valid delivery and exactly-once settlement on preserved data with fresh request IDs.',
  },
  {
    caseId: 'C06-RUNTIME-REDS', control: 'L5-C06-inherited-runtime-controls', phase: 'red',
    rows: ['L5-A01'], debts: ['runtime'],
    requiredOutcome: 'Execute inherited no-committer-identity, credential-read-set, missing-participant/final, output-cut, Compose-identity and page/worker/service-worker/WebSocket nonlocal-attempt refusals, including blocked/aborted outcomes; exact runtime evidence join, no real provider or credential read.',
  },
  {
    caseId: 'C06-RESTORED', control: 'L5-C06-inherited-runtime-controls', phase: 'restored',
    rows: ['L5-A01'], debts: ['runtime'],
    requiredOutcome: 'Exact clean child, complete guarded native/interpreter accounting and local-only app/observer pass the same accepted path with the full original control inventory.',
  },
  {
    caseId: 'FINAL-RESTORED-SEQUENCE', control: 'L5-C05-real-denial-and-positive', phase: 'restored',
    rows: ['L5-A09'],
    debts: ['runtime', 'pair', 'schedule', 'redis', 'barrier', 'identity', 'admission', 'ledger', 'mutation'],
    requiredOutcome: 'After ALL C01-C06 red/negative/restored obligations, run the full defect-free current-to-previous-to-original-current positive sequence. Never overwrite a failed workload with cleanup or a green control.',
  },
];
