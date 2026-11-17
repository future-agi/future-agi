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

// Staging walk, head-72: Settings > License with no license had no page
// header, unlike the licensed page ("License", "Enterprise license details
// for this deployment."). The no-license state keeps the same header, then
// one plain sentence: there is no license here, and what a license adds.
// Review r1: on a self-hosted Enterprise image the text said so three times in
// a row; the sentence that only repeated it is gone, the steps and the
// actions stay.

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

function expectLicenseHeader() {
  expect(
    screen.getByRole("heading", { level: 1, name: "License" }),
  ).toBeInTheDocument();
  expect(
    screen.getByText("Enterprise license details for this deployment."),
  ).toBeInTheDocument();
}

describe("License page header without a license", () => {
  it("a self-hosted Enterprise image: header, what a license adds, the existing action", () => {
    renderWithoutLicense({
      deployment_flavor: "self_hosted_ee_image",
      license_state: "missing",
    });
    expectLicenseHeader();
    const adds = screen.getByText(
      "There is no license on this deployment; a license turns on its Enterprise features.",
    );
    expect(adds).toBeInTheDocument();
    expect(
      screen.getByText(
        "To add one, set EE_LICENSE_KEY on this deployment to the key we sent you, then restart it.",
      ),
    ).toBeInTheDocument();
    // One sentence says there is no license; none repeats it.
    expect(screen.queryByText(/runs without an Enterprise license/)).toBeNull();
    const description = adds.parentElement.textContent;
    expect(
      description.match(/no license|without an Enterprise license/gi),
    ).toHaveLength(1);
    expect(
      screen.getByRole("link", { name: "Talk to us about a license" }),
    ).toHaveAttribute("href", CONTACT_URL);
    expect(
      screen.getByRole("link", { name: "Configuration reference" }),
    ).toHaveAttribute("href", CONFIG_REFERENCE_URL);
  });

  it("an open-source image: header and what a license adds with the Enterprise Edition", () => {
    renderWithoutLicense({
      deployment_flavor: "oss_image",
      license_state: "missing",
    });
    expectLicenseHeader();
    expect(
      screen.getByText(
        "There is no license on this deployment; with the Enterprise Edition, a license turns on its Enterprise features.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Talk to us about a license" }),
    ).toHaveAttribute("href", CONTACT_URL);
  });

  it("any other deployment: the same header over its no-license sentence", () => {
    renderWithoutLicense({
      deployment_flavor: "cloud_image",
      license_state: "not_applicable",
    });
    expectLicenseHeader();
    expect(
      screen.getByText("No active license is configured on this instance."),
    ).toBeInTheDocument();
  });
});
