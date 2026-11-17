import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import JuryDatasetReport from "./JuryDatasetReport";
import { juryInputKeys } from "./useJuryDatasetInputs";
import juryBindingPayload from "./juryBindingPayload";
vi.mock("src/launch/launchSwitch", () => ({ isSurfaceShown: () => true }));
afterEach(cleanup);

describe("dataset jury report", () => {
  it("derives structured inputs from the selected seats", () => {
    expect(
      juryInputKeys({ seats: [{ level: "step" }, { kind: "check" }] }),
    ).toEqual(["steps", "checks"]);
    expect(juryInputKeys({ seats: [{ level: "row", kind: "eval" }] })).toEqual(
      [],
    );
  });
  it("retains both a specific version and clearing the selection", () => {
    const payload = { config: { config: {} } };
    const selection = {
      templateType: "composite",
      config: { jury: {} },
      versionId: "version-one",
    };
    juryBindingPayload(payload, selection);
    expect(payload.config.config.jury_version_id).toBe("version-one");
    juryBindingPayload(payload, { ...selection, versionId: null });
    expect(payload.config.config.jury_version_id).toBeNull();
    payload.pinned_version_id = "latest";
    juryBindingPayload(payload, { ...selection, versionId: "latest" });
    expect(payload.config.config.jury_version_id).toBe("latest");
    expect(payload).not.toHaveProperty("pinned_version_id");
  });
  it("navigates only the disagreement row without rescoring", () => {
    const report = {
      run_id: "run",
      runs: [{ run_id: "run", run_label: "Saved run" }],
      n: 2,
      complete: true,
      split_rows: ["b"],
      pairs: [{ a: "judge", b: "safe", agreement: 0.5, n: 2 }],
      rows: [
        {
          row_id: "a",
          row_number: 1,
          disagreement: false,
          seats: [{ id: "safe", passed: true }],
        },
        {
          row_id: "b",
          row_number: 2,
          disagreement: true,
          seats: [{ id: "safe", passed: false }],
        },
      ],
    };
    render(<JuryDatasetReport report={report} onRunChange={vi.fn()} />);
    expect(screen.getByRole("status")).toHaveTextContent(
      "Judges disagree on 1 of 2 rows",
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Open disagreement rows" }),
    );
    expect(
      screen.getByRole("combobox", { name: "Jury row" }),
    ).toHaveTextContent("Row 2");
    expect(
      within(
        screen.getByRole("table", { name: "Dataset row jury verdicts" }),
      ).getByText("Fail"),
    ).toBeInTheDocument();
    expect(
      within(
        screen.getByRole("table", { name: "Dataset row jury verdicts" }),
      ).queryByText("Pass"),
    ).not.toBeInTheDocument();
  });
});
