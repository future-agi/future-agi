import { appendFileSync, closeSync, fsyncSync, openSync } from 'node:fs';
import { performance } from 'node:perf_hooks';
import { canonical, digest, requireValue, token } from './canonical.mjs';

export const ACTIONS = Object.freeze(['click', 'field_edit', 'command', 'review', 'approve', 'recovery_decision', 'configuration_edit']);
export const DIMENSIONS = Object.freeze(['A', 'S', 'H', 'R']);
const PHASES = ['cold', 'recurring', 'recovery', 'exit'];
const EVENT_TYPES = ['registration', 'start', 'end', 'observation', 'fault', 'outcome', 'stage'];
export const STAGE_SOURCES = Object.freeze(['api', 'pg', 'filesystem', 'git', 'model_boundary', 'guest', 'external_trace', 'external_pipeline', 'external_github']);

function validateDecision(decision) {
  requireValue(decision && Object.keys(decision).sort().join(',') === 'packet_sha256,preconditions_sha256'
    && [decision.packet_sha256, decision.preconditions_sha256].every((value) => /^[a-f0-9]{64}$/.test(value)),
  'Decision evidence must contain only packet and preconditions hashes');
}

function validateStageReceipt(receipt, preceding) {
  if (receipt.decision !== undefined) validateDecision(receipt.decision);
  token(receipt.step, 'stage');
  requireValue(PHASES.includes(receipt.phase), 'Invalid stage phase');
  requireValue(STAGE_SOURCES.includes(receipt.source) && /^[a-f0-9]{64}$/.test(receipt.artifact_hash), 'Independent stage evidence required');
  requireValue(Array.isArray(receipt.action_sequences) && new Set(receipt.action_sequences).size === receipt.action_sequences.length, 'Unique action references required');
  for (const sequence of receipt.action_sequences) {
    const action = preceding.find((event) => event.sequence === sequence);
    requireValue(action?.type === 'start' && action.dimension === 'A' && action.phase === receipt.phase, 'Stage must reference a same-phase operator action');
    requireValue(preceding.some((event) => event.type === 'end' && event.start_sequence === sequence && event.status === 'completed'), 'Stage action must have completed');
  }
  requireValue(!preceding.some((event) => event.type === 'stage' && event.step === receipt.step), 'Duplicate stage receipt');
}

/** Append-only, payload-free measurement journal. Events survive a failed journey. */
export class ActionLedger {
  #events = [];
  #sequence = 0;
  #previous = '0'.repeat(64);
  #clock;
  #fd;
  #origin;
  #closed = false;

  constructor({ runId, job, mode, path = null, clock = () => performance.now() }) {
    this.runId = token(runId, 'runId');
    requireValue(/^J[1-6]$/.test(job), 'Unknown job');
    requireValue(['product', 'competitor', 'selftest'].includes(mode), 'Invalid ledger mode');
    this.job = job;
    this.mode = mode;
    this.#clock = clock;
    this.#origin = clock();
    // Exclusive creation prevents accidentally replacing an earlier failed/slow run.
    this.#fd = path ? openSync(path, 'wx', 0o600) : null;
  }

