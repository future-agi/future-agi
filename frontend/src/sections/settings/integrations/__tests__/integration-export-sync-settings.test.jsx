import React from "react";
import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";

import { EXPORT_PLATFORMS, SKIP_PROJECT_MAPPING_PLATFORMS } from "../constants";
import StepSyncSettings from "../AddIntegrationWizard/StepSyncSettings";

// An export destination's sync settings say what its first sync exports:
// the last 24 hours (Datadog: the last 18 hours, all its log intake accepts),
// then each new request log, or from a chosen date. It has no "new only"
// choice and no end date, which an export does not honour, and no Langfuse
// project step.

const h = vi.hoisted(() => ({ create: vi.fn() }));

vi.mock("src/api/integrations", () => ({
  useCreateConnection: () => ({
    mutate: h.create,
    isPending: false,
    error: null,
  }),
}));

const renderStep = (platform) =>
  render(
    <StepSyncSettings
      data={{
        platform,
        credentials: { api_key: "k" },
        backfillOption: "new_only",
        syncIntervalSeconds: 300,
      }}
      onUpdate={vi.fn()}
      onBack={vi.fn()}
      onSuccess={vi.fn()}
    />,
  );

describe("Export sync settings", () => {
  it.each([
    ["posthog", 24],
    ["datadog", 18],
    ["mixpanel", 24],
    ["cloud_storage", 24],
    ["message_queue", 24],
  ])(
    "%s offers the two export starts (the last %i hours)",
    (platform, hours) => {
      renderStep(platform);
      expect(
        screen.getByText(`Export the last ${hours} hours`),
      ).toBeInTheDocument();
      expect(screen.getByText("Then each new request log")).toBeInTheDocument();
      expect(
        screen.getByText("Export from a specific date"),
      ).toBeInTheDocument();
      expect(screen.queryByText(/import/i)).toBeNull();
      expect(screen.queryByText(/traces/i)).toBeNull();
      expect(
        screen.getByRole("radio", { name: new RegExp(`last ${hours} hours`) }),
      ).toBeChecked();
    },
  );

  describe("Datadog", () => {
    const LIMIT =
      "Datadog accepts logs from the last 18 hours. Pick a start within them.";
    const startHoursAgo = (hours) => {
      const d = new Date(Date.now() - hours * 60 * 60 * 1000);
      const pad = (n) => String(n).padStart(2, "0");
      return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
    };
    const renderFrom = (hours) =>
      render(
        <StepSyncSettings
          data={{
            platform: "datadog",
            hostUrl: "https://api.datadoghq.eu",
            credentials: { api_key: "k", site: "eu1" },
            backfillOption: "from_date",
            backfillFromDate: startHoursAgo(hours),
          }}
          onUpdate={vi.fn()}
          onBack={vi.fn()}
          onSuccess={vi.fn()}
        />,
      );

    it("says why and refuses a start older than 18 hours", () => {
      h.create.mockClear();
      renderFrom(20);
      const start = screen.getByLabelText("Start Date & Time");
      expect(start).toHaveAttribute("min");
      expect(screen.getByText(LIMIT)).toBeInTheDocument();
      fireEvent.click(
        screen.getByRole("button", { name: "Connect Integration" }),
      );
      expect(screen.getAllByText(LIMIT)).toHaveLength(2);
      expect(h.create).not.toHaveBeenCalled();
    });

    it("drops the refusal once the last 18 hours are picked again", () => {
      h.create.mockClear();
      renderFrom(20);
      fireEvent.click(
        screen.getByRole("button", { name: "Connect Integration" }),
      );
      expect(screen.getAllByText(LIMIT)).toHaveLength(2);
      fireEvent.click(screen.getByRole("radio", { name: /last 18 hours/ }));
      expect(screen.queryByText(LIMIT)).toBeNull();
      expect(screen.queryByRole("alert")).toBeNull();
    });

    it("keeps a start within the 18 hours", () => {
      h.create.mockClear();
      renderFrom(12);
      fireEvent.click(
        screen.getByRole("button", { name: "Connect Integration" }),
      );
      expect(h.create).toHaveBeenCalledTimes(1);
      expect(h.create.mock.calls[0][0]).toMatchObject({
        platform: "datadog",
        backfill_option: "from_date",
      });
    });
  });

  it("asks no end date for an export from a date", () => {
    renderStep("posthog");
    fireEvent.click(screen.getByRole("radio", { name: /specific date/ }));
    expect(screen.getByLabelText("Start Date & Time")).toBeInTheDocument();
    expect(screen.queryByLabelText("End Date & Time")).toBeNull();
  });

  it("creates the export with the last 24 hours by default", () => {
    h.create.mockClear();
    renderStep("datadog");
    fireEvent.click(
      screen.getByRole("button", { name: "Connect Integration" }),
    );
    expect(h.create.mock.calls[0][0]).toMatchObject({
      platform: "datadog",
      backfill_option: "all",
    });
  });

  // What each credentials step names as the destination (hostUrl), sent as
  // host_url with the connection's name.
  it.each([
    [
      "cloud_storage",
      { sub_platform: "s3", bucket: "acme-logs", region: "eu-west-1" },
      "https://acme-logs.s3.eu-west-1.amazonaws.com/gw",
      "Amazon S3",
    ],
    [
      "datadog",
      { api_key: "k", site: "eu1" },
      "https://api.datadoghq.eu",
      "Datadog",
    ],
    [
      "mixpanel",
      { project_token: "t" },
      "https://api.mixpanel.com",
      "Mixpanel",
    ],
    ["posthog", { api_key: "phc" }, "https://eu.i.posthog.com", "PostHog"],
  ])(
    "creates %s with its destination and name",
    (platform, credentials, hostUrl, name) => {
      h.create.mockClear();
      render(
        <StepSyncSettings
          data={{ platform, hostUrl, credentials }}
          onUpdate={vi.fn()}
          onBack={vi.fn()}
          onSuccess={vi.fn()}
        />,
      );
      fireEvent.click(
        screen.getByRole("button", { name: "Connect Integration" }),
      );
      expect(h.create.mock.calls[0][0]).toMatchObject({
        host_url: hostUrl,
        display_name: name,
      });
    },
  );

  describe("Mixpanel without an API secret", () => {
    const fromDate = (daysAgo) => {
      const d = new Date(Date.now() - daysAgo * 24 * 60 * 60 * 1000);
      const pad = (n) => String(n).padStart(2, "0");
      return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
    };
    const renderMixpanel = (credentials, daysAgo) =>
      render(
        <StepSyncSettings
          data={{
            platform: "mixpanel",
            hostUrl: "https://api.mixpanel.com",
            credentials,
            backfillOption: "from_date",
            backfillFromDate: fromDate(daysAgo),
          }}
          onUpdate={vi.fn()}
          onBack={vi.fn()}
          onSuccess={vi.fn()}
        />,
      );
    const LIMIT =
      "Without an API secret, Mixpanel accepts request logs from the last 5 days. Add the API secret to export older ones.";

    it("says older logs need the API secret and refuses a start before it", () => {
      h.create.mockClear();
      renderMixpanel({ project_token: "t" }, 6);
      const start = screen.getByLabelText("Start Date & Time");
      expect(start).toHaveAttribute("min");
      expect(screen.getByText(LIMIT)).toBeInTheDocument();
      fireEvent.click(
        screen.getByRole("button", { name: "Connect Integration" }),
      );
      expect(h.create).not.toHaveBeenCalled();
      expect(screen.getAllByText(LIMIT)).toHaveLength(2);
    });

    it("exports from a date within the last 5 days", () => {
      h.create.mockClear();
      renderMixpanel({ project_token: "t" }, 2);
      fireEvent.click(
        screen.getByRole("button", { name: "Connect Integration" }),
      );
      expect(h.create.mock.calls[0][0].backfill_option).toBe("from_date");
    });

    it("exports from any date with the API secret", () => {
      h.create.mockClear();
      renderMixpanel({ project_token: "t", api_secret: "s" }, 30);
      expect(screen.getByLabelText("Start Date & Time")).not.toHaveAttribute(
        "min",
      );
      expect(screen.queryByText(LIMIT)).toBeNull();
      fireEvent.click(
        screen.getByRole("button", { name: "Connect Integration" }),
      );
      expect(h.create.mock.calls[0][0].backfill_option).toBe("from_date");
    });
  });

  it("keeps the import choices for Langfuse", () => {
    renderStep("langfuse");
    expect(screen.getByText("Import all traces")).toBeInTheDocument();
    expect(
      screen.getByText("Only import new traces going forward"),
    ).toBeInTheDocument();
  });

  it("asks no Langfuse project for an export", () => {
    // Fixed public inputs reach the baseline behavior, not an absent export.
    ["posthog", "datadog", "mixpanel", "cloud_storage"].forEach((platform) =>
      expect(SKIP_PROJECT_MAPPING_PLATFORMS).toContain(platform),
    );
    expect(EXPORT_PLATFORMS).toEqual(
      expect.arrayContaining([
        "posthog",
        "datadog",
        "mixpanel",
        "cloud_storage",
      ]),
    );
    EXPORT_PLATFORMS.forEach((platform) =>
      expect(SKIP_PROJECT_MAPPING_PLATFORMS).toContain(platform),
    );
  });
});
