import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import EscalationSettingsDialog from "../EscalationSettingsDialog";

const mocked = vi.hoisted(() => ({
  query: vi.fn(),
  mutation: vi.fn(),
  save: vi.fn(),
  refetch: vi.fn(),
}));
vi.mock("src/api/errorFeed/error-feed", () => ({
  useErrorFeedEscalationSettings: mocked.query,
  useUpdateErrorFeedEscalationSettings: mocked.mutation,
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

const values = {
  min_findings: 20,
  entry_multiplier: "3.0",
  entry_delta: 10,
  entry_confirmation_minutes: 15,
  recovery_min: 5,
  recovery_multiplier: "1.5",
  recovery_delta: 3,
  recovery_confirmation_minutes: 10,
};
const ranges = {
  min_findings: { min: 2, max: 1000000, step: 1 },
  entry_multiplier: { min: 1.1, max: 20, step: 0.1 },
  entry_delta: { min: 1, max: 1000000, step: 1 },
  entry_confirmation_minutes: { min: 5, max: 60, step: 5 },
  recovery_min: { min: 0, max: 999999, step: 1 },
  recovery_multiplier: { min: 1, max: 19.9, step: 0.1 },
  recovery_delta: { min: 0, max: 999999, step: 1 },
  recovery_confirmation_minutes: { min: 5, max: 60, step: 5 },
};
const data = {
  values,
  defaults: values,
  ranges,
  revision: 7,
  workspace_id: "workspace-a",
  workspace_name: "Agent team",
  can_edit: true,
};
const props = {
  open: true,
  organizationId: "org-a",
  workspaceId: "workspace-a",
  onClose: vi.fn(),
};
const change = (label, value) =>
  fireEvent.change(screen.getByRole("spinbutton", { name: label }), {
    target: { value },
  });

beforeEach(() => {
  vi.clearAllMocks();
  mocked.query.mockReturnValue({
    data,
    isLoading: false,
    isError: false,
    refetch: mocked.refetch,
  });
  mocked.mutation.mockReturnValue({
    mutateAsync: mocked.save,
    isPending: false,
  });
  mocked.save.mockResolvedValue({ data: { result: data } });
});

describe("Escalation settings dialog", () => {
  it("shows workspace scope, saved defaults and all eight controls", () => {
    render(<EscalationSettingsDialog {...props} />);
    expect(
      screen.getByRole("dialog", { name: "Escalation settings" }),
    ).toBeVisible();
    // MUI assigns DialogTitle the dialog label id unless it has its own id.
    // Keep the close button out of the dialog name in real browser trees.
    expect(
      document.querySelectorAll("#escalation-settings-title"),
    ).toHaveLength(1);
    expect(
      document.getElementById("escalation-settings-title").textContent,
    ).toBe("Escalation settings");
    expect(screen.getByText("Agent team")).toBeVisible();
    expect(
      screen.getByText("Applies to all projects in this workspace."),
    ).toBeVisible();
    expect(
      screen.getByRole("spinbutton", { name: "Minimum findings in an hour" }),
    ).toHaveValue(20);
    fireEvent.click(screen.getByRole("button", { name: "Recovery" }));
    expect(screen.getAllByRole("spinbutton")).toHaveLength(8);
    expect(
      screen.getByRole("spinbutton", {
        name: "Recovery confirmation (minutes)",
      }),
    ).toHaveValue(10);
  });

  it("saves a complete numeric draft with captured scope and revision", async () => {
    render(<EscalationSettingsDialog {...props} />);
    change("Minimum findings in an hour", "25");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(mocked.save).toHaveBeenCalledWith({
        organizationId: "org-a",
        workspaceId: "workspace-a",
        revision: 7,
        values: {
          ...values,
          min_findings: 25,
          entry_multiplier: 3,
          recovery_multiplier: 1.5,
        },
      }),
    );
    expect(props.onClose).toHaveBeenCalledOnce();
  });

  it("warns before discarding a dirty draft and does not save Cancel", () => {
    render(<EscalationSettingsDialog {...props} />);
    change("Minimum findings in an hour", "25");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(
      screen.getByRole("dialog", { name: "Discard changes?" }),
    ).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Discard changes" }));
    expect(props.onClose).toHaveBeenCalledOnce();
    expect(mocked.save).not.toHaveBeenCalled();
  });

  it("Reset changes only the draft until Save", () => {
    mocked.query.mockReturnValue({
      data: { ...data, values: { ...values, min_findings: 30 } },
      refetch: mocked.refetch,
    });
    render(<EscalationSettingsDialog {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "Reset to defaults" }));
    expect(
      screen.getByRole("spinbutton", { name: "Minimum findings in an hour" }),
    ).toHaveValue(20);
    expect(mocked.save).not.toHaveBeenCalled();
  });

  it("rejects overlapping recovery settings and invalid confirmation increments", async () => {
    render(<EscalationSettingsDialog {...props} />);
    change("Escalation confirmation (minutes)", "7");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("Use increments of 5.");
    expect(mocked.save).not.toHaveBeenCalled();
    change("Escalation confirmation (minutes)", "15");
    change("Minimum findings in an hour", "4");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("Must be lower than the entry minimum.");
    expect(mocked.save).not.toHaveBeenCalled();
  });

  it("retains the draft after a save failure", async () => {
    mocked.save.mockRejectedValue(new Error("Could not save"));
    render(<EscalationSettingsDialog {...props} />);
    change("Minimum findings in an hour", "25");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText(
      "Could not save escalation settings. Your changes are retained.",
    );
    expect(
      screen.getByRole("spinbutton", { name: "Minimum findings in an hour" }),
    ).toHaveValue(25);
    expect(props.onClose).not.toHaveBeenCalled();
  });

  it("offers explicit reload after a revision conflict", async () => {
    // The shared Axios interceptor rejects a normalized object, not AxiosError.
    mocked.save.mockRejectedValue({
      statusCode: 409,
      error: "Policy revision changed",
    });
    mocked.refetch.mockResolvedValue({
      data: { ...data, revision: 8, values: { ...values, min_findings: 40 } },
    });
    render(<EscalationSettingsDialog {...props} />);
    change("Minimum findings in an hour", "25");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText(
      "Settings changed. Reload the latest settings before saving.",
    );
    expect(
      screen.getByRole("spinbutton", { name: "Minimum findings in an hour" }),
    ).toHaveValue(25);
    fireEvent.click(screen.getByRole("button", { name: "Reload settings" }));
    await waitFor(() =>
      expect(
        screen.getByRole("spinbutton", { name: "Minimum findings in an hour" }),
      ).toHaveValue(40),
    );
  });

  it("allows readers to inspect settings without mutation controls", () => {
    mocked.query.mockReturnValue({ data: { ...data, can_edit: false } });
    render(<EscalationSettingsDialog {...props} />);
    expect(
      screen.getByRole("spinbutton", { name: "Minimum findings in an hour" }),
    ).toBeDisabled();
    expect(
      screen.queryByRole("button", { name: "Save" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Reset to defaults" }),
    ).not.toBeInTheDocument();
  });

  it("does not show a response belonging to a previous workspace", () => {
    render(<EscalationSettingsDialog {...props} workspaceId="workspace-b" />);
    expect(screen.queryByRole("spinbutton")).not.toBeInTheDocument();
    expect(screen.queryByText("Agent team")).not.toBeInTheDocument();
  });

  it("disables editing while a save is pending", () => {
    mocked.mutation.mockReturnValue({
      mutateAsync: mocked.save,
      isPending: true,
    });
    render(<EscalationSettingsDialog {...props} />);
    expect(
      screen.getByRole("spinbutton", { name: "Minimum findings in an hour" }),
    ).toBeDisabled();
  });

  it("does not close the new workspace dialog when an old save completes", async () => {
    let finish;
    mocked.save.mockReturnValue(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    const { rerender } = render(<EscalationSettingsDialog {...props} />);
    change("Minimum findings in an hour", "25");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(mocked.save).toHaveBeenCalledOnce());
    mocked.query.mockReturnValue({
      data: {
        ...data,
        workspace_id: "workspace-b",
        workspace_name: "Other team",
        values: { ...values, min_findings: 40 },
      },
    });
    rerender(<EscalationSettingsDialog {...props} workspaceId="workspace-b" />);
    await waitFor(() =>
      expect(
        screen.getByRole("spinbutton", { name: "Minimum findings in an hour" }),
      ).toHaveValue(40),
    );
    finish({ data: { result: data } });
    await waitFor(() => expect(screen.getByText("Other team")).toBeVisible());
    expect(props.onClose).not.toHaveBeenCalled();
  });

  it("shows loading and supports retry after a failed GET", () => {
    mocked.query.mockReturnValue({ isLoading: true });
    const { rerender } = render(<EscalationSettingsDialog {...props} />);
    expect(
      screen.getByRole("progressbar", { name: "Loading escalation settings" }),
    ).toBeVisible();
    mocked.query.mockReturnValue({ isError: true, refetch: mocked.refetch });
    rerender(<EscalationSettingsDialog {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(mocked.refetch).toHaveBeenCalledOnce();
  });
});
