// A run id is often cut by its column; the full id is on hover, as a link
// and as plain text.
import React from "react";
import { MemoryRouter } from "react-router-dom";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import RunLink from "./RunLink";

const hidden = vi.hoisted(() => ({ runs: true }));
vi.mock("../shell/page-status", async (importOriginal) => ({
  ...(await importOriginal()),
  isGatewayPageHidden: (pageId) => pageId === "runs" && hidden.runs,
}));

const RUN_ID = "run_2026-11-12T03-40-00Z_checkout-agent_7f3c9a1b";

function renderLink() {
  render(
    <MemoryRouter>
      <RunLink runId={RUN_ID} />
    </MemoryRouter>,
  );
}

describe("RunLink: the full run id on hover", () => {
  it("as a link", () => {
    hidden.runs = false;
    renderLink();
    expect(screen.getByRole("link", { name: RUN_ID })).toHaveAttribute(
      "title",
      RUN_ID,
    );
  });

  it("as plain text while the runs pages are hidden", () => {
    hidden.runs = true;
    renderLink();
    expect(screen.getByTestId("run-id")).toHaveAttribute("title", RUN_ID);
  });
});
