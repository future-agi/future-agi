import { describe, expect, it } from "vitest";
import { seededState } from "../seedState";
import { withScenarioHistoryDemo } from "../scenarioHistoryDemo";
import { runSummaries } from "../comparison";
import {
  anyScenarioRan,
  isComparableVersion,
  recentDotsOf,
} from "../scenarioRunHistory";

const h = (n, status, extra = {}) => ({
  runId: `r${n}`,
  name: `Run ${n}`,
  status,
  ...extra,
});

describe("recentDotsOf", () => {
  it("keeps the last three scored runs, oldest first", () => {
    const dots = recentDotsOf(
      [
        h(1, "passed"),
        h(2, null),
        h(3, "failed"),
        h(4, "passed"),
        h(5, "flaky"),
      ],
      "1.0.0",
    );
    expect(dots.map((d) => d.name)).toEqual(["Run 3", "Run 4", "Run 5"]);
  });
  it("words each dot", () => {
    const [a, b] = recentDotsOf(
      [
        h(1, "flaky", { samples: ["passed", "failed", "passed"] }),
        h(2, "failed"),
      ],
      "1.0.0",
    );
    expect(a.title).toBe("Run 1 · Flaky · 2 of 3 trials");
    expect(b.title).toBe("Run 2 · Failed");
  });
  it("shows fewer dots, or none, for a scenario that ran less", () => {
    expect(recentDotsOf([h(1, "passed")], "1.0.0")).toHaveLength(1);
    expect(recentDotsOf([h(1, null), h(2, "unscored")], "1.0.0")).toEqual([]);
  });
  it("leaves a dot blank when it ran on another version", () => {
    const [d] = recentDotsOf(
      [h(1, "passed", { ranVersion: "1.0.0" })],
      "2.0.0",
    );
    expect(d.comparable).toBe(false);
    expect(d.title).toBe("Ran on an earlier version of this scenario");
  });
});

describe("dots from another agent or environment version", () => {
  const cur = { agent: "v4", env: "v3" };
  it("fades a dot from another agent and names the versions", () => {
    const [a, b] = recentDotsOf(
      [
        h(2, "flaky", {
          samples: ["passed", "failed", "passed"],
          agentVersion: "v1",
          envVersion: "v3",
        }),
        h(3, "passed", { agentVersion: "v4", envVersion: "v3" }),
      ],
      "1.0.0",
      3,
      cur,
    );
    expect(a.otherAgent).toBe(true);
    expect(a.title).toBe(
      "Run 2 · agent v1 · env v3 · Flaky · 2 of 3 trials — not the current agent (v4)",
    );
    expect(b.otherAgent).toBe(false);
    expect(b.title).toBe("Run 3 · agent v4 · env v3 · Passed");
  });
  it("fades a dot from another environment version", () => {
    const [d] = recentDotsOf(
      [h(1, "failed", { agentVersion: "v4", envVersion: "v2" })],
      "1.0.0",
      3,
      cur,
    );
    expect(d.otherAgent).toBe(true);
  });
  it("never fades without a current version or run stamps", () => {
    expect(recentDotsOf([h(1, "failed")], "1.0.0", 3, cur)[0].otherAgent).toBe(
      false,
    );
    expect(
      recentDotsOf([h(1, "failed", { agentVersion: "v1" })], "1.0.0")[0]
        .otherAgent,
    ).toBe(false);
  });
});

describe("isComparableVersion", () => {
  it("is the same version for now", () => {
    expect(isComparableVersion("1.0.0", "1.0.0")).toBe(true);
    expect(isComparableVersion("2.0.0", "1.0.0")).toBe(false);
  });
});

describe("seeded history examples", () => {
  const s = withScenarioHistoryDemo(seededState());
  const env = s.myEnvironments.find((e) => e.id === "env-voice-support");
  const es = s.byEnv[env.id];
  const runs = runSummaries(env, es).filter((r) => r.finishedAt);
  it("fakes no newer scenario version, so no dot is hollow", () => {
    es.scenarios.forEach((sc) => expect(sc.version || "1.0.0").toBe("1.0.0"));
  });
  it("has a never-run scenario next to ones that ran", () => {
    const ran = new Set(
      runs.flatMap((r) => r.tasks.map((t) => t.scenarioId || t.id)),
    );
    expect(es.scenarios.some((r) => !ran.has(r.id))).toBe(true);
    expect(runs.some((r) => r.tasks.some((t) => t.status === "flaky"))).toBe(
      true,
    );
  });
  it("is idempotent", () => {
    expect(withScenarioHistoryDemo(s)).toBe(s);
  });
  it("anyScenarioRan is false when nothing ran", () => {
    expect(anyScenarioRan([{ id: "a" }], () => [h(1, null)])).toBe(false);
  });
});
