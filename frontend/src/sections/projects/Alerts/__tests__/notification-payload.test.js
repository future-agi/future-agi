import { describe, expect, it } from "vitest";
import { notificationPayload } from "../common";

const email = (emails) => ({ method: "email", emails });

describe("notificationPayload", () => {
  it("leaves out unchanged recipients on an edit, so an unsubscribe sticks", () => {
    const loaded = email(["oncall@example.com", "lead@example.com"]);
    const payload = notificationPayload(
      email(["Lead@example.com", "oncall@example.com"]),
      loaded,
      true,
    );

    expect(payload).not.toHaveProperty("notification_emails");
    expect(payload.webhook_url).toBe("");
  });

  it("sends recipients the editor changed", () => {
    const payload = notificationPayload(
      email(["oncall@example.com", "new@example.com"]),
      email(["oncall@example.com"]),
      true,
    );

    expect(payload.notification_emails).toEqual([
      "oncall@example.com",
      "new@example.com",
    ]);
  });

  it("always sends recipients when creating an alert", () => {
    const payload = notificationPayload(
      email(["oncall@example.com"]),
      email(["oncall@example.com"]),
      false,
    );

    expect(payload.notification_emails).toEqual(["oncall@example.com"]);
  });

  it("clears recipients when the alert moves to Slack", () => {
    const payload = notificationPayload(
      { method: "slack", slack: { webhookUrl: "https://hooks.example" } },
      email(["oncall@example.com"]),
      true,
    );

    expect(payload.notification_emails).toEqual([]);
    expect(payload.slack_webhook_url).toBe("https://hooks.example");
  });
});
