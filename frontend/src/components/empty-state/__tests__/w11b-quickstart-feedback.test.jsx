// W10-DESIGN-SYSTEM-U06 follow-ups (W11-QUICKSTART2-R), from the UX check of
// the quickstart Copy button and the tracing self-host note:
// - On an http origin the copy goes through the execCommand fallback, which
//   focuses a hidden textarea. Focus comes back to Copy code, so a keyboard
//   user keeps their place and the tooltip stays open.
// - When nothing copies, the message says so and says how to copy the selected
//   code, as the MCP client setup guide does.
// - The result is read out from a polite status region; the button keeps its
//   name, Copy code.
// - The tracing self-host note names the variable to set and an example
//   collector address (README.md, "Self-hosted tracing").
import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import EmptyLayout from "src/components/EmptyLayout/EmptyLayout";
import EmptyState from "src/components/empty-state";
import {
  QUICKSTART_PROMPT,
  quickstartTracing,
} from "src/components/empty-state/quickstarts";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import { logger } from "src/utils/logger";

vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));

const COPY_FAILED =
  "Could not copy. Press Ctrl+C (Cmd+C on Mac) to copy the selected code.";

const theme = createTheme({ palette: palette("light"), typography });

// The two quickstart frames: EmptyLayout (Prompts, Datasets) and the DS
// EmptyState (Tracing).
const SURFACES = [
  ["EmptyLayout", QUICKSTART_PROMPT, EmptyLayout],
  ["EmptyState", quickstartTracing("my-project"), EmptyState],
];

let clipboardDescriptor;
let execDescriptor;
let warnSpy;
let execCommand;

// Secure origin: the async Clipboard API exists.
function withClipboardApi(impl) {
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText: vi.fn(impl) },
  });
}

// Insecure (http) origin: no navigator.clipboard at all.
function withoutClipboardApi() {
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: undefined,
  });
}

// The legacy copy command, the only way to copy on an http origin.
function withExecCommand(result) {
  execCommand = vi.fn(() => result);
  Object.defineProperty(document, "execCommand", {
    configurable: true,
    value: execCommand,
  });
}

// The browser refuses the write. The rejection is handled here too, so a
// handler that never awaits it does not also report an unhandled rejection.
function refused() {
  const write = Promise.reject(new Error("Write permission denied"));
  write.catch(() => {});
  return write;
}

beforeEach(() => {
  clipboardDescriptor = Object.getOwnPropertyDescriptor(navigator, "clipboard");
  execDescriptor = Object.getOwnPropertyDescriptor(document, "execCommand");
  warnSpy = vi.spyOn(logger, "warn").mockImplementation(() => {});
  window.getSelection().removeAllRanges();
});

afterEach(() => {
  warnSpy.mockRestore();
  if (clipboardDescriptor)
    Object.defineProperty(navigator, "clipboard", clipboardDescriptor);
  else delete navigator.clipboard;
  if (execDescriptor)
    Object.defineProperty(document, "execCommand", execDescriptor);
  else delete document.execCommand;
});

function renderQuickstart(Frame, quickstart) {
  return render(
    <ThemeProvider theme={theme}>
      <Frame title="Nothing here yet" quickstart={quickstart} />
    </ThemeProvider>,
  );
}

const copyButton = () => screen.getByRole("button", { name: "Copy code" });

// A keyboard user tabs to Copy code and presses Enter: the button has focus
// before the click. Returns the spy on the button's focus().
function pressCopyFromKeyboard() {
  const button = copyButton();
  button.focus();
  expect(document.activeElement).toBe(button);
  const refocus = vi.spyOn(button, "focus");
  fireEvent.keyDown(button, { key: "Enter" });
  fireEvent.click(button);
  return refocus;
}

function hoverAndClickCopy() {
  const button = copyButton();
  fireEvent.mouseOver(button);
  fireEvent.click(button);
}

async function expectTooltip(text) {
  await waitFor(() =>
    expect(screen.getByRole("tooltip")).toHaveTextContent(text),
  );
}

async function expectStatus(text) {
  await waitFor(() =>
    expect(screen.getByRole("status")).toHaveTextContent(text),
  );
}

describe.each(SURFACES)("%s quickstart Copy button", (_, quickstart, Frame) => {
  const code = quickstart.snippets[0].code;

  it("keeps focus on Copy code after copying on an http origin", async () => {
    withoutClipboardApi();
    withExecCommand(true);
    renderQuickstart(Frame, quickstart);

    const refocus = pressCopyFromKeyboard();

    expect(execCommand).toHaveBeenCalledWith("copy");
    expect(refocus).toHaveBeenCalledTimes(1);
    expect(document.activeElement).toBe(copyButton());
    await expectStatus("Copied");
    await expectTooltip("Copied");
    expect(document.activeElement).toBe(copyButton());
  });

  it("keeps focus on Copy code when nothing copies on an http origin, with the code selected", async () => {
    withoutClipboardApi();
    withExecCommand(false);
    renderQuickstart(Frame, quickstart);

    const refocus = pressCopyFromKeyboard();

    expect(refocus).toHaveBeenCalledTimes(1);
    expect(document.activeElement).toBe(copyButton());
    await expectStatus(COPY_FAILED);
    expect(window.getSelection().toString()).toBe(code);
    expect(document.activeElement).toBe(copyButton());
  });

  it("when the browser refuses the write, says it could not copy and how to copy the selected code", async () => {
    withClipboardApi(refused);
    renderQuickstart(Frame, quickstart);

    hoverAndClickCopy();

    await expectTooltip(COPY_FAILED);
    expect(screen.getByRole("status")).toHaveTextContent(COPY_FAILED);
    expect(window.getSelection().toString()).toBe(code);
  });

  it("reads the result out from a polite status region and keeps the name Copy code", async () => {
    withClipboardApi(() => Promise.resolve());
    renderQuickstart(Frame, quickstart);

    // Mounted and empty before any copy, so the change to "Copied" is read.
    const status = screen.getByRole("status");
    expect(status).toHaveAttribute("aria-live", "polite");
    expect(status.textContent).toBe("");

    hoverAndClickCopy();

    await waitFor(() => expect(status).toHaveTextContent("Copied"));
    expect(screen.getAllByRole("status")).toHaveLength(1);
    expect(copyButton()).toBeInTheDocument();
  });
});

describe("tracing quickstart self-host note", () => {
  const lines = quickstartTracing("my-project").snippets[0].code.split("\n");
  const note = lines.findIndex((line) => line.startsWith("# Self-hosted?"));

  it("names the variable to set and an example collector address", () => {
    expect(note).toBeGreaterThan(-1);
    expect(lines[note]).toContain('os.environ["FI_BASE_URL"]');
    expect(lines[note]).toContain('"http://localhost:4318"');
    expect(lines[note]).toContain("Future AGI cloud");
  });

  it("stays a comment beside the keys, so the code still runs as pasted", () => {
    expect(lines[note - 1]).toBe(
      'os.environ["FI_SECRET_KEY"] = "your-secret-key"',
    );
    expect(lines.filter((line) => line.includes("FI_BASE_URL"))).toEqual(
      lines.filter(
        (line) => line.startsWith("#") && line.includes("FI_BASE_URL"),
      ),
    );
  });
});
