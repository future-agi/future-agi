import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";

import EditIntegrationDialog from "../EditIntegrationDialog";

// An export's Edit dialog replaces its credentials: Datadog's API key,
// PostHog's project API key, Mixpanel's project token and API secret, and an
// S3 connection's access key pair. A field left blank keeps the current value,
// and only the fields filled in are sent (the backend checks them with the
// platform before it saves them).

const h = vi.hoisted(() => ({ mutate: vi.fn() }));

vi.mock("src/api/integrations", () => ({
  useUpdateConnection: () => ({
    mutate: h.mutate,
    isPending: false,
    error: null,
  }),
}));

const HOSTS = {
  datadog: "https://api.datadoghq.eu",
  posthog: "http://posthog.example.test",
  mixpanel: "https://api.mixpanel.com",
  cloud_storage: "https://logs.s3.eu-west-1.amazonaws.com/agentcc",
};

const connection = (platform) => ({
  id: "c0a80101-0000-4000-8000-000000000003",
  platform,
  display_name: platform,
  host_url: HOSTS[platform],
  status: "error",
  sync_interval_seconds: 300,
});

const open = (platform) =>
  render(
    <EditIntegrationDialog
      open
      onClose={vi.fn()}
      connection={connection(platform)}
    />,
  );

describe("An export's Edit dialog", () => {
  beforeEach(() => h.mutate.mockClear());

  it.each([
    ["datadog", ["API Key"]],
    ["posthog", ["Project API Key"]],
    ["mixpanel", ["Project Token", "API Secret"]],
    ["cloud_storage", ["Access Key ID", "Secret Access Key"]],
  ])("%s offers its credentials, blank", (platform, labels) => {
    open(platform);
    labels.forEach((label) => {
      const input = screen.getByLabelText(label);
      expect(input).toHaveValue("");
      expect(input).toHaveAttribute(
        "placeholder",
        "Leave blank to keep the current one",
      );
    });
  });

  it("sends only the credentials filled in, and says they are checked", async () => {
    const user = userEvent.setup();
    open("mixpanel");
    await user.type(screen.getByLabelText("Project Token"), " tok-new ");
    expect(
      screen.getByText(
        "The new credentials are checked with Mixpanel before they are saved.",
      ),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Save Changes" }));
    await waitFor(() => expect(h.mutate).toHaveBeenCalledTimes(1));
    expect(h.mutate.mock.calls[0][0]).toEqual({
      id: "c0a80101-0000-4000-8000-000000000003",
      data: { credentials: { project_token: "tok-new" } },
    });
  });

  it("replaces an S3 key pair", async () => {
    const user = userEvent.setup();
    open("cloud_storage");
    await user.type(screen.getByLabelText("Access Key ID"), "AKIANEW");
    await user.type(screen.getByLabelText("Secret Access Key"), "new-secret");
    await user.click(screen.getByRole("button", { name: "Save Changes" }));
    await waitFor(() => expect(h.mutate).toHaveBeenCalledTimes(1));
    expect(h.mutate.mock.calls[0][0].data).toEqual({
      credentials: {
        access_key_id: "AKIANEW",
        secret_access_key: "new-secret",
      },
    });
  });
});
