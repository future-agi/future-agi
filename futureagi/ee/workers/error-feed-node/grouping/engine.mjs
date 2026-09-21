import assert from 'node:assert/strict';
import {digest, scopeKey, unique, validateMembers} from './f6/common.mjs';
import {validateInput} from './f6/input.mjs';
import {emptyRegistry} from './f6/registry.mjs';
import {runPipeline} from './f6/pipeline.mjs';
import {addCompanionEvidence} from './f6/companion-context.mjs';
import {F6_MINILM_POLICY, validateF6Policy} from './policy.mjs';

const MAX_PENDING = 100;
const MAX_ISSUES = 20;
const MAX_ROWS = MAX_PENDING + MAX_ISSUES * 16 + 3;

function boundedIds(ids, limit, label) {
  assert.ok(Array.isArray(ids) && ids.length <= limit
    && ids.every(id => typeof id === 'string' && id)
    && new Set(ids).size === ids.length, `Invalid ${label}`);
}

function validateFeatureSet(rows, features, policy) {
  assert.equal(features?.model, policy.embedding_model, 'Embedding model mismatch');
  assert.equal(features.dimension, policy.embedding_dimension, 'Embedding dimension mismatch');
  for (const row of rows) {
    const feature = features.rows?.[row.id];
    assert.ok(feature && feature.source_digest === row.source_digest
      && feature.evidence_revision === row.evidence_revision, 'Missing or stale F6 feature');
    for (const [view, entry] of Object.entries(feature.views || {})) {
      assert.ok(['semantics', 'task'].includes(view), 'Unapproved F6 view');
      assert.equal(entry.text_digest, digest(row.views[view]), 'Stale F6 view text');
      assert.ok(Array.isArray(entry.vector) && entry.vector.length === policy.embedding_dimension
        && entry.vector.every(Number.isFinite) && Math.hypot(...entry.vector) > 0,
      'Invalid F6 vector');
    }
    assert.ok(feature.views.semantics, 'Missing finding statement vector');
  }
}

function hydrateCandidates(window, byId, constraints, policy) {
  assert.ok(window && typeof window === 'object' && !Array.isArray(window), 'Candidate window required');
  assert.ok(Number.isSafeInteger(window.registry_revision) && window.registry_revision >= 0,
    'Registry revision required');
  assert.ok(Array.isArray(window.issues) && window.issues.length <= MAX_ISSUES,
    'Candidate issue limit exceeded');
  assert.ok(Array.isArray(window.omitted_candidates), 'Omitted candidate accounting required');
  const registry = emptyRegistry();
  const seen = new Set();
  const owned = new Set();
  for (const offered of window.issues) {
    const id = offered.issue_id;
    assert.ok(typeof id === 'string' && id && !seen.has(id), 'Invalid candidate issue identity');
    seen.add(id);
    assert.equal(offered.membership_complete, true, 'Incomplete candidate membership');
    assert.ok(Number.isSafeInteger(offered.revision) && offered.revision >= 1,
      'Invalid candidate revision');
    assert.equal(typeof offered.protected, 'boolean', 'Invalid candidate protection');
    assert.ok(offered.mechanism && typeof offered.mechanism === 'object'
      && ['mechanism', 'fix_hypothesis', 'falsifier'].every(key =>
        typeof offered.mechanism[key] === 'string' && offered.mechanism[key].trim()),
    'Invalid candidate mechanism');
    const members = offered.members?.map(member => member.occurrence_id);
    boundedIds(members, policy.max_reconcile_members, 'candidate members');
    assert.ok(members.length > 0, 'Empty candidate issue');
    assert.ok(members.every(memberId => !owned.has(memberId)),
      'Candidate occurrence belongs to multiple issues');
    members.forEach(memberId => owned.add(memberId));
    validateMembers(members, byId, constraints);
    const prototypes = offered.prototype_occurrence_ids;
    boundedIds(prototypes, policy.prototypes, 'candidate prototypes');
    assert.ok(prototypes.length > 0 && prototypes.every(id => members.includes(id)),
      'Candidate prototypes are not current members');
    for (const member of offered.members) {
      const row = byId.get(member.occurrence_id);
      assert.ok(row && !row.control, 'Candidate member row absent');
      assert.equal(member.source_digest, row.source_digest, 'Candidate member source changed');
      assert.equal(member.evidence_revision, row.evidence_revision, 'Candidate member evidence changed');
      assert.equal(member.trace_id, row.trace_id, 'Candidate member trace changed');
      assert.equal(member.report_id, row.investigation_report_ref?.report_id,
        'Candidate member report changed');
    }
    const firstRow = byId.get(members[0]);
    assert.ok(members.every(memberId => scopeKey(byId.get(memberId)) === scopeKey(firstRow)),
      'Candidate member scope mismatch');
    registry.issues.push({
      id,
      active: true,
      scope: scopeKey(firstRow),
      mechanism_revision: offered.revision,
      mechanism: offered.mechanism.mechanism,
      fix_hypothesis: offered.mechanism.fix_hypothesis,
      falsifier: offered.mechanism.falsifier,
      members: unique(members),
      prototypes: unique(prototypes),
      evidence_state: 'Emerging',
      admission: null,
      workflow: offered.protected ? 'triaged' : 'open',
      protected: offered.protected,
      aliases: [],
      membership_sequence: 0,
    });
  }
  return registry;
}

