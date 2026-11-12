import React from "react";
import { screen } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import useMediaQuery from "@mui/material/useMediaQuery";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";

import FilesTab from "../detail/FilesTab";
import SandboxesTable from "../list/SandboxesTable";
import { fakeApi, renderSandboxes, sandboxView } from "./helpers";
import { workspace } from "./session-mock";

vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => workspace,
}));
vi.mock("@mui/material/useMediaQuery", () => ({ default: vi.fn() }));

beforeEach(() => {
  useMediaQuery.mockReturnValue(true);
});

// jsdom does not model the browser cascade for `all: unset`. Assert the
// element's emitted focus-visible rule here; the browser probe checks the
// computed outline after keyboard Tab, with the real DS baseline loaded.
function expectFocusRing(element, theme) {
  const selectors = Array.from(element.classList, (name) => `.${name}:focus-visible`);
  const rules = Array.from(document.styleSheets).flatMap((sheet) =>
    Array.from(sheet.cssRules).filter((rule) =>
      rule.selectorText?.split(",").some((selector) => selectors.includes(selector.trim())),
    ),
  );
  const outline = rules.map((rule) => rule.style.getPropertyValue("outline")).filter(Boolean).pop();
  const offset = rules.map((rule) => rule.style.getPropertyValue("outline-offset")).filter(Boolean).pop();
  expect(outline?.toLowerCase()).toBe(`2px solid ${theme.palette.border.focus}`.toLowerCase());
  expect(offset).toBe("-2px");
}

describe.each(["light", "dark"])("Sandbox keyboard focus (%s)", (mode) => {
  const theme = createTheme({ palette: palette(mode), typography });

  it.each([
    { name: "notes.txt", directory: false, size: 17 },
    { name: "src", directory: true },
  ])("gives the $name row the DS focus ring", async (entry) => {
    const api = fakeApi({
      listFiles: vi.fn(async () => ({ path: "", entries: [entry] })),
    });
    renderSandboxes(
      <ThemeProvider theme={theme}>
        <FilesTab sandbox={sandboxView()} />
      </ThemeProvider>,
      { api },
    );
    expectFocusRing(await screen.findByTestId("file-entry"), theme);
  });

  it.each([
    { name: "narrow sandbox card", narrow: true, tag: "BUTTON" },
    { name: "desktop sandbox row", narrow: false, tag: "TR" },
  ])("gives the $name the DS focus ring", async ({ narrow, tag }) => {
    useMediaQuery.mockReturnValue(narrow);
    renderSandboxes(
      <ThemeProvider theme={theme}>
        <SandboxesTable sandboxes={[sandboxView()]} />
      </ThemeProvider>,
    );
    const row = await screen.findByTestId("sandbox-row");
    expect(row.tagName).toBe(tag);
    expectFocusRing(row, theme);
  });
});
