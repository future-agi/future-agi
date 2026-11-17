// W33-SUMMARY-NAMES: the experiment switcher in the page header never shows
// `#undefined`. The experiment read carries no experiment number, so the
// switcher names the experiment by its name; a number shows only when known.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  renderWithRouter,
  screen,
  within,
} from "src/utils/test-utils";
import DevelopExperimentSelect from "../DevelopExperimentSelect";

const hooks = vi.hoisted(() => ({ datasetRuns: vi.fn() }));
vi.mock("src/api/develop/runs", async (importOriginal) => ({
  ...(await importOriginal()),
  useDatasetRuns: hooks.datasetRuns,
}));
vi.mock("src/components/iconify", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => null,
}));

const DATASET = "11111111-1111-4111-8111-111111111111";

const datasetRun = (experimentId, experimentName, extra = {}) => ({
  run_id: `${experimentId}-run`,
  experiment_id: experimentId,
  experiment_name: experimentName,
  status: "completed",
  ...extra,
});

beforeEach(() => {
  hooks.datasetRuns.mockReset().mockReturnValue({
    data: {
      results: [
        datasetRun("exp-old", "dsx33-exp"),
        datasetRun("exp-new", "Support agent v3", { experiment_ordinal: 4 }),
      ],
    },
    isLoading: false,
  });
});

afterEach(() => {
  cleanup();
});

describe("W33-SUMMARY-NAMES experiment switcher number", () => {
  it("names an experiment without a number by its name, never #undefined", () => {
    renderWithRouter(
      <DevelopExperimentSelect
        experiment={{ id: "exp-old", name: "dsx33-exp", dataset_id: DATASET }}
      />,
      { route: "/dashboard/develop/experiment/exp-old/summary" },
    );
    const trigger = screen.getByRole("button", { name: "Switch experiment" });
    expect(trigger).toHaveTextContent("dsx33-exp");
    expect(trigger).not.toHaveTextContent(/#/);
    expect(trigger).not.toHaveTextContent(/undefined/);
  });

  it("keeps the number before the name when the experiment has one", () => {
    renderWithRouter(
      <DevelopExperimentSelect
        experiment={{ id: "exp-new", name: "Support agent v3", ordinal: 4, dataset_id: DATASET }}
      />,
      { route: "/dashboard/develop/experiment/exp-new/data" },
    );
    const trigger = screen.getByRole("button", { name: "Switch experiment" });
    expect(within(trigger).getByText("#4")).toBeInTheDocument();
    expect(within(trigger).getByText("Support agent v3")).toBeInTheDocument();
  });

  it("lists experiments without a number by name only", () => {
    renderWithRouter(
      <DevelopExperimentSelect
        experiment={{ id: "exp-old", name: "dsx33-exp", dataset_id: DATASET }}
      />,
      { route: "/dashboard/develop/experiment/exp-old/summary" },
    );
    fireEvent.click(screen.getByRole("button", { name: "Switch experiment" }));
    const items = screen.getAllByRole("menuitem");
    expect(items).toHaveLength(2);
    const unnumbered = items.find((item) => item.textContent.includes("dsx33-exp"));
    expect(unnumbered).not.toHaveTextContent(/#|undefined/);
    const numbered = items.find((item) => item.textContent.includes("Support agent v3"));
    expect(within(numbered).getByText("#4")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/#undefined/);
  });
});
