import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, waitFor } from "@testing-library/react";
import { render, screen } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";

import CreateAlertRuleDialog from "./CreateAlertRuleDialog";

const state = vi.hoisted(() => ({ mutate: vi.fn() }));
vi.mock("../providers/hooks/useGatewayConfig", () => ({
  useUpdateConfig: () => ({ mutate: state.mutate, isPending: false }),
}));

const rule = {
  name: "high-errors", metric: "error_count", condition: ">=", threshold: 10,
  window: "5m", cooldown: "30m", severity: "critical", channels: ["ops"],
  enabled: false, unknown: { preserve: true },
};

beforeEach(() => state.mutate.mockReset());

function show(overrides = {}) {
  const props = { open: true, gatewayId: "default", rule, onClose: vi.fn(), onSave: vi.fn().mockResolvedValue({}), channelNames: ["ops"], ...overrides };
  return { props, ...render(<CreateAlertRuleDialog {...props} />) };
}

describe("edit alert rule", () => {
  it("hydrates the existing rule and saves only edited fields, without resuming it", async () => {
    const user = userEvent.setup();
    const { props } = show();
    expect(screen.getByRole("heading", { name: "Edit alert rule" })).toBeInTheDocument();
    expect(screen.getByLabelText(/Rule name/)).toHaveValue("high-errors");
    expect(screen.getByLabelText(/Rule name/)).toHaveAttribute("readonly");
    expect(screen.getByLabelText(/Threshold/)).toHaveValue(10);
    expect(screen.getByLabelText(/Cooldown/)).toHaveValue("30m");
    expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
    await user.clear(screen.getByLabelText(/Threshold/));
    await user.type(screen.getByLabelText(/Threshold/), "0");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    expect(props.onSave).toHaveBeenCalledWith(rule, { threshold: 0 });
    await waitFor(() => expect(props.onClose).toHaveBeenCalledOnce());
    expect(state.mutate).not.toHaveBeenCalled();
  });

  it("does not materialize missing defaults or discard a stored channel not in the picker", async () => {
    const user = userEvent.setup();
    const sparse = { name: "old", threshold: 2, channels: ["retired-channel"] };
    const { props } = show({ rule: sparse });
    await user.type(screen.getByLabelText(/Cooldown/), "1h");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    expect(props.onSave).toHaveBeenCalledWith(sparse, { cooldown: "1h" });
  });

  it("clearing a duration explicitly requests removal", async () => {
    const user = userEvent.setup();
    const { props } = show();
    await user.clear(screen.getByLabelText(/Window/));
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    expect(props.onSave).toHaveBeenCalledWith(rule, { window: undefined });
  });

  it("keeps the draft and does not close on a conflict or failed apply", async () => {
    const user = userEvent.setup();
    const { props } = show({ onSave: vi.fn().mockRejectedValue(new Error("The rule changed. Reload and reopen it before saving.")) });
    await user.clear(screen.getByLabelText(/Threshold/));
    await user.type(screen.getByLabelText(/Threshold/), "12");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The rule changed");
    expect(screen.getByLabelText(/Threshold/)).toHaveValue(12);
    expect(props.onClose).not.toHaveBeenCalled();
  });

  it("blocks duplicate saves and dismissal while the latest read or write is pending", async () => {
    let finish;
    const pending = new Promise((resolve) => { finish = resolve; });
    const user = userEvent.setup();
    const { props } = show({ onSave: vi.fn().mockReturnValue(pending) });
    fireEvent.change(screen.getByLabelText(/Threshold/), { target: { value: "11" } });
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    expect(screen.getByRole("button", { name: "Saving..." })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
    await user.keyboard("{Escape}");
    expect(props.onClose).not.toHaveBeenCalled();
    expect(props.onSave).toHaveBeenCalledOnce();
    await act(async () => finish({}));
    expect(props.onClose).toHaveBeenCalledOnce();
  });
});
