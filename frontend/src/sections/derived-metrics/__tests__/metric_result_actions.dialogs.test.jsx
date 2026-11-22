import React, { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import MetricResultActions from "../metric_result_actions";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";

const defaults = {
  busy: false,
  readOnly: false,
  dirty: false,
  matched: true,
  pending: false,
  dashboardName: "Pinned quality",
  monitorName: "Quality threshold",
  threshold: "0.8",
  operator: "less_than",
  alertWhen: "point",
  email: "",
  onChange: vi.fn(),
  onDashboard: vi.fn(),
  onMonitor: vi.fn(),
};
function mount(props = {}, mode = "light") {
  const close = vi.fn();
  const initialAction = props.action || "dashboard";
  function Harness() {
    const [action, setAction] = useState(null);
    return (
      <ThemeProvider
        theme={createTheme({ palette: palette(mode), typography })}
      >
        <button onClick={() => setAction(initialAction)}>
          Open result action
        </button>
        <MetricResultActions
          {...defaults}
          {...props}
          action={action}
          onClose={() => {
            close();
            setAction(null);
          }}
        />
      </ThemeProvider>
    );
  }
  render(<Harness />);
  const trigger = screen.getByRole("button", { name: "Open result action" });
  trigger.focus();
  fireEvent.click(trigger);
  return { close, trigger };
}

describe("Metric result shared dialogs", () => {
  it.each(["light", "dark"])(
    "keeps name focus, focus containment and Escape return in %s",
    async (mode) => {
      const { trigger, close } = mount({}, mode);
      const dialog = screen.getByRole("dialog", {
        name: "Create dashboard from this run",
      });
      await waitFor(() =>
        expect(screen.getByLabelText("Dashboard name")).toHaveFocus(),
      );
      trigger.focus();
      // MUI redirects forced outside focus to its container before Tab enters the paper.
      expect(dialog.closest(".MuiDialog-container")).toContainElement(
        document.activeElement,
      );
      await userEvent.tab();
      await waitFor(() =>
        expect(dialog).toContainElement(document.activeElement),
      );
      fireEvent.keyDown(document.activeElement, { key: "Escape" });
      await waitFor(() =>
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
      );
      expect(close).toHaveBeenCalledTimes(1);
      expect(trigger).toHaveFocus();
    },
  );

  it.each(["dashboard", "monitor"])(
    "names the %s close control and returns focus",
    async (action) => {
      const user = userEvent.setup();
      const { close, trigger } = mount({ action });
      const dialog = screen.getByRole("dialog");
      const closeButton = within(dialog).getByRole("button", {
        name: "Close dialog",
        exact: true,
      });
      expect(closeButton).toHaveAccessibleName("Close dialog");
      await user.click(closeButton);
      await waitFor(() =>
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
      );
      expect(close).toHaveBeenCalledTimes(1);
      expect(trigger).toHaveFocus();
    },
  );

  it("dismisses on the actual MUI backdrop", async () => {
    const user = userEvent.setup();
    const { close } = mount();
    await user.click(document.querySelector(".MuiDialog-container"));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(close).toHaveBeenCalledTimes(1);
  });

  it("blocks Escape, backdrop, close control and submit while busy", async () => {
    const user = userEvent.setup();
    const submit = vi.fn();
    const { close } = mount({ busy: true, onDashboard: submit });
    const dialog = screen.getByRole("dialog");
    fireEvent.keyDown(screen.getByLabelText("Dashboard name"), {
      key: "Escape",
    });
    await user.click(document.querySelector(".MuiDialog-container"));
    const closeButton = within(dialog).getByRole("button", {
      name: "Close dialog",
      exact: true,
    });
    expect(closeButton).toBeDisabled();
    // Native activation respects disabled without user-event's pointer-events setup error.
    closeButton.click();
    const action = within(dialog).getByRole("button", {
      name: "Create dashboard",
    });
    expect(action).toBeDisabled();
    action.click();
    expect(close).not.toHaveBeenCalled();
    expect(submit).not.toHaveBeenCalled();
    expect(dialog).toBeInTheDocument();
  });

  it("submits a valid dashboard once through the keyboard action", async () => {
    const submit = vi.fn();
    mount({ onDashboard: submit });
    const action = screen.getByRole("button", { name: "Create dashboard" });
    action.focus();
    await userEvent.keyboard("{Enter}");
    expect(submit).toHaveBeenCalledTimes(1);
  });

  it.each([
    { readOnly: true },
    { matched: false },
    { pending: true },
    { dashboardBlocked: true },
    { dashboardName: "   " },
  ])("preserves dashboard validity gate %j", (props) => {
    mount(props);
    expect(
      screen.getByRole("button", { name: "Create dashboard" }),
    ).toBeDisabled();
  });

  it.each([
    { readOnly: true },
    { dirty: true },
    { monitorLocked: true },
    { monitorName: " " },
    { threshold: " " },
    { threshold: "Infinity" },
  ])("preserves monitor validity gate %j", (props) => {
    mount({ action: "monitor", ...props });
    expect(
      screen.getByRole("button", { name: "Create monitor" }),
    ).toBeDisabled();
  });

  it("preserves monitor field edits, feedback and valid submission", () => {
    const onChange = vi.fn();
    const onMonitor = vi.fn();
    mount({
      action: "monitor",
      onChange,
      onMonitor,
      feedback: { severity: "warning", message: "Fixture monitor warning" },
    });
    fireEvent.change(screen.getByLabelText("Monitor name"), {
      target: { value: "Revised quality" },
    });
    expect(onChange).toHaveBeenCalledWith("monitorName", "Revised quality");
    expect(
      screen.getByRole("alert", { name: "Monitor feedback" }),
    ).toHaveTextContent("Fixture monitor warning");
    fireEvent.click(screen.getByRole("button", { name: "Create monitor" }));
    expect(onMonitor).toHaveBeenCalledTimes(1);
  });
});
