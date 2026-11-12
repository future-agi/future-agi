import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { createTheme, ThemeProvider } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import StatusBadge, { StatusDot } from "./StatusBadge";
import StatusChip from "../custom-status-chip/CustomStatusChip";

describe.each(["light", "dark"])("status components in %s", (mode) => {
  const wrap = (node) =>
    render(
      <ThemeProvider theme={createTheme({ palette: palette(mode) })}>
        {node}
      </ThemeProvider>,
    );
  it("labels unknown states and supports disabled dot variants", () => {
    wrap(
      <>
        <StatusBadge status={null} />
        <StatusDot status="running" disabled data-testid="dot" />
      </>,
    );
    expect(screen.getByText("Unknown")).toBeTruthy();
    expect(screen.getByText("Running")).toBeTruthy();
    expect(screen.getByTestId("dot").getAttribute("aria-disabled")).toBe(
      "true",
    );
    expect(screen.getByTestId("dot").hasAttribute("disabled")).toBe(false);
  });
  it("consumes legacy chip props instead of leaking them to HTML", () => {
    wrap(
      <StatusChip
        status={null}
        label="Not measured"
        showIcon={false}
        neutralTextColor="text.secondary"
        data-testid="legacy"
      />,
    );
    const chip = screen.getByTestId("legacy");
    expect(chip.textContent).toBe("Not measured");
    expect(chip.hasAttribute("showIcon")).toBe(false);
    expect(chip.hasAttribute("neutralTextColor")).toBe(false);
    expect(chip.querySelector(".MuiChip-icon")).toBeNull();
  });
});
