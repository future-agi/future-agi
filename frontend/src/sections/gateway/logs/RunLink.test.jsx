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

function renderLink() {
  render(
    <MemoryRouter>
      <RunLink runId="run/7 a" />
    </MemoryRouter>,
  );
}

describe("RunLink", () => {
  it("is plain text while the runs pages are hidden", () => {
    hidden.runs = true;
    renderLink();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(screen.getByTestId("run-id")).toHaveTextContent("run/7 a");
  });

  it("links to the run page once runs are built", () => {
    hidden.runs = false;
    renderLink();
    expect(screen.getByRole("link", { name: "run/7 a" })).toHaveAttribute(
      "href",
      "/dashboard/gateway/runs/run%2F7%20a",
    );
  });
});
