import { describe, expect, it, vi } from "vitest";

vi.mock("src/components/iconify", () => ({ default: () => null }));

import { allDuplicatesSentence } from "../PreviewPane";

const dup = (name) => ({
  status: "duplicate",
  warnings: [`Already in ${name}`],
});

describe("IT-R5 G5 P4: no dead-end import names where matches live", () => {
  it("says this dataset only when the dedupe result says so", () => {
    expect(
      allDuplicatesSentence([dup("Golden"), dup("Golden")], "Golden"),
    ).toBe("All 2 matching sessions are already in this dataset.");
  });

  it("names the other dataset instead of claiming this one", () => {
    expect(
      allDuplicatesSentence([dup("Support ops"), dup("Support ops")], "Golden"),
    ).toBe('All 2 matching sessions are already in "Support ops".');
  });

  it("falls back to other datasets for a mix", () => {
    expect(allDuplicatesSentence([dup("A"), dup("B")], "Golden")).toBe(
      "All 2 matching sessions are already in other datasets.",
    );
    expect(allDuplicatesSentence([dup("A")], "Golden")).toBe(
      'The matching session is already in "A".',
    );
  });
});
