import React from "react";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { describe, expect, it } from "vitest";

import { CAPABILITIES_QUERY_KEY } from "src/hooks/useCapabilities";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";

import LicensePage from "../LicensePage";

// The License page lists what the license includes. The capabilities answer
// also carries service states (for example whether custom code evaluations
// run on this deployment), which no license grants.
// Each says so with kind "service", and the page leaves those out by that
// kind, whatever their name, on or off. Every other entry stays listed.

const feature = (display_name, allowed, extra = {}) => ({
  display_name,
  allowed,
  reason_code: allowed ? null : "LICENSE_FEATURE_MISSING",
  requires_network: null,
  oss_baseline: false,
  ...extra,
});
const serviceState = (display_name, allowed) =>
  feature(display_name, allowed, {
    reason_code: allowed ? null : "SERVICE_UNAVAILABLE",
    requires_network: false,
    oss_baseline: true,
    kind: "service",
  });

function renderWith(features) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  client.setQueryData(CAPABILITIES_QUERY_KEY, {
    data: {
      deployment_flavor: "self_hosted_ee_image",
      display_mode: "enterprise",
      license_state: "active",
      license: {
        issued_to: "Example Org",
        band: "enterprise",
        license_type: "standard",
        expires_at: null,
        grace_ends_at: null,
        state: "active",
      },
      instance_id: "00000000-0000-0000-0000-000000000000",
      features,
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

describe("License page and service states", () => {
  it("lists every licensed entry and no service state", () => {
    renderWith({
      error_feed: feature("Error Feed", true),
      protect: feature("Protect", false),
      dataset_stars: feature("Star datasets", false, {
        reason_code: "SERVICE_UNAVAILABLE",
        oss_baseline: true,
      }),
      experiment_run_control: serviceState("Manage runs", false),
      custom_code_evals: serviceState("Custom code evaluations", true),
    });
    for (const listed of ["Error Feed", "Protect", "Star datasets"]) {
      expect(screen.getByText(listed)).toBeInTheDocument();
    }
    for (const left of ["Manage runs", "Custom code evaluations"]) {
      expect(screen.queryByText(left)).toBeNull();
    }
    // The count follows the list when the license gives none.
    expect(screen.getByText("3")).toBeInTheDocument();
  });

  it("leaves out a service state by its kind, whatever its name", () => {
    renderWith({
      error_feed: feature("Error Feed", true),
      maintenance_window: serviceState("Maintenance window", true),
    });
    expect(screen.getByText("Error Feed")).toBeInTheDocument();
    expect(screen.queryByText("Maintenance window")).toBeNull();
    expect(screen.getByText("1")).toBeInTheDocument();
  });

  it("still never names an Omega product or part", () => {
    renderWith({
      error_feed: feature("Error Feed", true),
      omega_hub: feature("Omega Hub", false, {
        reason_code: "PRODUCT_NOT_ADMITTED",
      }),
      experiment_run_control: serviceState("Manage runs", true),
    });
    expect(screen.getByText("Error Feed")).toBeInTheDocument();
    expect(screen.queryByText("Omega Hub")).toBeNull();
    expect(screen.queryByText("Manage runs")).toBeNull();
    expect(screen.getByText("1")).toBeInTheDocument();
  });
});
