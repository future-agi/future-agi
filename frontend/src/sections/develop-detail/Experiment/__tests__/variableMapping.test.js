import { describe, expect, it } from "vitest";
import {
  autoMapVariable,
  autoMapVariables,
  mappedValueOnRow,
  mappingChipLabel,
  mappingStatus,
  normalizeName,
  unmappedMessage,
  variablesInTemplate,
} from "../variableMapping";

const columns = [
  { id: "c1", name: "customer_message" },
  { id: "c2", name: "Customer Tier" },
  { id: "c3", name: "conversation" },
];

describe("variableMapping", () => {
  it("normalizes snake, camel and spaces", () => {
    expect(normalizeName("customerTier")).toBe("customer_tier");
    expect(normalizeName("Customer Tier")).toBe("customer_tier");
    expect(normalizeName("customer-tier")).toBe("customer_tier");
  });

  it("maps exact, then case-insensitive, then normalized", () => {
    expect(autoMapVariable("customer_message", columns)).toEqual({
      columnId: "c1",
      how: "exact",
    });
    expect(autoMapVariable("CONVERSATION", columns)).toEqual({
      columnId: "c3",
      how: "case",
    });
    expect(autoMapVariable("customer_tier", columns)).toEqual({
      columnId: "c2",
      how: "normalized",
    });
    // R4 D24: `user_message` now maps by meaning; an unrelated name stays null.
    expect(autoMapVariable("user_message", columns)).toEqual({
      columnId: "c1",
      how: "meaning",
    });
    expect(autoMapVariable("order_ref", columns)).toBeNull();
  });

  it("keeps valid user choices and leaves unknown variables empty", () => {
    const mapping = autoMapVariables(
      ["user_message", "customer_tier"],
      columns,
      { user_message: "c1", stale: "c9" },
    );
    expect(mapping).toEqual({ user_message: "c1", customer_tier: "c2" });
    expect(autoMapVariables(["order_ref"], columns, {})).toEqual({
      order_ref: "",
    });
  });

  it("reports status and names unmapped variables", () => {
    const s = mappingStatus(
      ["user_message", "customer_tier"],
      { user_message: "", customer_tier: "c2" },
      columns,
    );
    expect(s).toEqual({ total: 2, mapped: 1, unmapped: ["user_message"] });
    expect(mappingChipLabel(s)).toBe("1/2 mapped");
    expect(mappingChipLabel({ total: 2, mapped: 2 })).toBe("All mapped");
    expect(mappingChipLabel({ total: 2, mapped: 0 })).toBe("Not mapped");
    expect(unmappedMessage(["user_message", "tier"])).toBe(
      "2 variables have no column: user_message, tier.",
    );
    expect(unmappedMessage(["tier"])).toBe(
      "{{tier}} has no column. Pick a column, or leave it empty.",
    );
  });

  it("finds template variables once", () => {
    expect(
      variablesInTemplate('{"m": "{{ customer_message }}", "x": "{{a}}{{a}}"}'),
    ).toEqual(["customer_message", "a"]);
  });

  it("reads mapped values and JSON paths from a table row", () => {
    const row = {
      c1: { cell_value: "Where is my order?" },
      c3: { cell_value: '{"turns":[{"text":"hi"}]}' },
    };
    expect(mappedValueOnRow(row, "c1")).toBe("Where is my order?");
    expect(mappedValueOnRow(row, "c3::$.turns[0].text")).toBe("hi");
    expect(mappedValueOnRow(row, "c9")).toBeNull();
  });
});

// P6 · D24: inputs map themselves by meaning.
import {
  mappingHowOf as _how,
  autoMapVariable as _auto,
} from "../variableMapping";
describe("by meaning (R4 P6)", () => {
  const golden = [
    { id: "c1", name: "customer_message" },
    { id: "c2", name: "expected_reply" },
  ];
  it("user_message → customer_message, marked matched by meaning", () => {
    expect(_auto("user_message", golden)).toEqual({
      columnId: "c1",
      how: "meaning",
    });
    expect(_how("user_message", "c1", golden)).toBe("meaning");
  });
  it("same names are not marked", () => {
    expect(_how("customer_message", "c1", golden)).toBe(null);
  });
  it("an unrelated variable stays unmapped", () => {
    expect(_auto("tier", golden)).toBe(null);
  });
});

