import { describe, expect, it } from "vitest";
import { dataProposalOf, missingRecordsOf, readFile } from "../orgData";

/* DA-3: an order row references a customer that isn't in the data. */
const orders = { ...readFile({ name: "orders.csv" }), inEnv: true };
const customers = { ...readFile({ name: "customers.csv" }), inEnv: true };
const scenarios = [
  { id: "sc-1", name: "Refund O-104 twice", task: "Caller asks to refund O-104 again." },
  { id: "sc-2", name: "Late return", task: "Return of O-220 on day 31." },
  { id: "sc-3", name: "Proposed", task: "O-377", proposed: { at: "x" } },
];

describe("rows that point at missing records", () => {
  it("flags the rows and names the scenarios that use them", () => {
    const [gap] = missingRecordsOf({ sources: [orders, customers], scenarios });
    expect(gap.table).toBe("orders");
    expect(gap.to).toBe("customers");
    expect(gap.rows.map((r) => r.row)).toEqual([104, 377]);
    expect(gap.scenarios.map((s) => s.id)).toEqual(["sc-1"]);
  });

  it("keeps the rows out of the draft as a blocked item that names its scenarios", () => {
    const item = dataProposalOf([orders, customers], { scenarios }).items.find(
      (i) => i.kind === "missing",
    );
    expect(item.blocking).toBe(true);
    expect(item.affects).toEqual(["Refund O-104 twice"]);
  });

  it("reports nothing once the rows are resolved", () => {
    const resolved = { ...orders, joins: { customer_id: "drop" } };
    expect(missingRecordsOf({ sources: [resolved, customers], scenarios })).toEqual([]);
  });
});
