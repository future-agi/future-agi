// W1-1 kept runs on the leaderboard: the newest run of each config, a
// "2 runs" chip that shows the config's runs in place, the View switch, and
// the Compare picker's groups.
import { describe, expect, it } from "vitest";
import {
  compareGroups,
  configKeyOf,
  parseEvery,
  runsChipOf,
  serializeEvery,
  toggleConfig,
  visibleRuns,
} from "../attemptsModel";

const run = (label, attempt, n, latest, exp = "e2", letter = "A") => ({
  run_id: `${label}`,
  label,
  experiment_id: exp,
  letter,
  attempt,
  attempts_count: n,
  latest_of_config: latest,
  name: "Support Reply V4",
});
const A1 = run("#2A", 1, 2, false);
const A2 = run("#2A·2", 2, 2, true);
const B1 = run("#2B", 1, 1, true, "e2", "B");
const runs = [A1, A2, B1];

describe("W1-1 attemptsModel", () => {
  it("lists the newest run of each config by default", () => {
    expect(visibleRuns(runs, "").map((r) => r.label)).toEqual(["#2A·2", "#2B"]);
  });
  it("a chip shows that config's runs; the switch shows every run", () => {
    const raw = toggleConfig("", configKeyOf(A2));
    expect(visibleRuns(runs, raw).map((r) => r.label)).toEqual([
      "#2A",
      "#2A·2",
      "#2B",
    ]);
    expect(toggleConfig(raw, configKeyOf(A2))).toBe("");
    expect(visibleRuns(runs, "all")).toHaveLength(3);
    expect(parseEvery("all").all).toBe(true);
    expect(serializeEvery({ all: false, configs: new Set(["e2|A"]) })).toBe(
      "e2|A",
    );
  });
  it("the chip reads `2 runs` with its tooltip, and none for one run", () => {
    expect(runsChipOf(A2, "")).toMatchObject({
      text: "2 runs",
      tooltip: "Show every run of #2A",
      open: false,
    });
    expect(runsChipOf(A2, "all").tooltip).toBe(
      "Show only the latest run of #2A",
    );
    expect(runsChipOf(B1, "")).toBeNull();
  });
  it("the Compare picker groups runs under their config, newest first", () => {
    const groups = compareGroups(runs);
    expect(groups.map((g) => g.title)).toEqual([
      "#2A · Support Reply V4 · 2 runs",
      "#2B · Support Reply V4 · 1 run",
    ]);
    expect(groups[0].runs.map((r) => r.label)).toEqual(["#2A·2", "#2A"]);
  });
});
