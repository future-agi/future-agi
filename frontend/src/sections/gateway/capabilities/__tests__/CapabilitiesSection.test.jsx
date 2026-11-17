import React from "react";
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";

import CapabilitiesSection from "../CapabilitiesSection";

// The capabilities page is hidden: mounted at any route, its section sends
// the visitor to the Models page and shows no placeholder.
describe("CapabilitiesSection", () => {
  it("forwards to Models with the query and hash, and renders no placeholder", () => {
    let location;
    const Probe = () => {
      location = useLocation();
      return <div data-testid="models" />;
    };
    const { container } = render(
      <MemoryRouter
        initialEntries={["/dashboard/gateway/capabilities?model=x#top"]}
      >
        <Routes>
          <Route
            path="/dashboard/gateway/capabilities"
            element={<CapabilitiesSection />}
          />
          <Route path="/dashboard/gateway/models" element={<Probe />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByTestId("models")).toBeInTheDocument();
    expect(location.pathname).toBe("/dashboard/gateway/models");
    expect(location.search).toBe("?model=x");
    expect(location.hash).toBe("#top");
    expect(container).not.toHaveTextContent(/coming|in this build|preview/i);
  });
});
