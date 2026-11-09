// HO results R5: the one pairwise vote of the drawer (keys and key text).
import { describe, expect, it } from "vitest";
import { pairwiseOptions } from "src/sections/develop-detail/Review/reviewModel";
import { myPairVotes, pairVoteKey, voteKeysBlocked } from "../usePairVote";

describe("pair vote", () => {
  it("keys 1 / 2 / 3 are baseline better / tie / candidate better (unblinded)", () => {
    const o = pairwiseOptions({ left: "A", labelA: "#2A", labelB: "#2B" });
    expect(o.map((x) => [x.key, x.choice, x.label])).toEqual([
      ["1", "A", "#2A is better"],
      ["2", "tie", "Tie"],
      ["3", "B", "#2B is better"],
    ]);
  });
  it("shares one session map between the Review and Compare tabs", () => {
    const k = pairVoteKey("a", "b", "e1");
    expect(k).toBe("a|b|e1");
    myPairVotes.set(k, { choice: "B" });
    expect(myPairVotes.get("a|b|e1").choice).toBe("B");
    myPairVotes.delete(k);
  });
  it("never fires inside text fields or open lists", () => {
    const input = document.createElement("input");
    expect(voteKeysBlocked(input)).toBe(true);
    const li = document.createElement("li");
    const menu = document.createElement("ul");
    menu.setAttribute("role", "menu");
    menu.appendChild(li);
    expect(voteKeysBlocked(li)).toBe(true);
    expect(voteKeysBlocked(document.body)).toBe(false);
  });
});
