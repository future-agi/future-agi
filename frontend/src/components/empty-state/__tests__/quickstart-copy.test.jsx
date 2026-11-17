// W10-DESIGN-SYSTEM-U06: the quickstart Copy button says "Copied" only once
// the code is on the clipboard. A self-hosted instance opened over plain http
// has no navigator.clipboard, and a browser can refuse the write; then the
// code is selected and the button says how to copy it by hand.
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
let writeText;
let execCommand;

// Secure origin: the async Clipboard API exists.
function withClipboardApi(impl) {
  writeText = vi.fn(impl);
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText },
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

// Hover the button, as a pointer user does when clicking it, so its tooltip
// shows what the click did.
function hoverAndClickCopy() {
  const button = screen.getByRole("button", { name: "Copy code" });
  fireEvent.mouseOver(button);
  fireEvent.click(button);
}

async function expectTooltip(text) {
  await waitFor(() =>
    expect(screen.getByRole("tooltip")).toHaveTextContent(text),
  );
}

describe.each(SURFACES)("%s quickstart Copy button", (_, quickstart, Frame) => {
  const code = quickstart.snippets[0].code;

  it("says Copied once the clipboard has the code", async () => {
    withClipboardApi(() => Promise.resolve());
    renderQuickstart(Frame, quickstart);

    hoverAndClickCopy();

    expect(writeText).toHaveBeenCalledWith(code);
    await expectTooltip("Copied");
  });

  it("copies with the legacy command on an http origin, then says Copied", async () => {
    withoutClipboardApi();
    withExecCommand(true);
    renderQuickstart(Frame, quickstart);

    hoverAndClickCopy();

    await expectTooltip("Copied");
    expect(execCommand).toHaveBeenCalledWith("copy");
  });

  it("on an http origin where nothing copies, selects the code instead of saying Copied", async () => {
    withoutClipboardApi();
    withExecCommand(false);
    renderQuickstart(Frame, quickstart);

    hoverAndClickCopy();

    await expectTooltip(COPY_FAILED);
    expect(screen.getByRole("tooltip")).not.toHaveTextContent("Copied");
    expect(window.getSelection().toString()).toBe(code);
  });

  it("when the browser refuses the write, selects the code instead of saying Copied", async () => {
    withClipboardApi(refused);
    renderQuickstart(Frame, quickstart);

    hoverAndClickCopy();

    expect(writeText).toHaveBeenCalledWith(code);
    await expectTooltip(COPY_FAILED);
    expect(screen.getByRole("tooltip")).not.toHaveTextContent("Copied");
    expect(window.getSelection().toString()).toBe(code);
  });
});
