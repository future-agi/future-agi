import { describe, it, expect, vi } from "vitest";
import { getDateLabel } from "../Sessions-view";

vi.hoisted(() => {
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: {
      clear: vi.fn(),
      getItem: vi.fn(() => null),
      removeItem: vi.fn(),
      setItem: vi.fn(),
    },
  });
});

describe("Sessions-view getDateLabel", () => {
  it("returns 'Past 7D' when dateFilter is undefined", () => {
    expect(getDateLabel(undefined)).toBe("Past 7D");
  });

  it("returns 'Past 7D' when dateFilter is an empty object", () => {
    expect(getDateLabel({})).toBe("Past 7D");
  });

  it("maps preset options to 'Past <option>' labels", () => {
    expect(getDateLabel({ dateOption: "7D" })).toBe("Past 7D");
    expect(getDateLabel({ dateOption: "30D" })).toBe("Past 30D");
    expect(getDateLabel({ dateOption: "6M" })).toBe("Past 6M");
    expect(getDateLabel({ dateOption: "12M" })).toBe("Past 12M");
  });

  it("returns option as-is for Today/Yesterday", () => {
    expect(getDateLabel({ dateOption: "Today" })).toBe("Today");
    expect(getDateLabel({ dateOption: "Yesterday" })).toBe("Yesterday");
  });

  it("renders the picked date range when dateOption is 'Custom' — the URL-restore case", () => {
    const label = getDateLabel({
      dateOption: "Custom",
      dateFilter: ["2026-03-01 00:00:00", "2026-03-05 23:59:59"],
    });
    const expectedStart = new Date("2026-03-01 00:00:00").toLocaleDateString();
    const expectedEnd = new Date("2026-03-05 23:59:59").toLocaleDateString();
    expect(label).toBe(`${expectedStart} - ${expectedEnd}`);
  });

  it("falls back to 'Past 7D' when Custom has a malformed date array", () => {
    expect(
      getDateLabel({ dateOption: "Custom", dateFilter: ["bogus", "also-bad"] }),
    ).toBe("Past 7D");
    expect(getDateLabel({ dateOption: "Custom", dateFilter: [] })).toBe(
      "Past 7D",
    );
    expect(
      getDateLabel({ dateOption: "Custom", dateFilter: ["2026-03-01"] }),
    ).toBe("Past 7D");
  });
});
