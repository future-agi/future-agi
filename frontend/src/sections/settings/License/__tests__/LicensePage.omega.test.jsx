import React from "react";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { describe, expect, it } from "vitest";

import { CAPABILITIES_QUERY_KEY } from "src/hooks/useCapabilities";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";

import LicensePage from "../LicensePage";

// HIDE-UNFINISHED-FA: the License page lists what the license includes. The
// hosted Omega products and their parts (`omega_*`) are opened by rollout and
// readiness, never by a license, so a dark product or an unreleased part
// ("Deploy from the CLI") must not be named there.

const feature = (display_name, allowed) => ({
  display_name,
  allowed,
  reason_code: allowed ? null : "PRODUCT_NOT_ADMITTED",
  requires_network: null,
  oss_baseline: false,
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

describe("License page", () => {
  it("lists licensed features and never an Omega product or part", () => {
    renderWith({
      error_feed: feature("Error Feed", true),
      protect: feature("Protect", false),
      omega_hub: feature("Omega Hub", false),
      omega_studio_tab: feature("Studio", true),
      omega_part_deploy_cli: feature("Deploy from the CLI", false),
      omega_part_hub_cli: feature("Omega Hub from the CLI", false),
    });
    expect(screen.getByText("Error Feed")).toBeInTheDocument();
    expect(screen.getByText("Protect")).toBeInTheDocument();
    for (const hidden of [
      "Omega Hub",
      "Studio",
      "Deploy from the CLI",
      "Omega Hub from the CLI",
    ]) {
      expect(screen.queryByText(hidden)).toBeNull();
    }
    // The count follows the list when the license gives none.
    expect(screen.getByText("2")).toBeInTheDocument();
  });
});
