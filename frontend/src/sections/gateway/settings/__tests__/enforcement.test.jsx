import React from "react";
import { describe, expect, it } from "vitest";
import { render, screen } from "src/utils/test-utils";

import {
  ENFORCEMENT,
  NOT_ENFORCED_LABEL,
  enforcementFor,
  hasEnforcement,
} from "../enforcement";
import {
  enforcementSentence,
  notEnforcedReason,
  sectionEnforcement,
} from "../sectionEnforcement";
import EnforcementNote from "../ui/EnforcementNote";
import RecordedOnly from "../ui/RecordedOnly";

describe("the enforcement registry", () => {
  it("gives every entry the not-enforced kind and a reason", () => {
    const entries = Object.entries(ENFORCEMENT);
    expect(entries.length).toBeGreaterThan(5);
    for (const [id, entry] of entries) {
      expect(entry.kind, id).toBe("not-enforced");
      expect(entry.reason.length, id).toBeGreaterThan(30);
      expect(entry.reason, id).not.toMatch(
        /coming soon|in this build|preview/i,
      );
    }
  });

  it("uses one label for a setting this gateway stores but does not apply", () => {
    expect(NOT_ENFORCED_LABEL).toBe("Not enforced on this gateway");
  });

  it("knows the settings this gateway keeps and never reads", () => {
    for (const id of [
      "guardrails.failOpen",
      "guardrails.pipelineMode",
      "routing.defaultModel",
      "routing.conditionalRoutes",
      "routing.unsupportedKeys",
      "routing.fastest",
      "cache.backends",
      "mcp.guardrails",
      "customProperties.rules",
    ]) {
      expect(hasEnforcement(id), id).toBe(true);
    }
  });

  // The gateway applies these: email alerts are sent, budget.exceeded and
  // batch.completed are produced, webhook retries run, a Throttle budget
  // throttles at its rate, the alert threshold is pushed, the org's model
  // database, model map, rate limits, strategy, retry, failover, breaker and
  // alert severity apply. No note may say otherwise.
  it("has no entry for a setting the gateway applies", () => {
    for (const id of [
      "emailAlerts",
      "webhooks.budgetExceeded",
      "webhooks.batchCompleted",
      "webhooks.retry",
      "budgets.throttle",
      "budgets.alertThreshold",
      "budgets.hardLimit",
      "budgets.hardLimitDropped",
      "budgets",
      "cache",
      "alerting",
      "ipAcl",
      "toolPolicy",
      "rateLimiting",
      "rateLimiting.orgWide",
      "modelDatabase",
      "modelMap",
      "routing.byokReliability",
      "routing.weights",
      "alerting.severity",
      "audit",
      "a2a",
    ]) {
      expect(hasEnforcement(id), id).toBe(false);
    }
  });

  it("throws on an unknown id when one is read", () => {
    expect(() => enforcementFor("routing.nope")).toThrow(/Unknown/);
  });
});

describe("EnforcementNote", () => {
  it("shows the neutral chip and the reason for a stored-only setting", () => {
    render(<EnforcementNote id="cache.backends" />);
    const note = screen.getByTestId("enforcement-cache.backends");
    expect(note).toHaveTextContent(NOT_ENFORCED_LABEL);
    expect(note).toHaveTextContent(/Only on\/off and the TTL apply/);
  });

  it("renders nothing for a setting the gateway applies", () => {
    const { container } = render(
      <EnforcementNote ids={["budgets.throttle", "emailAlerts"]} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("lists several notes at once, leaving out what applies", () => {
    render(
      <EnforcementNote
        ids={["routing.defaultModel", "webhooks.retry", "routing.fastest"]}
      />,
    );
    expect(screen.getAllByText(NOT_ENFORCED_LABEL)).toHaveLength(2);
  });
});

describe("RecordedOnly", () => {
  const rows = [{ label: "Hard limit budget", value: "$9.00" }];

  it("omits the description paragraph when description is null", () => {
    render(
      <RecordedOnly
        ids={["mcp.guardrails"]}
        rows={rows}
        description={null}
        testId="no-description"
      />,
    );
    const block = screen.getByTestId("no-description");
    expect(
      block.querySelector("h3").parentElement.querySelector("p"),
    ).toBeNull();
    expect(block).toHaveTextContent("$9.00");
  });

  it("uses its nonempty default for an undefined description", () => {
    render(
      <RecordedOnly
        ids={["mcp.guardrails"]}
        rows={rows}
        description={undefined}
        testId="default-description"
      />,
    );
    expect(
      screen
        .getByTestId("default-description")
        .querySelector("h3")
        .parentElement.querySelector("p"),
    ).toHaveTextContent(
      "Stored with this configuration and not read by the gateway.",
    );
  });

  it("renders nothing when every id is a setting the gateway applies", () => {
    const { container } = render(
      <RecordedOnly ids={["budgets.hardLimitDropped"]} rows={rows} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the stored rows read-only with the note when the gateway never reads them", () => {
    render(<RecordedOnly ids={["routing.fastest"]} rows={rows} testId="x" />);
    const block = screen.getByTestId("x");
    expect(block).toHaveAttribute("data-read-only");
    expect(block).toHaveTextContent(NOT_ENFORCED_LABEL);
    expect(block).toHaveTextContent("$9.00");
  });
});

// The gateway's own report (GET /-/config org_sections_enforced and
// org_sections_not_enforced, passed through by the config read).
describe("sectionEnforcement", () => {
  const query = (data, isLoading = false) => ({ data, isLoading });

  it("is pending while the config read is", () => {
    expect(sectionEnforcement(query(undefined, true), "cache")).toBeNull();
  });

  it("is unknown when the gateway does not report the list, or the read failed", () => {
    expect(sectionEnforcement(query({}), "cache")).toBe("unknown");
    expect(sectionEnforcement(query(undefined), "cache")).toBe("unknown");
  });

  it("reads enforced and not enforced from the gateway's list", () => {
    const q = query({ org_sections_enforced: ["budgets"] });
    expect(sectionEnforcement(q, "budgets")).toBe("enforced");
    expect(sectionEnforcement(q, "cache")).toBe("not_enforced");
  });

  it("takes the gateway's reason, and nothing that is not a sentence", () => {
    const q = query({
      org_sections_not_enforced: { ip_acl: "client IP unknown", cache: 3 },
    });
    expect(notEnforcedReason(q, "ip_acl")).toBe("client IP unknown");
    expect(notEnforcedReason(q, "cache")).toBeNull();
  });

  it("states each case in one sentence", () => {
    expect(enforcementSentence("budgets", "unknown")).toBe(
      "Budgets: enforcement unknown. This gateway does not report which organization settings it enforces.",
    );
    expect(
      enforcementSentence("ip_acl", "not_enforced", "client IP unknown"),
    ).toBe("IP ACL: Not enforced on this gateway: client IP unknown.");
    expect(enforcementSentence("cache", "not_enforced")).toMatch(
      /Response caching is off on this gateway/,
    );
    expect(enforcementSentence("tool_policy", "not_enforced")).toBe(
      "Tool Policy: Not enforced on this gateway. This gateway does not run the Tool Policy plugin, so these settings are saved but not applied to requests.",
    );
  });
});
