import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "src/utils/test-utils";

import {
  LISTED_PLATFORMS,
  PLATFORMS,
  UNLISTED_DESTINATIONS,
  isDestinationListed,
  offeredPlatforms,
} from "../constants";
import StepPlatformSelect from "../AddIntegrationWizard/StepPlatformSelect";
import CloudStorageCredentials from "../AddIntegrationWizard/credentials/CloudStorageCredentials";
import MessageQueueCredentials from "../AddIntegrationWizard/credentials/MessageQueueCredentials";
import IntegrationsList from "../IntegrationsList";

// The page grid and the Add Integration wizard offer only the destinations
// listed in constants.js; a destination left out of them is left out of both,
// with nothing in its place, and a connection that already exists for one
// stays on the page. Linear, whose one use is Error Feed, is offered only
// where the deployment allows Error Feed.

const h = vi.hoisted(() => ({ connections: [], errorFeed: false }));

vi.mock("src/hooks/useCapabilities", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    useFeatureAllowed: (feature) => ({
      allowed: feature === actual.CAPABILITY.ERROR_FEED && h.errorFeed,
      reasonCode: null,
      isLoading: false,
      isError: false,
    }),
  };
});

vi.mock("src/api/integrations", () => ({
  integrationKeys: { connections: () => ["integrations", "connections"] },
  useIntegrationConnections: () => ({
    data: h.connections,
    isLoading: false,
    isError: false,
  }),
  useStartSlackInstall: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteConnection: () => ({ mutate: vi.fn(), isPending: false }),
  useValidateCredentials: () => ({
    mutate: vi.fn(),
    isPending: false,
    error: null,
  }),
}));

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));

vi.mock("../AddIntegrationWizard", () => ({ default: () => null }));

// The sub-platform choices the credential steps can show, by label.
const SUB_PLATFORM_LABELS = {
  cloud_storage: {
    s3: "Amazon S3",
    azure_blob: "Azure Blob",
    gcs: "Google Cloud Storage",
  },
  message_queue: { sqs: "Amazon SQS", pubsub: "Google Pub/Sub" },
};

const renderList = () =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <IntegrationsList />
    </QueryClientProvider>,
  );

const offered = () => offeredPlatforms(() => h.errorFeed);
const notOffered = () =>
  PLATFORMS.filter((p) => !offered().some((o) => o.id === p.id));

describe("Offered integration destinations", () => {
  beforeEach(() => {
    h.connections = [];
    h.errorFeed = false;
  });

  it("names only known platforms and sub-platforms", () => {
    const ids = PLATFORMS.map((p) => p.id);
    expect(PLATFORMS.find((p) => p.id === "cloud_storage").description).toBe(
      "Request log archival in Amazon S3",
    );
    expect(Array.isArray(UNLISTED_DESTINATIONS)).toBe(true);
    UNLISTED_DESTINATIONS.forEach((entry) => {
      const [platform, sub] = entry.split("/");
      expect(ids).toContain(platform);
      if (sub)
        expect(Object.keys(SUB_PLATFORM_LABELS[platform])).toContain(sub);
    });
    expect(LISTED_PLATFORMS.map((p) => p.id)).toEqual(
      ids.filter((id) => !UNLISTED_DESTINATIONS.includes(id)),
    );
  });

  it("leaves PagerDuty out of the page and the wizard", () => {
    expect(isDestinationListed("pagerduty")).toBe(false);
  });

  it("leaves Langfuse out until a flow proves the import", () => {
    expect(isDestinationListed("langfuse")).toBe(false);
  });

  it("leaves Message Queue out until a flow proves a publish", () => {
    expect(isDestinationListed("message_queue")).toBe(false);
  });

  it("offers Amazon S3 as the one Cloud Storage choice", () => {
    expect(isDestinationListed("cloud_storage", "azure_blob")).toBe(false);
    expect(isDestinationListed("cloud_storage", "gcs")).toBe(false);
    expect(isDestinationListed("cloud_storage", "s3")).toBe(
      isDestinationListed("cloud_storage"),
    );
    const card = PLATFORMS.find((p) => p.id === "cloud_storage");
    expect(card.description).not.toMatch(/azure|gcs|google/i);
  });

  it.each([false, true])(
    "offers Linear only where Error Feed is allowed (allowed: %s)",
    (allowed) => {
      h.errorFeed = allowed;
      renderList();
      expect(Boolean(screen.queryByText("Linear"))).toBe(allowed);
      expect(
        Boolean(screen.queryByText("Issue tracking for Error Feed clusters")),
      ).toBe(allowed);
      expect(
        screen.getByText(
          allowed
            ? "Export request logs and metrics, or send alerts and issues to an external platform."
            : "Export request logs and metrics, or send alerts to an external platform.",
        ),
      ).toBeInTheDocument();
    },
  );

  it("shows exactly the offered platforms on the Integrations page", () => {
    renderList();
    ["PagerDuty", "Langfuse", "Message Queue"].forEach((name) =>
      expect(screen.queryByText(name)).toBeNull(),
    );
    expect(Array.isArray(LISTED_PLATFORMS)).toBe(true);
    offered().forEach((p) => {
      expect(screen.getByText(p.name)).toBeInTheDocument();
    });
    notOffered().forEach((p) => {
      expect(screen.queryByText(p.name)).toBeNull();
      expect(screen.queryByText(p.description)).toBeNull();
    });
    expect(screen.queryAllByText(/coming soon/i)).toHaveLength(0);
  });

  it("shows exactly the offered platforms in the Add Integration wizard", () => {
    render(
      <StepPlatformSelect
        platforms={offered()}
        data={{ platform: "" }}
        onUpdate={vi.fn()}
        onNext={vi.fn()}
        onSelectSlack={vi.fn()}
      />,
    );
    ["PagerDuty", "Langfuse", "Message Queue"].forEach((name) =>
      expect(screen.queryByText(name)).toBeNull(),
    );
    expect(Array.isArray(LISTED_PLATFORMS)).toBe(true);
    offered().forEach((p) => {
      expect(screen.getByText(p.name)).toBeInTheDocument();
    });
    notOffered().forEach((p) => {
      expect(screen.queryByText(p.name)).toBeNull();
      expect(
        screen.queryByText(p.wizardDescription || p.description),
      ).toBeNull();
    });
    expect(screen.queryAllByText(/coming soon/i)).toHaveLength(0);
  });

  it("keeps an existing connection of a platform it no longer offers", () => {
    h.connections = [
      {
        id: "c0a80101-0000-4000-8000-000000000001",
        platform: "pagerduty",
        display_name: "On-call routing",
        status: "active",
        created_at: "2026-10-01T00:00:00Z",
      },
    ];
    renderList();
    expect(screen.getByText("Connections (1)")).toBeInTheDocument();
    expect(screen.getByText("On-call routing")).toBeInTheDocument();
  });

  it.each([
    ["cloud_storage", CloudStorageCredentials],
    ["message_queue", MessageQueueCredentials],
  ])("offers only the listed %s choices", (platform, Form) => {
    render(
      <Form
        data={{ credentials: {} }}
        onUpdate={vi.fn()}
        onNext={vi.fn()}
        onBack={vi.fn()}
      />,
    );
    Object.entries(SUB_PLATFORM_LABELS[platform]).forEach(([sub, label]) => {
      if (isDestinationListed(platform, sub))
        expect(screen.getByText(label)).toBeInTheDocument();
      else expect(screen.queryByText(label)).toBeNull();
    });
  });
});
