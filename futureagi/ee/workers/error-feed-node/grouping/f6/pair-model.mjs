import assert from 'node:assert/strict';
import {VIEWS, cosine, pairSafety} from './common.mjs';

// The saved F6 run supplied no calibrated pair release. Preserve its exact
// uncalibrated inference: hard rules reject; similarity never auto-attaches.
export function pairFeatures(first, second, features) {
  return [...VIEWS.flatMap(view => {
    const left = features.rows[first.id].views[view];
    const right = features.rows[second.id].views[view];
    return left && right ? [cosine(left.vector, right.vector), 1] : [0, 0];
  }), Number(first.kind === second.kind),
  Number(first.terminal_effect === second.terminal_effect
    && first.terminal_effect !== 'unknown')];
}

export function createPairScorer({rows, features, constraints, release = null}) {
  assert.equal(release, null, 'No calibrated F6 + MiniLM pair release is approved');
  const byId = new Map(rows.map(row => [row.id, row]));
  return (firstId, secondId) => {
    const first = byId.get(firstId);
    const second = byId.get(secondId);
    const contradiction = pairSafety(first, second, constraints);
    if (contradiction) return {decision: 'reject', probability: null,
      confidence: 'rule', contradictions: [contradiction], model_version: null};
    return {decision: 'hold', probability: null, confidence: 'not_calibrated',
      contradictions: [], features: pairFeatures(first, second, features), model_version: null};
  };
}
