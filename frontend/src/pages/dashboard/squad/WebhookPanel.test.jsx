import { describe, expect, it, vi } from "vitest";
import { screen, userEvent, waitFor, render, act } from "src/utils/test-utils";
import WebhookPanel from "./WebhookPanel";

function makeClient() {
  return {
    listChannels: vi.fn().mockResolvedValue({
      available: true,
      channels: [
        { channelId: "managed-runs", events: 1, subscriptions: { active: 1 } },
      ],
    }),
    listChannelSubscriptions: vi.fn().mockResolvedValue({
      available: true,
      subscriptions: [
        {
          id: "sub-1",
          status: "active",
          url: "https://hooks.example.test/omega",
          hasSigningSecret: true,
        },
      ],
    }),
    listChannelEvents: vi.fn().mockResolvedValue({
      available: true,
      items: [
        {
          id: "event-1",
          eventKey: "run.failed",
          at: "2026-10-12T00:00:00.000Z",
          taskId: "task-1",
        },
      ],
    }),
    getChannelDeliverySlo: vi.fn().mockResolvedValue({
      available: true,
      summary: { timelyDeliveryRate: 1 },
      alert: { severity: "none", verdict: "meets" },
    }),
    listChannelEventDeliveries: vi.fn().mockResolvedValue({
      available: true,
      deliveries: [
        {
          id: "delivery-1",
          status: "dead_letter",
          attempts: 3,
          targetUrl: "https://hooks.example.test/omega",
        },
      ],
    }),
    createChannelSubscription: vi
      .fn()
      .mockResolvedValue({
        available: true,
        transport: { headers: { "x-omega-receipt-id": "receipt-create" } },
      }),
    testChannelSubscription: vi.fn().mockResolvedValue({ available: true }),
    deleteChannelSubscription: vi.fn().mockResolvedValue({ available: true }),
    redriveChannelDelivery: vi
      .fn()
      .mockResolvedValue({
        available: true,
        transport: { headers: { "x-omega-receipt-id": "receipt-redrive" } },
      }),
  };
}

