import React from "react";
import { describe, expect, it } from "vitest";
import { render, within } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { MemoryRouter } from "react-router-dom";
import { palette } from "src/theme/palette";

import OSSUpgradeGate from "../oss-upgrade-gate";

// G2: Observe shows the Insights and Instant eval gates as the body of a tab
// (/observe/:id/insights and /observe/:id/ask). ObservePage pads its toolbar
// and tab bar 16 px each side but not the tab body, so the gate's grey
// bordered panel ran from the sidebar border (x=200) to the right edge of the
// window while the toolbar above kept 16 px gutters (x=216 to width-16), at
// 1024, 1280 and 1440. Those two gates now sit inside the same 16 px side
// gutters, with 16 px below; every other gate is laid out as before.
// The checks read the styles the gate sets, never jsdom layout.

const theme = createTheme({ palette: palette("light") });
// ObservePage pads its toolbar and tab bar with theme.spacing(2).
const GUTTER = theme.spacing(2);

function renderGate(feature, reasonCode) {
  return render(
    <ThemeProvider theme={theme}>
      <MemoryRouter>
        <OSSUpgradeGate feature={feature} reasonCode={reasonCode} />
      </MemoryRouter>
    </ThemeProvider>,
  );
}

describe("the Insights and Instant eval gates keep the Observe toolbar's gutters (G2)", () => {
  it.each([
    ["insights", undefined],
    ["insights", "LICENSE_MISSING"],
    ["insights", "EE_CODE_UNAVAILABLE"],
    ["instantEval", undefined],
    ["instantEval", "LICENSE_MISSING"],
    ["instantEval", "EE_CODE_UNAVAILABLE"],
  ])(
    "insets the %s panel 16 px from each side and the bottom (%s)",
    (feature, reasonCode) => {
      const { container } = renderGate(feature, reasonCode);
      const gutter = container.firstElementChild;
      expect(gutter).toHaveStyle({
        paddingLeft: GUTTER,
        paddingRight: GUTTER,
        paddingBottom: GUTTER,
      });
      // The bordered panel (the overlay root, which clips its own rounded
      // corners) sits inside the gutter instead of being the gate's root.
      const panel = gutter.firstElementChild;
      expect(panel).toHaveStyle({ overflow: "hidden" });
      // The whole card is still inside the panel.
      expect(within(panel).getByRole("heading")).toBeInTheDocument();
      expect(within(panel).getByTestId("oss-gate-needs")).toBeInTheDocument();
    },
  );
});

describe("every other gate is laid out as before (G2)", () => {
  it.each([
    ["errorFeed", "LICENSE_MISSING"],
    ["errorFeed", "EE_CODE_UNAVAILABLE"],
    ["falconAI", "LICENSE_MISSING"],
    ["falconAI", "EE_CODE_UNAVAILABLE"],
  ])("keeps the %s panel as the gate's root (%s)", (feature, reasonCode) => {
    const { container } = renderGate(feature, reasonCode);
    const panel = container.firstElementChild;
    expect(panel).toHaveStyle({ overflow: "hidden" });
    expect(within(panel).getByRole("heading")).toBeInTheDocument();
    expect(within(panel).getByTestId("oss-gate-needs")).toBeInTheDocument();
  });
});
