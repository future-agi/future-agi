import { describe, expect, it } from "vitest";

import { fitHeight } from "./stickyFit";

// The filter panel's height: the room from where it sits on screen to the
// bottom of the window, less a 16 px gap; never under 240 px.
describe("fitHeight", () => {
  it("fills the room below the panel's top", () => {
    expect(fitHeight(300, 900)).toBe(584);
  });

  it("uses the whole window once the panel is stuck at the top", () => {
    expect(fitHeight(12, 900)).toBe(872);
    expect(fitHeight(-40, 900)).toBe(884);
  });

  it("keeps a usable height when the panel starts low on a short window", () => {
    expect(fitHeight(650, 720)).toBe(240);
  });
});
