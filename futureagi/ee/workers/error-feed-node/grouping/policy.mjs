import assert from 'node:assert/strict';
import {digest} from './f6/common.mjs';
import {MAX_EVIDENCE_BYTES} from './request-limits.mjs';

// Decision settings retain the sealed F6 run's actual overrides. Production
// features use MiniLM and the separately versioned chunk-pooling recipe, and
// Django supplies a bounded current Registry window. Those are explicit input
// differences, not a claim of historical score parity. Historical run-wide
// spend limits do not belong to this policy; Django's durable ledger owns them.
const SETTINGS = Object.freeze({
  version: 'f6-minilm/v1',
  model: 'google/gemini-3.8-flash',
  embedding_model: 'all-MiniLM-L6-v2',
  embedding_dimension: 384,
  index_mode: 'lsh',
  index_tables: 8,
  index_bits: 8,
  neighbours_per_view: 20,
  cohort_size: 32,
  representatives: 10,
  controls: 3,
  protected_peers: 5,
  refill_selection: true,
  coverage_selection: true,
  hybrid_retrieval: false,
  companion_context: true,
  companion_limit: 2,
  focused_attachment: false,
  max_focused_reviews: 40,
  refresh_before_reconciliation: false,
  same_proposal_repair: true,
  max_same_proposal_repairs: 12,
  targeted_revisit: true,
  max_targeted_reviews: 40,
  prototypes: 5,
  candidate_issues: 4,
  attach_threshold: 0.95,
  reject_threshold: 0.05,
  alternative_margin: 0.15,
  calibration_min_units: 30,
  calibration_precision_floor: 0.90,
  max_revisits: 2,
  max_reconcile_members: 16,
  max_reconcile_candidates: 20,
  // Lossless packing's transport ceiling; the gateway checks native tokens.
  max_input_bytes: MAX_EVIDENCE_BYTES,
  max_output_tokens: 8192,
  timeout_ms: 120000,
});

export const F6_MINILM_POLICY = Object.freeze({...SETTINGS, digest: digest(SETTINGS)});

export function validateF6Policy(policy) {
  assert.deepEqual(policy, F6_MINILM_POLICY, 'Unapproved F6 + MiniLM policy');
  return policy;
}
