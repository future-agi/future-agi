import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";

import RowActionsMenu from "../RowActionsMenu";
import { ENV_STATUS, ROW_ACTION_LABEL } from "../../myEnvironments.constants";

const openFor = (row) => {
  const anchorEl = document.createElement("button");
  document.body.appendChild(anchorEl);
  render(<RowActionsMenu menuFor={{ row, anchorEl }} onClose={() => {}} />);
};

describe("RowActionsMenu", () => {
  it.each([ENV_STATUS.BUILDING, ENV_STATUS.FINALIZING, ENV_STATUS.CANCELLING])(
    "disables Run while the environment is %s, and does not offer a re-run",
    (status) => {
      openFor({ id: "env-1", status, runsTotal: 2 });
      const item = screen.getByRole("menuitem", { name: ROW_ACTION_LABEL.run });
      expect(item).toHaveAttribute("aria-disabled", "true");
      expect(screen.queryByText(ROW_ACTION_LABEL.rerun)).toBeNull();
    },
  );

  it("enables Re-run for a completed environment that already has runs", () => {
    openFor({ id: "env-1", status: ENV_STATUS.COMPLETED, runsTotal: 2 });
    const item = screen.getByRole("menuitem", { name: ROW_ACTION_LABEL.rerun });
    expect(item).not.toHaveAttribute("aria-disabled", "true");
  });
});
