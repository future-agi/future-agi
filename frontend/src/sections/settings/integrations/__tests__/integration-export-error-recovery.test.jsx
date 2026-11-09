import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";

import IntegrationDetail from "../IntegrationDetail";

// A connection in Error has a way back on its page: Retry (a real retry,
// views/integration_connection.py sync_now) and Pause, next to the reason it
// failed. While it is paused, Sync Now waits and says why.

const h = vi.hoisted(() => ({
  detail: null,
  syncNow: vi.fn(),
  pause: vi.fn(),
  resume: vi.fn(),
}));

vi.mock("src/api/integrations", () => ({
  useIntegrationConnection: () => ({
    data: h.detail,
    isLoading: false,
    isError: false,
    error: null,
  }),
  useSyncNow: () => ({ mutate: h.syncNow, isPending: false }),
  usePauseConnection: () => ({ mutate: h.pause, isPending: false }),
  useResumeConnection: () => ({ mutate: h.resume, isPending: false }),
  useUpdateConnection: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("../IntegrationSyncHistory", () => ({ default: () => null }));
vi.mock("../DeleteIntegrationDialog", () => ({ default: () => null }));
vi.mock("react-router-dom", async (importOriginal) => ({
  ...(await importOriginal()),
  useParams: () => ({ connectionId: "c0a80101-0000-4000-8000-000000000004" }),
}));

const REASON =
  "Could not reach PostHog. It is retried automatically; Retry tries again now.";

const connection = (status) => ({
  id: "c0a80101-0000-4000-8000-000000000004",
  platform: "posthog",
  display_name: "PostHog",
  host_url: "http://posthog.example.test",
  status,
  status_message: status === "error" ? REASON : "",
  sync_interval_seconds: 60,
  created_at: "2026-10-01T00:00:00Z",
  last_synced_at: "2026-10-01T00:05:00Z",
  total_traces_synced: 3,
  backfill_completed: true,
});

const buttons = () =>
  screen
    .getAllByRole("button")
    .map((b) => b.textContent)
    .filter((t) => ["Sync Now", "Retry", "Pause", "Resume"].includes(t));

describe("A connection in Error", () => {
  beforeEach(() => {
    h.syncNow.mockClear();
    h.pause.mockClear();
  });

  it("offers Retry and Pause next to its reason", async () => {
    const user = userEvent.setup();
    h.detail = connection("error");
    render(<IntegrationDetail />);

    expect(screen.getByText(REASON)).toBeInTheDocument();
    expect(buttons()).toEqual(["Retry", "Pause"]);
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(h.syncNow).toHaveBeenCalledWith(
      "c0a80101-0000-4000-8000-000000000004",
    );
    await user.click(screen.getByRole("button", { name: "Pause" }));
    expect(h.pause).toHaveBeenCalledWith(
      "c0a80101-0000-4000-8000-000000000004",
    );
  });

  it("is synced with Sync Now once it is active again", () => {
    h.detail = connection("active");
    render(<IntegrationDetail />);
    expect(buttons()).toEqual(["Sync Now", "Pause"]);
    expect(screen.getByRole("button", { name: "Sync Now" })).toBeEnabled();
  });
});

describe("A paused connection", () => {
  it("waits to sync until it is resumed, and says so", async () => {
    const user = userEvent.setup();
    h.detail = connection("paused");
    render(<IntegrationDetail />);

    expect(buttons()).toEqual(["Sync Now", "Resume"]);
    const syncNow = screen.getByRole("button", { name: "Sync Now" });
    expect(syncNow).toBeDisabled();
    await user.hover(syncNow.parentElement);
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      "Resume the connection to sync it.",
    );
  });
});

// Unit proof of integration text/actions, not an offline-icon or browser proof.
// Avoid the icon library's live asset loader in these component-only tests.
vi.mock("@iconify/react", () => ({ Icon: () => null }));