function deferred() {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

describe("Omega Squad webhook panel", () => {
  it("loads tenant channel health and keeps endpoint actions on the remote client", async () => {
    const client = makeClient();
    vi.spyOn(globalThis, "confirm").mockReturnValue(true);
    render(<WebhookPanel client={client} />);

    expect(await screen.findByText("Webhook channels")).toBeInTheDocument();
    expect(await screen.findByText("run.failed")).toBeInTheDocument();
    expect(await screen.findByText(/24h timely 100%/)).toBeInTheDocument();
    expect(
      await screen.findByText(/Delivery attempts for run.failed/),
    ).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Test" }));
    await waitFor(() =>
      expect(client.testChannelSubscription).toHaveBeenCalledWith(
        "managed-runs",
        "sub-1",
      ),
    );

    await userEvent.click(screen.getByRole("button", { name: "Redrive" }));
    await waitFor(() =>
      expect(client.redriveChannelDelivery).toHaveBeenCalledWith(
        "managed-runs",
        "run.failed",
        "delivery-1",
        expect.stringMatching(/^squad-redrive-delivery-1-/),
      ),
    );

    await userEvent.type(
      screen.getByRole("textbox", { name: "Endpoint URL" }),
      "https://new.example.test/hook",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Create endpoint" }),
    );
    await waitFor(() =>
      expect(client.createChannelSubscription).toHaveBeenCalledWith(
        "managed-runs",
        { url: "https://new.example.test/hook" },
        expect.stringMatching(/^squad-endpoint-/),
      ),
    );
  });

  it("refreshes the selected event's delivery after redrive without requiring reselection", async () => {
    const client = makeClient();
    render(<WebhookPanel client={client} />);
    await screen.findByText(/dead_letter · 3 attempts/);
    client.listChannelEventDeliveries.mockResolvedValueOnce({
      available: true,
      deliveries: [
        { id: "delivery-1", status: "delivered", attempts: 4, statusCode: 204 },
      ],
    });
    await userEvent.click(screen.getByRole("button", { name: "Redrive" }));
    expect(
      await screen.findByText("delivered · 4 attempts · HTTP 204"),
    ).toBeInTheDocument();
    expect(client.listChannelEventDeliveries).toHaveBeenCalledTimes(2);
    expect(
      screen.queryByRole("button", { name: "Redrive" }),
    ).not.toBeInTheDocument();
  });

  it("ignores stale channel details and never sends their event key to the new channel", async () => {
    const client = makeClient();
    const pending = deferred();
    client.listChannels.mockResolvedValue({
      available: true,
      channels: [{ channelId: "a" }, { channelId: "b" }],
    });
    client.listChannelSubscriptions.mockImplementation((channel) =>
      channel === "a"
        ? pending.promise
        : Promise.resolve({ available: true, subscriptions: [] }),
    );
    client.listChannelEvents.mockImplementation(async (channel) => ({
      available: true,
      items: [{ id: `${channel}-event`, eventKey: `${channel}-event` }],
    }));
    render(<WebhookPanel client={client} />);
    await waitFor(() =>
      expect(client.listChannelSubscriptions).toHaveBeenCalledWith("a"),
    );
    await userEvent.click(screen.getByRole("button", { name: /^b / }));
    expect(await screen.findByText("b-event")).toBeInTheDocument();
    await act(async () =>
      pending.resolve({
        available: true,
        subscriptions: [
          {
            id: "stale-endpoint",
            description: "A private endpoint",
            status: "active",
          },
        ],
      }),
    );
    expect(screen.queryByText("A private endpoint")).not.toBeInTheDocument();
    expect(screen.queryByText("a-event")).not.toBeInTheDocument();
    expect(client.listChannelEventDeliveries).not.toHaveBeenCalledWith(
      "b",
      "a-event",
      expect.anything(),
    );
  });

  it("clears workspace data and ignores a mutation response after the authenticated client changes", async () => {
    const previous = makeClient();
    const next = makeClient();
    const pending = deferred();
    const nextInventory = deferred();
    previous.createChannelSubscription.mockReturnValue(pending.promise);
    next.listChannels.mockReturnValue(nextInventory.promise);
    const { rerender } = render(<WebhookPanel client={previous} />);
    await screen.findByText("run.failed");
    await userEvent.type(
      screen.getByRole("textbox", { name: "Endpoint URL" }),
      "https://private.example.test/hook",
    );
    await userEvent.type(
      screen.getByLabelText("Signing secret (optional)"),
      "private-secret",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Create endpoint" }),
    );
    rerender(<WebhookPanel client={next} />);
    expect(screen.queryByText("run.failed")).not.toBeInTheDocument();
    expect(
      screen.queryByDisplayValue("private-secret"),
    ).not.toBeInTheDocument();
    await act(async () =>
      pending.resolve({
        available: true,
        receipt: { id: "old-workspace-receipt" },
      }),
    );
    expect(screen.queryByText("old-workspace-receipt")).not.toBeInTheDocument();
    expect(previous.listChannelSubscriptions).toHaveBeenCalledTimes(1);
    await act(async () =>
      nextInventory.resolve({ available: true, channels: [] }),
    );
    expect(
      await screen.findByText("No tenant webhook channels are registered."),
    ).toBeInTheDocument();
  });

  it("hides the prior event's delivery until the newly selected event finishes loading", async () => {
    const client = makeClient();
    const pending = deferred();
    client.listChannelEvents.mockResolvedValue({
      available: true,
      items: [{ eventKey: "first-event" }, { eventKey: "second-event" }],
    });
    render(<WebhookPanel client={client} />);
    await screen.findByText(/delivery-1 ·/);
    client.listChannelEventDeliveries.mockReturnValueOnce(pending.promise);
    await userEvent.click(screen.getByRole("button", { name: /second-event/ }));
    expect(screen.queryByText(/delivery-1 ·/)).not.toBeInTheDocument();
    expect(
      screen.getByLabelText("Loading delivery history"),
    ).toBeInTheDocument();
    await act(async () => pending.resolve({ available: true, deliveries: [] }));
    expect(
      await screen.findByText("No delivery attempts recorded."),
    ).toBeInTheDocument();
  });

  it("preserves a failed endpoint draft, clears its secret and recovers from a network error", async () => {
    const client = makeClient();
    client.createChannelSubscription.mockRejectedValueOnce(
      new Error("private-transport-value"),
    );
    render(<WebhookPanel client={client} />);
    await screen.findByText("run.failed");
    await userEvent.type(
      screen.getByRole("textbox", { name: "Endpoint URL" }),
      "https://retry.example.test/hook",
    );
    await userEvent.type(
      screen.getByLabelText("Signing secret (optional)"),
      "private-signing-secret",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Create endpoint" }),
    );
    expect(
      await screen.findByText(/could not be confirmed/),
    ).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Endpoint URL" })).toHaveValue(
      "https://retry.example.test/hook",
    );
    expect(screen.getByLabelText("Signing secret (optional)")).toHaveValue("");
    expect(
      screen.queryByText("private-transport-value"),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Create endpoint" }),
    ).toBeEnabled();
    expect(client.createChannelSubscription).toHaveBeenCalledTimes(1);
  });

  it("recovers inventory after a failed fetch", async () => {
    const client = makeClient();
    client.listChannels.mockRejectedValueOnce(
      new Error("private-transport-value"),
    );
    render(<WebhookPanel client={client} />);
    await screen.findByText(/webhook channel service is unavailable/);
    await userEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(await screen.findByText("run.failed")).toBeInTheDocument();
    expect(
      screen.queryByText("private-transport-value"),
    ).not.toBeInTheDocument();
  });
});
