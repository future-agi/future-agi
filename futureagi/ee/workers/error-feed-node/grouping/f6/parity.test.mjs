import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {adaptGroupingSnapshot} from '../snapshot.mjs';
import {makeGroupingSnapshotFixture} from '../snapshot-fixture.mjs';
import {F6_MINILM_POLICY} from '../policy.mjs';
import {addCompanionEvidence} from './companion-context.mjs';
import {reconciliationCandidates} from './pipeline.mjs';

const historicalDir = process.env.F6_HISTORICAL_DIR;
const historical = name => import(pathToFileURL(resolve(historicalDir, `${name}.mjs`)).href);

test('unaltered F6 safety modules match the sealed source bytes',
  {skip: !historicalDir}, async () => {
    for (const name of ['common', 'admission', 'companion-context']) {
      const [worker, sealed] = await Promise.all([
        readFile(new URL(`./${name}.mjs`, import.meta.url)),
        readFile(resolve(historicalDir, `${name}.mjs`)),
      ]);
      assert.deepEqual(worker, sealed, `${name} diverged from the sealed F6 source`);
    }
  });

test('companion evidence and reconciliation ordering retain sealed F6 behavior',
  {skip: !historicalDir}, async () => {
    const sealedCompanions = await historical('companion-context');
    const sealedPipeline = await historical('pipeline');
    const [first] = adaptGroupingSnapshot(makeGroupingSnapshotFixture());
    const second = {...structuredClone(first), id: '99999999-9999-4999-8999-999999999999',
      occurrence_id: '99999999-9999-4999-8999-999999999999'};
    const rows = [first, second];
    assert.deepEqual(addCompanionEvidence(rows, F6_MINILM_POLICY),
      sealedCompanions.addCompanionEvidence(rows, F6_MINILM_POLICY));

    const issues = [
      {id: 'issue-a', active: true, scope: 'scope', members: [first.id, second.id],
        prototypes: [first.id, second.id]},
      {id: 'issue-b', active: true, scope: 'scope', members: ['third'],
        prototypes: ['third']},
    ];
    const index = {similarity: (a, b) => a === b ? 1 : 0.6};
    const scorer = () => ({decision: 'hold'});
    const registry = {issues};
    assert.deepEqual(reconciliationCandidates(registry, index, scorer, F6_MINILM_POLICY),
      sealedPipeline.reconciliationCandidates(registry, index, scorer, F6_MINILM_POLICY));
  });
