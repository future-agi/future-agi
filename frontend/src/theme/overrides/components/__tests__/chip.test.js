import { createTheme } from "@mui/material/styles";
import { describe, expect, it } from "vitest";
import { chip } from "../chip";

const darkTheme = createTheme({ palette: { mode: "dark" } });

// The override returns an array of style objects; flatten it the way emotion
// would so a single assertion can look at the merged result.
const rootStyles = (theme, ownerState) =>
  Object.assign({}, ...chip(theme).MuiChip.styleOverrides.root({ ownerState }));

describe("chip theme override", () => {
  it("leaves a non-clickable filled default chip alone on hover", () => {
    const styles = rootStyles(darkTheme, {
      color: "default",
      variant: "filled",
    });

    expect(styles["&:hover"]).toBeUndefined();
  });

  it("repaints a clickable filled default chip on hover, using the plain &:hover selector", () => {
    const styles = rootStyles(darkTheme, {
      color: "default",
      variant: "filled",
      clickable: true,
    });

    expect(styles["&:hover"]).toEqual({
      backgroundColor: darkTheme.palette.grey[100],
    });
  });

  it("leaves a non-clickable soft default chip alone on hover", () => {
    const styles = rootStyles(darkTheme, {
      color: "default",
      variant: "soft",
    });

    expect(styles["&:hover"]).toBeUndefined();
  });

  it("repaints a clickable soft default chip on hover", () => {
    const styles = rootStyles(darkTheme, {
      color: "default",
      variant: "soft",
      clickable: true,
    });

    expect(styles["&:hover"]).toBeDefined();
  });

  it("leaves a non-clickable filled coloured chip alone on hover", () => {
    const styles = rootStyles(darkTheme, {
      color: "primary",
      variant: "filled",
    });

    expect(styles["&:hover"]).toBeUndefined();
  });

  it("repaints a clickable filled coloured chip on hover", () => {
    const styles = rootStyles(darkTheme, {
      color: "primary",
      variant: "filled",
      clickable: true,
    });

    expect(styles["&:hover"]).toEqual({
      backgroundColor: darkTheme.palette.primary.dark,
    });
  });
});
