// Ruling 383: plan copy never lists a capability that is not built. The
// backend's plan entitlements still carry has_scim and has_project_rbac, and
// the upgrade dialog turns every true flag into a feature line.
import { describe, expect, it } from "vitest";

import { formatFeatures } from "../planFormatters";

describe("formatFeatures", () => {
  it("drops entitlements whose capability is not built (SCIM, project RBAC)", () => {
    const lines = formatFeatures({
      has_scim: true,
      hasScim: true,
      has_project_rbac: true,
      hasProjectRbac: true,
      has_optimization: true,
      monitors: -1,
    });
    expect(lines).toEqual(["Optimization", "Monitors: Unlimited"]);
    expect(lines.join(" ")).not.toMatch(/scim|rbac/i);
  });

  it("keeps every other true or positive entitlement", () => {
    expect(
      formatFeatures({
        has_custom_roles: true,
        has_audit_logs: false,
        queues: 5,
      }),
    ).toEqual(["Custom Roles", "Queues: 5"]);
  });
});
