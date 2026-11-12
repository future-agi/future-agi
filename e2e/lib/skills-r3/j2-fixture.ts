import { randomUUID } from 'node:crypto';
import type { FixtureRule } from '../skills-fixtures';

/** Synthetic support-desk application used by J2. Every behavioral difference between
 * context versions is carried by bytes the application actually receives (root policy and
 * catalog), read by the deterministic model fixture. Evidence class: synthetic fixture. */
export type Req = (method: string, path: string, data?: unknown) => Promise<any>;

export const REQUESTS = {
  refund: 'My ceramic mug arrived cracked. Please refund this damaged order.',
  chargeback: 'I do not recognize this charge on my card statement. Dispute the charge.',
  shipping: 'Where is my package? Please track the shipment.',
} as const;
export type CaseKey = keyof typeof REQUESTS;
export const STATE: Record<CaseKey, object> = {
  refund: { age_days: 10, amount: 25, disputed: false },
  chargeback: { age_days: 10, amount: 25, disputed: true },
  shipping: { age_days: 5, amount: 25, disputed: false },
};
export const ACTION: Record<CaseKey, string> = { refund: 'issue_refund', chargeback: 'open_chargeback', shipping: 'track_shipment' };
export const CONTAINS: Record<CaseKey, string> = { refund: 'receipt', chargeback: 'chargeback', shipping: 'package' };

const LEAVES: [string, string, string][] = [
  ['refund-policy', 'Refund an order for a damaged or defective item within thirty days.', 'SKILL-MARKER: refund\nIssue a refund for the damaged item only when the order is within thirty days and not disputed.'],
  ['chargeback-handling', 'Handle a customer dispute of a charge on their card or bank statement.', 'SKILL-MARKER: chargeback\nOpen a chargeback for a disputed order; never refund a disputed order.'],
  ['shipping-tracking', 'Track where a customer package is and report the delivery status.', 'SKILL-MARKER: shipping\nTrack the shipment and report its status.'],
  ['billing-address-change', 'Change the billing address on an account.', 'SKILL-MARKER: billing'],
  ['loyalty-points', 'Explain and adjust loyalty points balances.', 'SKILL-MARKER: loyalty'],
  ['warranty-claims', 'Start a manufacturer warranty claim for a defective product.', 'SKILL-MARKER: warranty'],
  ['gift-cards', 'Redeem or check the balance of a gift card.', 'SKILL-MARKER: gift'],
  ['subscription-cancel', 'Cancel or pause a subscription plan.', 'SKILL-MARKER: subscription'],
  ['order-cancellation', 'Cancel an order before it ships.', 'SKILL-MARKER: cancellation'],
  ['product-compatibility', 'Answer product compatibility questions.', 'SKILL-MARKER: compatibility'],
  ['price-match', 'Apply a price match request.', 'SKILL-MARKER: price'],
  ['tax-invoices', 'Provide tax invoices and receipts for past orders.', 'SKILL-MARKER: tax'],
  ['account-recovery', 'Recover access to a locked customer account.', 'SKILL-MARKER: recovery'],
  ['returns-labels', 'Create a prepaid return shipping label.', 'SKILL-MARKER: labels'],
  ['store-pickup', 'Arrange in-store pickup for an order.', 'SKILL-MARKER: pickup'],
];

const HEADER = '# Support desk policy\nLoad the one skill whose description fits the request, follow it, then use the matching business tool.\n';
/** Root bytes per context release. v4 is the recorded failing policy; v6 is an unrelated head. */
export const ROOT = {
  initial: (n: number) => `${HEADER}POLICY-REFUND: full-amount\nPOLICY-NOTE: revision ${n}\n`,
  recorded: `${HEADER}POLICY-REFUND: full-amount\nPOLICY-NOTE: recorded\n`,
  head: `${HEADER}POLICY-REFUND: full-amount\nPOLICY-NOTE: shipping-faq-update\n`,
  repaired: `${HEADER}POLICY-REFUND: capped-25\nPOLICY-NOTE: recorded\n`,
  wordingOnly: `${HEADER}POLICY-REFUND: wording-only\nPOLICY-NOTE: recorded\n`,
  overbroad: `${HEADER}POLICY-REFUND: capped-25\nPOLICY-ROUTING: disputes-as-refunds\nPOLICY-NOTE: recorded\n`,
} as const;

export async function publishLeaves(req: Req, prefix: string) {
  const leaves: { id: string; name: string }[] = [];
  for (const [name, description, body] of LEAVES) {
    const skill = await req('POST', 'publish/', {
      files: [{ path: 'SKILL.md', content: `---\nname: ${prefix}-${name}\ndescription: ${description}\n---\n${body}\n` }],
      labels: ['production'], expected_release_revision: 0, acknowledge_warnings: true,
    });
    leaves.push({ id: skill.skill.id, name: `${prefix}-${name}` });
  }
  return leaves;
}

/** Publishes the root bytes as the next release (draft from `from`, or the initial draft). */
export async function publishRoot(req: Req, contextId: string, from: number | null, content: string, note: string) {
  const root = `contexts/${contextId}/`;
  let draft = from === null ? await req('GET', `${root}draft/`) : await req('POST', `${root}draft/start/`, { revision: 0, from_version: from });
  draft = await req('PUT', `${root}draft/`, { revision: draft.revision, base_version: draft.base_version,
    files: [{ path: 'AGENTS.md', content }], description: 'Synthetic support desk', note });
  const review = await req('POST', `${root}draft/head-review/`, { revision: draft.revision });
  const ack = await req('POST', `${root}draft/acknowledge-head/`, { revision: draft.revision, expected_digest: draft.digest,
    expected_head_version: review.expected_head_version, review_fingerprint: review.review_fingerprint });
  return req('POST', `${root}draft/publish/`, { revision: ack.revision, expected_digest: draft.digest, base_version: draft.base_version,
    expected_head_version: review.expected_head_version, review_fingerprint: ack.review_fingerprint, note, acknowledge_warnings: true });
}

