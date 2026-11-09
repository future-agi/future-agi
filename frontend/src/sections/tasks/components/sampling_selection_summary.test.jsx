import React from "react";
import { describe, expect, it } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import SavedTaskSelection from "./sampling_selection_summary";
import { defaultSamplingPolicy } from "./samplingPolicyForm";
import { productionTheme } from "./__tests__/samplingContrastTestUtils";

const show = (policy, mode, overrides = {}) => {
  const task = {
    sampling_policy: policy,
    row_type: "spans",
    run_type: "historical",
    sampling_rate: 50,
    spans_limit: 275,
    ...overrides,
  };
  const before = structuredClone(task);
  render(<SavedTaskSelection task={task} />, { theme: productionTheme(mode) });
  expect(task).toEqual(before);
  return screen.getByLabelText("Saved data selection");
};
describe.each(["light", "dark"])("saved selection in %s", (mode) => {
  it("describes picked conversations and evaluated spans without inventing executed counts", () => {
    const summary = show(
      { ...defaultSamplingPolicy(), default: { target_n: 100 } },
      mode,
    );
    expect(summary).toHaveTextContent(
      "100 conversations; evaluate every matching span in each picked group",
    );
    expect(summary).not.toHaveTextContent("100 evaluations");
  });
  it("identifies opaque saved quotas and coverage without misrepresenting fallback rates as selected counts", () => {
    const summary = show(
      {
        ...defaultSamplingPolicy(),
        default: { rate: 100, min_n: 5, max_n: 10, rare_below: 6 },
        cell_quotas: { '["default",[]]': 0 },
      },
      mode,
    );
    expect(summary).toHaveTextContent(
      "advanced sampling settings set via the API",
    );
    expect(summary).toHaveTextContent("saved group quotas");
    expect(summary).toHaveTextContent("additional count rules");
    expect(summary).not.toHaveTextContent("100 conversations");
    expect(summary).not.toHaveTextContent("0 evaluations");
  });
  it("keeps the exact UTC window available with local dates and no timestamp truncation", async () => {
    const window = {
      start: "2026-10-01T12:34:56.123456Z",
      end: "2026-10-02T12:34:56.654321Z",
    };
    const summary = show(
      { ...defaultSamplingPolicy(), window, default: { target_n: 2 } },
      mode,
    );
    expect(summary).toHaveTextContent("local time");
    await userEvent.click(screen.getByText("Exact UTC window"));
    expect(summary).toHaveTextContent(window.start);
    expect(summary).toHaveTextContent(window.end);
  });
  it("distinguishes historical row processing limits from selected evaluation counts", () => {
    const summary = show(null, mode);
    expect(summary).toHaveTextContent("About 50% of filter-matching spans");
    expect(summary).toHaveTextContent("Process up to 275 matching rows");
    expect(summary).not.toHaveTextContent("275 evaluations");
  });
  it("names custom identity and missing-ID behavior without editable advanced controls", () => {
    const summary = show(
      {
        ...defaultSamplingPolicy(),
        sampling_unit: "attribute",
        unit_attribute: { source: "metadata", key: "account.id" },
        missing_id: "row",
      },
      mode,
    );
    expect(summary).toHaveTextContent("Custom identity: Metadata: account.id");
    expect(summary).toHaveTextContent(
      "Rows without an ID are sampled individually",
    );
  });
});
