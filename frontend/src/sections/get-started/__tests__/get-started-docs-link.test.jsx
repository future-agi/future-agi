// Get started: the header's "View docs" link was so pale it
// looked disabled (primary.light). It uses the link colour token, readable in
// the light and the dark theme.
import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import GetStartedHeaderView from "../GetStartedHeaderView";

vi.mock("src/utils/Mixpanel", () => ({ handleOnDocsClicked: vi.fn() }));

describe("Get started header: View docs link", () => {
  it.each(["light", "dark"])(
    "uses the link colour token in the %s theme",
    (mode) => {
      const theme = createTheme({ palette: palette(mode) });
      render(
        <ThemeProvider theme={theme}>
          <GetStartedHeaderView />
        </ThemeProvider>,
      );

      const link = screen.getByRole("link", { name: "View docs" });
      expect(link).toHaveStyle({ color: theme.palette.text.link });
      expect(link).not.toHaveStyle({ color: theme.palette.primary.light });
    },
  );
});
