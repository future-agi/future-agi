import { describe, expect, it } from "vitest";
import { checkableRuleIds, reviewItems } from "../importSourceReview";

// Trimmed from real review previews captured on a local stack.
const src = "src_3bdd80a23023454a80e42c4e1d405463";
const statement = (n, text) => ({
  page: 1,
  text,
  statement_id: `${src}:page:1:statement:${n}`,
  text_truncated: false,
});
const rule = (n, status, title, check_type = null) => ({
  kind: "required_behavior",
  status,
  rule_id: `${src}:page:1:statement:${n}:rule`,
  check_type,
  statement_id: `${src}:page:1:statement:${n}`,
  conflict_group: null,
  ...(title && { title }),
});
const policyCheckable = {
  sources: [
    {
      pin: { role: "policy", source_id: src },
      filename: "refund-rules.pdf",
      origin: { kind: "upload" },
      statements: [
        statement(1, "Refund rules"),
        statement(2, 'The response must contain "order number".'),
        statement(3, 'The response must not contain "full card number".'),
        statement(
          4,
          "Refunds are paid back to the original payment method within 5 business days.",
        ),
        statement(5, 'Always include "Refund ID" in every reply.'),
      ],
    },
  ],
  proposals: {
    policy_rules: [
      rule(1, "unsupported"),
      rule(2, "supported", "Responses must include “order number”", "contains"),
      rule(
        3,
        "supported",
        "Responses must not include “full card number”",
        "not_contains",
      ),
      rule(4, "unsupported"),
      rule(5, "supported", "Responses must include “Refund ID”", "contains"),
    ],
    relationships: [],
    field_mappings: [],
  },
  diagnostics: [],
};
const privacyText = {
  sources: [
    {
      pin: { role: "reference", source_id: "src_3efd" },
      filename: "callback-notes.txt",
      origin: { kind: "upload" },
      statements: [{ page: 1, statement_id: "src_3efd:page:1:statement:1" }],
    },
  ],
  proposals: { policy_rules: [], relationships: [], field_mappings: [] },
  diagnostics: [
    {
      code: "source_privacy_unresolved",
      span: { row: null, page: 1, source_id: "src_3efd" },
      severity: "blocking",
    },
  ],
};

describe("reviewItems", () => {
  it("turns titled supported rules into ticked rules and keeps the rest as background", () => {
    const items = reviewItems(policyCheckable, { testedAgent: true });
    const rules = items.filter((item) => item.kind === "rule");
    expect(rules.map((item) => [item.title, item.cite.ref])).toEqual([
      ["Responses must include “order number”", "refund-rules.pdf, page 1"],
      [
        "Responses must not include “full card number”",
        "refund-rules.pdf, page 1",
      ],
      ["Responses must include “Refund ID”", "refund-rules.pdf, page 1"],
    ]);
    expect(rules[0].cite.excerpt).toBe(
      'The response must contain "order number".',
    );
    // The file's own heading line isn't a passage.
    const [reference] = items.filter((item) => item.kind === "reference");
    expect(reference.read.passages.map((passage) => passage.text)).toEqual([
      "Refunds are paid back to the original payment method within 5 business days.",
    ]);
    expect(reference.rules).toBe(3);
    expect(checkableRuleIds(policyCheckable)).toEqual(rules.map((r) => r.id));
  });

  it("gives withheld text a page count and a personal data choice, with no rules", () => {
    const items = reviewItems(privacyText, {});
    expect(items.map((item) => item.kind)).toEqual(["reference", "personal"]);
    expect(items[0]).toMatchObject({ withheld: true, pages: 1 });
    expect(items[1]).toMatchObject({
      id: "src_3efd:personal",
      sourceId: "src_3efd",
      title: "Personal data in callback-notes.txt",
    });
  });

  it("reads a repair preview that carries only file counts", () => {
    const preview = {
      sources: [
        {
          pin: { role: "policy", source_id: "s" },
          filename: "refund.pdf",
          row_count: 0,
          field_count: 0,
        },
      ],
    };
    expect(reviewItems(preview, {})).toEqual([]);
    expect(checkableRuleIds(preview)).toEqual([]);
  });
});
