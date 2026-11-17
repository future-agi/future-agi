import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { HelmetProvider } from "react-helmet-async";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";

vi.mock("src/sections/develop/DevelopView", () => ({ default: () => null }));
vi.mock("src/components/iconify", () => ({ default: () => null }));

import Develop from "../Develop";

const sealedDescription =
  "Build test data, then run prompts, evals and experiments against it.";

afterEach(() => {
  cleanup();
  setLaunchOverrideForTest(null);
});

describe.each([
  ["launch", true],
  ["prototype", false],
])("c26 Datasets header copy: %s", (_name, launch) => {
  it("renders the exact sealed description on both profiles", () => {
    setLaunchOverrideForTest({ launch, experimentsBasic: true });
    render(<HelmetProvider><Develop /></HelmetProvider>);
    expect(screen.getByRole("heading", { level: 1, name: "Datasets" })).toBeTruthy();
    const description = screen.getByText(sealedDescription, { exact: true });
    expect(description.textContent).toBe(sealedDescription);
    expect(description.textContent).not.toMatch(/version|history/i);
  });
});
