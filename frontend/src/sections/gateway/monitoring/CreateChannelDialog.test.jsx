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
  await user.type(screen.getByLabelText(/Channel Name/), name);
  await user.type(screen.getByLabelText(/URL/), url);
  await user.click(screen.getByRole("button", { name: "Add Channel" }));
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

    const payload = await fillAndSubmit(user, "oncall", "https://hooks.example/oncall");

    expect(payload.config.alerting.channels).toEqual([
      existingChannels[0],
      { name: "oncall", type: "webhook", url: "https://hooks.example/oncall" },
    ]);
  });

  it("replaces a channel of the same name rather than duplicating it", async () => {
    const user = userEvent.setup();
    render(
      <CreateChannelDialog
        open
        onClose={vi.fn()}
        gatewayId="default"
        existingChannels={existingChannels}
      />,
    );

    const payload = await fillAndSubmit(user, "ops", "https://hooks.example/new");

    expect(payload.config.alerting.channels).toEqual([
      { name: "ops", type: "webhook", url: "https://hooks.example/new" },
    ]);
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
