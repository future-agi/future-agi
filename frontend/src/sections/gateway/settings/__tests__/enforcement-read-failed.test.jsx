import React from "react";
import { describe, expect, it } from "vitest";
import { render, screen } from "src/utils/test-utils";

import { enforcementSentence } from "../sectionEnforcement";
import { orgConfigSections } from "../orgConfigSections";
import SectionEnforcementNotes from "../ui/SectionEnforcementNotes";

const READ_FAILED =
  "Tool Policy: enforcement unknown. The gateway's settings report couldn't be read.";
const NOT_REPORTED =
  "Tool Policy: enforcement unknown. This gateway does not report which organization settings it enforces.";

// "Enforcement unknown" has two causes. A gateway that answered without the
// org_sections_enforced list does not report it; a read that failed says
// nothing about the gateway, so its sentence claims nothing about it either.
describe("enforcement unknown: a failed read is not a gateway that does not report", () => {
  it("names a failed read without a claim about the gateway", () => {
    expect(
      enforcementSentence("tool_policy", "unknown", null, "read_failed"),
    ).toBe(READ_FAILED);
    expect(enforcementSentence("tool_policy", "unknown")).toBe(NOT_REPORTED);
  });

  it("says the read failed on the Security notes when the config read errors", () => {
    render(
      <SectionEnforcementNotes
        sections={["ip_acl", "tool_policy"]}
        gatewayConfigQuery={{ isLoading: false, isError: true }}
      />,
    );
    expect(screen.getByText(READ_FAILED)).toBeInTheDocument();
    expect(
      screen.getByText(
        "IP ACL: enforcement unknown. The gateway's settings report couldn't be read.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/does not report which organization settings/),
    ).toBeNull();
  });

  it("keeps the not-reported sentence for a loaded config with no list", () => {
    render(
      <SectionEnforcementNotes
        sections={["tool_policy"]}
        gatewayConfigQuery={{ isLoading: false, isError: false, data: {} }}
      />,
    );
    expect(screen.getByText(NOT_REPORTED)).toBeInTheDocument();
  });

  it("keeps a cached list after a failed refresh: no unknown note", () => {
    const { container } = render(
      <SectionEnforcementNotes
        sections={["tool_policy"]}
        gatewayConfigQuery={{
          isLoading: false,
          isError: true,
          data: { org_sections_enforced: ["tool_policy"] },
        }}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("gives the Org config sections row the read-failed sentence too", () => {
    const rows = orgConfigSections(
      { tool_policy: { enabled: true } },
      { isLoading: false, isError: true },
    );
    const row = rows.find((r) => r.id === "tool_policy");
    expect(row.gap).toEqual({ label: "Enforcement unknown", tone: "neutral" });
    expect(JSON.stringify(row)).toContain(READ_FAILED);
    expect(JSON.stringify(row)).not.toContain("does not report");
  });

  // Django answers 200 without the gateway's blocks when its own read of the
  // gateway's config failed; gateway.config_read says so.
  it("says the read failed when Django could not read the gateway config", () => {
    render(
      <SectionEnforcementNotes
        sections={["tool_policy"]}
        gatewayConfigQuery={{
          isLoading: false,
          isError: false,
          data: { gateway: { status: "healthy", config_read: "failed" } },
        }}
      />,
    );
    expect(screen.getByText(READ_FAILED)).toBeInTheDocument();
  });

  it("keeps not-reported when Django read the gateway config and it had no list", () => {
    render(
      <SectionEnforcementNotes
        sections={["tool_policy"]}
        gatewayConfigQuery={{
          isLoading: false,
          isError: false,
          data: { gateway: { status: "healthy", config_read: "ok" } },
        }}
      />,
    );
    expect(screen.getByText(NOT_REPORTED)).toBeInTheDocument();
  });
});
