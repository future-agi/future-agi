import { describe, it, expect } from "vitest";
import { screen } from "@testing-library/react";
import { render, userEvent } from "src/utils/test-utils";

import {
  applyUserVisibility,
  resolveColumnVisibility,
  mergeNonCustomColumns,
  getSessionListColumnDef,
} from "../common";
import SessionCellRenderer from "../SessionCellRenderer";

describe("applyUserVisibility", () => {
  it("forces a non-default column visible when the user turned it on", () => {
    const col = { id: "total_tokens", isVisible: false };
    expect(applyUserVisibility(col, { total_tokens: true })).toEqual({
      id: "total_tokens",
      isVisible: true,
    });
  });

  it("leaves a non-default column hidden when the user did not enable it", () => {
    const col = { id: "user_id_type", isVisible: false };
    expect(applyUserVisibility(col, {})).toBe(col);
    expect(applyUserVisibility(col, { user_id_type: false })).toBe(col);
  });

  it("never overrides a default column (follows the backend)", () => {
    const col = { id: "duration", isVisible: false };
    expect(applyUserVisibility(col, { duration: true })).toBe(col);
  });

  it("passes through an already-visible column untouched", () => {
    const col = { id: "total_tokens", isVisible: true };
    expect(applyUserVisibility(col, { total_tokens: true })).toBe(col);
  });

  it("tolerates a missing updateObj", () => {
    const col = { id: "total_tokens", isVisible: false };
    expect(applyUserVisibility(col, undefined)).toBe(col);
  });
});

describe("resolveColumnVisibility (dropdown checkbox state)", () => {
  it("shows a backend-hidden non-default column as unchecked with no local override", () => {
    // Regression guard: pre-fix `updateObj[id] ?? true` wrongly returned true.
    const col = { id: "total_tokens", isVisible: false };
    expect(resolveColumnVisibility(col, {})).toBe(false);
  });

  it("lets a local override win over the backend value", () => {
    const col = { id: "total_tokens", isVisible: false };
    expect(resolveColumnVisibility(col, { total_tokens: true })).toBe(true);
  });

  it("falls back to the backend value, then to visible", () => {
    expect(resolveColumnVisibility({ id: "user_id", isVisible: true }, {})).toBe(
      true,
    );
    expect(resolveColumnVisibility({ id: "unknown" }, {})).toBe(true);
  });
});

describe("mergeNonCustomColumns", () => {
  it("keeps a user-shown non-default column visible on a fresh load (empty current)", () => {
    // Root cause A: fresh load routes every column through the `added` branch.
    const incoming = [
      { id: "session_id", isVisible: true },
      { id: "total_tokens", isVisible: false },
    ];
    const merged = mergeNonCustomColumns([], incoming, { total_tokens: true });
    expect(merged.find((c) => c.id === "total_tokens").isVisible).toBe(true);
    expect(merged.find((c) => c.id === "session_id").isVisible).toBe(true);
  });

  it("does not resurrect a non-default column the user never enabled", () => {
    const incoming = [{ id: "user_id_hash", isVisible: false }];
    const merged = mergeNonCustomColumns([], incoming, {});
    expect(merged[0].isVisible).toBe(false);
  });

  it("preserves visibility for kept columns and appends new ones", () => {
    const current = [{ id: "session_id", isVisible: true }];
    const incoming = [
      { id: "session_id", isVisible: true },
      { id: "total_tokens", isVisible: false },
    ];
    const merged = mergeNonCustomColumns(current, incoming, {
      total_tokens: true,
    });
    expect(merged.map((c) => c.id)).toEqual(["session_id", "total_tokens"]);
    expect(merged[1].isVisible).toBe(true);
  });
});

describe("getSessionListColumnDef — custom columns", () => {
  const customCol = (overrides = {}) => ({
    id: "attr.flag",
    name: "Flag",
    isVisible: true,
    groupBy: "Custom Columns",
    ...overrides,
  });

  it("renders through SessionCellRenderer with the isCustomColumn flag", () => {
    const def = getSessionListColumnDef(customCol());
    expect(def.cellRenderer).toBe(SessionCellRenderer);
    expect(def.cellRendererParams).toEqual({ isCustomColumn: true });
    // The old valueFormatter path is gone — the renderer owns display now.
    expect(def.valueFormatter).toBeUndefined();
  });

  describe("valueGetter stringifies each representative value", () => {
    const getValue = (data, id = "attr.flag") =>
      getSessionListColumnDef(customCol({ id })).valueGetter({ data });

    it("keeps falsey scalars as their string form (not blank)", () => {
      // Regression guard: a naive truthiness check would drop these to a dash.
      expect(getValue({ "attr.flag": false })).toBe("false");
      expect(getValue({ "attr.flag": 0 })).toBe("0");
    });

    it("JSON-stringifies objects and arrays", () => {
      expect(getValue({ "attr.flag": { a: 1 } })).toBe('{"a":1}');
      expect(getValue({ "attr.flag": [1, 2] })).toBe("[1,2]");
    });

    it("returns null for missing / null values", () => {
      expect(getValue({})).toBeNull();
      expect(getValue({ "attr.flag": null })).toBeNull();
      expect(getValue(null)).toBeNull();
    });

    it("resolves dot-notation attribute keys", () => {
      expect(
        getSessionListColumnDef(customCol({ id: "meta.env" })).valueGetter({
          data: { meta: { env: "prod" } },
        }),
      ).toBe("prod");
    });
  });
});

describe("SessionCellRenderer — custom column cell", () => {
  // Drive the getter → renderer pipeline so a source value can't silently vanish.
  const cellText = (source) => {
    const def = getSessionListColumnDef({
      id: "attr.flag",
      name: "Flag",
      isVisible: true,
      groupBy: "Custom Columns",
    });
    const value = def.valueGetter({ data: { "attr.flag": source } });
    render(
      <SessionCellRenderer
        column={{ colId: "attr.flag" }}
        value={value}
        data={{}}
        isCustomColumn
      />,
    );
  };

  it("shows false and 0 as text rather than an empty dash", () => {
    cellText(false);
    expect(screen.getByText("false")).toBeInTheDocument();
  });

  it("shows 0", () => {
    cellText(0);
    expect(screen.getByText("0")).toBeInTheDocument();
  });

  it("shows a stringified object", () => {
    // The getter JSON-stringifies objects, so the renderer receives a plain
    // string and takes the text branch (not the JSON-viewer branch).
    cellText({ a: 1 });
    expect(screen.getByText('{"a":1}')).toBeInTheDocument();
  });

  it("shows a stringified array", () => {
    cellText([1, 2]);
    expect(screen.getByText("[1,2]")).toBeInTheDocument();
  });

  it("falls back to a dash for a missing value", () => {
    cellText(undefined);
    expect(screen.getByText("-")).toBeInTheDocument();
  });

  it("enables the hover tooltip for custom columns (the PR's UX change)", async () => {
    const user = userEvent.setup();
    render(
      <SessionCellRenderer
        column={{ colId: "attr.flag" }}
        value="a-long-custom-value"
        data={{}}
        isCustomColumn
      />,
    );
    await user.hover(screen.getByText("a-long-custom-value"));
    const tooltip = await screen.findByRole("tooltip");
    expect(tooltip).toHaveTextContent("a-long-custom-value");
  });

  it("leaves the tooltip off for a non-custom, non-whitelisted column", async () => {
    const user = userEvent.setup();
    render(
      <SessionCellRenderer
        column={{ colId: "attr.flag" }}
        value="a-long-custom-value"
        data={{}}
      />,
    );
    await user.hover(screen.getByText("a-long-custom-value"));
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });
});
