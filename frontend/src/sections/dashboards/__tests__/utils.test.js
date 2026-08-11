import { describe, it, expect } from "vitest";

import {
  formatDashboardListDate,
  formatDashboardTooltipDate,
  formatDashboardWidgetCount,
  getAvatarColor,
  getDashboardCreatorLabel,
  getDashboardCreatorName,
  getDashboardPeopleSummary,
  getDashboardViewers,
  getInitials,
  labelCreatorsWithStableUnknownIndex,
  timeAgo,
} from "../utils";
import { AVATAR_COLORS } from "../constants";

describe("formatDashboardWidgetCount", () => {
  it("singularizes exactly one widget", () => {
    expect(formatDashboardWidgetCount(1)).toBe("1 widget");
    expect(formatDashboardWidgetCount("1")).toBe("1 widget");
  });

  it("pluralizes zero and many widgets", () => {
    expect(formatDashboardWidgetCount(0)).toBe("0 widgets");
    expect(formatDashboardWidgetCount(7)).toBe("7 widgets");
  });

  it("falls back to zero for missing or non-numeric counts", () => {
    expect(formatDashboardWidgetCount(null)).toBe("0 widgets");
    expect(formatDashboardWidgetCount(undefined)).toBe("0 widgets");
    expect(formatDashboardWidgetCount("not-a-number")).toBe("0 widgets");
    expect(formatDashboardWidgetCount(Infinity)).toBe("0 widgets");
  });
});

describe("formatDashboardListDate", () => {
  it("renders a calendar date, never relative text", () => {
    expect(formatDashboardListDate("2026-06-15T12:00:00.000Z")).toBe(
      "15 Jun 2026",
    );
  });

  it("renders an em dash for missing or invalid dates", () => {
    expect(formatDashboardListDate(null)).toBe("—");
    expect(formatDashboardListDate(undefined)).toBe("—");
    expect(formatDashboardListDate("")).toBe("—");
  });
});

describe("formatDashboardTooltipDate", () => {
  it("returns an empty string rather than a dash for missing dates", () => {
    expect(formatDashboardTooltipDate(null)).toBe("");
    expect(formatDashboardTooltipDate("")).toBe("");
  });

  it("includes time detail for a real date", () => {
    expect(formatDashboardTooltipDate("2026-06-15T12:00:00.000Z")).toContain(
      "2026",
    );
  });
});

describe("timeAgo", () => {
  it("returns an empty string for missing dates", () => {
    expect(timeAgo(null)).toBe("");
    expect(timeAgo(undefined)).toBe("");
  });
});

describe("creator naming", () => {
  it("reads the creator name when present", () => {
    expect(getDashboardCreatorName({ created_by: { name: "Alice" } })).toBe(
      "Alice",
    );
  });

  it("never falls back to an email address", () => {
    const db = { created_by: { email: "owner@example.com" } };

    expect(getDashboardCreatorName(db)).toBe("");
    expect(getDashboardCreatorLabel(db)).toBe("Unknown creator");
    expect(getDashboardCreatorLabel(db)).not.toContain("@");
  });

  it("handles a null or missing creator", () => {
    expect(getDashboardCreatorLabel({ created_by: null })).toBe(
      "Unknown creator",
    );
    expect(getDashboardCreatorLabel({})).toBe("Unknown creator");
    expect(getDashboardCreatorLabel(undefined)).toBe("Unknown creator");
  });
});

describe("labelCreatorsWithStableUnknownIndex", () => {
  it("leaves named creators untouched", () => {
    const entries = [{ email: "a@example.com", name: "Alice" }];

    expect(labelCreatorsWithStableUnknownIndex(entries)).toEqual(entries);
  });

  it("does not number a lone unnamed creator", () => {
    const result = labelCreatorsWithStableUnknownIndex([
      { email: "a@example.com", name: "Alice" },
      { email: "z@example.com", name: "" },
    ]);

    expect(result[1].name).toBe("Unknown creator");
  });

  it("gives each unnamed creator a suffix that survives reordering", () => {
    const entries = [
      { email: "zoe@example.com", name: "" },
      { email: "amy@example.com", name: "" },
      { email: "named@example.com", name: "Named" },
    ];

    const first = labelCreatorsWithStableUnknownIndex(entries);
    // Same people, different list order — as happens on any refetch or re-sort.
    const reordered = labelCreatorsWithStableUnknownIndex([
      entries[2],
      entries[1],
      entries[0],
    ]);

    const labelFor = (result, email) =>
      result.find((creator) => creator.email === email).name;

    expect(labelFor(first, "amy@example.com")).toBe("Unknown creator 1");
    expect(labelFor(first, "zoe@example.com")).toBe("Unknown creator 2");

    // The label a given person gets must not depend on iteration order.
    expect(labelFor(reordered, "amy@example.com")).toBe("Unknown creator 1");
    expect(labelFor(reordered, "zoe@example.com")).toBe("Unknown creator 2");
  });

  it("never leaks an email into an unnamed creator label", () => {
    const result = labelCreatorsWithStableUnknownIndex([
      { email: "a@example.com", name: "" },
      { email: "b@example.com", name: "" },
    ]);

    result.forEach((creator) => expect(creator.name).not.toContain("@"));
  });
});

describe("getDashboardViewers", () => {
  it("dedupes a person who both created and last updated the dashboard", () => {
    const viewers = getDashboardViewers({
      created_by: { name: "Alice", email: "alice@example.com" },
      created_at: "2026-05-01T00:00:00.000Z",
      updated_by: { name: "Alice", email: "alice@example.com" },
      updated_at: "2026-06-01T00:00:00.000Z",
    });

    expect(viewers).toHaveLength(1);
  });

  it("skips people with no email and labels missing names", () => {
    const viewers = getDashboardViewers({
      created_by: { email: "owner@example.com" },
      updated_by: { name: "No Email" },
    });

    expect(viewers).toHaveLength(1);
    expect(viewers[0].displayName).toBe("Unknown user");
  });

  it("returns an empty list when nobody is attached", () => {
    expect(getDashboardViewers({})).toEqual([]);
  });
});

describe("getDashboardPeopleSummary", () => {
  it("summarizes zero, one and many people", () => {
    expect(getDashboardPeopleSummary({})).toBe("No people");
    expect(
      getDashboardPeopleSummary({
        created_by: { name: "Alice", email: "alice@example.com" },
      }),
    ).toBe("1 person");
    expect(
      getDashboardPeopleSummary({
        created_by: { name: "Alice", email: "alice@example.com" },
        updated_by: { name: "Uma", email: "uma@example.com" },
      }),
    ).toBe("2 people");
  });
});

describe("getInitials", () => {
  it("uses the first letter of the first two words", () => {
    expect(getInitials("Alice Creator")).toBe("AC");
    expect(getInitials("  Alice   Creator  ")).toBe("AC");
  });

  it("uses the first two letters of a single word", () => {
    expect(getInitials("Alice")).toBe("AL");
  });

  it("falls back to a question mark for missing names", () => {
    expect(getInitials("")).toBe("?");
    expect(getInitials(null)).toBe("?");
    expect(getInitials(undefined)).toBe("?");
  });
});

describe("getAvatarColor", () => {
  it("always returns a color from the palette", () => {
    ["Alice", "Bob", "", null, undefined].forEach((name) => {
      expect(AVATAR_COLORS).toContain(getAvatarColor(name));
    });
  });

  it("is stable for the same name", () => {
    expect(getAvatarColor("Alice Creator")).toBe(
      getAvatarColor("Alice Creator"),
    );
  });
});
