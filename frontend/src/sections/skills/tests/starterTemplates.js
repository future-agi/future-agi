// Editable starting points. Ones that match the skill's own name or description come first;
// when nothing matches, everyday support requests are offered instead of unrelated domains.
const STARTERS = [
  {
    title: "Refund policy boundary",
    keywords: ["refund", "return", "eligib"],
    prompt:
      "An unopened item was delivered exactly 30 days ago. Is it eligible for a refund? Return JSON with an eligible boolean.",
    fixture:
      "Illustrative policy — replace with approved policy before reviewing: Unopened items are eligible through day 30, inclusive. Opened items are not eligible.",
    checks: [
      { kind: "json_equals", config: { pointer: "/eligible", value: true } },
    ],
  },
  {
    title: "Tenant isolation in a supplied patch",
    keywords: ["patch", "code review", "tenant", "security", "isolation"],
    prompt:
      "Review this supplied patch for tenant isolation. Identify a missing workspace constraint without claiming to run the repository.",
    fixture:
      "Illustrative tenant rule: every Document read must filter by both organization_id and workspace_id. Patch: Document.objects.filter(organization_id=organization.id, id=document_id).first()",
    checks: [
      { kind: "contains", config: { text: "workspace", caseSensitive: false } },
    ],
  },
  {
    title: "Structured output with a missing value",
    keywords: ["extract", "json", "invoice", "structured", "parse"],
    prompt:
      "Extract amount and invoice_id from the supplied text. Return only JSON. Use null when a value is absent.",
    fixture: "Amount: 12.50 USD. No invoice identifier was supplied.",
    checks: [
      { kind: "json_equals", config: { pointer: "/amount", value: 12.5 } },
      { kind: "json_equals", config: { pointer: "/invoice_id", value: null } },
    ],
  },
];
const SUPPORT_STARTERS = [
  {
    title: "Customer asks for a refund on a damaged item",
    keywords: ["refund", "damaged", "defective"],
    prompt: "My ceramic mug arrived cracked. Can I get a refund?",
    fixture:
      "Illustrative policy — replace with approved policy before reviewing: Damaged items are refunded in full within 30 days. Shipping is not refunded.",
    checks: [{ kind: "contains", config: { text: "refund", caseSensitive: false } }],
  },
  {
    title: "Customer asks where their order is",
    keywords: ["order", "shipping", "delivery", "package", "track"],
    prompt: "Where is my order 1042? It was supposed to arrive yesterday.",
    fixture: "Illustrative order record: order 1042 shipped two days ago with tracking number TRK-5531 and is in transit.",
    checks: [{ kind: "contains", config: { text: "TRK-5531", caseSensitive: false } }],
  },
  {
    title: "Customer doesn't recognise a charge",
    keywords: ["charge", "dispute", "billing", "payment", "card"],
    prompt: "I don't recognise a $49 charge on my card from you. What is it?",
    fixture: "Illustrative billing record: $49 annual plan renewal on 3 Nov for the account owner's card ending 4242.",
    checks: [{ kind: "contains", config: { text: "renewal", caseSensitive: false } }],
  },
];
export function startersFor(skill) {
  const text = `${skill?.name ?? ""} ${skill?.description ?? ""}`.toLowerCase();
  const matches = [...STARTERS, ...SUPPORT_STARTERS].filter((starter) => starter.keywords.some((word) => text.includes(word)));
  return matches.length ? matches : SUPPORT_STARTERS;
}
