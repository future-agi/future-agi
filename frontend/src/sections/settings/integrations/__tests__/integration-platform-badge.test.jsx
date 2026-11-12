import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "src/utils/test-utils";

import IntegrationsList from "../IntegrationsList";

// Settings > Integrations: a platform's card says how many connections it
// has ("Connected", "{n} connected"), in the success colour only while every
// one of them is working (active, syncing or backfilling); a paused or
// failed one turns it to a warning. Same harness as
// integration-logos-ux.test.jsx, with the shared badge drawn as its label and
// role so the role can be read.

const h = vi.hoisted(() => ({ connections: [] }));

vi.mock("src/api/integrations", () => ({
  integrationKeys: { connections: () => ["integrations", "connections"] },
  useIntegrationConnections: () => ({
    data: h.connections,
    isLoading: false,
    isError: false,
  }),
  useStartSlackInstall: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteConnection: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));

vi.mock("../AddIntegrationWizard", () => ({ default: () => null }));

vi.mock("src/hooks/useCapabilities", async (importOriginal) => ({
  ...(await importOriginal()),
  useFeatureAllowed: () => ({
    allowed: true,
    reasonCode: null,
    isLoading: false,
    isError: false,
  }),
}));

vi.mock("src/components/status-badge/StatusBadge", () => {
  // eslint-disable-next-line react/prop-types -- a test-local stand-in for StatusBadge
  const Badge = ({ status, label, role }) => (
    <span data-testid="status-badge" data-role={role || ""}>
      {label || status}
    </span>
  );
  return { default: Badge, StatusBadge: Badge, StatusDot: Badge };
});

const renderList = () =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <IntegrationsList />
    </QueryClientProvider>,
  );

const escape = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const platformCard = (name) =>
  screen.getByRole("button", {
    name: new RegExp(`^${escape(name)}(?:\\s|$)`, "i"),
  });

// The count badge on a platform's card (the only badge the card draws).
function countBadge(name) {
  const badges = within(platformCard(name)).getAllByTestId("status-badge");
  expect(badges).toHaveLength(1);
  return badges[0];
}

const connection = (id, status, name = id) => ({
  id,
  platform: "posthog",
  display_name: name,
  host_url: "https://us.i.posthog.com",
  status,
  created_at: "2026-10-01T00:00:00Z",
});

beforeEach(() => {
  h.connections = [];
});

describe("the platform card's connection badge", () => {
  it("is a success Connected for one active connection", () => {
    h.connections = [connection("c1", "active")];
    renderList();
    const badge = countBadge("PostHog");
    expect(badge).toHaveTextContent("Connected");
    expect(badge.getAttribute("data-role")).toBe("success");
  });

  it("stays success while every connection is active, syncing or backfilling", () => {
    h.connections = [
      connection("c1", "active"),
      connection("c2", "syncing"),
      connection("c3", "backfilling"),
    ];
    renderList();
    const badge = countBadge("PostHog");
    expect(badge).toHaveTextContent("3 connected");
    expect(badge.getAttribute("data-role")).toBe("success");
  });

  it.each(["paused", "error"])(
    "keeps the count but warns when one connection is %s",
    (status) => {
      h.connections = [connection("c1", "active"), connection("c2", status)];
      renderList();
      const badge = countBadge("PostHog");
      expect(badge).toHaveTextContent("2 connected");
      expect(badge.getAttribute("data-role")).toBe("warning");
    },
  );

  it.each(["paused", "error"])(
    "keeps Connected but warns for a single %s connection",
    (status) => {
      h.connections = [connection("c1", status)];
      renderList();
      const badge = countBadge("PostHog");
      expect(badge).toHaveTextContent("Connected");
      expect(badge.getAttribute("data-role")).toBe("warning");
    },
  );

  it("counts only the platform's own connections", () => {
    h.connections = [
      connection("c1", "active"),
      { ...connection("c2", "error"), platform: "datadog" },
    ];
    renderList();
    const posthog = countBadge("PostHog");
    expect(posthog).toHaveTextContent("Connected");
    expect(posthog.getAttribute("data-role")).toBe("success");
    const datadog = countBadge("Datadog");
    expect(datadog).toHaveTextContent("Connected");
    expect(datadog.getAttribute("data-role")).toBe("warning");
  });
});
