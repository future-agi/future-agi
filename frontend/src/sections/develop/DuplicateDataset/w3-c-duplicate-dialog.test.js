// W3 integration (W3-11): the Duplicate or move dialog's form, plan line and
// workspace choice.
import { describe, expect, it } from "vitest";
import {
  planNotes,
  planSentence,
  runsInDataset,
  transferForm,
  workspacesYouCanEdit,
} from "./duplicateDatasetResponse";

describe("Duplicate or move helpers", () => {
  it("sends today's fields plus the transfer keys", () => {
    const f = transferForm({
      modelType: "GenerativeLLM",
      name: "GS copy",
      toWorkspaceId: "ws-b",
      mode: "move",
      history: "all",
      includeExperiments: true,
      dryRun: true,
    });
    expect(Object.fromEntries(f.entries())).toEqual({
      model_type: "GenerativeLLM",
      new_dataset_name: "GS copy",
      to_workspace_id: "ws-b",
      mode: "move",
      history: "all",
      include_experiments: "true",
      dry_run: "true",
    });
    expect(Object.fromEntries(transferForm({ name: "x" }).entries())).toEqual({
      new_dataset_name: "x",
      mode: "copy",
      history: "latest",
      include_experiments: "false",
    });
  });

  it("reads the plan the dry run returns", () => {
    expect(
      planSentence(
        {
          plan: { versions: 6, rows: 248, splits: 4, experiments: 8, runs: 10 },
        },
        { workspace: "Billing Agents" },
      ),
    ).toBe(
      "Will copy 6 versions · 248 rows · 4 splits · 10 runs into Billing Agents",
    );
    expect(
      planSentence(
        { plan: { versions: 1, rows: 1, splits: 0, runs: 0 } },
        { move: true, workspace: "Billing Agents" },
      ),
    ).toBe("Will move 1 version · 1 row into Billing Agents");
  });

  it("says which runs stay behind because they had not ended", () => {
    const whole = {
      plan: { runs: 7, runs_left_out: 3, runs_waiting_left_out: 1 },
    };
    expect(planNotes(whole)).toEqual([
      "2 runs still running and 1 run waiting for results are not copied. Copy again once they finish to include them.",
    ]);
    expect(
      planNotes({
        plan: { runs: 9, runs_left_out: 1, runs_waiting_left_out: 1 },
      }),
    ).toEqual([
      "1 run waiting for results is not copied. Copy again once it finishes to include it.",
    ]);
    expect(
      planNotes({
        plan: { runs: 8, runs_left_out: 2, runs_waiting_left_out: 0 },
      }),
    ).toEqual([
      "2 runs still running are not copied. Copy again once they finish to include them.",
    ]);
    expect(planNotes({ plan: { runs: 10, runs_left_out: 0 } })).toEqual([]);
    expect(planNotes(null)).toEqual([]);
    // The checkbox counts every run the dataset has.
    expect(runsInDataset(whole)).toBe(10);
    expect(runsInDataset({ plan: { runs: 4 } })).toBe(4);
    expect(runsInDataset(null)).toBeNull();
  });

  it("offers the workspaces whose role may create datasets", () => {
    expect(
      workspacesYouCanEdit([
        { id: "a", name: "Support Agents", user_ws_role: "Owner" },
        {
          id: "b",
          display_name: "Billing Agents",
          user_ws_role: "workspace_member",
        },
        { id: "c", name: "Read only", user_ws_role: "Viewer" },
        { id: "d", name: "No role" },
      ]),
    ).toEqual([
      { id: "a", name: "Support Agents" },
      { id: "b", name: "Billing Agents" },
    ]);
  });
});
