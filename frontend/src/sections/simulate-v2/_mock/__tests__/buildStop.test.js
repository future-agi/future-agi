import { describe, expect, it } from "vitest";
import {
  BUILD_STOP_CASES,
  buildStopOf,
  isBuildStopped,
  resumedBuildOf,
  stoppedBuildOf,
  stoppedRunReason,
} from "../buildStop";
import { changeOf, firstBuildOf, versionBuildProgress } from "../versionBuild";

const env = {
  id: "e1",
  tools: [{ name: "lookup_order" }, { name: "issue_refund" }],
  seed: {
    tables: [
      { name: "orders", rows: 1200 },
      { name: "customers", rows: 400 },
    ],
  },
};
const scenarios = [
  { id: "s1", title: "Refund a damaged item" },
  { id: "s2", title: "Refund over the limit" },
  { id: "s3", title: "Change address after dispatch" },
];
const envState = { scenarios };
const NOW = 1_800_000_000_000;

const stopped = (kase) => stoppedBuildOf({ env, envState, kase, now: NOW });
const view = (kase, agent = null) =>
  buildStopOf(stopped(kase), { scenarios, agent });

describe("first build steps", () => {
  it("numbers steps 2–12 in three phases", () => {
    const b = firstBuildOf({ tools: env.tools, tables: env.seed.tables });
    expect(b.steps.map((s) => s.no)).toEqual([
      2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12,
    ]);
    expect(b.steps.map((s) => s.phase)).toEqual([
      "world",
      "world",
      "world",
      "world",
      "scenarios",
      "scenarios",
      "checks",
      "checks",
      "checks",
      "checks",
      "checks",
    ]);
  });
});

describe("a stopped build", () => {
  it("freezes on the step it stopped at, in every case", () => {
    Object.entries(BUILD_STOP_CASES).forEach(([kase, no]) => {
      const b = stopped(kase);
      expect(isBuildStopped(b)).toBe(true);
      const p = versionBuildProgress(b, NOW + 60_000);
      expect(p.stopped).toBe(true);
      expect(p.done).toBe(false);
      expect(b.steps[p.step].no).toBe(no);
    });
  });

  it("says what stopped in the person's words, per phase", () => {
    expect(view("fail-world").sentence).toBe(
      "lookup_order didn't answer truthfully: it returned an order that isn't in the world.",
    );
    expect(view("fail-scenarios").sentence).toBe(
      "2 use cases got no scenario that passed all three checks (Refund over the limit, Change address after dispatch).",
    );
    expect(
      view("fail-checks", {
        values: { endpoint: "https://agent.example/chat" },
      }).sentence,
    ).toBe("Couldn't reach your agent at https://agent.example/chat.");
    expect(view("fail-ours").sentence).toBe(
      "An issue occurred while building. Nothing you set up was lost.",
    );
  });

  it("keeps finished phases and marks the rest", () => {
    const states = (k) => view(k).phases.map((p) => p.state);
    expect(states("fail-world")).toEqual(["stopped", "next", "next"]);
    expect(states("fail-scenarios")).toEqual(["done", "stopped", "next"]);
    expect(states("fail-checks")).toEqual(["done", "done", "stopped"]);
    expect(view("fail-scenarios").kept).toMatch(
      /^The world and its 2 tables are kept/,
    );
  });

  it("offers one retry from the phase, and a fix only where a change can help", () => {
    expect(view("fail-world").retry.label).toBe("Try again from the world");
    expect(view("fail-scenarios").retry.label).toBe(
      "Try again from the scenarios",
    );
    expect(view("fail-scenarios").fix.draft).toBe(
      "Rewrite the use cases that got no scenario",
    );
    expect(view("fail-checks").fix.kind).toBe("connection");
    expect(view("fail-ours").fix).toBeNull();
    expect(view("fail-world").log).toMatch(/validate_environment/);
  });

  it("holds the run with a reason", () => {
    expect(stoppedRunReason(stopped("fail-scenarios"))).toMatch(
      /stopped at Validating scenarios/,
    );
  });

  it("leaves what it didn't write unwritten in the tabs", () => {
    const b = stopped("fail-world");
    expect(changeOf(b, "tables", NOW + 1000).state).toBe("stopped");
    expect(changeOf(b, "tables", NOW + 1000).shown).toBe(0);
    expect(changeOf(stopped("fail-scenarios"), "tables", NOW).state).toBe(
      "done",
    );
  });
});

describe("Try again", () => {
  it("resumes at the stopped step and runs to the end", () => {
    const b = stopped("fail-scenarios");
    const r = resumedBuildOf(b, NOW);
    expect(isBuildStopped(r)).toBe(false);
    const p = versionBuildProgress(r, NOW + 10);
    expect(r.steps[p.step].no).toBe(7);
    expect(versionBuildProgress(r, NOW + 60_000).done).toBe(true);
  });
});
