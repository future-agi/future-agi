import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";
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

const calm = {
  min_findings: 50,
  entry_multiplier: 4,
  entry_delta: 25,
  entry_confirmation_minutes: 30,
  recovery_min: 15,
  recovery_multiplier: 2,
  recovery_delta: 8,
  recovery_confirmation_minutes: 20,
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
  window_minutes: 60,
  baseline_hours: 24,
  cadence_minutes: 5,
  processing_allowance_minutes: 10,
};
const props = {
  open: true,
  organizationId: "org-a",
  workspaceId: "workspace-a",
  onClose: vi.fn(),
};
const field = (group, label) =>
  within(screen.getByRole("group", { name: group })).getByRole("spinbutton", {
    name: `${group} ${label.toLowerCase()}`,
  });
const change = (group, label, value) =>
  fireEvent.change(field(group, label), { target: { value } });
const openFineTune = () =>
  fireEvent.click(screen.getByRole("button", { name: /^Fine-tune/ }));
const preset = (name) =>
  screen.getByRole("button", { name: new RegExp(`^${name}:`) });
const sentence = () =>
  screen.getByText(/^For example, an issue that/).closest("[aria-live]");

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
  it("shows workspace scope, the saved preset, a plain summary and all eight values", () => {
    render(<EscalationSettingsDialog {...props} />);
    expect(
      screen.getByRole("dialog", { name: "Escalation settings" }),
    ).toBeVisible();
    // MUI assigns DialogTitle the dialog label id unless it has its own id.
    // Keep the close and info buttons out of the dialog name.
    expect(
      document.querySelectorAll("#escalation-settings-title"),
    ).toHaveLength(1);
    expect(
      document.getElementById("escalation-settings-title").textContent,
    ).toBe("Escalation settings");
    expect(screen.getByText("Agent team")).toBeVisible();
    expect(preset("Balanced")).toHaveAttribute("aria-pressed", "true");
    expect(sentence()).toHaveTextContent(
      "For example, an issue that usually gets −+ findings an hour becomes Escalating after 15 minutes at 30+ an hour, and goes back to For review after 10 minutes at 15 or fewer.",
    );
    openFineTune();
    expect(screen.getAllByRole("spinbutton")).toHaveLength(8);
    expect(field("Escalate", "Findings")).toHaveValue(20);
    expect(field("Recover", "Hold for")).toHaveValue(10);
    // 3× usual decides both limits for an issue that usually gets 10.
    expect(
      within(screen.getByRole("group", { name: "Escalate" })).getByText(
        "decides",
      ),
    ).toBeVisible();
  });

  it("keeps Save off until something changes", () => {
    render(<EscalationSettingsDialog {...props} />);
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Reset to defaults" }),
    ).toBeDisabled();
  });

  it("applies a preset to all eight values and saves it", async () => {
    render(<EscalationSettingsDialog {...props} />);
    fireEvent.click(preset("Calm"));
    expect(preset("Calm")).toHaveAttribute("aria-pressed", "true");
    expect(sentence()).toHaveTextContent("after 30 minutes at 50+ an hour");
    expect(sentence()).toHaveTextContent("after 20 minutes at 20 or fewer");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(mocked.save).toHaveBeenCalledWith({
        organizationId: "org-a",
        workspaceId: "workspace-a",
        revision: 7,
        values: {
          ...calm,
          entry_multiplier: "4.0",
          recovery_multiplier: "2.0",
        },
      }),
    );
    expect(props.onClose).toHaveBeenCalledOnce();
  });

  it("switches to Custom when a value is fine-tuned and saves a complete numeric draft", async () => {
    render(<EscalationSettingsDialog {...props} />);
    openFineTune();
    change("Escalate", "Findings", "25");
    expect(preset("Custom")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("Edited")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(mocked.save).toHaveBeenCalledWith({
        organizationId: "org-a",
        workspaceId: "workspace-a",
        revision: 7,
        values: { ...values, min_findings: 25 },
      }),
    );
  });

  it("opens Fine-tune from the Custom option", () => {
    render(<EscalationSettingsDialog {...props} />);
    fireEvent.click(preset("Custom"));
    expect(field("Escalate", "Times usual")).toBeVisible();
    expect(preset("Balanced")).toHaveAttribute("aria-pressed", "true");
  });

  it("previews other traffic levels without changing the draft", () => {
    render(<EscalationSettingsDialog {...props} />);
    fireEvent.click(
      screen.getByRole("button", { name: "More findings an hour" }),
    );
    expect(sentence()).toHaveTextContent("at 33+ an hour");
    fireEvent.change(
      screen.getByRole("textbox", {
        name: "Usual findings an hour (preview only)",
      }),
      { target: { value: "2" } },
    );
    // At low traffic the 20-finding minimum decides.
    expect(sentence()).toHaveTextContent("at 20+ an hour");
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("keeps Enter in the preview-only field from submitting the form", () => {
    render(<EscalationSettingsDialog {...props} />);
    fireEvent.click(preset("Calm"));
    const usual = screen.getByRole("textbox", {
      name: "Usual findings an hour (preview only)",
    });
    fireEvent.change(usual, { target: { value: "0" } });
    // Clamped to 1 and shown as 1, so the field matches the preview.
    expect(usual).toHaveValue("1");
    // fireEvent returns false when the handler called preventDefault.
    expect(fireEvent.keyDown(usual, { key: "Enter" })).toBe(false);
    expect(mocked.save).not.toHaveBeenCalled();
  });

  it("keeps the sample-issue chart collapsed until asked", () => {
    render(<EscalationSettingsDialog {...props} />);
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Preview on a sample issue" }),
    );
    expect(screen.getByRole("img")).toHaveAccessibleName(
      /^Escalates after 15 min at 30\+, recovers after 10 min at 15 or fewer\.$/,
    );
  });

  it("warns before discarding a dirty draft and does not save Cancel", () => {
    render(<EscalationSettingsDialog {...props} />);
    fireEvent.click(preset("Sensitive"));
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
    expect(preset("Custom")).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: "Reset to defaults" }));
    expect(preset("Balanced")).toHaveAttribute("aria-pressed", "true");
    openFineTune();
    expect(field("Escalate", "Findings")).toHaveValue(20);
    expect(mocked.save).not.toHaveBeenCalled();
  });

  it("flags invalid values as they are typed and blocks Save", async () => {
    render(<EscalationSettingsDialog {...props} />);
    openFineTune();
    change("Escalate", "Hold for", "7");
    expect(screen.getByText("Use steps of 5.")).toBeVisible();
    expect(
      screen.getByText("Fix the highlighted values to update this preview."),
    ).toBeVisible();
    // The summary keeps the last valid setup while a value is invalid.
    expect(sentence()).toHaveTextContent("after 15 minutes at 30+");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    change("Escalate", "Hold for", "15");
    change("Escalate", "Findings", "4");
    expect(screen.getByText("Must be below 4.")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    change("Escalate", "Findings", "");
    expect(screen.getByText("Enter 2 to 1,000,000.")).toBeVisible();
    await waitFor(() => expect(mocked.save).not.toHaveBeenCalled());
  });

  it("shows how many values need fixing when Fine-tune is closed", () => {
    render(<EscalationSettingsDialog {...props} />);
    openFineTune();
    change("Recover", "Times usual", "3");
    openFineTune();
    expect(screen.getByText("1 to fix")).toBeVisible();
  });

  it("retains the draft after a save failure", async () => {
    mocked.save.mockRejectedValue(new Error("Could not save"));
    render(<EscalationSettingsDialog {...props} />);
    openFineTune();
    change("Escalate", "Findings", "25");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText(
      "Could not save escalation settings. Your changes are retained.",
    );
    expect(field("Escalate", "Findings")).toHaveValue(25);
    expect(props.onClose).not.toHaveBeenCalled();
  });

  it("offers explicit reload after a revision conflict", async () => {
    // The shared Axios interceptor rejects a normalized object, not AxiosError.
    mocked.save.mockRejectedValue({
      statusCode: 409,
      error: "Policy revision changed",
    });
    mocked.refetch.mockResolvedValue({
      data: { ...data, revision: 8, values: calm },
    });
    render(<EscalationSettingsDialog {...props} />);
    openFineTune();
    change("Escalate", "Findings", "25");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText(
      "Settings changed. Reload the latest settings before saving.",
    );
    expect(field("Escalate", "Findings")).toHaveValue(25);
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Reload settings" }));
    await screen.findByText("Loaded the latest settings.");
    expect(field("Escalate", "Findings")).toHaveValue(50);
    expect(preset("Calm")).toHaveAttribute("aria-pressed", "true");
  });

  it("allows readers to inspect settings without mutation controls", () => {
    mocked.query.mockReturnValue({ data: { ...data, can_edit: false } });
    render(<EscalationSettingsDialog {...props} />);
    expect(
      screen.getByText(
        "Only workspace administrators can change these settings.",
      ),
    ).toBeVisible();
    expect(preset("Sensitive")).toBeDisabled();
    openFineTune();
    expect(field("Escalate", "Findings")).toBeDisabled();
    expect(
      screen.queryByRole("button", { name: "Save" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Reset to defaults" }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(props.onClose).toHaveBeenCalledOnce();
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
    expect(preset("Calm")).toBeDisabled();
    openFineTune();
    expect(field("Escalate", "Findings")).toBeDisabled();
  });

  it("does not close the new workspace dialog when an old save completes", async () => {
    let finish;
    mocked.save.mockReturnValue(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    const { rerender } = render(<EscalationSettingsDialog {...props} />);
    fireEvent.click(preset("Sensitive"));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(mocked.save).toHaveBeenCalledOnce());
    mocked.query.mockReturnValue({
      data: {
        ...data,
        workspace_id: "workspace-b",
        workspace_name: "Other team",
        values: calm,
      },
    });
    rerender(<EscalationSettingsDialog {...props} workspaceId="workspace-b" />);
    await waitFor(() =>
      expect(preset("Calm")).toHaveAttribute("aria-pressed", "true"),
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
