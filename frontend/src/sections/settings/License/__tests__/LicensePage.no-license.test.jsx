import React from "react";
import PropTypes from "prop-types";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { describe, expect, it, vi } from "vitest";

import { CAPABILITIES_QUERY_KEY } from "src/hooks/useCapabilities";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";

vi.mock("src/components/iconify", () => {
  const Icon = ({ icon }) => <span data-testid="icon" data-icon={icon} />;
  Icon.propTypes = { icon: PropTypes.string };
  return { default: Icon };
});

import LicensePage from "../LicensePage";

// W10-DESIGN-SYSTEM-U02: an Owner or Admin of a self-hosted install with no
// license opens Settings > License to turn on Enterprise features. The page
// must say how a key goes in and where to get one, not stop at "No license
// yet". The steps follow the page's one sentence that there is no license
// (LicensePage.no-license-header.test.jsx), so they do not say it again.

const CONTACT_URL = "https://futureagi.com/talk-to-human";
const CONFIG_REFERENCE_URL =
  "https://docs.futureagi.com/docs/self-hosting/configuration/reference";

function renderWithoutLicense({ deployment_flavor, license_state }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  client.setQueryData(CAPABILITIES_QUERY_KEY, {
    data: {
      deployment_flavor,
      display_mode: "community",
      license_state,
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

describe("License page without a license", () => {
  it("tells a self-hosted admin how to add a key and where to get one", () => {
    renderWithoutLicense({
      deployment_flavor: "self_hosted_ee_image",
      license_state: "missing",
    });
    expect(screen.getByText("No license")).toBeInTheDocument();
    expect(
      screen.getByText(
        "To add one, set EE_LICENSE_KEY on this deployment to the key we sent you, then restart it.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Talk to us about a license" }),
    ).toHaveAttribute("href", CONTACT_URL);
    expect(
      screen.getByRole("link", { name: "Configuration reference" }),
    ).toHaveAttribute("href", CONFIG_REFERENCE_URL);
  });

  it("tells an open-source image that a key alone is not enough", () => {
    renderWithoutLicense({
      deployment_flavor: "oss_image",
      license_state: "missing",
    });
    expect(screen.getByText("No license")).toBeInTheDocument();
    expect(screen.getByText(/Enterprise Edition image/)).toBeInTheDocument();
    expect(
      screen.getByText(/a license key alone does not turn on/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Talk to us about a license" }),
    ).toHaveAttribute("href", CONTACT_URL);
  });

  it("gives Cloud no self-hosting steps", () => {
    renderWithoutLicense({
      deployment_flavor: "cloud_image",
      license_state: "not_applicable",
    });
    expect(screen.getByText("No license")).toBeInTheDocument();
    expect(screen.queryByText(/EE_LICENSE_KEY/)).toBeNull();
    expect(
      screen.queryByRole("link", { name: "Talk to us about a license" }),
    ).toBeNull();
  });
});
