// options-truth L7b G4 (action:leaderboard:group-rerun-all-runs): the group
// `Rerun all runs` confirm says how many runs it reruns and names the ones
// it skips.
import { describe, expect, it } from "vitest";
import { groupRerunText } from "../useLeaderboardActions";

const r = (label) => ({ run_id: label, label });

describe("groupRerunText", () => {
  it("names the runs that ran outside Future AGI", () => {
    const t = groupRerunText({
      mode: "all",
      runs: [r("#3G"), r("#3H"), r("#3I"), r("#3J")],
      skipped: [r("#3A"), r("#3B"), r("#3C")],
    });
    expect(t.title).toBe("Re-run 4 of 7 runs?");
    expect(t.body).toMatch(
      / #3A, #3B and #3C ran outside Future AGI and are not rerun\.$/,
    );
  });

  it("keeps the plain confirm when nothing is skipped", () => {
    const t = groupRerunText({ mode: "all", runs: [r("#2A"), r("#2B")] });
    expect(t.title).toBe("Re-run 2 runs?");
    expect(t.body).not.toMatch(/outside/);
  });

  it("one skipped run reads `is`", () => {
    const t = groupRerunText({
      mode: "all",
      runs: [r("#4A")],
      skipped: [r("#4B")],
    });
    expect(t.title).toBe("Re-run 1 of 2 runs?");
    expect(t.body).toMatch(/#4B ran outside Future AGI and is not rerun\.$/);
  });
});