function citations(receipt) {
  return (receipt?.group?.citations || []).map(citation => ({
    occurrence_id: citation.finding_id,
    evidence_id: citation.evidence_id,
    evidence_digest: citation.evidence_digest,
    quote: citation.quote,
  }));
}

function mechanism(issue) {
  return {mechanism: issue.mechanism, fix_hypothesis: issue.fix_hypothesis,
    falsifier: issue.falsifier};
}

function createdIssues(event) {
  const before = new Set(event.before.issues.map(issue => issue.id));
  return event.after.issues.filter(issue => !before.has(issue.id));
}

function translateEvent(event, constraints) {
  const command = event.command;
  const current = id => event.after.issues.find(issue => issue.id === id);
  switch (command.type) {
    case 'create': {
      const issue = createdIssues(event)[0];
      return {type: 'create', temporary_id: issue.id, occurrence_ids: issue.members,
        citations: citations(command.receipt), mechanism: mechanism(issue),
        prototype_occurrence_ids: issue.prototypes,
        admission: command.receipt.model_provenance};
    }
    case 'attach':
      return {type: 'attach', issue_id: command.issue_id,
        expected_issue_revision: command.expected_revisions[command.issue_id],
        occurrence_ids: command.member_ids, citations: citations(command.receipt),
        admission: command.receipt.model_provenance};
    case 'refresh':
      return {type: 'refresh', issue_id: command.issue_id,
        expected_issue_revision: command.expected_revisions[command.issue_id],
        prototype_occurrence_ids: current(command.issue_id).prototypes,
        mechanism: mechanism(current(command.issue_id))};
    case 'merge': {
      const issue = createdIssues(event)[0];
      return {type: 'merge', source_issue_ids: command.issue_ids,
        expected_revisions: command.expected_revisions, temporary_id: issue.id,
        mechanism: mechanism(issue), prototype_occurrence_ids: issue.prototypes,
        citations: citations(command.receipt),admission:command.receipt.model_provenance};
    }
    case 'split':
      return {type: 'split', issue_id: command.issue_id,
        expected_issue_revision: command.expected_revisions[command.issue_id],
        parts: createdIssues(event).map(issue => ({temporary_id: issue.id,
          occurrence_ids: issue.members, mechanism: mechanism(issue),
          prototype_occurrence_ids: issue.prototypes})),
        citations: command.receipts.flatMap(citations),
        admissions: command.receipts.map(receipt=>receipt.model_provenance)};
    case 'remove': {
      const retained = current(command.issue_id).members;
      const hardConstraints = [...constraints].map(pair => JSON.parse(pair))
        .filter(([first, second]) => (command.member_ids.includes(first) && retained.includes(second))
          || (command.member_ids.includes(second) && retained.includes(first)));
      assert.ok(hardConstraints.length, 'Uncalibrated F6 removal lacks hard-rule proof');
      return {type: 'remove', issue_id: command.issue_id,
        expected_issue_revision: command.expected_revisions[command.issue_id],
        occurrence_ids: command.member_ids, hard_constraints: hardConstraints,
        reason: 'F6 validated current hard contradiction',
        admission: command.model_provenance};
    }
    default:
      throw new Error(`Unsupported F6 worker command: ${command.type}`);
  }
}

