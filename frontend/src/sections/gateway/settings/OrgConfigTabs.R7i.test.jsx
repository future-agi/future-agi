// NG-17: the org A2A tab is removed from Org Config (per-org agents, the card
// and the a2a/<name> provider never work); its component file goes with it,
// so nothing can mount it again. A stored a2a section is never deleted
// (OrgConfigEditor.R7ConsoleB.test.jsx).
import { describe, it, expect } from "vitest";

describe("Org Config tab components (NG-17)", () => {
  it("ships no A2A config tab component", () => {
    const tabs = Object.keys(import.meta.glob("./*ConfigTab.jsx"));
    expect(tabs).toContain("./CacheConfigTab.jsx");
    expect(tabs).not.toContain("./A2AConfigTab.jsx");
  });
});
