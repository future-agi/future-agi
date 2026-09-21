import assert from 'node:assert/strict';
import {digest, unique, validateMembers, scopeKey} from './common.mjs';
import {validateGroup, admitQualifiedObservations} from './admission.mjs';

export const emptyRegistry = () => ({sequence: 0, issues: [], events: [], relationships: []});
const active = (registry, id) => { const issue = registry.issues.find(i => i.id === id && i.active); assert.ok(issue, 'Unknown/inactive issue'); return issue; };

function validateReceipt(receipt, members, context, registry) {
  assert.ok(receipt, 'Admission receipt required');
  if (receipt.kind === 'investigation') {
    const group = receipt.group;
    assert.deepEqual(unique(group.member_ids), unique(members), 'Receipt membership mismatch');
    for (const [id, revision] of Object.entries(receipt.evidence_revisions)) assert.equal(context.byId.get(id)?.evidence_revision, revision, 'Stale evidence');
    const targets = group.target_issue_id ? [active(registry, group.target_issue_id)] : [];
    const admission = validateGroup(group, {...context, allowedMembers: new Set(members), shown: new Set(context.byId.keys()), targets});
    assert.deepEqual(receipt.admission, admission, 'Admission changed');
    assert.equal(admission.state, 'Emerging', 'Unresolved mechanism cannot publish ordinary membership');
    return admission;
  }
  assert.equal(receipt.kind, 'pair_match');
  const issue = active(registry, receipt.target_issue_id);
  assert.equal(receipt.mechanism_revision, issue.mechanism_revision);
  assert.ok(context.scorePair, 'Pair scorer unavailable');
  for (const id of members) for (const p of issue.prototypes) assert.equal(context.scorePair(id, p).decision, 'attach', 'Uncalibrated/uncertain match');
  // Recheck the separation from ALL other eligible active issues at the authority boundary.
  for (const id of members) {
    const targetScore = Math.min(...issue.prototypes.map(p => context.scorePair(id, p).probability));
    for (const other of registry.issues.filter(i => i.active && i.id !== issue.id && i.scope === issue.scope)) {
      const scores = other.prototypes.map(p => context.scorePair(id, p));
      if (scores.every(s => s.decision === 'reject')) continue;
      assert.ok(scores.every(s => s.probability !== null), 'Unknown competing issue');
      assert.ok(targetScore - Math.max(...scores.map(s => s.probability)) >= context.policy.alternative_margin, 'Ambiguous competing issue');
    }
  }
  return {state: issue.evidence_state, confidence: 'calibrated_slice'};
}