export async function runGrouping({rows, pendingIds, features, candidateWindow,
  cannotLinks = [], investigate, store, policy = F6_MINILM_POLICY}) {
  validateF6Policy(policy);
  assert.ok(Array.isArray(rows) && rows.length <= MAX_ROWS, 'F6 row window exceeded');
  boundedIds(pendingIds, MAX_PENDING, 'pending finding IDs');
  assert.ok(pendingIds.length > 0, 'No pending findings');
  assert.ok(typeof investigate === 'function' && typeof store?.read === 'function'
    && typeof store.save === 'function', 'F6 model/store interfaces required');
  const {byId, constraints} = validateInput(rows, cannotLinks);
  const scope = scopeKey(byId.get(pendingIds[0]));
  assert.ok(rows.every(row => scopeKey(row) === scope), 'Cross-scope F6 row window');
  assert.ok(pendingIds.every(id => byId.has(id) && !byId.get(id).control),
    'Pending occurrence absent or control');
  validateFeatureSet(rows, features, policy);
  const initialRegistry = hydrateCandidates(candidateWindow, byId, constraints, policy);
  assert.ok(initialRegistry.issues.every(issue => issue.scope === scope),
    'Cross-scope candidate issue');
  const currentMembers = new Set(initialRegistry.issues.flatMap(issue => issue.members));
  assert.ok(pendingIds.every(id => !currentMembers.has(id)),
    'Pending occurrence already belongs to a current issue');
  // The sealed run enriches same-investigation companions before F6 review.
  // Views are unchanged; vectors were verified against the original source
  // above, then only their internal source/evidence revision bindings change.
  const preparedRows = addCompanionEvidence(rows, policy);
  const preparedFeatures = structuredClone(features);
  for (const row of preparedRows) {
    preparedFeatures.rows[row.id].source_digest = row.source_digest;
    preparedFeatures.rows[row.id].evidence_revision = row.evidence_revision;
  }
  const result = await runPipeline({rows: preparedRows, pendingIds, features: preparedFeatures, cannotLinks, policy,
    pairRelease: null, investigate, store, initialRegistry,
    inputBinding: {registry_revision: candidateWindow.registry_revision,
      omitted_candidates: candidateWindow.omitted_candidates}});
  const nativeEvents = result.state.registry.events;
  if (result.state.status !== 'complete') {
    return {status: result.state.status, policy_digest: policy.digest,
      registry_revision: candidateWindow.registry_revision,
      omitted_candidates: candidateWindow.omitted_candidates,
      commands: [], dispositions: pendingIds.map(id => ({occurrence_id: id, state: 'pending'})),
      decision_receipts: [], native_events: [], registry: null};
  }
  const commands = nativeEvents.map(event => translateEvent(event, constraints));
  const dispositions = pendingIds.map(id => {
    const issue = result.state.registry.issues.find(item => item.active && item.members.includes(id));
    const deferred = result.predictions.deferred.find(item => item.finding_id === id);
    return issue
      ? {occurrence_id: id, state: 'assigned', issue_id: issue.id}
      : {occurrence_id: id, state: 'deferred', reason: deferred?.reason || 'Unresolved after bounded F6 review'};
  });
  return {status: result.state.status, policy_digest: policy.digest,
    registry_revision: candidateWindow.registry_revision,
    omitted_candidates: candidateWindow.omitted_candidates,
    commands, dispositions, decision_receipts: result.state.receipts,
    native_events: nativeEvents, registry: result.state.registry};
}
