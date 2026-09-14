import { beforeEach, describe, expect, it } from "vitest";
import { render, screen } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";

import ExperimentColumnSummaryCell from "../ExperimentColumnSummaryCell";
import { useColumnSummaryStore } from "../states";

const STATS = {
  isColumnSummary: true,
  average: 50,
  max: 90,
  min: 10,
  median: 40,
};

function renderCell({ field = "eval-a", stats = STATS } = {}) {
  return render(
    <ExperimentColumnSummaryCell
      colDef={{ field }}
      data={{ [field]: stats }}
    />,
  );
}

describe("ExperimentColumnSummaryCell", () => {
  beforeEach(() => {
    useColumnSummaryStore.getState().resetColumnSummaries();
  });

  it("defaults to Average labelled the same way as today", () => {
    renderCell();
    expect(screen.getByText("Average: 50.00%")).toBeInTheDocument();
  });

  it("lets the user switch a column to Maximum, Minimum, or Median", async () => {
    const user = userEvent.setup();
    renderCell();

    await user.click(
      screen.getByRole("button", {
        name: "Change column summary, currently Average: 50.00%",
      }),
    );

    expect(
      screen.getByRole("menuitem", { name: "Average" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("menuitem", { name: "Maximum" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("menuitem", { name: "Minimum" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("menuitem", { name: "Median" }),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("menuitem", { name: "Maximum" }));
    expect(screen.getByText("Maximum: 90.00%")).toBeInTheDocument();
  });

  it("keeps independent choices for two columns at once", async () => {
    const user = userEvent.setup();
    render(
      <>
        <ExperimentColumnSummaryCell
          colDef={{ field: "eval-a" }}
          data={{ evalA: undefined, "eval-a": STATS }}
        />
        <ExperimentColumnSummaryCell
          colDef={{ field: "eval-b" }}
          data={{
            "eval-b": {
              isColumnSummary: true,
              average: 20,
              max: 80,
              min: 1,
              median: 15,
            },
          }}
        />
      </>,
    );

    const buttons = screen.getAllByRole("button");
    await user.click(buttons[0]);
    await user.click(screen.getByRole("menuitem", { name: "Minimum" }));

    await user.click(
      screen.getByRole("button", {
        name: "Change column summary, currently Average: 20.00%",
      }),
    );
    await user.click(screen.getByRole("menuitem", { name: "Median" }));

    expect(screen.getByText("Minimum: 10.00%")).toBeInTheDocument();
    expect(screen.getByText("Median: 15.00%")).toBeInTheDocument();
  });

  it("does not offer a picker when the column cannot be summarised further", () => {
    renderCell({
      stats: {
        isColumnSummary: true,
        average: 12.3,
        max: null,
        min: null,
        median: null,
      },
    });
    expect(screen.getByText("Average: 12.30%")).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
