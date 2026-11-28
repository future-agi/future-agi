// Synthetic, neutral demo content for the "Acme Support" walkthrough.
// Every behavioural difference the recorded runs show is carried by bytes the
// product actually delivers (skill files), and read by the deterministic model fixture.

const skill = (name, description, body) => `---\nname: ${name}\ndescription: ${description}\n---\n${body}`;

export const REFUND_V1 = skill('refund-policy',
  'Use when a customer asks for a refund on a damaged, defective or unwanted order.',
  `# Refund policy

1. Look up the order first (order-lookup) and confirm it was delivered in the last 30 days.
2. Never refund an order that is under dispute. Hand it to chargeback-handling instead.
3. Refund the order total plus original shipping to the original payment method.
4. Tell the customer when the money arrives and that a receipt follows by email.

Regional windows: references/refund-windows.md
`);

export const REFUND_V2 = skill('refund-policy',
  'Use when a customer asks for a refund on a damaged, defective or unwanted order.',
  `# Refund policy

1. Look up the order first (order-lookup) and confirm it was delivered in the last 30 days.
2. Never refund an order that is under dispute. Hand it to chargeback-handling instead.
3. Refund the item price only. Shipping is not refundable.
4. Refunds above 25 USD need a supervisor. Use escalation-tone for the hand-off.
5. Tell the customer when the money arrives and that a receipt follows by email.

Regional windows: references/refund-windows.md
`);

// Phrases the model fixture keys on. Each appears in exactly one refund-policy version.
export const V1_PHRASE = 'order total plus original shipping';
export const V2_PHRASE = 'item price only';

// Obviously fake value, proven to trip the generic secret detector. Never a real key.
export const FAKE_SECRET_LINE = 'api_key=FAKE_ONLY_0123456789_abcdefghijklmnopqrstuvwxyz';

export const SKILLS = [
  { name: 'refund-policy', files: [
    { path: 'SKILL.md', content: REFUND_V1 },
    { path: 'references/refund-windows.md', content: `# Refund windows

| Region | Window | Counted from |
| --- | --- | --- |
| United States | 30 days | Delivery date |
| European Union | 30 days | Delivery date, plus the 14-day cooling-off period |
| United Kingdom | 30 days | Delivery date |

Gift orders: refund to the gift purchaser, never the recipient.
` } ] },
  { name: 'chargeback-handling', files: [
    { path: 'SKILL.md', content: skill('chargeback-handling',
      'Use when a customer disputes a charge on their card or bank statement.',
      `# Chargeback handling

1. Confirm the order and the disputed amount with order-lookup.
2. Open a chargeback case for the disputed order. Never issue a refund on a disputed order.
3. Explain that the bank decides the outcome, usually within 10 business days.
4. Keep the tone neutral; do not suggest the customer is at fault.

Reason codes: references/dispute-reasons.md
`) },
    { path: 'references/dispute-reasons.md', content: `# Dispute reasons

- **Not recognised**: the customer does not recognise the merchant name.
- **Not received**: the order shows as undelivered.
- **Duplicate**: the same order was charged twice.
- **Cancelled**: the order was cancelled but still charged.
` } ] },
  { name: 'shipping-delays', files: [
    { path: 'SKILL.md', content: skill('shipping-delays',
      'Use when a customer asks where their package is or reports a late delivery.',
      `# Shipping delays

1. Find the order with order-lookup and track the shipment.
2. Share the carrier status and the latest estimated delivery date.
3. If the package is more than 5 business days late, offer a replacement or a refund.
4. Never promise a delivery date the carrier has not given.

Carrier targets: references/carrier-sla.md
`) },
    { path: 'references/carrier-sla.md', content: `# Carrier delivery targets

| Service | Target | Late after |
| --- | --- | --- |
| Standard | 3-5 business days | 8 business days |
| Express | 1-2 business days | 3 business days |
| International | 7-14 business days | 20 business days |
` } ] },
  { name: 'escalation-tone', files: [
    { path: 'SKILL.md', content: skill('escalation-tone',
      'Use when a customer is upset or a conversation needs a hand-off to a human agent.',
      `# Escalation tone

- Acknowledge the problem in the first sentence. Apologise once, specifically.
- Say what happens next and when.
- Hand off to a human for legal threats, safety issues or a third failed attempt.

Approved phrases: references/phrases.md
`) },
    { path: 'references/phrases.md', content: `# Approved phrases

- "I'm sorry your order arrived damaged. Here is what I can do right now."
- "I've passed this to a senior colleague, who will reply within one business day."
- "Thanks for your patience while I check this."
` } ] },
  { name: 'order-lookup', files: [
    { path: 'SKILL.md', content: skill('order-lookup',
      'Use to find an order by order number or email address before taking any action on it.',
      `# Order lookup

1. Ask for the order number, or the email address used at checkout.
2. Confirm the delivery date, items, amount paid and whether a dispute is open.
3. Never read card numbers back to the customer.

Order fields: references/order-fields.md
`) },
    { path: 'references/order-fields.md', content: `# Order fields

| Field | Meaning |
| --- | --- |
| delivered_at | Carrier delivery timestamp |
| amount | Item price paid, excluding shipping |
| shipping | Shipping charged |
| disputed | A card dispute is open for this order |
` } ] },
];

export const ESCALATION_V2 = skill('escalation-tone',
  'Use when a customer is upset or a conversation needs a hand-off to a human agent.',
  `# Escalation tone

- Acknowledge the problem in the first sentence. Apologise once, specifically.
- Say what happens next and when.
- Offer a callback window when the customer asks for a person.
- Hand off to a human for legal threats, safety issues or a third failed attempt.

Approved phrases: references/phrases.md
`);

export const AGENTS_MD = `# Acme Support agent

You answer customer support conversations for Acme, an online homeware store.

## Ground rules
- Look up the order before you promise anything.
- Load the one skill that fits the request and follow it exactly.
- Use the matching business tool. Never claim an action you did not take.
- Keep replies short, friendly and specific.

## Hand-offs
Escalate to a human for legal threats, safety issues or repeated failures.
`;

export const RETURNS_AGENTS_MD = `# Acme Returns desk

You help customers return items bought in Acme stores.
Follow the linked refund policy exactly as approved for the returns desk.
`;

export const REQUESTS = {
  refund: 'My ceramic mug arrived cracked. Please refund this damaged order.',
  chargeback: 'I do not recognize this charge on my card statement. Dispute the charge.',
  shipping: 'Where is my package? Please track the shipment.',
  captured: 'The blender I ordered arrived with a cracked jug. Can I get my money back?',
};
