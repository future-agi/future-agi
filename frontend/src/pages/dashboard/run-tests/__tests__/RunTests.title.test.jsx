import React from "react";
import { describe, expect, it, vi } from "vitest";
import { HelmetProvider } from "react-helmet-async";
import { render, waitFor } from "src/utils/test-utils";

import RunTests from "../RunTests";

vi.mock("src/components/run-tests/CreateRunTestPage", () => ({
  default: () => null,
}));
vi.mock("src/components/run-tests/RunTestsContent", () => ({
  default: () => null,
}));

describe("Run Simulation page", () => {
  // Before: the tab read "Run Tests | Dashboard" while the menu item and the
  // page heading both say "Run Simulation".
  it("names the browser tab after the page", async () => {
    render(
      <HelmetProvider>
        <RunTests />
      </HelmetProvider>,
    );
    await waitFor(() =>
      expect(document.title).toBe("Run Simulation | Future AGI"),
    );
    expect(document.title).not.toMatch(/Run Tests/);
  });
});
