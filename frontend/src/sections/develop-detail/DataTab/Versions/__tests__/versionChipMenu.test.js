import { describe, expect, it, vi } from "vitest";
import {
  CHIP_MENU_VERSIONS,
  versionChipMenuItems,
  versionMenuLabel,
} from "../versionChipMenu";

const V = (n, extra = {}) => ({
  number: n,
  is_latest: false,
  tags: [],
  created_at: "2026-10-16T10:00:00Z",
  created_by: { name: "Maya Chen" },
  ...extra,
});

describe("version chip menu (one view, A1-10)", () => {
  it("leads with Version history (H) and Compare two versions…", () => {
    const onHistory = vi.fn();
    const onCompare = vi.fn();
    const items = versionChipMenuItems({
      versions: [V(1), V(2, { is_latest: true, tags: ["prod-failures"] })],
      viewingNumber: 2,
      onHistory,
      onCompare,
      onView: vi.fn(),
      now: Date.parse("2026-10-16T12:00:00Z"),
    });
    expect(items[0]).toMatchObject({
      label: "Version history",
      shortcut: "history",
    });
    expect(items[1]).toMatchObject({
      label: "Compare two versions…",
      disabled: false,
    });
    items[0].run();
    items[1].run();
    expect(onHistory).toHaveBeenCalled();
    expect(onCompare).toHaveBeenCalled();
    // Newest first, the viewed one checked.
    const versions = items.filter((i) => i.id?.startsWith("v"));
    expect(versions.map((i) => i.label)).toEqual([
      "v2 · latest · prod-failures",
      "v1",
    ]);
    expect(versions[0].checked).toBe(true);
    expect(versions[0].secondary).toBe("2h ago · Maya Chen");
  });

  it("keeps the last five and says why Compare is off", () => {
    const many = Array.from({ length: 8 }, (_, i) => V(i + 1));
    const items = versionChipMenuItems({
      versions: many,
      viewingNumber: 8,
      onHistory: vi.fn(),
      onCompare: vi.fn(),
      onView: vi.fn(),
    });
    expect(items.filter((i) => i.id?.startsWith("v"))).toHaveLength(
      CHIP_MENU_VERSIONS,
    );
    const one = versionChipMenuItems({
      versions: [V(1)],
      onHistory: vi.fn(),
      onCompare: vi.fn(),
      onView: vi.fn(),
    });
    expect(one[1]).toMatchObject({
      disabled: true,
      reason: "Compare needs at least two versions",
    });
    expect(versionMenuLabel(V(3, { tags: ["a", "b"] }))).toBe("v3 · a, b");
  });
});
