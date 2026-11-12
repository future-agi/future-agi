import React from "react";
import { MemoryRouter } from "react-router-dom";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import ObserveLinkOrigin from "../ObserveLinkOrigin";

const location = { origin: "https://app.futureagi.test" };
const renderWithRouter = (element) =>
  render(<MemoryRouter>{element}</MemoryRouter>);

describe("ObserveLinkOrigin", () => {
  it("renders the origin and a same-origin SPA back link when both params are valid", () => {
    renderWithRouter(
      <ObserveLinkOrigin
        search="?observeFrom=support-agent%20%C2%B7%20revision%205%20in%20Deployments&observeReturnTo=%2Fdashboard%2Fdeployments%2Fdep-1"
        location={location}
      />,
    );
    expect(screen.getByText(/Opened from/)).toBeInTheDocument();
    expect(screen.getByText(/support-agent/)).toBeInTheDocument();
    expect(screen.getByText(/ordinary filters/)).toBeInTheDocument();
    expect(screen.getByTestId("observe-link-back")).toHaveAttribute(
      "href",
      "/dashboard/deployments/dep-1",
    );
  });

  it("does not render a back bar for missing or cross-origin return paths", () => {
    const { rerender } = renderWithRouter(
      <ObserveLinkOrigin
        search="?observeFrom=Deployments"
        location={location}
      />,
    );
    expect(screen.queryByTestId("observe-link-origin")).toBeNull();

    rerender(
      <MemoryRouter>
        <ObserveLinkOrigin
          search="?observeFrom=Deployments&observeReturnTo=https%3A%2F%2Fevil.example%2F"
          location={location}
        />
      </MemoryRouter>,
    );
    expect(screen.queryByTestId("observe-link-origin")).toBeNull();
  });
});
