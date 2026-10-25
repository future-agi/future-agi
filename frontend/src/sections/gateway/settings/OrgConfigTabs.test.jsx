// Org Config has no A2A tab (the gateway applies no per-org A2A section),
// and no A2A tab component ships. A stored a2a section is never deleted
// (OrgConfigEditor.tabs.test.jsx).
import { describe, it, expect } from "vitest";

describe("Org Config tab components", () => {
  it("ships no A2A config tab component", () => {
    const tabs = Object.keys(import.meta.glob("./*ConfigTab.jsx"));
    expect(tabs).toContain("./CacheConfigTab.jsx");
    expect(tabs).not.toContain("./A2AConfigTab.jsx");
  });
});
