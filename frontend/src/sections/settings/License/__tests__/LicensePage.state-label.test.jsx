import React from "react";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { describe, expect, it } from "vitest";

import { CAPABILITIES_QUERY_KEY } from "src/hooks/useCapabilities";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";

import LicensePage from "../LicensePage";

// FA-SET-LICENSE-02: the license state chip names the state in words. The
// server sends an enum value (tfc/licensing/types.py LicenseState), and the
// page must not print it as is ("trial_active", "grace").

function renderWithState(license_state) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  client.setQueryData(CAPABILITIES_QUERY_KEY, {
    data: {
      deployment_flavor: "self_hosted_ee_image",
      display_mode: "enterprise",
      license_state,
      license: {
        issued_to: "Example Org",
        band: "enterprise",
        license_type: "production",
        expires_at: null,
        grace_ends_at: null,
        state: license_state,
      },
      instance_id: "00000000-0000-0000-0000-000000000000",
      features: {},
    },
  });
  return render(
    <QueryClientProvider client={client}>
      <ThemeProvider
        theme={createTheme({ palette: palette("light"), typography })}
      >
        <LicensePage />
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

describe("License state chip", () => {
  it.each([
    ["active", "Active"],
    ["grace", "Grace period"],
    ["expired", "Expired"],
    ["invalid", "Invalid"],
    ["trial_active", "Trial"],
    ["trial_expired", "Trial expired"],
  ])("shows %s as %s", (state, label) => {
    renderWithState(state);
    expect(screen.getByText(label, { exact: true })).toBeInTheDocument();
    expect(screen.queryByText(state, { exact: true })).toBeNull();
  });

  it("shows no chip for a state it does not know", () => {
    renderWithState("some_new_state");
    expect(
      screen.getByRole("heading", { level: 1, name: "License" }),
    ).toBeInTheDocument();
    expect(screen.queryByText(/some_new_state/)).toBeNull();
  });
});
