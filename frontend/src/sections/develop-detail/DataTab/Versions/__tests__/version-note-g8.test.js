// v2.1 g8 (corrections:provenance): the version chip menu shows a version's
// note, so "1 correction from review “review” by Maya Chen" is seen where the
// version is picked.
import { describe, expect, it, vi } from "vitest";
import { noteOf, versionChipMenuItems } from "../versionChipMenu";

describe("version note in the chip menu", () => {
  it("adds the note after time and author", () => {
    const items = versionChipMenuItems({
      versions: [
        {
          number: 7,
          is_latest: true,
          tags: [],
          created_at: "2026-10-16T10:00:00Z",
          created_by: { name: "Maya Chen" },
          description: "1 correction from review “B17 QA queue” by Maya Chen",
        },
      ],
      viewingNumber: 7,
      onHistory: vi.fn(),
      onCompare: vi.fn(),
      onView: vi.fn(),
      now: Date.parse("2026-10-16T12:00:00Z"),
    });
    const v7 = items.find((i) => i.id === "v7");
    expect(v7.secondary).toBe(
      "2h ago · Maya Chen · 1 correction from review “B17 QA queue” by Maya Chen",
    );
  });

  it("clips a long note to one line and skips an empty one", () => {
    expect(noteOf({ description: `${"x".repeat(100)}\nmore` })).toHaveLength(
      80,
    );
    expect(noteOf({ description: "  " })).toBeNull();
    expect(noteOf({})).toBeNull();
  });
});
