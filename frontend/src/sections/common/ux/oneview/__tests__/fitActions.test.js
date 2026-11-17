import { describe, expect, it } from "vitest";
import { fitActions } from "../fitActions";

describe("fitActions inMore (IT-R4 G5 P6)", () => {
  it("keeps an inMore action in More even when everything fits", () => {
    const actions = [
      { id: "view", priority: 1 },
      { id: "run", priority: 2 },
      { id: "evaluate", priority: 2, inMore: true },
    ];
    const widths = {
      view: { full: 60 },
      run: { full: 90 },
      evaluate: { full: 90 },
    };
    expect(fitActions(actions, widths, 2000)).toEqual({
      shown: ["view", "run"],
      compact: [],
      overflow: ["evaluate"],
    });
  });
});