  #append(data) {
    requireValue(!this.#closed, 'Ledger is closed');
    const elapsedMs = Math.round((this.#clock() - this.#origin) * 1000) / 1000;
    requireValue(Number.isFinite(elapsedMs) && elapsedMs >= (this.#events.at(-1)?.elapsed_ms ?? 0), 'Clock moved backwards');
    const event = { schema: 1, run_id: this.runId, job: this.job, mode: this.mode,
      sequence: ++this.#sequence, elapsed_ms: elapsedMs, previous: this.#previous, ...data };
    event.hash = digest(event);
    if (this.#fd !== null) {
      appendFileSync(this.#fd, `${canonical(event)}\n`);
      fsyncSync(this.#fd);
    }
    this.#events.push(event);
    this.#previous = event.hash;
    return event;
  }

  registration({ comparisonIdentity, preregistrationHash }) {
    requireValue(this.#events.length === 0, 'Registration must precede measurement');
    requireValue([comparisonIdentity, preregistrationHash].every((hash) => /^[a-f0-9]{64}$/.test(hash)), 'Registration hashes required');
    this.#append({ type: 'registration', comparison_identity: comparisonIdentity, preregistration_hash: preregistrationHash });
  }

  async measure({ dimension, primitive, actor, step, phase, target, items = [], exceptionItems = [], decision = null }, effect) {
    if (decision !== null) validateDecision(decision);
    requireValue(DIMENSIONS.includes(dimension), 'Invalid dimension');
    requireValue(dimension !== 'A' || ACTIONS.includes(primitive), 'A must be a meaningful primitive');
    requireValue(dimension === 'A' || ({ S: 'transition', H: 'handoff', R: 'reconcile' })[dimension] === primitive, 'Use the dimension-specific primitive');
    requireValue(PHASES.includes(phase), 'Invalid phase');
    [actor, step, target].forEach((v) => token(v, 'action identity'));
    items.forEach((v) => token(v, 'review item'));
    exceptionItems.forEach((v) => token(v, 'exception item'));
    requireValue(new Set(items).size === items.length, 'Duplicate review item');
    requireValue(exceptionItems.every((v) => items.includes(v)), 'Exception was not exposed');
    requireValue(!['review', 'approve'].includes(primitive) || items.length > 0, 'Review must name exposed items');
    const start = this.#append({ type: 'start', dimension, primitive, actor, step, phase, target, items, exception_items: exceptionItems, ...(decision ? { decision: structuredClone(decision) } : {}) });
    try {
      const result = await effect();
      this.#append({ type: 'end', start_sequence: start.sequence, status: 'completed' });
      return result;
    } catch (error) {
      // Raw exception messages may contain request bodies or credentials. Keep the original
      // exception for the caller, but never copy it into the measurement artifact.
      this.#append({ type: 'end', start_sequence: start.sequence, status: 'failed' });
      throw error;
    }
  }

  /** Evidence that a mechanical stage completed. The same completed action can support
   * several stages without being counted again. Empty references mean automatic work,
   * never an implicit human review/authorization. Cross-phase attribution is forbidden. */
  stage({ step, phase, source, artifactHash, actionSequences = [], decision = null }) {
    const receipt = { type: 'stage', step, phase, source, artifact_hash: artifactHash, action_sequences: actionSequences, ...(decision ? { decision: structuredClone(decision) } : {}) };
    validateStageReceipt(receipt, this.#events);
    this.#append(receipt);
  }

  observation({ step, id, artifactHash }) {
    token(step, 'step'); token(id, 'observation id');
    requireValue(/^[a-f0-9]{64}$/.test(artifactHash), 'Observation needs an evidence artifact hash');
    this.#append({ type: 'observation', step, id, artifact_hash: artifactHash });
  }

  fault(id, scenarioId) {
    this.#append({ type: 'fault', id: token(id, 'fault'), scenario_id: token(scenarioId, 'scenario') });
  }

  outcome(id, status, artifactHash) {
    requireValue(['pass', 'fail', 'unknown'].includes(status), 'Invalid outcome');
    requireValue(/^[a-f0-9]{64}$/.test(artifactHash), 'Outcome needs an evidence artifact hash');
    this.#append({ type: 'outcome', id: token(id, 'outcome'), status, artifact_hash: artifactHash });
  }

  events() { return structuredClone(this.#events); }
  close() {
    if (!this.#closed && this.#fd !== null) closeSync(this.#fd);
    this.#closed = true;
  }
}

export function verifyLedger(events) {
  requireValue(Array.isArray(events), 'Ledger must be an array');
  let previous = '0'.repeat(64);
  let elapsed = 0;
  const open = new Set();
  const first = events[0];
  for (const [index, event] of events.entries()) {
    const { hash, ...body } = event;
    requireValue(event.schema === 1 && event.sequence === index + 1 && event.previous === previous && digest(body) === hash, 'Ledger chain mismatch');
    requireValue(event.run_id === first.run_id && event.mode === first.mode && event.job === first.job, 'Ledger mixed runs');
    requireValue(EVENT_TYPES.includes(event.type) && Number.isFinite(event.elapsed_ms) && event.elapsed_ms >= elapsed, 'Invalid ledger event');
    if (event.type === 'registration') requireValue(index === 0 && [event.comparison_identity, event.preregistration_hash].every((value) => /^[a-f0-9]{64}$/.test(value)), 'Invalid registration event');
    if (event.type === 'start') {
      if (event.decision !== undefined) validateDecision(event.decision);
      requireValue(DIMENSIONS.includes(event.dimension) && PHASES.includes(event.phase), 'Invalid measurement');
      requireValue(event.dimension !== 'A' || ACTIONS.includes(event.primitive), 'Invalid action');
      requireValue(event.dimension === 'A' || ({ S: 'transition', H: 'handoff', R: 'reconcile' })[event.dimension] === event.primitive, 'Invalid dimension primitive');
      [event.actor, event.step, event.target].forEach((value) => token(value, 'action identity'));
      requireValue(Array.isArray(event.items) && Array.isArray(event.exception_items), 'Missing review exposure');
      event.items.forEach((value) => token(value, 'review item'));
      requireValue(new Set(event.items).size === event.items.length && event.exception_items.every((value) => event.items.includes(value)), 'Invalid review exposure');
      requireValue(!['review', 'approve'].includes(event.primitive) || event.items.length > 0, 'Review must name exposed items');
      open.add(event.sequence);
    }
    if (event.type === 'stage') validateStageReceipt(event, events.slice(0, index));
    if (event.type === 'end') {
      requireValue(open.delete(event.start_sequence), 'Orphan or duplicate completion');
      requireValue(['completed', 'failed'].includes(event.status), 'Invalid completion');
    }
    elapsed = event.elapsed_ms;
    previous = hash;
  }
  return { complete: open.size === 0, unfinished: [...open], hash: previous };
}

export function summarizeLedger(events) {
  const integrity = verifyLedger(events);
  const counts = Object.fromEntries(DIMENSIONS.map((d) => [d, 0]));
  const phases = Object.fromEntries(PHASES.map((p) => [p, { A: 0, S: 0, H: 0, R: 0 }]));
  for (const event of events.filter((e) => e.type === 'start')) {
    counts[event.dimension] += 1;
    phases[event.phase][event.dimension] += 1;
  }
  return { counts, phases, integrity, failed_actions: events.filter((e) => e.type === 'end' && e.status === 'failed').length,
    automation_elapsed_ms: events.at(-1)?.elapsed_ms ?? 0,
    human_active_ms: null, human_elapsed_ms: null,
    review_exposures: events.filter((e) => e.type === 'start' && ['review', 'approve'].includes(e.primitive))
      .map((e) => ({ sequence: e.sequence, actor: e.actor, items: e.items, exception_items: e.exception_items })) };
}
