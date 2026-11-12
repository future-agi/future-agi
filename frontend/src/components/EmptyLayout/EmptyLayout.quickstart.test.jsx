import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import EmptyLayout from "src/components/EmptyLayout/EmptyLayout";
import EmptyState from "src/components/empty-state";
import * as quickstarts from "src/components/empty-state/quickstarts";
import {
  QUICKSTART_DATASET,
  QUICKSTART_PROMPT,
  quickstartTracing,
} from "src/components/empty-state/quickstarts";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";

// W10-DESIGN-SYSTEM-U04: the first-run code quickstarts are labelled Python
// and have one Copy button. What it copies has to run when pasted into a .py
// file or a notebook cell: no shell lines (`pip install …`, `export …`).
const SHELL_LINE = /^\s*(pip|pip3|npm|export|curl)\s/m;

const theme = createTheme({ palette: palette("light"), typography });

let clipboardDescriptor;
let writeText;
beforeEach(() => {
  clipboardDescriptor = Object.getOwnPropertyDescriptor(navigator, "clipboard");
  writeText = vi.fn(() => Promise.resolve());
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText },
  });
});
afterEach(() => {
  if (clipboardDescriptor)
    Object.defineProperty(navigator, "clipboard", clipboardDescriptor);
  else delete navigator.clipboard;
  vi.clearAllMocks();
});

function renderThemed(ui) {
  return render(<ThemeProvider theme={theme}>{ui}</ThemeProvider>);
}

function copyAndRead() {
  fireEvent.click(screen.getByRole("button", { name: "Copy code" }));
  expect(writeText).toHaveBeenCalledTimes(1);
  return writeText.mock.calls[0][0];
}

function expectRunnablePython(code, firstSdkImport) {
  expect(code).not.toMatch(SHELL_LINE);
  // The install step stays visible, as a Python comment.
  expect(code).toMatch(/^# pip install \S+$/m);
  // Keys are set from Python, before the SDK is imported.
  const keyLine = code.indexOf('os.environ["FI_API_KEY"]');
  const secretLine = code.indexOf('os.environ["FI_SECRET_KEY"]');
  const sdkImport = code.indexOf(firstSdkImport);
  expect(code).toMatch(/^import os$/m);
  expect(keyLine).toBeGreaterThan(-1);
  expect(secretLine).toBeGreaterThan(-1);
  expect(sdkImport).toBeGreaterThan(secretLine);
  expect(sdkImport).toBeGreaterThan(keyLine);
}

describe("first-run code quickstarts copy runnable Python", () => {
  it("Prompts empty folder: Copy code gives Python that runs as pasted", () => {
    renderThemed(
      <EmptyLayout
        title="No prompts yet"
        description="Create a prompt."
        quickstart={QUICKSTART_PROMPT}
      />,
    );

    expect(screen.getByText("Or create one from code")).toBeInTheDocument();
    const copied = copyAndRead();
    expect(copied).toBe(QUICKSTART_PROMPT.snippets[0].code);
    expectRunnablePython(copied, "from fi.prompt import Prompt");
    expect(copied).toMatch(/^# pip install futureagi$/m);
    // What the user reads is what they copy.
    expect(screen.getByText(/from fi\.prompt import Prompt/).textContent).toBe(
      copied,
    );
  });

  it("Datasets empty state: Copy code gives Python that runs as pasted", () => {
    renderThemed(
      <EmptyLayout
        title="No datasets yet"
        description="Create a dataset."
        quickstart={QUICKSTART_DATASET}
      />,
    );

    const copied = copyAndRead();
    expectRunnablePython(copied, "from fi import Client");
    expect(copied).toMatch(/^# pip install futureagi$/m);
  });

  it("Tracing first run: Copy code gives Python that runs as pasted", () => {
    const quickstart = quickstartTracing("my-project");
    renderThemed(
      <EmptyState
        title="No traces yet"
        description="Instrument your app."
        quickstart={quickstart}
      />,
    );

    const copied = copyAndRead();
    expectRunnablePython(
      copied,
      "from fi_instrumentation import FITracer, register",
    );
    expect(copied).toMatch(/^# pip install fi-instrumentation-otel$/m);
    expect(copied).toContain('project_name="my-project"');
  });

  it("no snippet labelled Python starts with shell commands", () => {
    const all = Object.values(quickstarts).map((q) =>
      typeof q === "function" ? q("my-project") : q,
    );
    const python = all.flatMap((q) =>
      q.snippets.filter((s) => s.label === "Python"),
    );
    expect(python.length).toBeGreaterThan(0);
    python.forEach((s) => expect(s.code).not.toMatch(SHELL_LINE));
  });
});
