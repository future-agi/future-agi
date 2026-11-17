import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, within } from "src/utils/test-utils";

import IntegrationCard from "../IntegrationCard";
import IntegrationDetail from "../IntegrationDetail";
import DeleteIntegrationDialog from "../DeleteIntegrationDialog";
import DatadogCredentials from "../AddIntegrationWizard/credentials/DatadogCredentials";
import MixpanelCredentials from "../AddIntegrationWizard/credentials/MixpanelCredentials";
import CloudStorageCredentials from "../AddIntegrationWizard/credentials/CloudStorageCredentials";
import { connectionDestination, s3Destination } from "../utils";

// A connection shows where it delivers: the Connections card and the
// connection's header read the destination its credentials step named (the
// Datadog site's API host, Mixpanel's API, the S3 bucket in its region under
// its prefix, the PostHog address entered), never a made-up host. An export's
// card counts the request logs it exported, and its page names its platform.

const h = vi.hoisted(() => ({
  detail: null,
  validate: vi.fn(),
  create: vi.fn(),
}));

vi.mock("src/api/integrations", () => ({
  useIntegrationConnection: () => ({
    data: h.detail,
    isLoading: false,
    isError: false,
    error: null,
  }),
  useSyncNow: () => ({ mutate: vi.fn(), isPending: false }),
  usePauseConnection: () => ({ mutate: vi.fn(), isPending: false }),
  useResumeConnection: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateConnection: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteConnection: () => ({ mutate: vi.fn(), isPending: false }),
  useStartSlackInstall: () => ({ mutate: vi.fn(), isPending: false }),
  useValidateCredentials: () => ({
    mutate: h.validate,
    isPending: false,
    error: null,
  }),
  useCreateConnection: () => ({
    mutate: h.create,
    isPending: false,
    error: null,
  }),
}));

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("../IntegrationSyncHistory", () => ({ default: () => null }));

const S3_URL =
  "https://acme-gateway-logs.s3.eu-west-1.amazonaws.com/agentcc/production";

const connection = (platform, host_url, extra = {}) => ({
  id: "c0a80101-0000-4000-8000-000000000003",
  platform,
  display_name: { cloud_storage: "Amazon S3" }[platform] || platform,
  host_url,
  status: "active",
  external_project_name: platform,
  total_traces_synced: 1234,
  sync_interval_seconds: 300,
  created_at: "2026-10-01T00:00:00Z",
  last_synced_at: "2026-10-01T00:05:00Z",
  backfill_completed: true,
  ...extra,
});

const DESTINATIONS = [
  ["datadog", "https://api.datadoghq.eu", "https://api.datadoghq.eu"],
  ["mixpanel", "https://api.mixpanel.com", "https://api.mixpanel.com"],
  [
    "cloud_storage",
    S3_URL,
    "s3://acme-gateway-logs/agentcc/production in eu-west-1",
  ],
  ["posthog", "https://posthog.acme.test", "https://posthog.acme.test"],
];

