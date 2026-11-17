// fitActions with `more`: the bar shows More anyway (it carries an entry
// besides the actions that fold), so the shown actions must fit beside it.
import { describe, expect, it } from "vitest";
import { fitActions } from "../fitActions";

const actions = [
  { id: "a", priority: 2 },
  { id: "b", priority: 2 },
  { id: "c", priority: 3 },
];
const widths = { a: { full: 100 }, b: { full: 100 }, c: { full: 100 } };
const opts = { gap: 4, moreWidth: 72 };

describe("fitActions with More shown anyway", () => {
  it("leaves room for More where every action fits without it", () => {
    // The actions take 308 px; with More and its gap they need 384.
    expect(fitActions(actions, widths, 320, opts).overflow).toEqual([]);
    expect(fitActions(actions, widths, 320, { ...opts, more: true })).toEqual({
      shown: ["a", "b"],
      compact: [],
      overflow: ["c"],
    });
  });

  // Guard: room for every action and More.
  it("shows every action beside More when they all fit with it", () => {
    expect(fitActions(actions, widths, 400, { ...opts, more: true })).toEqual({
      shown: ["a", "b", "c"],
      compact: [],
      overflow: [],
    });
  });

  // Guard: once an action folds, More is counted either way.
  it("folds as it does without the option once an action folds anyway", () => {
    for (const room of [100, 176, 250, 280, 300]) {
      const anyway = fitActions(actions, widths, room, { ...opts, more: true });
      expect(anyway).toEqual(fitActions(actions, widths, room, opts));
    }
  });
});
