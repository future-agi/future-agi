import React from "react";
import { HelmetProvider } from "react-helmet-async";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";

const { drawerProps } = vi.hoisted(() => ({ drawerProps: vi.fn() }));

// Only the props the page hands the drawer matter here.
vi.mock("src/components/traceDetail/TraceDetailDrawerV2", () => ({
  default: (props) => {
    drawerProps(props);
    return null;
  },
}));

import TraceFullPage from "../TraceFullPage";

const renderAt = (url) =>
  render(
    <HelmetProvider>
      <MemoryRouter initialEntries={[url]}>
        <Routes>
          <Route
            path="/dashboard/observe/:observeId/trace/:traceId"
            element={<TraceFullPage />}
          />
        </Routes>
      </MemoryRouter>
    </HelmetProvider>,
  );

const lastDrawerProps = () => drawerProps.mock.lastCall[0];

describe("full-page trace view span link", () => {
  beforeEach(() => {
    drawerProps.mockClear();
  });

  it("opens the drawer on the span named in span_id", () => {
    renderAt("/dashboard/observe/project-1/trace/trace-1?span_id=span-llm");

    expect(lastDrawerProps()).toMatchObject({
      traceId: "trace-1",
      projectId: "project-1",
      initialSpanId: "span-llm",
    });
  });

  it("leaves the span choice to the drawer without span_id", () => {
    renderAt("/dashboard/observe/project-1/trace/trace-1");

    expect(lastDrawerProps().initialSpanId).toBeNull();
  });
});
