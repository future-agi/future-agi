import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { matchRoutes, MemoryRouter, useLocation } from "react-router-dom";

import { dashboardRoutes } from "src/routes/sections/dashboard";
import ErrorCellRenderer from "../ErrorCellRenderer";

const state = vi.hoisted(() => ({ mode: "cloud" }));
vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({
    isCloud: state.mode === "cloud",
    isOSS: state.mode === "oss",
    isEE: state.mode === "ee",
    isLoading: false,
  }),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("../cellRendererHelper", () => ({ tooltipSlotProp: {} }));

// Independent expectations: do not import the implementation's copy constants.
// PROPOSED: denials include entitlements, licenses and limits, not just usage.
const SELF_HOST_TEXT =
  "This action could not be completed. Contact your administrator.";
const PROFILE = "/dashboard/settings/profile-settings";
const PRICING = "/dashboard/settings/pricing";
const HOSTED_TEXT = [
  ["FREE_TIER_LIMIT", "Upgrade for more usage"],
  ["RATE_LIMITED", "Upgrade to raise rate limit"],
  ["ENTITLEMENT_LIMIT", "Upgrade to increase limits"],
  ["ENTITLEMENT_DENIED", "Upgrade plan to unlock"],
  ["BUDGET_PAUSED", "Increase budget to resume"],
  ["PAYMENT_REQUIRED", "Add payment method to continue"],
  ["ACCOUNT_SUSPENDED", "Clear dues to reactivate"],
  ["LICENSE_EXPIRED", "Renew license to continue"],
  ["LICENSE_FEATURE_DENIED", "Contact sales to unlock"],
  ["USAGE_LIMIT_EXCEEDED", "Upgrade for more usage"],
];

function Location() {
  return <output data-testid="location">{useLocation().pathname}</output>;
}

function mount(errorCode, encode = (value) => value) {
  const onRerun = vi.fn();
  const onCellClick = vi.fn();
  render(
    <MemoryRouter initialEntries={["/cell"]}>
      <div onClick={onCellClick}>
        <ErrorCellRenderer
          valueReason="Stored failure"
          formattedValueReason={() => "Stored failure"}
          onRerun={onRerun}
          props={{
            column: { colId: "score" },
            data: {
              score: {
                value_infos: encode({
                  error_code: errorCode,
                  reason: "Stored failure",
                }),
              },
            },
          }}
        />
      </div>
      <Location />
    </MemoryRouter>,
  );
  return { onRerun, onCellClick };
}

function registeredRoute(destination, role = "Owner") {
  const routes = dashboardRoutes(
    { organization_role: role, ws_enabled: false },
    null,
    { isCloud: state.mode === "cloud" },
  );
  const matches = matchRoutes([...routes, { path: "*" }], destination);
  return matches?.at(-1)?.route;
}

beforeEach(() => {
  state.mode = "cloud";
  vi.stubGlobal("fetch", () => {
    throw new Error("Network disabled for deployment CTA tests");
  });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe.each(["oss", "ee"])("self-host usage CTA (%s)", (mode) => {
  it.each(HOSTED_TEXT)(
    "keeps %s visible and navigates to a registered settings page",
    (code) => {
      state.mode = mode;
      const { onRerun, onCellClick } = mount(code);
      expect(screen.getByText(SELF_HOST_TEXT)).toBeVisible();
      const action = screen.getByRole("button", { name: "Open settings" });
      expect(action).toBeVisible();
      expect(screen.queryByRole("button", { name: "Upgrade" })).toBeNull();
      fireEvent.click(action);
      const destination = screen.getByTestId("location").textContent;
      expect(destination).toBe(PROFILE);
      expect(registeredRoute(destination)?.path).toBe("profile-settings");
      expect(registeredRoute(PRICING)?.path).toBe("*");
      expect(onRerun).not.toHaveBeenCalled();
      expect(onCellClick).not.toHaveBeenCalled();
    },
  );

  it.each([
    "Owner",
    "Admin",
    "Member",
    "Viewer",
    "workspace_admin",
    "workspace_member",
    "workspace_viewer",
  ])("uses settings accessible to %s with JSON value_infos", (role) => {
    state.mode = mode;
    mount("FREE_TIER_LIMIT", JSON.stringify);
    fireEvent.click(screen.getByRole("button", { name: "Open settings" }));
    const destination = screen.getByTestId("location").textContent;
    expect(destination).toBe(PROFILE);
    const route = registeredRoute(destination, role);
    expect(route?.path).toBe("profile-settings");
    expect(route?.element.props.allowedRoles).toContain(role);
  });
});

describe("hosted usage CTA remains unchanged", () => {
  it.each(HOSTED_TEXT)("preserves %s text, label and target", (code, text) => {
    const { onRerun, onCellClick } = mount(code);
    expect(screen.getByText(text)).toBeVisible();
    const action = screen.getByRole("button", { name: "Upgrade" });
    expect(action).toBeVisible();
    expect(screen.queryByRole("button", { name: "Open settings" })).toBeNull();
    fireEvent.click(action);
    const destination = screen.getByTestId("location").textContent;
    expect(destination).toBe(PRICING);
    expect(registeredRoute(destination)?.path).toBe("pricing");
    expect(onRerun).not.toHaveBeenCalled();
    expect(onCellClick).not.toHaveBeenCalled();
  });
});

it.each(["cloud", "oss", "ee"])(
  "does not invent a CTA without a usage code on %s",
  (mode) => {
    state.mode = mode;
    mount("UNKNOWN_ERROR");
    expect(screen.getByText("Error")).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
  },
);
