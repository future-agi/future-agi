import React from "react";
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { createTheme, ThemeProvider } from "@mui/material/styles";
import {
  Button,
  Checkbox,
  Table,
  TableBody,
  TableRow,
  TableCell,
} from "@mui/material";
import { palette } from "../palette";
import { typography } from "../typography";
import { customShadows } from "../custom-shadows";
import { dsSize, dsShape } from "../ds-tokens";
import { componentsOverrides } from "./index";

const rgb = (hex) =>
  `rgb(${hex
    .slice(1)
    .match(/../g)
    .map((v) => parseInt(v, 16))
    .join(", ")})`;
describe.each(["light", "dark"])("pinned component geometry in %s", (mode) => {
  const theme = createTheme({
    palette: palette(mode),
    typography,
    customShadows: customShadows(mode),
    shape: dsShape,
  });
  theme.components = componentsOverrides(theme);
  it("retains primary state styling when layering v0.2 density", () => {
    render(
      <ThemeProvider theme={theme}>
        <Button color="primary" variant="contained">
          Save changes
        </Button>
      </ThemeProvider>,
    );
    const style = getComputedStyle(screen.getByRole("button"));
    expect(style.height).toBe(`${dsSize.control.md}px`);
    expect(style.backgroundColor).toBe(rgb(theme.palette.primary.main));
    expect(style.color).toBe(rgb(theme.palette.primary.contrastText));
    expect(style.fontSize).toBe(typography.button.fontSize);
  });
  it("uses the same control color for checked and indeterminate", () => {
    render(
      <ThemeProvider theme={theme}>
        <Checkbox checked inputProps={{ "aria-label": "Checked" }} />
        <Checkbox indeterminate inputProps={{ "aria-label": "Mixed" }} />
      </ThemeProvider>,
    );
    for (const label of ["Checked", "Mixed"])
      expect(
        getComputedStyle(screen.getByLabelText(label).parentElement).color,
      ).toBe(rgb(theme.palette.control.checked));
  });
  it("keeps 32px rows rather than the v0.1 36px rows", () => {
    render(
      <ThemeProvider theme={theme}>
        <Table>
          <TableBody>
            <TableRow>
              <TableCell>Value</TableCell>
            </TableRow>
          </TableBody>
        </Table>
      </ThemeProvider>,
    );
    const style = getComputedStyle(screen.getByRole("cell"));
    expect(style.height).toBe(`${dsSize.row.default}px`);
    expect(style.lineHeight).toBe(typography.body_sm.lineHeight);
  });
});
