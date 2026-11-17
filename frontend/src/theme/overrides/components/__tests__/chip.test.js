import { alpha, createTheme } from "@mui/material/styles";
import { describe, expect, it } from "vitest";
import { palette } from "src/theme/palette";
import { chip } from "../chip";

// Flatten the override array as Emotion does. Keep the plain &:hover selector
// contract so screen-level sx can still override the interactive state.
const rootStyles = (theme, ownerState) =>
  Object.assign({}, ...chip(theme).MuiChip.styleOverrides.root({ ownerState }));

// A clickable neutral chip hovers one step darker (action.selected). A theme
// built without the design-system palette already rests on action.selected, so
// there it hovers on action.hover instead: the hover must never match the rest.
const neutralHover = (theme) =>
  theme.palette.status
    ? theme.palette.action.selected
    : theme.palette.action.hover;

const themes = [
  ["bare MUI", createTheme({ palette: { mode: "dark" } })],
  ["light", createTheme({ palette: palette("light") })],
  ["dark", createTheme({ palette: palette("dark") })],
];

describe.each(themes)("chip theme override in %s", (_name, theme) => {
  it("leaves a non-clickable filled default chip alone on hover", () => {
    const styles = rootStyles(theme, { color: "default", variant: "filled" });
    expect(styles["&:hover"]).toBeUndefined();
  });

  it("repaints a clickable filled default chip with the neutral hover role", () => {
    const styles = rootStyles(theme, {
      color: "default",
      variant: "filled",
      clickable: true,
    });
    expect(styles["&:hover"]).toEqual({
      backgroundColor: neutralHover(theme),
    });
    expect(styles["&:hover"].backgroundColor).not.toBe(styles.backgroundColor);
  });

  it("leaves a non-clickable soft default chip alone on hover", () => {
    const styles = rootStyles(theme, { color: "default", variant: "soft" });
    expect(styles["&:hover"]).toBeUndefined();
  });

  it("repaints a clickable soft default chip on hover", () => {
    const styles = rootStyles(theme, {
      color: "default",
      variant: "soft",
      clickable: true,
    });
    expect(styles["&:hover"]).toEqual({
      backgroundColor: neutralHover(theme),
    });
    expect(styles["&:hover"].backgroundColor).not.toBe(styles.backgroundColor);
  });

  it("leaves a non-clickable filled coloured chip alone on hover", () => {
    const styles = rootStyles(theme, { color: "primary", variant: "filled" });
    expect(styles["&:hover"]).toBeUndefined();
  });

  it("repaints a clickable primary chip without treating the accent as a status", () => {
    const styles = rootStyles(theme, {
      color: "primary",
      variant: "filled",
      clickable: true,
    });
    expect(styles["&:hover"]).toEqual({
      backgroundColor: neutralHover(theme),
    });
    expect(styles["&:hover"].backgroundColor).not.toBe(styles.backgroundColor);
  });

  it.each(["success", "info", "warning", "error"])(
    "keeps the %s role on hover",
    (color) => {
      const role = color === "error" ? "danger" : color;
      const styles = rootStyles(theme, {
        color,
        variant: "filled",
        clickable: true,
      });
      const status = theme.palette.status?.[role];
      expect(styles.color).toBe(status?.fg ?? theme.palette[color].dark);
      expect(styles["&:hover"]).toEqual({
        backgroundColor: alpha(
          status?.solid ?? theme.palette[color].main,
          0.18,
        ),
      });
      expect(styles["&:hover"].backgroundColor).not.toBe(
        styles.backgroundColor,
      );
    },
  );
});
