import { describe, expect, it } from "vitest";
import {
  CATALOG_ITEMS,
  CATALOG_TABS,
  CODE_SECTIONS,
  searchCatalog,
} from "../buildDrawerCatalog";

const inTab = (tab) => CATALOG_ITEMS.filter((it) => it.tab === tab);

describe("build drawer catalog", () => {
  it("lists only the ways live in prod: two per tab", () => {
    expect(CATALOG_TABS.map((t) => t.id)).toEqual(["code", "hosted", "other"]);
    expect(inTab("code").map((it) => it.id)).toEqual(["github", "upload"]);
    expect(inTab("hosted").map((it) => it.id)).toEqual(["vapi", "retell"]);
    expect(inTab("other").map((it) => it.id)).toEqual(["livekit", "phone"]);
  });

  it("opens a setup for every way in", () => {
    for (const it of CATALOG_ITEMS) expect(it.setup?.panel).toBeTruthy();
  });

  it("builds for real only from the sources the build API supports", () => {
    const live = CATALOG_ITEMS.filter((it) => !it.setup.uiOnly).map(
      (it) => it.id,
    );
    expect(live.sort()).toEqual(
      ["github", "livekit", "phone", "retell", "upload", "vapi"].sort(),
    );
  });

  it("puts every code item in a known section", () => {
    const sections = CODE_SECTIONS.map((s) => s.id);
    for (const it of inTab("code")) expect(sections).toContain(it.section);
    expect(inTab("code").filter((it) => it.section === "sdk")).toHaveLength(0);
  });

  it("gives ids once", () => {
    const ids = CATALOG_ITEMS.map((it) => it.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("searches names and keywords, trimmed and case-insensitive", () => {
    expect(searchCatalog(CATALOG_ITEMS, "  VAPI ").map((it) => it.id)).toEqual([
      "vapi",
    ]);
    expect(searchCatalog(CATALOG_ITEMS, "zip").map((it) => it.id)).toEqual([
      "upload",
    ]);
    expect(searchCatalog(CATALOG_ITEMS, "   ")).toBe(CATALOG_ITEMS);
  });

  it("keeps em dashes out of the copy", () => {
    const copy = [
      ...CATALOG_ITEMS.flatMap((it) => [it.name, it.next]),
      ...CODE_SECTIONS.flatMap((s) => [s.title, s.line]),
    ].filter(Boolean);
    for (const text of copy) expect(text).not.toMatch(/—/);
  });

  it("says only the name on SDK rows", () => {
    for (const it of inTab("code").filter((i) => i.section === "sdk"))
      expect(it.next).toBeUndefined();
  });
});
