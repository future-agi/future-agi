import { expect } from "vitest";
import { screen } from "src/utils/test-utils";
import { GATEWAY_ADMIN_ONLY } from "../hooks/useGatewayAdmin";

// Test helpers for the role check in the console:
// every gateway config write control reads useGatewayAdmin, and for a Member
// or a Viewer shows disabled inside a span whose tooltip (and so aria-label)
// says GATEWAY_ADMIN_ONLY. A test mocks src/contexts/OrganizationContext with
// the org level under test.

export const NOT_ADMIN_LEVELS = [
  ["a Member", 3],
  ["a Viewer", 1],
  ["a user whose level is not loaded", null],
];
export const ADMIN_LEVELS = [
  ["an Admin", 8],
  ["an Owner", 15],
];

// The control is disabled and says why.
export function expectAdminOnly(control) {
  expect(control).toBeDisabled();
  expect(
    control.closest(`[aria-label="${GATEWAY_ADMIN_ONLY}"]`),
  ).not.toBeNull();
}

// The control works and carries no "Owner or Admin only" label.
export function expectEditable(control) {
  expect(control).toBeEnabled();
  expect(control.closest(`[aria-label="${GATEWAY_ADMIN_ONLY}"]`)).toBeNull();
}

export function adminOnlyLabels() {
  return screen.queryAllByLabelText(GATEWAY_ADMIN_ONLY);
}
