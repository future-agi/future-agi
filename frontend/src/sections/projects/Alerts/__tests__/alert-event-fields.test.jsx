import React from "react";
import PropTypes from "prop-types";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const MARK = "SYNTHETIC_HOOK_VALUE_51c2";

const mocks = vi.hoisted(() => {
  vi.stubEnv("VITE_MIXPANEL_TOKEN", "synthetic-project-token");
  return {
    mixpanel: {
      init: vi.fn(),
      track: vi.fn(),
      get_config: vi.fn(),
      set_config: vi.fn(),
      stop_session_recording: vi.fn(),
    },
    post: vi.fn(),
  };
});

vi.mock("mixpanel-browser", () => ({ default: mocks.mixpanel }));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/utils/axios", () => ({
  default: { post: mocks.post },
  endpoints: { project: { createMonitor: "/tracer/monitor/" } },
}));

import {
  alertEventFields,
  useCreateAlertMutation,
} from "../useCreateAlertMutation";
import { Events } from "src/utils/Mixpanel";

const alertPayload = {
  name: "Synthetic alert name",
  metric_type: "count_of_errors",
  project: "project-1",
  organization: "org-1",
  alert_frequency: 60,
  filters: { observation_type: ["llm"] },
  threshold_type: "static",
  threshold_operator: "greater_than",
  critical_threshold_value: 10,
  warning_threshold_value: 5,
  notification_emails: [],
  slack_webhook_url: `https://hooks.slack.example.test/services/${MARK}`,
  slack_notes: `Synthetic notes ${MARK}`,
  webhook_url: "",
};

describe("alert saved analytics event", () => {
  beforeEach(() => {
    mocks.mixpanel.track.mockClear();
    mocks.post.mockResolvedValue({ data: { id: "alert-1" } });
  });

  it("sends only the declared alert fields when an alert is created", async () => {
    const client = new QueryClient({
      defaultOptions: { mutations: { retry: false } },
    });
    function Wrapper({ children }) {
      return (
        <QueryClientProvider client={client}>{children}</QueryClientProvider>
      );
    }
    Wrapper.propTypes = { children: PropTypes.node };
    const { result } = renderHook(
      () => useCreateAlertMutation({ metricType: "count_of_errors" }),
      { wrapper: Wrapper },
    );

    result.current.mutate(alertPayload);

    await waitFor(() =>
      expect(mocks.mixpanel.track).toHaveBeenCalledWith(
        Events.createAlertConfirmed,
        expect.anything(),
      ),
    );
    const [, properties] = mocks.mixpanel.track.mock.calls.find(
      ([name]) => name === Events.createAlertConfirmed,
    );
    expect(properties).toEqual({
      form_fields: {
        metricType: "count_of_errors",
        thresholdType: "static",
        metric_type: "count_of_errors",
        alert_frequency: 60,
        threshold_operator: "greater_than",
        notification_method: "slack",
        notification_email_count: 0,
      },
    });
    expect(JSON.stringify(mocks.mixpanel.track.mock.calls)).not.toContain(MARK);
    expect(JSON.stringify(mocks.mixpanel.track.mock.calls)).not.toContain(
      "Synthetic",
    );
  });

  it("reports the webhook and email channels without their addresses", () => {
    expect(
      alertEventFields(
        {
          ...alertPayload,
          slack_webhook_url: "",
          slack_notes: "",
          webhook_url: `https://hooks.example.test/${MARK}`,
        },
        "count_of_errors",
      ),
    ).toEqual(expect.objectContaining({ notification_method: "webhook" }));

    const emailFields = alertEventFields(
      {
        ...alertPayload,
        slack_webhook_url: "",
        slack_notes: "",
        notification_emails: ["a@example.test", "b@example.test"],
      },
      "count_of_errors",
    );
    expect(emailFields).toEqual(
      expect.objectContaining({
        notification_method: "email",
        notification_email_count: 2,
      }),
    );
    expect(JSON.stringify(emailFields)).not.toContain("example.test");
  });
});
