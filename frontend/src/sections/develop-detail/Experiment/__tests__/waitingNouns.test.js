// A waiting run names what it waits on by its mode (options-truth L1 G2):
// a CI run never "waits for your terminal".
import { describe, expect, it, vi } from "vitest";
import {
  launchedHowLine,
  waitNounOf,
  waitNounOfAll,
  waitingOf,
} from "../waitingModel";
import { waitingTitle } from "../ExternalRunWaitingBanner";
import { waitingNote } from "src/sections/develop-detail/ExperimentTab/LeaderboardAnswer";

// The drawer's local line names "the command below" only once the SDK
// runner is released; the unreleased line is in sdk-runner-not-released.test.jsx.
vi.mock("../sdkRunner", () => ({ SDK_RUNNER_RELEASED: true }));

const run = (mode, extra = {}) => ({
  run_id: `r-${mode}`,
  label: "#4A",
  name: "LangGraph",
  kind: "code",
  experiment_id: "e4",
  status: "WaitingForResults",
  progress: { done: 0, total: 30 },
  config: { kind: "code", execution: { mode } },
  ...extra,
});

describe("what a waiting run waits on", () => {
  it("follows the run's mode", () => {
    expect(waitNounOf(run("local"))).toBe("your terminal");
    expect(waitNounOf(run("ci"))).toBe("the CI job");
    expect(waitNounOf(run("sandbox"))).toBe("the sandbox");
    expect(waitNounOf({ target_type: "external" })).toBe("results");
    expect(waitNounOf({})).toBe("your terminal");
    expect(waitNounOfAll([run("ci"), run("ci")])).toBe("the CI job");
    expect(waitNounOfAll([run("ci"), run("local")])).toBe("you");
  });
  it("the banner and the leaderboard say it", () => {
    expect(waitingTitle([run("ci")])).toBe(
      "#4A LangGraph waits for the CI job",
    );
    expect(
      waitingTitle([run("ci", { progress: { done: 3, total: 30 } })]),
    ).toBe("3 of 30 received · waiting for the other 27 from the CI job");
    expect(waitingTitle([run("local"), run("local")])).toBe(
      "2 runs wait for your terminal",
    );
    expect(waitingNote([run("ci")])).toBe("#4A waits for the CI job.");
  });
  it("the drawer's line follows the launched cards", () => {
    expect(launchedHowLine([{ mode: "ci" }])).toMatch(/^Commit the workflow/);
    expect(launchedHowLine([{ mode: "local" }])).toMatch(
      /^Run the command below on your machine/,
    );
    expect(launchedHowLine([null])).toMatch(/^Post your results/);
    expect(launchedHowLine([{ mode: "ci" }, { mode: "local" }])).toMatch(
      /^Start each run as shown below/,
    );
  });
  // dataset integration FE-0: the waiting CI file ran a generated runner; none prints.
  it("a waiting CI run prints no workflow", () => {
    const w = waitingOf(run("ci"), null, { datasetName: "Support ops" });
    expect(w.title).toBe("#4A waits for the CI job");
    expect(w.command).toBeNull();
  });
});
