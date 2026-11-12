// Auto routing and catalog polish: decisions newest first by the instant
// they started, times that name their zone, own-model price lines that say
// what the gateway did, and grids that keep their first column in view when
// the columns run past the edge.
import React from "react";
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { localZoneName } from "../../utils/timeZone";
import { catalogFreshness } from "../catalog";
import { catalogColumns } from "../catalog/columns";
import {
  decisionsFromLogs,
  gatewayPriceEvidence,
  newestFirst,
} from "./autoDecisions";
import { decisionColumns, decisionColumnsFor } from "./decisionColumns";

const log = (id, startedAt, decision) => ({
  id,
  request_id: `req-${id}`,
  started_at: startedAt,
  status_code: 200,
  metadata: {
    auto_model: "m",
    ...(decision ? { auto_decision: JSON.stringify(decision) } : {}),
  },
});
const byId = (cols, id) => cols.find((c) => c.colId === id);

describe("recent decisions are newest first", () => {
  // The read's own order is not trusted, and the stamps are compared as
  // instants: "18:00+02:00" is 16:00 UTC, earlier than "17:00Z".
  it("orders by when each request started, whatever the read's order and offsets", () => {
    const decisions = decisionsFromLogs([
      log("a", "2026-11-11T17:30:00Z"),
      log("b", "2026-11-11T09:09:00-08:00"),
      log("c", "2026-11-11T17:45:00.123456+00:00"),
      log("d", "2026-11-11T17:30:00.000Z"),
      log("e", "2026-11-11T18:00:00+02:00"),
    ]);
    expect(decisions.map((d) => d.id)).toEqual(["c", "d", "a", "b", "e"]);
  });

  it("puts a decision with no readable time last", () => {
    const decisions = decisionsFromLogs([
      log("x", "not a time"),
      log("y", "2026-11-11T17:00:00Z"),
    ]);
    expect(decisions.map((d) => d.id)).toEqual(["y", "x"]);
  });

  it("breaks a tie the same way every time", () => {
    const one = { id: "a", at: "2026-11-11T17:30:00Z" };
    const two = { id: "b", at: "2026-11-11T17:30:00.000Z" };
    expect(newestFirst(one, two)).toBe(-newestFirst(two, one));
    expect(newestFirst(one, two)).not.toBe(0);
  });

  // The grid sorts the Time column by instant too, not by the text.
  it("sorts the Time column by instant", () => {
    const time = byId(decisionColumns, "at");
    expect(time.sort).toBe("desc");
    const a = { data: { id: "a" } };
    const b = { data: { id: "b" } };
    expect(
      time.comparator(
        "2026-11-11T18:00:00+02:00",
        "2026-11-11T17:00:00Z",
        a,
        b,
      ),
    ).toBeLessThan(0);
    expect(
      time.comparator("2026-11-11T17:45:00Z", "2026-11-11T17:30:00Z", a, b),
    ).toBeGreaterThan(0);
  });
});

describe("decision times name their zone", () => {
  it("shows the exact time with its zone on hover", () => {
    const Cell = byId(decisionColumns, "at").cellRenderer;
    render(<Cell value="2026-11-11T17:30:05.250Z" />);
    const cell = screen.getByTestId("decision-time");
    expect(cell.getAttribute("title")).toContain(localZoneName());
    expect(cell.getAttribute("title")).toMatch(/:05\.250/);
    expect(cell).toHaveTextContent(/2026/);
  });

  it("shows a dash with no time", () => {
    const Cell = byId(decisionColumns, "at").cellRenderer;
    render(<Cell value={null} />);
    expect(screen.getByText("—")).toBeInTheDocument();
  });
});

describe("catalog refresh times name their zone", () => {
  it("names the zone where it says when prices were fetched", () => {
    const fetched = catalogFreshness([
      {
        provider: "openrouter",
        source: "live",
        fetched_at: "2026-11-11T17:30:00Z",
      },
    ]);
    expect(fetched.tip).toContain(`Catalog fetched `);
    expect(fetched.tip).toContain(localZoneName());
    const bundled = catalogFreshness([
      {
        provider: "acme",
        source: "bundled",
        fetched_at: "2026-11-11T17:30:00Z",
      },
    ]);
    expect(bundled.tip).toContain(localZoneName());
  });
});

describe("what the gateway did about a model's price", () => {
  const priced = {
    ranked: [
      { model: "o3-deep-research", provider: "walk", est_cost_usd: 0.0123 },
    ],
  };
  const unpriced = {
    ranked: [],
    rejected: [
      { model: "my-tuned-model", reason: "no price data" },
      { model: "o3-deep-research", reason: "no price data" },
      { model: "bare-model", reason: "no price or capability data" },
      { model: "other", reason: "price_cap" },
    ],
  };

  it("reads the newest decision that ranked or set aside each model", () => {
    const decisions = decisionsFromLogs([
      log("old", "2026-11-11T10:00:00Z", unpriced),
      log("new", "2026-11-11T17:00:00Z", priced),
    ]);
    const evidence = gatewayPriceEvidence(decisions);
    expect(evidence.get("o3-deep-research")).toBe("priced");
    expect(evidence.get("walk/o3-deep-research")).toBe("priced");
    expect(evidence.get("my-tuned-model")).toBe("unpriced");
    expect(evidence.get("bare-model")).toBe("unpriced");
    expect(evidence.has("other")).toBe(false);
  });

  it("does not count a ranked model with no estimate as priced", () => {
    const decisions = decisionsFromLogs([
      log("n", "2026-11-11T17:00:00Z", {
        ranked: [{ model: "x", provider: "p" }],
      }),
    ]);
    expect(gatewayPriceEvidence(decisions).has("x")).toBe(false);
  });
});

describe("grids keep their first column in view when narrow", () => {
  it("pins the catalog's model name with a fixed width when asked", () => {
    // null, not undefined: AG Grid keeps a pin a column def leaves undefined.
    const wide = byId(catalogColumns({ markupPercent: 5 }), "name");
    expect(wide.pinned).toBeNull();
    expect(wide.flex).toBeGreaterThan(0);
    const narrow = byId(
      catalogColumns({ markupPercent: 5, pinName: true }),
      "name",
    );
    expect(narrow.pinned).toBe("left");
    expect(narrow.flex).toBeUndefined();
    expect(narrow.width).toBeGreaterThanOrEqual(narrow.minWidth);
  });

  it("pins the decisions' Time column with a fixed width when narrow", () => {
    expect(byId(decisionColumnsFor({ narrow: false }), "at").pinned).toBeNull();
    const time = byId(decisionColumnsFor({ narrow: true }), "at");
    expect(time.pinned).toBe("left");
    expect(time.flex).toBeUndefined();
    expect(time.width).toBeGreaterThanOrEqual(time.minWidth);
    expect(time.sort).toBe("desc");
  });
});