export function commitCommand(registry, command, context) {
  const existing = registry.events.find(e => e.key === command.key);
  if (existing) { assert.equal(existing.command_digest, digest(command), 'Idempotency key reused with different command'); return registry; }
  assert.equal(command.expected_sequence, registry.sequence, 'Stale Registry command');
  for (const [id, revision] of Object.entries(command.expected_revisions || {})) assert.equal(active(registry, id).mechanism_revision, revision, 'Stale mechanism revision');
  const next = structuredClone(registry), before = {issues: structuredClone(registry.issues), relationships: structuredClone(registry.relationships)};
  const {byId, constraints} = context;
  const newIssue = (group, receipt, suffix) => {
    validateMembers(group.member_ids, byId, constraints);
    const admission = validateReceipt(receipt, group.member_ids, context, next);
    assert.equal(digest(receipt.group), digest(group), 'Issue semantics not bound to evidence receipt');
    const id = `issue-${digest([command.key, suffix]).slice(0, 24)}`;
    assert.ok(!next.issues.some(i => i.id === id));
    const issue = {id, active: true, scope: scopeKey(byId.get(group.member_ids[0])), mechanism_revision: 1,
      mechanism: group.mechanism, fix_hypothesis: group.fix_hypothesis, falsifier: group.falsifier,
      members: unique(group.member_ids), prototypes: context.selectPrototypes(group.member_ids),
      evidence_state: admission.state, admission, workflow: 'open', protected: false, aliases: [],
      membership_sequence: next.sequence + 1};
    next.issues.push(issue); return issue;
  };
  const requiredRevision = issue => assert.equal(command.expected_revisions?.[issue.id], issue.mechanism_revision, 'Missing expected mechanism revision');
  switch (command.type) {
    case 'create': newIssue(command.group, command.receipt, 'create'); break;
    case 'attach': {
      const issue = active(next, command.issue_id); requiredRevision(issue);
      if (command.receipt.kind === 'investigation') assert.equal(command.receipt.group.target_issue_id, issue.id);
      else assert.equal(command.receipt.target_issue_id, issue.id);
      validateReceipt(command.receipt, command.member_ids, context, next);
      validateMembers(unique([...issue.members, ...command.member_ids]), byId, constraints);
      issue.members = unique([...issue.members, ...command.member_ids]); issue.membership_sequence = next.sequence + 1;
      // Routine membership doesn't bump semantics/prototypes. Explicit refresh does.
      break;
    }
    case 'merge': {
      assert.ok(unique(command.issue_ids).length === command.issue_ids.length && command.issue_ids.length >= 2);
      const sources = command.issue_ids.map(id => active(next, id)); sources.forEach(requiredRevision);
      assert.ok(sources.every(i => !i.protected), 'Human-owned/ticketed issue requires operator approval');
      assert.deepEqual(unique(command.group.member_ids), unique(sources.flatMap(i => i.members)), 'Merge lost/invented members');
      assert.equal(command.group.target_issue_id, null);
      const target = newIssue(command.group, command.receipt, 'merge');
      target.aliases = unique(sources.flatMap(i => [i.id, ...i.aliases]));
      sources.forEach(i => { i.active = false; i.mechanism_revision++; });
      break;
    }
    case 'split': {
      const source = active(next, command.issue_id); requiredRevision(source); assert.ok(!source.protected, 'Protected issue');
      assert.ok(command.groups.length >= 2 && command.groups.length === command.receipts.length);
      assert.deepEqual(unique(command.groups.flatMap(g => g.member_ids)), unique(source.members), 'Split lost/invented members');
      assert.equal(command.groups.flatMap(g => g.member_ids).length, source.members.length, 'Split children must partition parent');
      command.groups.forEach((g, i) => { assert.equal(g.target_issue_id, null); newIssue(g, command.receipts[i], `split-${i}`); });
      source.active = false; source.mechanism_revision++; break;
    }
    case 'remove': {
      const issue = active(next, command.issue_id); requiredRevision(issue); assert.ok(!issue.protected);
      assert.ok(command.member_ids.length && command.member_ids.every(id => issue.members.includes(id)));
      // Removal is evidence-backed: each removed occurrence must have a current hard contradiction
      // or a calibrated rejection against every retained prototype. Otherwise reconciliation holds.
      const kept = issue.members.filter(id => !command.member_ids.includes(id)); assert.ok(kept.length);
      for (const id of command.member_ids) assert.ok(kept.some(p => context.scorePair(id, p).contradictions.length)
        || issue.prototypes.filter(p => kept.includes(p)).length && issue.prototypes.filter(p => kept.includes(p)).every(p => context.scorePair(id, p).decision === 'reject'), 'Unsupported removal');
      issue.members = kept; issue.prototypes = context.selectPrototypes(kept); issue.mechanism_revision++; break;
    }
    case 'refresh': {
      const issue = active(next, command.issue_id); requiredRevision(issue);
      const prototypes = context.selectPrototypes(issue.members);
      if (digest(issue.prototypes) !== digest(prototypes)) { issue.prototypes = prototypes; issue.mechanism_revision++; }
      break;
    }
    case 'admit': {
      const issue = active(next, command.issue_id); requiredRevision(issue);
      assert.ok(command.observations.every(o => issue.members.includes(o.citation.finding_id)));
      issue.admission = admitQualifiedObservations(command.observations, byId); issue.evidence_state = issue.admission.state; issue.mechanism_revision++; break;
    }
    case 'workflow': {
      const issue = active(next, command.issue_id); requiredRevision(issue);
      assert.ok(command.operator && ['open', 'triaged', 'resolved', 'ignored'].includes(command.workflow));
      issue.workflow = command.workflow; issue.protected = command.workflow === 'triaged' || Boolean(command.ticket_ref); issue.mechanism_revision++; break;
    }
    case 'relate': {
      const a = active(next, command.from), b = active(next, command.to); requiredRevision(a); requiredRevision(b);
      assert.ok(a.scope === b.scope && a.id !== b.id && command.operator && ['related', 'causes', 'duplicate_candidate'].includes(command.relation));
      next.relationships.push({from: a.id, to: b.id, relation: command.relation, operator: command.operator}); break;
    }
    case 'restore': {
      // Explicit compensating operation. Only latest event can be reversed without rebasing later work.
      const event = next.events.at(-1); assert.ok(command.operator && event?.key === command.event_key && event.command.type !== 'restore');
      next.issues = structuredClone(event.before.issues); next.relationships = structuredClone(event.before.relationships); break;
    }
    default: throw Error(`Unsupported Registry command ${command.type}`);
  }
  for (const issue of next.issues.filter(i => i.active)) validateMembers(issue.members, byId, constraints);
  const event = {key: command.key, sequence: ++next.sequence, command_digest: digest(command), command: structuredClone(command),
    previous_hash: registry.events.at(-1)?.hash || null, before, after: {issues: structuredClone(next.issues), relationships: structuredClone(next.relationships)}};
  event.hash = digest(event); next.events.push(event); return next;
}

export function replayRegistry(events, initialRegistry = emptyRegistry()) {
  let state = structuredClone(initialRegistry); const keys = new Set(state.events.map(event => event.key));
  for (const event of events) {
    const {hash, ...unsigned} = event;
    assert.equal(hash, digest(unsigned), 'Corrupt event'); assert.equal(event.sequence, state.sequence + 1);
    assert.equal(event.previous_hash, state.events.at(-1)?.hash || null);
    assert.equal(event.command_digest, digest(event.command)); assert.ok(!keys.has(event.key)); keys.add(event.key);
    assert.deepEqual(event.before, {issues: state.issues, relationships: state.relationships});
    state = {sequence: event.sequence, ...structuredClone(event.after), events: [...state.events, structuredClone(event)]};
  }
  return state;
}
