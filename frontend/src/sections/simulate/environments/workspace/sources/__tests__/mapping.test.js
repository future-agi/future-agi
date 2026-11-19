import { describe, expect, it } from "vitest";
import { readSource } from "src/api/simulate-environments/_fixtures/sources";
import { approveIdMapping, joinsOf } from "../mapping";

const read = (name) =>
  readSource({ name, size: 1, origin: { kind: "upload" } });

describe("joinsOf", () => {
  const orders = read("orders.csv");
  const customers = read("customers.csv");

  it("blocks on every row while the table it points at isn't attached", () => {
    const removed = { ...customers, ui: { ...customers.ui, removed: true } };
    [[orders], [orders, removed]].forEach((sources) => {
      const [join] = joinsOf(orders, sources);
      expect(join).toMatchObject({ present: false, missing: 500 });
      expect(join.blocking).toBe(true);
    });
  });

  it("lists only the rows that miss it once attached, and stops blocking when left out", () => {
    const [join] = joinsOf(orders, [orders, customers]);
    expect(join.present).toBe(true);
    expect(join.rows.map(({ row }) => row)).toEqual([104, 377]);
    expect(join.blocking).toBe(true);

    const omitted = {
      ...orders,
      ui: { ...orders.ui, joins: { customer_id: "omit" } },
    };
    expect(joinsOf(omitted, [omitted, customers])[0].blocking).toBe(false);
  });
});

describe("approveIdMapping", () => {
  it("points the column at the other ID and rejects the mismatched rows of a saved file", () => {
    const q3 = { ...read("orders-q3-sample.csv"), status: "ready" };
    const next = approveIdMapping(q3, "buyer_id", { replace: true });
    const [join] = joinsOf(next, [next]);
    expect(join).toMatchObject({ field: "buyer_id", present: false });
    expect(next.status).toBe("rows_rejected");
    expect(next.ui.parsed.accepted).toBe(498);
    expect(next.ui.privacy.buyer_id).toBe("replace");
  });

  it("sets the file's personal-data choice to custom once replacing IDs sets a column apart", () => {
    const q3 = read("orders-q3-sample.csv");
    const kept = {
      ...q3,
      ui: { ...q3.ui, personal_choice: "keep", privacy: { email: "retain" } },
    };
    const replace = (source) =>
      approveIdMapping(source, "buyer_id", { replace: true }).ui
        .personal_choice;

    expect(replace(kept)).toBe("custom");
    // Not chosen yet stays unchosen, so the file still asks.
    expect(replace(q3)).toBeFalsy();
  });
});
