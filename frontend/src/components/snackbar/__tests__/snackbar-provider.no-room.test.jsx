import React from "react";
import { act, cleanup, render, screen } from "@testing-library/react";
import { createTheme, ThemeProvider } from "@mui/material/styles";
import { closeSnackbar, enqueueSnackbar } from "notistack";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import SnackbarProvider from "../snackbar-provider";

// A toast raised while an open drawer leaves no room beside the toast column,
// with notistack and the provider real. jsdom does no layout, so the page
// content, the drawer and its buttons get their boxes by hand: the window is
// jsdom's 1024×768, <main> runs from x=200, and the drawer is the Create
// Label drawer (960 px wide, so from x=64) with Cancel and Create in its
// footer, 32 px tall and 16 px above the window's bottom.
//
// Notistack's CJS entry adds enqueueSnackbar/closeSnackbar only when its
// provider mounts; live getters reach the mounted provider.
vi.mock("notistack", async () => {
  const actual = await vi.importActual("notistack");
  const live = actual.default ?? actual;
  return {
    ...actual,
    get enqueueSnackbar() {
      return live.enqueueSnackbar;
    },
    get closeSnackbar() {
      return live.closeSnackbar;
    },
  };
});
vi.mock("../../settings", () => ({
  useSettingsContext: () => ({ themeDirection: "ltr" }),
}));
vi.mock("../../iconify", () => ({ default: () => null }));

const theme = createTheme({ palette: { border: { default: "#dddddd" } } });
const MESSAGE = "A label with this name already exists";

const boxAt = (left, top, right, bottom) => () => ({
  left,
  top,
  right,
  bottom,
  x: left,
  y: top,
  width: right - left,
  height: bottom - top,
});

// <main> from x=200 to `mainRight`, and a right drawer from `drawerLeft` to
// the window's right edge with Cancel and Create in its footer.
function openDrawer(mainRight, drawerLeft) {
  const main = document.createElement("main");
  main.id = "main-content";
  main.getBoundingClientRect = boxAt(200, 0, mainRight, 768);
  const drawer = document.createElement("div");
  drawer.className = "MuiPaper-root MuiDrawer-paper";
  drawer.getBoundingClientRect = boxAt(drawerLeft, 0, 1024, 768);
  [
    ["Cancel", boxAt(664, 720, 824, 752)],
    ["Create", boxAt(840, 720, 1000, 752)],
  ].forEach(([label, at]) => {
    const button = document.createElement("button");
    button.textContent = label;
    button.getBoundingClientRect = at;
    drawer.appendChild(button);
  });
  document.body.append(main, drawer);
  return [main, drawer];
}
const openCreateLabelDrawer = () => openDrawer(1024, 64);

const renderProvider = () =>
  render(
    <ThemeProvider theme={theme}>
      <SnackbarProvider>
        <span>page</span>
      </SnackbarProvider>
    </ThemeProvider>,
  );

const styleText = () =>
  Array.from(document.querySelectorAll("style"))
    .map((s) => s.textContent)
    .join("\n");

const RULE = /([^{}]*(?:fa-toasts|SnackbarContainer)[^{}]*)\{([^{}]*)\}/g;
const HIDES = /visibility:\s*hidden|display:\s*none|opacity:\s*0(?![.\d])/;
const Z_RULE = /\.fa-toasts\{[^}]*z-index:\s*(\d+)\s*!important/;

/** The selectors of every style rule that hides the toast container. */
function rulesHidingTheContainer() {
  return [...styleText().matchAll(RULE)]
    .filter(([, , decl]) => HIDES.test(decl))
    .map(([, selector]) => selector.trim());
}

describe("a toast raised while a drawer leaves no room beside it", () => {
  let opened = [];
  beforeEach(() => {
    opened = openCreateLabelDrawer();
  });
  afterEach(() => {
    act(() => {
      closeSnackbar();
    });
    cleanup();
    opened.forEach((el) => el.remove());
    document.body.className = "";
    document.body.removeAttribute("style");
  });

  it("is shown: no class on <body> and no style rule hides its container", async () => {
    renderProvider();
    act(() => {
      enqueueSnackbar(MESSAGE, { variant: "error" });
    });
    const message = await screen.findByText(MESSAGE);
    expect(message.closest(".fa-toasts")).not.toBeNull();
    const classes = Array.from(document.body.classList);
    expect(classes.filter((c) => /hidden/i.test(c))).toEqual([]);
    expect(rulesHidingTheContainer()).toEqual([]);
    // Placed over the drawer: 16 px inside the window's right edge, 420 px
    // wide, lifted 8 px over Cancel and Create (768 − 720 + 8).
    const body = document.body.style;
    expect(body.getPropertyValue("--fa-toast-right")).toBe("16px");
    expect(body.getPropertyValue("--fa-toast-maxw")).toBe("420px");
    expect(body.getPropertyValue("--fa-toast-lift")).toBe("56px");
  });

  it("paints the toasts above every drawer and dialog layer the app uses", () => {
    renderProvider();
    // MUI puts drawers at 1200 and dialogs at 1300; several of the app's
    // drawers and dialogs set 9999. Notistack's own container is 1400.
    const z = styleText().match(Z_RULE);
    expect(z).not.toBeNull();
    expect(Number(z[1])).toBeGreaterThan(9999);
  });

  it("lets a toast narrow to the column's width over a narrow drawer or dialog", () => {
    renderProvider();
    // The toast's own minimum width (320 px) gives way to the column's
    // measured width, and only when that is narrower.
    expect(styleText()).toMatch(
      /\.fa-toasts \.notistack-MuiContent\{[^}]*min-width:\s*min\(320px,\s*var\(--fa-toast-maxw,\s*320px\)\)/,
    );
  });
});

describe("a toast raised over a drawer while the assistant sidebar is docked", () => {
  let opened = [];
  beforeEach(() => {
    // The sidebar (420 px) is docked right of <main>, which so ends at
    // x=604; a 480 px drawer from x=544 covers both.
    opened = openDrawer(604, 544);
  });
  afterEach(() => {
    act(() => {
      closeSnackbar();
    });
    cleanup();
    opened.forEach((el) => el.remove());
    document.body.className = "";
    document.body.removeAttribute("style");
  });

  it("is shown at the drawer's right edge, wide enough to read", async () => {
    renderProvider();
    act(() => {
      enqueueSnackbar(MESSAGE, { variant: "error" });
    });
    const message = await screen.findByText(MESSAGE);
    expect(message.closest(".fa-toasts")).not.toBeNull();
    const classes = Array.from(document.body.classList);
    expect(classes.filter((c) => /hidden/i.test(c))).toEqual([]);
    // At the content's edge the column would be 28 px wide; it sits 16 px
    // inside the drawer's (and the window's) right edge instead, 420 px
    // wide, lifted 8 px over Cancel and Create (768 − 720 + 8).
    const body = document.body.style;
    expect(body.getPropertyValue("--fa-toast-right")).toBe("16px");
    expect(body.getPropertyValue("--fa-toast-maxw")).toBe("420px");
    expect(body.getPropertyValue("--fa-toast-lift")).toBe("56px");
  });
});
