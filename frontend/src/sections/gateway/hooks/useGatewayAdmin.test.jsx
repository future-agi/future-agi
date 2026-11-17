import { describe, it, expect, vi } from "vitest";
import { renderHook } from "@testing-library/react";
import {
  GATEWAY_ADMIN_ONLY,
  isGatewayAdminLevel,
  useGatewayAdmin,
} from "./useGatewayAdmin";

let mockOrgLevel = null;
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: mockOrgLevel }),
}));

// The gateway admin role check (agentcc/permissions.py is_gateway_admin):
// Owner (15) and Admin (8) may change the gateway config; Member (3) and
// Viewer (1) may not.
describe("useGatewayAdmin", () => {
  it.each([
    [15, true],
    [8, true],
    [3, false],
    [1, false],
    [0, false],
    [null, false],
    [undefined, false],
    ["8", false],
    [Number.NaN, false],
  ])("org level %s can edit: %s", (level, canEdit) => {
    expect(isGatewayAdminLevel(level)).toBe(canEdit);
    mockOrgLevel = level;
    const { result } = renderHook(() => useGatewayAdmin());
    expect(result.current).toEqual({
      canEdit,
      disabledReason: canEdit ? null : GATEWAY_ADMIN_ONLY,
    });
  });

  it("says Owner or Admin only", () => {
    expect(GATEWAY_ADMIN_ONLY).toBe("Owner or Admin only");
  });
});
