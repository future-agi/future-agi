import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router";
import useExecutionLinkBase from "../useExecutionLinkBase";

// Renders the hook's result so we can assert the resolved base path for the
// URL the component is mounted at.
const Probe = () => <div data-testid="base">{useExecutionLinkBase()}</div>;

const renderAt = (initialPath, routePath) =>
  render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route path={routePath} element={<Probe />} />
      </Routes>
    </MemoryRouter>,
  );

describe("useExecutionLinkBase", () => {
  it("returns the URL up to and including the execution id on the product test route", () => {
    renderAt(
      "/dashboard/simulate/test/t1/e1/call-details",
      "/dashboard/simulate/test/:testId/:executionId/*",
    );
    expect(screen.getByTestId("base").textContent).toBe(
      "/dashboard/simulate/test/t1/e1",
    );
  });

  it("returns the URL up to and including the execution id on the workspace runs route", () => {
    renderAt(
      "/dashboard/simulate/environments/env1/runs/t1/e1/analytics",
      "/dashboard/simulate/environments/:envId/runs/:testId/:executionId/*",
    );
    expect(screen.getByTestId("base").textContent).toBe(
      "/dashboard/simulate/environments/env1/runs/t1/e1",
    );
  });
});
