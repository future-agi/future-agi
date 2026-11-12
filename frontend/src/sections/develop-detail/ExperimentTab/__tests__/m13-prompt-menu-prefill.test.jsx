import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import RunRowMenu from "../RunRowMenu";

vi.mock("../cellContext", () => ({ useCell: (props) => props }));
vi.mock("src/sections/common/Runs/useRunActions", () => ({
  default: () => ({}),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));

describe("M13 G8 leaderboard Open in Prompts", () => {
  it("hands off the run's model and params, not only the prompt version", () => {
    const navigate = vi.fn();
    const run = {
      run_id: "r2b",
      label: "#2B",
      experiment_id: "e2",
      target_type: "prompt",
      status: "Completed",
      kind: "prompt",
      target: {
        prompt_id: "p1",
        prompt_version_name: "V4",
        model: "claude-sonnet-4-5",
        model_params: { temperature: 0.3, max_tokens: 400 },
      },
    };
    render(
      <RunRowMenu
        node={{ group: false }}
        data={run}
        context={{
          navigate,
          canEdit: true,
          canRun: true,
          runsPerExperiment: { e2: 2 },
          datasetId: "ds",
          baselineId: "r2a",
        }}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "#2B actions" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Open in Prompts" }));
    expect(navigate).toHaveBeenCalledWith(
      expect.stringContaining("version=V4"),
      {
        state: {
          prefillModel: "claude-sonnet-4-5",
          prefillParams: { temperature: 0.3, max_tokens: 400 },
        },
      },
    );
  });
});
