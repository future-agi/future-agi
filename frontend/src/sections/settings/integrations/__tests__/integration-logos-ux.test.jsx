import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "src/utils/test-utils";

import { PLATFORMS, offeredPlatforms } from "../constants";
import StepPlatformSelect from "../AddIntegrationWizard/StepPlatformSelect";
import IntegrationsList from "../IntegrationsList";

// Settings > Integrations shows each platform with its logo, on the page's
// platform cards and in the Add Integration wizard's platform step (the logo
// beside the name, as on the page), says on a platform's card when it is
// already connected, offers one Add Integration, and describes in the wizard
// what each export sends, as its card does.

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

// Error Feed allowed, so Linear is offered too.
vi.mock("src/hooks/useCapabilities", async (importOriginal) => ({
  ...(await importOriginal()),
  useFeatureAllowed: () => ({
    allowed: true,
    reasonCode: null,
    isLoading: false,
    isError: false,
  }),
}));

const offered = () => offeredPlatforms(() => true);

const renderList = () =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <IntegrationsList />
    </QueryClientProvider>,
  );

const renderPicker = (props = {}) => {
  const handlers = {
    onUpdate: vi.fn(),
    onNext: vi.fn(),
    onSelectSlack: vi.fn(),
  };
  render(
    <StepPlatformSelect
      platforms={offered()}
      data={{ platform: "" }}
      {...handlers}
      {...props}
    />,
  );
  return handlers;
};

const escape = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const platformCard = (name) =>
  screen.getByRole("button", {
    name: new RegExp(`^${escape(name)}(?:\\s|$)`, "i"),
  });

// A platform's mark inside its card: its bundled logo when it has one, else
// its icon; never the generic avatar MUI draws when there is neither.
function expectLogo(card, platform) {
  const mark = card.querySelector(".MuiAvatar-root");
  expect(mark, platform.id).not.toBeNull();
  expect(mark.querySelector(".MuiAvatar-fallback"), platform.id).toBeNull();
  if (platform.logo) {
    expect(mark.querySelector("img").getAttribute("src")).toBe(platform.logo);
  }
  return mark;
}

const connection = (id, platform, name) => ({
  id,
  platform,
  display_name: name,
  host_url: "https://us.i.posthog.com",
  status: "active",
  created_at: "2026-10-01T00:00:00Z",
});

beforeEach(() => {
  h.connections = [];
});

describe("platform logos", () => {
  it("every offered platform card on the page shows its logo beside its name", () => {
    renderList();
    expect(offered().length).toBeGreaterThan(0);
    offered().forEach((platform) => {
      const card = platformCard(platform.name);
      const mark = expectLogo(card, platform);
      expect(mark.parentElement).toContainElement(
        within(card).getByText(platform.name),
      );
    });
  });

  it("every platform in the wizard is a button with its logo beside its name", () => {
    renderPicker();
    offered().forEach((platform) => {
      const card = platformCard(platform.name);
      const mark = expectLogo(card, platform);
      const row = mark.parentElement;
      expect(row).toHaveStyle({ display: "flex" });
      expect(row).toContainElement(within(card).getByText(platform.name));
    });
  });
});

describe("wizard platform step", () => {
  it("picks an export platform and moves on", () => {
    const handlers = renderPicker();
    fireEvent.click(platformCard("Datadog"));
    expect(handlers.onUpdate).toHaveBeenCalledWith({ platform: "datadog" });
    expect(handlers.onNext).toHaveBeenCalledTimes(1);
    expect(handlers.onSelectSlack).not.toHaveBeenCalled();
  });

  it("starts the Slack install for Slack", () => {
    const handlers = renderPicker();
    fireEvent.click(platformCard("Slack"));
    expect(handlers.onSelectSlack).toHaveBeenCalledTimes(1);
    expect(handlers.onUpdate).not.toHaveBeenCalled();
    expect(handlers.onNext).not.toHaveBeenCalled();
  });

  it("describes what each export sends, as the page's card does", () => {
    const byId = Object.fromEntries(PLATFORMS.map((p) => [p.id, p]));
    expect(byId.posthog.wizardDescription).toBe(
      "Export AI Gateway request logs to PostHog as events",
    );
    expect(byId.mixpanel.wizardDescription).toBe(
      "Export AI Gateway request logs to Mixpanel as events",
    );
    expect(byId.cloud_storage.wizardDescription).toBe(
      "Archive AI Gateway request logs in Amazon S3",
    );
    renderPicker();
    offered().forEach((platform) => {
      expect(
        within(platformCard(platform.name)).getByText(
          platform.wizardDescription,
        ),
      ).toBeInTheDocument();
    });
    expect(screen.queryByText(/LLM usage|object storage/i)).toBeNull();
  });
});

describe("connected platforms", () => {
  it("marks a platform with one connection Connected", () => {
    h.connections = [connection("c1", "posthog", "Product analytics")];
    renderList();
    expect(
      within(platformCard("PostHog")).getByText("Connected"),
    ).toBeInTheDocument();
    expect(
      within(platformCard("Datadog")).queryByText(/connected/i),
    ).toBeNull();
  });

  it("counts a platform's connections when there are more", () => {
    h.connections = [
      connection("c1", "posthog", "Product analytics"),
      connection("c2", "posthog", "Staging analytics"),
    ];
    renderList();
    const card = platformCard("PostHog");
    expect(within(card).getByText("2 connected")).toBeInTheDocument();
    expect(within(card).queryByText("Connected")).toBeNull();
    expect(
      within(platformCard("Datadog")).queryByText(/connected/i),
    ).toBeNull();
  });

  it("offers one Add Integration when nothing is connected", () => {
    renderList();
    expect(screen.getByText("No integrations connected")).toBeInTheDocument();
    expect(
      screen.getAllByRole("button", { name: /add integration/i }),
    ).toHaveLength(1);
    offered().forEach((platform) => {
      expect(
        within(platformCard(platform.name)).queryByText(/connected/i),
      ).toBeNull();
    });
  });
});
