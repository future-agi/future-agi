import { test, expect } from '@playwright/test';
import { runW16Synthetic } from './w16-synthetic-contract';
import { W16MockFixture, type Fault } from './w16-synthetic-fixture';

// Contract authoring only: no product, browser, grant or ledger qualification.
// A real walk must bind W16SyntheticPort using the accepted runner and execute
// both billing modes and both browsers. Never replace that binding with this
// mock or label these results WALKED_PASS.
const controls: Array<{ id: string; fault: Fault; assertion: string }> = [
  {
    id: 'W16.R1',
    fault: 'absent-dispatch',
    assertion: 'W16.A01 absent dispatch',
  },
  {
    id: 'W16.R1',
    fault: 'incomplete-dispatch',
    assertion: 'W16.A01 incomplete dispatch',
  },
  {
    id: 'W16.R1',
    fault: 'third-dispatch',
    assertion: 'W16.A02 third dispatch',
  },
  {
    id: 'W16.R1',
    fault: 'post-disable-dispatch',
    assertion: 'W16.A02 post-disable dispatch',
  },
  {
    id: 'W16.R2',
    fault: 'missing-ledger',
    assertion: 'W16.A03 ledger linkage',
  },
  {
    id: 'W16.R2',
    fault: 'wrong-request-id',
    assertion: 'W16.A03 request linkage',
  },
  {
    id: 'W16.R2',
    fault: 'wrong-billing-mode',
    assertion: 'W16.A03 billing mode',
  },
  {
    id: 'W16.R2',
    fault: 'config-version',
    assertion: 'W16.A04 version mismatch',
  },
  {
    id: 'W16.R2',
    fault: 'org-b-dispatch',
    assertion: 'W16.A04 org B dispatch',
  },
  { id: 'W16.R2', fault: 'member-write', assertion: 'W16.A04 Member write' },
  { id: 'W16.R2', fault: 'viewer-write', assertion: 'W16.A04 Viewer write' },
  {
    id: 'W16.R2',
    fault: 'qualified-teardown',
    assertion: 'W16.A05 qualification',
  },
];

for (const mode of ['byok', 'managed'] as const) {
  test(
    `W16.A01-A05 ${mode}: synthetic contract baseline`,
    {
      tag: ['@journey-J15-oracle'],
      annotation: {
        type: 'qualification',
        description: 'MOCK_CONTRACT_ONLY; NOT_WALKED',
      },
    },
    async () => {
      expect(await runW16Synthetic(new W16MockFixture(), mode)).toEqual({
        fixtureKind: 'mock-contract',
        walked: false,
      });
    },
  );
}

for (const control of controls) {
  test(
    `${control.id} ${control.fault}: baseline / intended assertion / restored`,
    {
      tag: ['@journey-J15-oracle'],
      annotation: {
        type: 'qualification',
        description: 'MOCK_CONTRACT_ONLY; NOT_WALKED',
      },
    },
    async () => {
      await runW16Synthetic(new W16MockFixture(), 'managed');
      // Same assertions and operations; exactly one local fixture fault changes.
      await expect(
        runW16Synthetic(new W16MockFixture(control.fault), 'managed'),
      ).rejects.toThrow(control.assertion);
      await runW16Synthetic(new W16MockFixture(), 'managed');
    },
  );
}
