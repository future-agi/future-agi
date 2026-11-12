import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, waitFor } from "@testing-library/react";
import { render, screen } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import CreateAlertRuleDialog from "./CreateAlertRuleDialog";

const mockUpdateMutate = vi.fn();

vi.mock("../providers/hooks/useGatewayConfig", () => ({
  useUpdateConfig: () => ({
    mutate: mockUpdateMutate,
    isPending: false,
    isError: false,
    error: null,
  }),
}));

const existingRules = [
  {
    name: "high-errors",
    metric: "error_count",
    condition: ">=",
    threshold: 5,
    window: "5m",
    cooldown: "15m",
    channels: ["ops"],
    enabled: false,
  },
];

async function fillAndSubmit(user, name = "new-rule", threshold = "5") {
  await user.type(screen.getByLabelText(/Rule name/), name);
  await user.type(screen.getByLabelText(/Threshold/), threshold);
  await user.click(screen.getByRole("button", { name: "Create rule" }));
}

describe("CreateAlertRuleDialog", () => {
  beforeEach(() => {
    mockUpdateMutate.mockReset();
  });

  it("only offers metrics the gateway's alerting plugin records", async () => {
    const user = userEvent.setup();
    render(<CreateAlertRuleDialog open onClose={vi.fn()} gatewayId="gw-1" />);

    await user.click(screen.getByLabelText(/Metric/));

    expect(
      screen.getByRole("option", { name: "Error Count" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("option", { name: /error_rate/ }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("option", { name: /latency_p99/ }),
    ).not.toBeInTheDocument();
  });

  it("appends to the existing rules array instead of replacing it", async () => {
    const user = userEvent.setup();
    render(
      <CreateAlertRuleDialog
        open
        onClose={vi.fn()}
        gatewayId="gw-1"
        existingRules={existingRules}
      />,
    );

    await fillAndSubmit(user);

    const [payload] = mockUpdateMutate.mock.calls[0];
    expect(payload.config.alerting.rules).toEqual([
      existingRules[0],
      expect.objectContaining({ name: "new-rule", metric: "error_count" }),
    ]);
  });

  it("turns per-org alerting on so the new rule is actually evaluated", async () => {
    const user = userEvent.setup();
    render(<CreateAlertRuleDialog open onClose={vi.fn()} gatewayId="gw-1" />);

    await fillAndSubmit(user);

    const [payload] = mockUpdateMutate.mock.calls[0];
    expect(payload.config.alerting.enabled).toBe(true);
  });

  it.each(["high-errors", " high-errors "])("rejects duplicate name %j without replacing a paused rule", async (name) => {
    // Create rule is disabled for a duplicate name; the click must do nothing.
    const user = userEvent.setup({ pointerEventsCheck: 0 });
    const onClose = vi.fn();
    render(
      <CreateAlertRuleDialog
        open
        onClose={onClose}
        gatewayId="gw-1"
        existingRules={existingRules}
        channelNames={["ops"]}
      />,
    );

    await fillAndSubmit(user, name, "99");

    expect(mockUpdateMutate).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByLabelText(/Rule name/)).toBeInvalid();
    expect(screen.getByLabelText(/Rule name/)).toHaveAccessibleDescription(/already exists.*unique name.*Edit/i);
    expect(screen.getByRole("button", { name: "Create rule" })).toBeDisabled();
    expect(existingRules[0]).toMatchObject({ threshold: 5, enabled: false, channels: ["ops"] });
  });

  it("allows a unique name after a collision without changing the paused rule", async () => {
    // Create rule is disabled for a duplicate name; the click must do nothing.
    const user = userEvent.setup({ pointerEventsCheck: 0 });
    render(<CreateAlertRuleDialog open onClose={vi.fn()} gatewayId="gw-1" existingRules={existingRules} />);
    await fillAndSubmit(user, "high-errors", "99");
    expect(mockUpdateMutate).not.toHaveBeenCalled();
    await user.clear(screen.getByLabelText(/Rule name/));
    await user.type(screen.getByLabelText(/Rule name/), "new-rule");
    expect(screen.getByLabelText(/Rule name/)).not.toBeInvalid();
    await user.click(screen.getByRole("button", { name: "Create rule" }));

    const [payload] = mockUpdateMutate.mock.calls[0];
    expect(payload.config.alerting.rules).toEqual([
      existingRules[0],
      expect.objectContaining({ name: "new-rule", threshold: 99, enabled: true, channels: [] }),
    ]);
  });

  it("rejects a name added elsewhere since the dialog opened", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    const getLatestRules = vi.fn().mockResolvedValue([...existingRules, { name: "new-rule", threshold: 7, enabled: false, channels: ["ops"] }]);
    render(<CreateAlertRuleDialog open onClose={onClose} gatewayId="gw-1" existingRules={existingRules} getLatestRules={getLatestRules} />);
    await fillAndSubmit(user, " new-rule ", "99");
    expect(await screen.findByRole("alert")).toHaveTextContent(/already exists.*unique name.*Edit/i);
    expect(getLatestRules).toHaveBeenCalledOnce();
    expect(mockUpdateMutate).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByLabelText(/Threshold/)).toHaveValue(99);
  });

  it("appends to the latest rules instead of a stale dialog snapshot", async () => {
    const user = userEvent.setup();
    const latest = [{ ...existingRules[0], threshold: 12, custom: { keep: null } }, { name: "added-elsewhere", threshold: 3 }];
    const getLatestRules = vi.fn().mockResolvedValue(latest);
    render(<CreateAlertRuleDialog open onClose={vi.fn()} gatewayId="gw-1" existingRules={existingRules} getLatestRules={getLatestRules} />);
    await fillAndSubmit(user);
    await waitFor(() => expect(mockUpdateMutate).toHaveBeenCalledOnce());
    expect(getLatestRules).toHaveBeenCalledOnce();
    expect(mockUpdateMutate.mock.calls[0][0].config.alerting.rules).toEqual([
      ...latest,
      expect.objectContaining({ name: "new-rule", threshold: 5, enabled: true }),
    ]);
    expect(latest).toHaveLength(2);
  });

  it("keeps the draft and refuses to write when the latest read fails", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    const getLatestRules = vi.fn().mockRejectedValue(new Error("Could not read the latest rules."));
    render(<CreateAlertRuleDialog open onClose={onClose} gatewayId="gw-1" existingRules={existingRules} getLatestRules={getLatestRules} />);
    await fillAndSubmit(user);
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not read the latest rules.");
    expect(mockUpdateMutate).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByLabelText(/Rule name/)).toHaveValue("new-rule");
    expect(screen.getByRole("button", { name: "Create rule" })).toBeEnabled();
  });

  it("blocks repeated creation and dismissal until the read and write settle", async () => {
    let finish;
    const getLatestRules = vi.fn().mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    // Creating... is disabled while the read is pending; the click must do nothing.
    const user = userEvent.setup({ pointerEventsCheck: 0 });
    const onClose = vi.fn();
    render(<CreateAlertRuleDialog open onClose={onClose} gatewayId="gw-1" getLatestRules={getLatestRules} />);
    await fillAndSubmit(user);
    const create = screen.getByRole("button", { name: "Creating..." });
    expect(create).toBeDisabled();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
    await user.click(create);
    await user.keyboard("{Escape}");
    expect(getLatestRules).toHaveBeenCalledOnce();
    expect(mockUpdateMutate).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
    await act(async () => finish(existingRules));
    expect(mockUpdateMutate).toHaveBeenCalledOnce();
    expect(screen.getByRole("button", { name: "Creating..." })).toBeDisabled();
    act(() => mockUpdateMutate.mock.calls[0][1].onSettled());
    expect(screen.getByRole("button", { name: "Create rule" })).toBeEnabled();
  });

  it("still allows Edit to save the existing name without a creation read", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue({});
    const getLatestRules = vi.fn();
    render(<CreateAlertRuleDialog open onClose={vi.fn()} gatewayId="gw-1" existingRules={existingRules} rule={existingRules[0]} onSave={onSave} getLatestRules={getLatestRules} />);
    await user.clear(screen.getByLabelText(/Threshold/));
    await user.type(screen.getByLabelText(/Threshold/), "99");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    expect(onSave).toHaveBeenCalledWith(existingRules[0], { threshold: 99 });
    expect(getLatestRules).not.toHaveBeenCalled();
    expect(mockUpdateMutate).not.toHaveBeenCalled();
  });

  it("puts the picked channels on the rule so it has somewhere to notify", async () => {
    const user = userEvent.setup();
    render(
      <CreateAlertRuleDialog
        open
        onClose={vi.fn()}
        gatewayId="gw-1"
        channelNames={["ops", "oncall"]}
      />,
    );

    await user.click(screen.getByLabelText(/Notify channels/));
    await user.click(screen.getByRole("option", { name: "ops" }));
    await user.keyboard("{Escape}");
    await fillAndSubmit(user);

    const [payload] = mockUpdateMutate.mock.calls[0];
    expect(payload.config.alerting.rules[0].channels).toEqual(["ops"]);
  });
});
