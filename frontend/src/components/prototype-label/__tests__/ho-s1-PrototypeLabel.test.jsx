// HO S1 (the capability contract): the prototype label's copy, landmark and reserving
// styles.
import React from "react";
import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";

import PrototypeLabel from "../PrototypeLabel";
import {
  PROTOTYPE_LABEL_HEIGHT,
  PROTOTYPE_LABEL_NAME,
  PROTOTYPE_LABEL_TEXT,
  PROTOTYPE_LABEL_VAR,
  prototypeLabelGlobalStyles,
} from "../prototypeLabelModel";

const theme = createTheme({
  palette: { warning: { main: "#F5E65F", dark: "#B8AC47" } },
});

const renderLabel = () =>
  render(
    <ThemeProvider theme={theme}>
      <PrototypeLabel />
    </ThemeProvider>,
  );

describe("PrototypeLabel", () => {
  it("reads exactly the ruling's copy as real text in a note named Prototype notice", () => {
    renderLabel();
    const note = screen.getByRole("note", { name: PROTOTYPE_LABEL_NAME });
    expect(PROTOTYPE_LABEL_TEXT).toBe(
      "Prototype: mock data, resets per browser",
    );
    expect(note.textContent).toBe(PROTOTYPE_LABEL_TEXT);
    expect(note.tagName).toBe("ASIDE");
    expect(note.getAttribute("data-testid")).toBe("prototype-label");
    expect(note.getAttribute("aria-hidden")).toBeNull();
  });

  it("has no close button, takes no focus and stays after Escape and clicks", () => {
    renderLabel();
    expect(screen.queryByRole("button")).toBeNull();
    const note = screen.getByTestId("prototype-label");
    expect(note.getAttribute("tabindex")).toBeNull();
    fireEvent.keyDown(document.body, { key: "Escape" });
    fireEvent.click(note);
    expect(screen.getByTestId("prototype-label")).toBeTruthy();
    expect(
      document.querySelectorAll("[data-testid=prototype-label]"),
    ).toHaveLength(1);
  });

  it("sets the height token on :root through its global styles", () => {
    renderLabel();
    const css = Array.from(document.querySelectorAll("style"))
      .map((s) => s.textContent)
      .join("\n");
    expect(css).toContain(`${PROTOTYPE_LABEL_VAR}:${PROTOTYPE_LABEL_HEIGHT}`);
    expect(css).toContain("padding-top:var(--fa-proto-label-h, 0px)");
  });
});

describe("prototypeLabelGlobalStyles", () => {
  it("reserves the strip for the document, the dashboard row and the fixed layers", () => {
    const styles = prototypeLabelGlobalStyles();
    expect(styles[":root"]).toEqual({ [PROTOTYPE_LABEL_VAR]: "24px" });
    expect(styles.body.paddingTop).toBe("var(--fa-proto-label-h, 0px)");
    const keys = Object.keys(styles).join("\n");
    expect(keys).toContain("[data-fa-shell='row'] > *");
    expect(keys).toContain(".MuiDrawer-paperAnchorRight");
    expect(keys).toContain(".MuiDrawer-paperAnchorLeft");
    expect(keys).toContain(".MuiDialog-paperFullScreen");
    const drawer = Object.entries(styles).find(([k]) =>
      k.includes("paperAnchorRight"),
    )[1];
    expect(drawer).toEqual({
      top: "var(--fa-proto-label-h, 0px)",
      height: "calc(100% - var(--fa-proto-label-h, 0px))",
    });
  });
});
