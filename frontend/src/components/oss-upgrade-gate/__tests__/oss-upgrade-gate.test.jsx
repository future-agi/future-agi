import React from "react";
import PropTypes from "prop-types";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { palette } from "src/theme/palette";

vi.mock("src/components/iconify", () => {
  const Icon = ({ icon }) => <span data-testid="icon" data-icon={icon} />;
  Icon.propTypes = { icon: PropTypes.string };
  return { default: Icon };
});

import OSSUpgradeGate from "../oss-upgrade-gate";

function renderGate(feature, reasonCode) {
  return render(
    <ThemeProvider theme={createTheme({ palette: palette("light") })}>
      <MemoryRouter initialEntries={["/gate"]}>
        <Routes>
          <Route
            path="/gate"
            element={
              <OSSUpgradeGate feature={feature} reasonCode={reasonCode} />
            }
          />
          <Route
            path="/dashboard/settings/ee-licenses"
            element={<div>The License page</div>}
          />
        </Routes>
      </MemoryRouter>
    </ThemeProvider>,
  );
}

const steps = () =>
  screen
    .getByText("What to do next")
    .parentElement.querySelectorAll("p:not(.MuiTypography-overline)");
const stepTexts = () => Array.from(steps(), (step) => step.textContent);

describe("the Instant eval and Insights cards say what unlocks them", () => {
  it("asks a Community install to talk to us about a license", () => {
    renderGate("insights", "LICENSE_MISSING");
    expect(screen.getByText("Upgrade to use Insights")).toBeInTheDocument();
    expect(stepTexts()[0]).toBe(
      "Talk to us about an Enterprise license that includes Insights.",
    );
    expect(
      screen.getByRole("link", { name: "Upgrade to EE license key" }),
    ).toHaveAttribute("href", "https://futureagi.com/talk-to-human");
  });

  it("tells a license that lacks the feature to add it, not to buy one", () => {
    renderGate("insights", "LICENSE_FEATURE_MISSING");
    expect(
      screen.getByText("Add Insights to your license"),
    ).toBeInTheDocument();
    expect(stepTexts()).toEqual([
      "Ask us to add Insights to your Enterprise license.",
      "Set the new key as EE_LICENSE_KEY on this deployment and restart it.",
      "Choose what to learn (usage, failures, experience) and which data to read.",
    ]);
    expect(
      screen.getByText(
        "Your Enterprise license is active but doesn't include this feature.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Talk to us" })).toHaveAttribute(
      "href",
      "https://futureagi.com/talk-to-human",
    );
    expect(screen.queryByText(/Upgrade to EE license key/)).toBeNull();
    expect(screen.queryByText(/Talk to us about an Enterprise/)).toBeNull();
  });

  it.each([
    ["instantEval", "Instant eval", "Your past runs are kept"],
    ["insights", "Insights", "Your past reports are kept"],
  ])("asks an expired %s license to renew", (feature, name, kept) => {
    renderGate(feature, "LICENSE_EXPIRED");
    expect(
      screen.getByText(`Renew your license to use ${name}`),
    ).toBeInTheDocument();
    const [renew, key, keep] = stepTexts();
    expect(renew).toBe("Talk to us to renew your Enterprise license.");
    expect(key).toMatch(/EE_LICENSE_KEY/);
    expect(keep).toMatch(kept);
    expect(
      screen.getByText(
        "Your license has expired — renew it to restore access.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Talk to us" }),
    ).toBeInTheDocument();
  });

  it("asks for the key to be checked when it can't be validated", () => {
    renderGate("instantEval", "LICENSE_INVALID");
    expect(
      screen.getByText("Fix your license to use Instant eval"),
    ).toBeInTheDocument();
    expect(stepTexts()[0]).toMatch(/^Check that EE_LICENSE_KEY/);
    expect(stepTexts()[1]).toBe(
      "If it still can't be validated, talk to us for a new key.",
    );
    expect(screen.queryByText(/Talk to us about an Enterprise/)).toBeNull();
  });

  it("names the plan, not an Enterprise license, on Cloud", () => {
    renderGate("instantEval", "PLAN_FEATURE_MISSING");
    expect(
      screen.getByText("Upgrade your plan to use Instant eval"),
    ).toBeInTheDocument();
    expect(stepTexts()[0]).toBe(
      "Talk to us about a plan that includes Instant eval.",
    );
    expect(screen.queryByText(/Enterprise/)).toBeNull();
    expect(screen.queryByText(/EE_LICENSE_KEY/)).toBeNull();
    expect(
      screen.getByText("Your plan doesn't include this feature."),
    ).toBeInTheDocument();
  });
});

describe("the primary button's arrow", () => {
  it("points out of the app only when it opens another site", () => {
    renderGate("insights", "LICENSE_FEATURE_MISSING");
    const link = screen.getByRole("link", { name: "Talk to us" });
    expect(within(link).getByTestId("icon")).toHaveAttribute(
      "data-icon",
      "solar:arrow-right-up-linear",
    );
  });

  it("is a plain arrow for Manage license, which stays in the app", () => {
    renderGate("errorFeed", "LICENSE_EXPIRED");
    const button = screen.getByRole("button", { name: "Manage license" });
    expect(button).not.toHaveAttribute("target");
    expect(within(button).getByTestId("icon")).toHaveAttribute(
      "data-icon",
      "solar:arrow-right-linear",
    );
    fireEvent.click(button);
    expect(screen.getByText("The License page")).toBeInTheDocument();
  });
});
