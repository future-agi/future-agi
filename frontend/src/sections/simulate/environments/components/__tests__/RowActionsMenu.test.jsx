import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, within } from "@testing-library/react";

import RowActionsMenu from "../RowActionsMenu";
import { ENV_STATUS, ROW_ACTION_LABEL } from "../../myEnvironments.constants";

const openFor = (row, props = {}) => {
  const anchorEl = document.createElement("button");
  document.body.appendChild(anchorEl);
  render(
    <RowActionsMenu
      menuFor={{ row, anchorEl }}
      onClose={() => {}}
      {...props}
    />,
  );
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

  // A failed or canceled build has nothing to run: Run simulation only reopened
  // the same failed page. Nothing is removed: Run stays, turned off, with the
  // reason under it, and Open (which offers a new build) and Delete stay.
  it.each([ENV_STATUS.FAILED, ENV_STATUS.CANCELLED])(
    "shows Run simulation turned off, with the reason, for a %s build",
    (status) => {
      const onRun = vi.fn();
      openFor({ id: "env-1", status, runsTotal: 0 }, { onRun });

      const item = screen.getByRole("menuitem", { name: /^Run simulation/ });
      expect(item).toHaveAttribute("aria-disabled", "true");
      expect(
        within(item).getByText("This environment did not finish building."),
      ).toBeInTheDocument();
      expect(
        screen.queryByRole("menuitem", { name: /^Re-run simulation/ }),
      ).toBeNull();

      fireEvent.click(item);
      expect(onRun).not.toHaveBeenCalled();

      expect(
        screen.getByRole("menuitem", { name: ROW_ACTION_LABEL.open }),
      ).toBeInTheDocument();
      expect(
        screen.getByRole("menuitem", { name: ROW_ACTION_LABEL.delete }),
      ).toBeInTheDocument();
    },
  );

  it.each([ENV_STATUS.FAILED, ENV_STATUS.CANCELLED])(
    "shows Re-run simulation turned off, with the reason, for a %s build that has runs",
    (status) => {
      const onRun = vi.fn();
      openFor({ id: "env-1", status, runsTotal: 3 }, { onRun });

      const item = screen.getByRole("menuitem", {
        name: /^Re-run simulation/,
      });
      expect(item).toHaveAttribute("aria-disabled", "true");
      expect(
        within(item).getByText("This environment did not finish building."),
      ).toBeInTheDocument();

      fireEvent.click(item);
      expect(onRun).not.toHaveBeenCalled();
    },
  );
});
