import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import ActivityList from "../ActivityList";

const run = (experimentId) => ({
  type: "run",
  run_id: "run-1",
  run_label: "#1",
  experiment_id: experimentId,
  at: "2026-10-09T10:00:00Z",
});

describe("Activity run link", () => {
  it("names a result without an experiment as just a result", () => {
    const onOpenRun = vi.fn();
    render(<ActivityList events={[run(null)]} onOpenRun={onOpenRun} />);
    const open = screen.getByRole("button", { name: "Open this result" });
    fireEvent.click(open);
    expect(onOpenRun).toHaveBeenCalledWith(
      expect.objectContaining({ run_id: "run-1" }),
    );
  });

  it("keeps naming the experiment when the result has one", () => {
    render(<ActivityList events={[run("exp-1")]} onOpenRun={vi.fn()} />);
    expect(
      screen.getByRole("button", {
        name: "Open this result in the experiment",
      }),
    ).toBeInTheDocument();
  });
});
