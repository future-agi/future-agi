// W10-DESIGN-SYSTEM-U05: on a self-hosted instance the first-run code a user
// copies must point the SDK at this instance. The quickstart prints this
// build's API base (src/sections/common/commands/apiBase.js) as FI_BASE_URL in
// the keys step, beside FI_API_KEY and FI_SECRET_KEY. The snippets are Python
// (W10-DESIGN-SYSTEM-U04), so the step sets os.environ before the SDK is
// imported; the SDK reads FI_BASE_URL from the environment.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import EmptyLayout from "src/components/EmptyLayout/EmptyLayout";
import {
  QUICKSTART_DATASET,
  QUICKSTART_PROMPT,
  quickstartTracing,
} from "./quickstarts";

// A self-hosted build's configured API host. vi.mock is hoisted above the
// imports, so the literal is repeated in the factory. The trailing slash is
// trimmed by apiBase.js.
vi.mock("src/config-global", async (importOriginal) => ({
  ...(await importOriginal()),
  HOST_API: "https://fa.acme.internal/",
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));

const BASE_URL_LINE = 'os.environ["FI_BASE_URL"] = "https://fa.acme.internal"';

const KEYS_STEP = [
  'os.environ["FI_API_KEY"] = "your-api-key"',
  'os.environ["FI_SECRET_KEY"] = "your-secret-key"',
  BASE_URL_LINE,
].join("\n");

// The first futureagi import: `from fi import …` or `from fi.<module> import …`.
const SDK_IMPORT = /^from fi[ .]/m;

let writeText;
beforeEach(() => {
  writeText = vi.fn();
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText },
  });
});
afterEach(() => {
  delete navigator.clipboard;
});

function expectBaseUrlBeforeSdkImport(text) {
  const baseUrl = text.indexOf(BASE_URL_LINE);
  const sdkImport = text.search(SDK_IMPORT);
  expect(baseUrl).toBeGreaterThan(-1);
  expect(sdkImport).toBeGreaterThan(baseUrl);
}

describe("first-run quickstarts on a self-hosted instance", () => {
  it.each([
    ["No prompts yet", QUICKSTART_PROMPT],
    ["No datasets yet", QUICKSTART_DATASET],
  ])("%s: the code sets FI_BASE_URL to this instance", (title, quickstart) => {
    render(<EmptyLayout title={title} quickstart={quickstart} />);

    const code = screen.getByText(
      /os\.environ\["FI_BASE_URL"\] = "https:\/\/fa\.acme\.internal"/,
    );
    expect(code.textContent).toContain(KEYS_STEP);
    expectBaseUrlBeforeSdkImport(code.textContent);

    fireEvent.click(screen.getByRole("button", { name: "Copy code" }));
    expect(writeText).toHaveBeenCalledTimes(1);
    const copied = writeText.mock.calls[0][0];
    expect(copied).toContain(KEYS_STEP);
    expectBaseUrlBeforeSdkImport(copied);
    expect(copied).not.toMatch(/^\s*export\s/m);
  });

  it("tracing keeps its own base: its FI_BASE_URL is the trace collector, not this API", () => {
    const text = quickstartTracing("my-project").snippets[0].code;
    expect(text).toContain('os.environ["FI_API_KEY"] = "your-api-key"');
    // The self-host note names FI_BASE_URL for the collector, but only in a
    // comment: no code on any line sets or reads it.
    expect(text).not.toMatch(/^[^#\n]*FI_BASE_URL/m);
    expect(text).not.toContain("fa.acme.internal");
  });
});