describe("IT-R4 G1 P1 · a variable with no column never blocks", () => {
  const cols = [
    { id: "c1", name: "customer_message" },
    { id: "c9", name: "metadata" },
  ];
  it("counts empty and constant mappings as mapped", async () => {
    const m = await import("../variableMapping");
    expect(m.isValidMapping({ kind: "empty" }, cols)).toBe(true);
    expect(m.isValidMapping({ kind: "constant", value: "x" }, cols)).toBe(true);
    // A constant with nothing typed yet is not a mapping.
    expect(m.isValidMapping({ kind: "constant", value: "" }, cols)).toBe(false);
    expect(m.isValidMapping("c1", cols)).toBe(true);
    expect(m.isValidMapping("c9::$.customer.tier", cols)).toBe(true);
    expect(m.isValidMapping("gone", cols)).toBe(false);
    expect(m.isValidMapping("", cols)).toBe(false);
    expect(m.isValidMapping(undefined, cols)).toBe(false);
    expect(
      m.mappingStatus(
        ["customer_message", "customer_tier", "customer_email", "order"],
        {
          customer_message: "c1",
          customer_tier: { kind: "empty" },
          customer_email: { kind: "constant", value: "test@acme.com" },
        },
        cols,
      ),
    ).toEqual({ total: 4, mapped: 3, unmapped: ["order"] });
  });

  it("resolves fixed mappings on any row and keeps them on auto-map", async () => {
    const m = await import("../variableMapping");
    expect(m.mappedValueOnRow(null, { kind: "empty" })).toBe("");
    expect(m.mappedValueOnRow({}, { kind: "constant", value: "vip" })).toBe(
      "vip",
    );
    const current = { customer_tier: { kind: "empty" } };
    const next = m.autoMapVariables(["customer_tier"], cols, current);
    expect(next.customer_tier).toBe(current.customer_tier);
    expect(m.fixedMappingLabel({ kind: "constant", value: "enterprise" })).toBe(
      '"enterprise" · same for every row',
    );
    expect(m.leaveEmptyLabel(1)).toBe("Leave empty");
    expect(m.leaveEmptyLabel(2)).toBe("Leave both empty");
    expect(m.leaveEmpty({ a: "c1" }, ["b", "c"])).toEqual({
      a: "c1",
      b: { kind: "empty" },
      c: { kind: "empty" },
    });
  });

  it("suggests the column or nested path sharing the most tokens", async () => {
    const m = await import("../variableMapping");
    const options = [
      { value: "c1", label: "customer_message" },
      {
        value: "c9::$.customer.tier",
        label: "metadata.customer.tier",
        path: true,
        coverage: { present: 30, total: 30 },
      },
      { value: "c9::$.order.id", label: "metadata.order.id", path: true },
    ];
    expect(m.suggestedOption("customer_tier", options)?.value).toBe(
      "c9::$.customer.tier",
    );
    expect(m.suggestedOption("refund_reason", options)).toBeNull();
    // Shares `customer` only: not what customer_tier holds.
    expect(
      m.suggestedOption("customer_tier", [
        { value: "g", label: "customer_goal" },
      ]),
    ).toBeNull();
  });

  it("reads variables from the prompt's messages", async () => {
    const m = await import("../variableMapping");
    expect(
      m.variablesInMessages([
        { role: "system", content: [{ type: "text", text: "Tier {{tier}}" }] },
        { role: "user", content: "{{customer_email}} {{ tier }}" },
      ]),
    ).toEqual(["tier", "customer_email"]);
  });
});