describe("Where a connection delivers", () => {
  it.each(DESTINATIONS)(
    "%s reads %s as %s",
    (platform, hostUrl, destination) => {
      expect(connectionDestination({ platform, host_url: hostUrl })).toBe(
        destination,
      );
    },
  );

  it("names an S3 bucket without a prefix and a bucket with dots", () => {
    expect(
      connectionDestination({
        platform: "cloud_storage",
        host_url: "https://acme-logs.s3.us-east-1.amazonaws.com/",
      }),
    ).toBe("s3://acme-logs in us-east-1");
    expect(
      connectionDestination({
        platform: "cloud_storage",
        host_url: "https://logs.acme.io.s3.ap-south-1.amazonaws.com/gw",
      }),
    ).toBe("s3://logs.acme.io/gw in ap-south-1");
  });

  it("derives the S3 address the backend stores", () => {
    expect(
      s3Destination({
        bucket: "acme-gateway-logs",
        region: "eu-west-1",
        prefix: "/agentcc/production/",
      }),
    ).toBe(S3_URL);
  });

  it.each(DESTINATIONS)(
    "the %s card shows its destination and its export count",
    (platform, hostUrl, destination) => {
      render(<IntegrationCard connection={connection(platform, hostUrl)} />);
      expect(
        screen.getByTestId("integration-card-destination"),
      ).toHaveTextContent(destination);
      expect(
        screen.getByText("1,234 request log deliveries (including repeats)"),
      ).toBeInTheDocument();
      expect(screen.queryByText(/traces/)).toBeNull();
    },
  );

  it("an import card still counts its traces", () => {
    render(
      <IntegrationCard
        connection={connection("langfuse", "https://cloud.langfuse.com")}
      />,
    );
    expect(screen.getByText("1,234 traces")).toBeInTheDocument();
  });

  it.each(DESTINATIONS)(
    "the %s page header shows its destination",
    (platform, hostUrl, destination) => {
      h.detail = connection(platform, hostUrl);
      render(<IntegrationDetail />);
      expect(
        screen.getByTestId("integration-header-destination"),
      ).toHaveTextContent(destination);
      expect(
        screen.getByText("Request Log Deliveries (Including Repeats)"),
      ).toBeInTheDocument();
      expect(screen.getByText("1,234")).toBeInTheDocument();
    },
  );

  it("names the platform on the connection page", () => {
    h.detail = connection("cloud_storage", S3_URL);
    render(<IntegrationDetail />);
    const platformRow = screen.getByText("Platform").parentElement;
    expect(within(platformRow).getByText("Cloud Storage")).toBeInTheDocument();
    expect(screen.queryByText("cloud_storage")).toBeNull();
  });

  it("says what deleting an export keeps", () => {
    render(
      <DeleteIntegrationDialog
        open
        onClose={vi.fn()}
        connection={connection("datadog", "https://api.datadoghq.eu")}
      />,
    );
    expect(
      screen.getByText(
        /This stops exporting request logs and removes the connection/,
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/synced traces/)).toBeNull();
  });
});

const validated = () =>
  h.validate.mockImplementation((_payload, { onSuccess }) =>
    onSuccess({ data: { result: { valid: true } } }),
  );

describe("Each credentials step names its destination", () => {
  beforeEach(() => {
    h.validate.mockReset();
    h.create.mockReset();
    validated();
  });

  it("Datadog names the chosen site's API host and asks no Application Key", () => {
    const onUpdate = vi.fn();
    render(
      <DatadogCredentials
        data={{ credentials: { site: "eu1" } }}
        onUpdate={onUpdate}
        onNext={vi.fn()}
        onBack={vi.fn()}
      />,
    );
    expect(screen.queryByLabelText(/Application Key/)).toBeNull();
    expect(screen.queryByText(/dashboard/i)).toBeNull();
    fireEvent.change(screen.getByLabelText(/API Key/), {
      target: { value: "dd-key" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Validate & Continue" }),
    );
    expect(h.validate.mock.calls[0][0].credentials).toEqual({
      api_key: "dd-key",
      site: "eu1",
    });
    expect(onUpdate).toHaveBeenCalledWith(
      expect.objectContaining({ hostUrl: "https://api.datadoghq.eu" }),
    );
  });

  it("Mixpanel names its API", () => {
    const onUpdate = vi.fn();
    render(
      <MixpanelCredentials
        data={{ credentials: {} }}
        onUpdate={onUpdate}
        onNext={vi.fn()}
        onBack={vi.fn()}
      />,
    );
    fireEvent.change(screen.getByLabelText(/Project Token/), {
      target: { value: "mp-token" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Validate & Continue" }),
    );
    expect(onUpdate).toHaveBeenCalledWith(
      expect.objectContaining({ hostUrl: "https://api.mixpanel.com" }),
    );
  });

  it("Amazon S3 names the bucket in its region under its prefix", () => {
    const onUpdate = vi.fn();
    render(
      <CloudStorageCredentials
        data={{
          credentials: {
            sub_platform: "s3",
            bucket: "acme-gateway-logs",
            region: "eu-west-1",
            access_key_id: "AKIAEXAMPLE",
            secret_access_key: "example-secret",
            prefix: "agentcc/production",
          },
        }}
        onUpdate={onUpdate}
        onNext={vi.fn()}
        onBack={vi.fn()}
      />,
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Validate & Continue" }),
    );
    expect(onUpdate).toHaveBeenCalledWith(
      expect.objectContaining({ hostUrl: S3_URL }),
    );
  });
});

// Unit proof of integration text/actions, not an offline-icon or browser proof.
// Avoid the icon library's live asset loader in these component-only tests.
vi.mock("@iconify/react", () => ({ Icon: () => null }));
