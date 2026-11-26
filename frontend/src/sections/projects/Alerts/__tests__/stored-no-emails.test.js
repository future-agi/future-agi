import { describe, expect, it } from "vitest";
import { allowStoredNoEmails } from "../common";
import {
  AlertConfigValidationSchema,
  getDefaultAlertConfigValues,
} from "../components/validation";

// An email alert whose last recipient unsubscribed from the emailed link.
const stored = {
  name: "Checkout errors",
  metric_type: "count_of_errors",
  notification_emails: [],
};

const errorsOf = (values) => {
  const result = AlertConfigValidationSchema.safeParse(values);
  return result.success
    ? []
    : result.error.issues.map((issue) => issue.message);
};

describe("an alert stored without recipients", () => {
  it("saves an unrelated edit", () => {
    const values = allowStoredNoEmails(
      getDefaultAlertConfigValues({
        ...stored,
        name: "Checkout errors (renamed)",
      }),
      true,
    );

    expect(errorsOf(values)).not.toContain("Emails are required");
  });

  it("still needs a destination when created or duplicated", () => {
    const values = allowStoredNoEmails(
      getDefaultAlertConfigValues(stored),
      false,
    );

    expect(errorsOf(values)).toContain("Emails are required");
  });

  it("is untouched when it still has recipients", () => {
    const values = getDefaultAlertConfigValues({
      ...stored,
      notification_emails: ["oncall@example.com"],
    });

    expect(allowStoredNoEmails(values, true)).toBe(values);
  });
});
