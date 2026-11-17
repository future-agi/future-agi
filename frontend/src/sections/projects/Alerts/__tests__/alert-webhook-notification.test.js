import { describe, expect, it } from "vitest";

import {
  AlertConfigValidationSchema,
  getDefaultAlertConfigValues,
} from "../components/validation";

const SAVED = {
  metric_type: "count_of_errors",
  threshold_type: "static",
  threshold_operator: "greater_than",
  critical_threshold_value: 20,
  warning_threshold_value: 10,
  alert_frequency: 5,
};

const notificationIssues = (values) => {
  const result = AlertConfigValidationSchema.safeParse(values);
  if (result.success) return [];
  return result.error.issues
    .filter((issue) => issue.path[0] === "notification")
    .map((issue) => `${issue.path.join(".")}: ${issue.message}`);
};

describe("an alert that notifies a webhook", () => {
  it("opens with the webhook method and its saved URL", () => {
    const values = getDefaultAlertConfigValues({
      ...SAVED,
      webhook_url: "https://hooks.example.test/alerts",
    });

    expect(values.notification.method).toBe("webhook");
    expect(values.notification.webhook.url).toBe(
      "https://hooks.example.test/alerts",
    );
    expect(notificationIssues(values)).toEqual([]);
  });

  it("keeps Slack and email for alerts saved without a webhook", () => {
    expect(
      getDefaultAlertConfigValues({
        ...SAVED,
        slack_webhook_url: "https://hooks.slack.test/x",
      }).notification.method,
    ).toBe("slack");
    expect(
      getDefaultAlertConfigValues({
        ...SAVED,
        notification_emails: ["alerts@example.test"],
      }).notification.method,
    ).toBe("email");
  });

  it("asks for a webhook URL, and for a valid one", () => {
    const values = getDefaultAlertConfigValues({
      ...SAVED,
      webhook_url: "https://hooks.example.test/alerts",
    });

    expect(
      notificationIssues({
        ...values,
        notification: { ...values.notification, webhook: { url: "" } },
      }),
    ).toEqual(["notification.webhook.url: Webhook URL is required"]);
    expect(
      notificationIssues({
        ...values,
        notification: {
          ...values.notification,
          webhook: { url: "not a url" },
        },
      }),
    ).toEqual(["notification.webhook.url: Invalid webhook URL"]);
  });

  it("keeps the threshold defaults that follow the alert's direction", () => {
    const below = getDefaultAlertConfigValues(
      {
        ...SAVED,
        threshold_operator: "less_than",
        webhook_url: "https://hooks.example.test/alerts",
      },
      { keepSavedWords: false },
    );

    expect(below.notification.method).toBe("webhook");
    expect(below.threshold_operator).toBe("less_than");
  });
});
