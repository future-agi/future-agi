import React from "react";
import { describe, expect, it } from "vitest";
import { render, within } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { MemoryRouter } from "react-router-dom";
import { palette } from "src/theme/palette";

import OSSUpgradeGate from "../oss-upgrade-gate";

// The Chat gate keeps the gate's name-led form (a title built from the
// feature's name, no guided steps) and says Chat, never Falcon AI.

const theme = createTheme({ palette: palette("light") });

function renderGate(reasonCode) {
  return render(
    <ThemeProvider theme={theme}>
      <MemoryRouter>
        <OSSUpgradeGate feature="falconAI" reasonCode={reasonCode} />
      </MemoryRouter>
    </ThemeProvider>,
  );
}

describe("the Chat gate", () => {
  it.each([undefined, "LICENSE_MISSING", "EE_CODE_UNAVAILABLE"])(
    "is titled from its name, Chat, and never says Falcon AI (%s)",
    (reasonCode) => {
      const { container } = renderGate(reasonCode);
      expect(within(container).getByRole("heading")).toHaveTextContent(
        "Chat is not available on this deployment",
      );
      expect(container.textContent).toMatch(/Chat is an assistant/);
      expect(container.textContent).not.toMatch(/Falcon AI/);
    },
  );
});