/** Context with the fifteen-member catalog and releases v1..v6 (v4 recorded failure, v6 unrelated head). */
export async function seedApplication(req: Req, prefix: string) {
  const leaves = await publishLeaves(req, prefix);
  const created = await req('POST', 'contexts/', { name: `${prefix}-support`, description: 'Synthetic support desk application', tags: [], files: [{ path: 'AGENTS.md', content: ROOT.initial(1) }] });
  const contextId: string = created.context_id;
  const links = leaves.map((leaf) => ({ skill_id: leaf.id, selector: { label: 'production' } }));
  const draft = await req('GET', `contexts/${contextId}/draft/`);
  const input = { revision: draft.revision, links, refresh_skill_ids: [] };
  const preview = await req('POST', `contexts/${contextId}/dependencies/preview/`, input);
  await req('POST', `contexts/${contextId}/dependencies/apply/`, { ...input, expected_fingerprint: preview.fingerprint });
  const files = [ROOT.initial(1), ROOT.initial(2), ROOT.initial(3), ROOT.recorded, ROOT.initial(5), ROOT.head];
  const releases: { id: string; number: number; digest: string }[] = [];
  for (let number = 1; number <= 6; number++) releases.push((await publishRoot(req, contextId, number === 1 ? null : number - 1, files[number - 1], `Seed v${number}`)).version);
  const byRole = (role: string) => leaves.find((leaf) => leaf.name.endsWith(role))!.id;
  return { contextId, releases, leaves, skills: { refund: byRole('refund-policy'), chargeback: byRole('chargeback-handling'), shipping: byRole('shipping-tracking') } };
}

export function expectationFixture(skillId: string, action: string) {
  return { id: randomUUID(), name: 'fi.application.expectations', text: JSON.stringify({ schema: 1, skill_id: skillId, action }) };
}
export const stateFixture = (key: CaseKey) => ({ id: randomUUID(), name: 'order-state', text: JSON.stringify({ 'support-order': STATE[key] }) });

/** A reviewed neighboring case that already works (preexisting in a configured workspace). */
export function neighborCase(key: CaseKey, skills: Record<CaseKey, string>) {
  return { id: randomUUID(), title: `Working ${key} case`, source_group_id: `synthetic:${key}`, prompt: REQUESTS[key],
    fixtures: [stateFixture(key), expectationFixture(skills[key], ACTION[key])], expected_trigger: null, reviewed: true,
    checks: [{ id: randomUUID(), kind: 'contains', critical: true, config: { text: CONTAINS[key], case_sensitive: false } }] };
}

const call = (id: string, name: string, args: object) => ({ id, type: 'function', function: { name, arguments: JSON.stringify(args) } });
const tool = (turn: number, request: string, name: string, args: object, extra: Partial<FixtureRule['match']> = {}): FixtureRule => ({
  match: { request, turn, ...extra }, response: { message: { role: 'assistant', content: null, tool_calls: [call(`call-${turn}-${name}`, name, args)] }, finish_reason: 'tool_calls' },
});
const say = (request: string, content: string, extra: Partial<FixtureRule['match']>): FixtureRule => ({
  match: { request, turn: 2, ...extra }, response: { message: { role: 'assistant', content }, finish_reason: 'stop' },
});

/** The model fixture reads the delivered system/root bytes and the skill file the application loaded. */
export function modelRules(skills: Record<CaseKey, string>): FixtureRule[] {
  const R = REQUESTS;
  const ROUTE = 'POLICY-ROUTING: disputes-as-refunds';
  return [
    tool(0, R.refund, 'load_skill', { skill_id: skills.refund }),
    tool(0, R.chargeback, 'load_skill', { skill_id: skills.refund }, { system_text: ROUTE }),
    tool(0, R.chargeback, 'load_skill', { skill_id: skills.chargeback }, { system_not: ROUTE }),
    tool(0, R.shipping, 'load_skill', { skill_id: skills.shipping }),
    tool(1, R.refund, 'issue_refund', { amount: 25 }, { tool_text: 'SKILL-MARKER: refund', system_text: 'POLICY-REFUND: capped-25' }),
    tool(1, R.refund, 'issue_refund', { amount: 100 }, { tool_text: 'SKILL-MARKER: refund', system_text: 'POLICY-REFUND: full-amount' }),
    tool(1, R.refund, 'issue_refund', { amount: 100 }, { tool_text: 'SKILL-MARKER: refund', system_text: 'POLICY-REFUND: wording-only' }),
    tool(1, R.chargeback, 'open_chargeback', {}, { tool_text: 'SKILL-MARKER: chargeback' }),
    tool(1, R.chargeback, 'issue_refund', { amount: 25 }, { tool_text: 'SKILL-MARKER: refund' }),
    tool(1, R.shipping, 'track_shipment', {}, { tool_text: 'SKILL-MARKER: shipping' }),
    say(R.refund, 'Refund of $25 issued. Your receipt is on its way.', { tool_text: '"status":"applied"' }),
    say(R.refund, 'Your refund has been submitted and a receipt follows by email.', { tool_text: '"status":"declined"' }),
    say(R.chargeback, 'A chargeback has been opened for this charge.', { tool_text: '"action":"open_chargeback"' }),
    say(R.chargeback, 'Your refund has been submitted and a receipt follows by email.', { tool_text: '"action":"issue_refund"' }),
    say(R.shipping, 'Your package is in transit and tracking was updated.', { tool_text: '"action":"track_shipment"' }),
  ];
}
