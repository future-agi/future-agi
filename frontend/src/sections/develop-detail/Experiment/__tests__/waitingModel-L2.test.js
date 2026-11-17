// options-truth L2 G3 (L4): how a waiting run is started, by its mode.
import { describe, expect, it } from "vitest";
import {
  CI_WORKFLOW_PATH,
  ciStepsOf,
  standInBodyOf,
  trayActionOf,
} from "../waitingModel";

const run = (execution, extra = {}) => ({
  run_id: "r1",
  label: "#4A",
  trials: 2,
  config: { kind: "code", framework: { id: "crewai" }, execution, ...extra },
});

describe("trayActionOf", () => {
  it("names the next step by where the run runs", () => {
    expect(trayActionOf(run({ mode: "local" }))).toEqual({
      kind: "run",
      label: "How to run",
    });
    expect(trayActionOf(run({ mode: "ci" }))).toEqual({
      kind: "ci",
      label: "Open the CI job",
    });
    expect(trayActionOf({ kind: "upload", target_type: "external" })).toEqual({
      kind: "upload",
      label: "How to upload",
    });
    ["sandbox", "remote", "platform"].forEach((mode) =>
      expect(trayActionOf(run({ mode }))).toBeNull(),
    );
    expect(trayActionOf(run({ mode: "local", machine_id: "m1" }))).toBeNull();
  });
});

describe("ciStepsOf", () => {
  it("reads the secrets, variables and triggers off the workflow", () => {
    const yaml = [
      "on:",
      "  pull_request:",
      "  workflow_dispatch:",
      "env:",
      "  FI_API_KEY: ${{ secrets.FI_API_KEY }}",
      "  OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}",
      "run: python run_and_upload.py --run ${{ vars.FI_RUN_ID }}",
    ].join("\n");
    expect(ciStepsOf(yaml)).toEqual([
      `Save the workflow as ${CI_WORKFLOW_PATH}`,
      "Add the repository secrets FI_API_KEY, OPENAI_API_KEY",
      "Set the repository variables FI_RUN_ID",
      "Run the workflow from the Actions tab or open a pull request; each row lands here as the job uploads it and is scored.",
    ]);
    expect(ciStepsOf("on:\n  push:\n")).toEqual([
      `Save the workflow as ${CI_WORKFLOW_PATH}`,
      "Push a commit; each row lands here as the job uploads it and is scored.",
    ]);
  });
});

describe("standInBodyOf", () => {
  it("posts the run's own config, tries and stand-in kind", () => {
    const r = run({ mode: "ci" }, { limits: { tries: 3 } });
    expect(standInBodyOf(r, { datasetId: "d" })).toEqual({
      rows: "all",
      as: "ci_job",
      dataset_id: "d",
      entry_id: "crewai",
      tries: 3,
      config: r.config,
    });
    expect(standInBodyOf(run({ mode: "local" })).tries).toBe(2);
  });
});
