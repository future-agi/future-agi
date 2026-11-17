import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  render,
  screen,
  waitFor,
  fireEvent,
  within,
} from "src/utils/test-utils";
import ActivityCard from "../ActivityCard";
import { activityText } from "../copy";

const mocks = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock("src/utils/axios", () => ({
  default: { get: mocks.get },
  endpoints: {
    webhooks: { activity: (id) => `/webhooks/endpoints/${id}/activity/` },
  },
}));

const ok = (result) =>
  Promise.resolve({ status: 200, data: { status: true, result } });

const entry = (id, action, extra = {}) => ({
  id,
  action,
  at: "2026-10-23T10:00:00Z",
  actor: { id: "u1", name: "Ada Admin" },
  system: false,
  changed: [],
  changes: null,
  metadata: {},
  ...extra,
});

function withClient(ui) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return <QueryClientProvider client={client}>{ui}</QueryClientProvider>;
}

describe("activityText", () => {
  it("says what happened in the section's words", () => {
    expect(
      activityText(
        entry("a", "webhook.endpoint.updated", {
          changed: ["timeout_ms", "url_sha256", "url_display"],
        }),
      ),
    ).toBe("Settings changed: timeout, URL");
    expect(
      activityText(
        entry("b", "webhook.endpoint.auto_disabled", {
          metadata: { reason: "gone_410" },
        }),
      ),
    ).toBe("Turned off automatically: gone 410");
    expect(
      activityText(
        entry("c", "webhook.endpoint.disabled", {
          metadata: { reason: "Maintenance_window tonight" },
        }),
      ),
    ).toBe("Turned off: Maintenance_window tonight");
    expect(
      activityText(
        entry("d", "webhook.delivery.redelivered", {
          metadata: { bulk: true, count: 12 },
        }),
      ),
    ).toBe("Redelivered: 12 deliveries");
    expect(
      activityText(
        entry("e", "webhook.delivery.redelivered", {
          metadata: { attempt: 2 },
        }),
      ),
    ).toBe("Redelivered: attempt 2");
    expect(
      activityText(
        entry("f", "webhook.test.sent", {
          metadata: { event_type: "prompt.label.changed", status_code: 200 },
        }),
      ),
    ).toBe("Test sent: prompt.label.changed, 200");
    expect(activityText(entry("g", "webhook.future.thing"))).toBe(
      "webhook.future.thing",
    );
  });
});

describe("ActivityCard", () => {
  beforeEach(() => vi.clearAllMocks());

  it("lists the endpoint's entries newest first and pages to older ones", async () => {
    mocks.get.mockImplementation((_url, cfg) =>
      cfg?.params?.cursor
        ? ok({
            results: [entry("e1", "webhook.endpoint.created")],
            next_cursor: null,
          })
        : ok({
            results: [
              entry("e3", "webhook.endpoint.disabled", {
                actor: null,
                system: true,
                metadata: { reason: "creator_removed" },
              }),
              entry("e2", "webhook.secret.rotated"),
            ],
            next_cursor: "e2",
          }),
    );
    render(withClient(<ActivityCard endpoint={{ id: "whep_1" }} />));
    const table = await screen.findByRole("table", {
      name: "Activity entries",
    });
    const rows = within(table).getAllByRole("row");
    expect(rows[1]).toHaveTextContent("Turned off: creator removed");
    expect(rows[1]).toHaveTextContent("Future AGI");
    expect(rows[2]).toHaveTextContent("Signing secret rotated");
    expect(rows[2]).toHaveTextContent("Ada Admin");
    expect(mocks.get).toHaveBeenCalledWith(
      "/webhooks/endpoints/whep_1/activity/",
      { params: { limit: 20 } },
    );
    fireEvent.click(screen.getByRole("button", { name: "Show older" }));
    await waitFor(() =>
      expect(within(table).getAllByRole("row")).toHaveLength(4),
    );
    expect(mocks.get).toHaveBeenLastCalledWith(
      "/webhooks/endpoints/whep_1/activity/",
      { params: { limit: 20, cursor: "e2" } },
    );
    expect(screen.queryByRole("button", { name: "Show older" })).toBeNull();
  });

  it("says so when there is nothing yet, and when the read fails", async () => {
    mocks.get.mockImplementationOnce(() =>
      ok({ results: [], next_cursor: null }),
    );
    const { unmount } = render(
      withClient(<ActivityCard endpoint={{ id: "whep_1" }} />),
    );
    expect(
      await screen.findByText("Nothing has happened to this webhook yet."),
    ).toBeInTheDocument();
    unmount();
    mocks.get.mockImplementationOnce(() => Promise.reject(new Error("503")));
    render(withClient(<ActivityCard endpoint={{ id: "whep_2" }} />));
    expect(
      await screen.findByText("The activity could not be loaded."),
    ).toBeInTheDocument();
  });
});
