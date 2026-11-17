// W7 B1 (Dataset Summary › Judge vs human): the per-label line, and Improve
// this judge only while the Agreement tab it opens exists (the integration routes
// are registered) and the pair names its eval template.
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { render } from "src/utils/test-utils";

const routes = { value: true };
vi.mock("../alignmentApi", () => ({
  hasAlignmentRoutes: () => routes.value,
}));

import JudgePerLabelLine from "../JudgePerLabelLine";

const pair = {
  eval_template_id: "tpl-1",
  label_id: "lbl-1",
  per_label: [
    { label: "Pass", precision: 0.884, recall: 0.897, f1: 0.89, support: 68 },
    { label: "Fail", precision: 0.741, recall: 0.714, f1: 0.73, support: 28 },
  ],
};

describe("JudgePerLabelLine", () => {
  beforeEach(() => {
    routes.value = true;
  });

  it("reads each label's precision and recall and links to that source's Agreement tab", () => {
    render(<JudgePerLabelLine pair={pair} datasetId="ds-1" />);
    expect(
      screen.getByText(
        "Pass: precision 88% · recall 90%; Fail: precision 74% · recall 71%",
      ),
    ).toBeTruthy();
    expect(screen.getByTestId("w7-improve-link").getAttribute("href")).toBe(
      "/dashboard/evaluations/tpl-1?tab=agreement&dataset=ds-1&label=lbl-1",
    );
  });

  it("shows no Improve this judge while the Agreement routes are not registered", () => {
    routes.value = false;
    render(<JudgePerLabelLine pair={pair} datasetId="ds-1" />);
    expect(screen.queryByTestId("w7-improve-link")).toBeNull();
    expect(screen.getByTestId("w7-per-label-line")).toBeTruthy();
  });

  it("shows no Improve this judge when the pair names no eval template", () => {
    render(
      <JudgePerLabelLine
        pair={{ ...pair, eval_template_id: null }}
        datasetId="ds-1"
      />,
    );
    expect(screen.queryByTestId("w7-improve-link")).toBeNull();
  });
});
