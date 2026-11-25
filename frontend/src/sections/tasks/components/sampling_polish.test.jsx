import React from "react";
import { describe, expect, it } from "vitest";
import { render, screen } from "src/utils/test-utils";
import SavedTaskSelection from "./sampling_selection_summary";
import SamplingPickIllustration from "./sampling_pick_illustration";
import { defaultSamplingPolicy } from "./samplingPolicyForm";
import { productionTheme } from "./__tests__/samplingContrastTestUtils";

describe.each(["light", "dark"])("sampling comprehension in %s", (mode) => {
  it("keeps long advanced rule names out of the saved header and never changes their order or quotas", () => {
    const names = Array.from({ length: 100 }, (_, index) =>
      `${index}-${"Long group name ".repeat(6)}`.slice(0, 80),
    );
    const policy = {
      ...defaultSamplingPolicy(),
      default: { rate: 50, min_n: 1, max_n: 5, rare_below: 2 },
      strata: names.map((name) => ({
        name,
        rate: 25,
        filters: [],
        min_n: 1,
        max_n: 5,
        rare_below: 2,
      })),
      cell_quotas: { '["default",[]]': 0 },
    };
    const task = {
      sampling_policy: policy,
      row_type: "spans",
      run_type: "historical",
    };
    const before = structuredClone(task);
    render(<SavedTaskSelection task={task} />, {
      theme: productionTheme(mode),
    });
    const header = screen.getByLabelText("Saved data selection");
    expect(header.textContent.length).toBeLessThan(1000);
    expect(header).not.toHaveTextContent(names[0]);
    expect(header).not.toHaveTextContent(names[99]);
    expect(header).toHaveTextContent("advanced sampling settings");
    expect(task).toEqual(before);
  });
  it("shows the evaluated row level inside the picked conversation, not a fake population count", () => {
    render(<SamplingPickIllustration unit="session" rowType="spans" />, {
      theme: productionTheme(mode),
    });
    const figure = screen.getByRole("figure");
    expect(figure).toHaveTextContent("Conversation · picked");
    expect(figure).toHaveTextContent("Spans · evaluated");
    expect(figure).toHaveAccessibleName(
      "Pick conversations. Every matching span in each picked group is evaluated.",
    );
    expect(figure).not.toHaveTextContent(/100|10%|Preview/);
  });
});
