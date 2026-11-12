import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import CreateChannelDialog from "./CreateChannelDialog";

const mockUpdateMutate = vi.fn();

vi.mock("../providers/hooks/useGatewayConfig", () => ({
  useUpdateConfig: () => ({
    mutate: mockUpdateMutate,
    isPending: false,
    isError: false,
    error: null,
  }),
}));

const existingChannels = [
  { name: "ops", type: "webhook", url: "https://hooks.example/ops" },
];

async function fillAndSubmit(user, name, url) {
  await user.type(screen.getByLabelText(/Channel name/), name);
  await user.type(screen.getByLabelText(/URL/), url);
  await user.click(screen.getByRole("button", { name: "Add channel" }));
  return mockUpdateMutate.mock.calls[0][0];
}

describe("CreateChannelDialog", () => {
  beforeEach(() => {
    mockUpdateMutate.mockClear();
  });

  it("appends to the existing channels array instead of replacing it", async () => {
    const user = userEvent.setup();
    render(
      <CreateChannelDialog
        open
        onClose={vi.fn()}
        gatewayId="default"
        existingChannels={existingChannels}
      />,
    );

    const payload = await fillAndSubmit(
      user,
      "oncall",
      "https://hooks.example/oncall",
    );

    expect(payload.config.alerting.channels).toEqual([
      existingChannels[0],
      { name: "oncall", type: "webhook", url: "https://hooks.example/oncall" },
    ]);
  });

  it("rejects a trimmed duplicate name without retargeting existing alert rules", async () => {
    const user = userEvent.setup();
    render(
      <CreateChannelDialog
        open
        onClose={vi.fn()}
        gatewayId="default"
        existingChannels={existingChannels}
      />,
    );

    await user.type(screen.getByLabelText(/Channel name/), " ops ");
    await user.type(screen.getByLabelText(/URL/), "https://hooks.example/new");
    expect(
      screen.getByText(/A channel with this name already exists/),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add channel" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Add channel" }));
    expect(mockUpdateMutate).not.toHaveBeenCalled();

    await user.clear(screen.getByLabelText(/Channel name/));
    await user.type(screen.getByLabelText(/Channel name/), "new-ops");
    expect(screen.getByRole("button", { name: "Add channel" })).toBeEnabled();
    expect(
      screen.queryByText(/A channel with this name already exists/),
    ).not.toBeInTheDocument();
  });

  it("offers only the channel types the gateway can deliver to", async () => {
    const user = userEvent.setup();
    render(<CreateChannelDialog open onClose={vi.fn()} gatewayId="default" />);

    await user.click(screen.getByLabelText(/Type/));

    expect(
      screen.getAllByRole("option").map((option) => option.textContent),
    ).toEqual(["Webhook", "Slack", "Log Only"]);
  });
});
