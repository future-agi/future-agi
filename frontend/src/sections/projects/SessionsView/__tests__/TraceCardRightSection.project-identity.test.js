import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import TraceCardRightSection from "../TraceCardRightSection";

const mocks = vi.hoisted(() => ({
  getTraceDetail: vi.fn(),
  drawer: vi.fn(),
}));

vi.mock("src/api/project/trace-detail", () => ({
  useGetTraceDetail: mocks.getTraceDetail,
}));
vi.mock("src/components/traceDetail/TraceDetailDrawerV2", () => ({
  default: mocks.drawer,
}));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("src/components/show", () => ({
  ShowComponent: ({ condition, children }) => (condition ? children : null),
}));
vi.mock("../EvaluationsContent", () => ({ default: () => null }));
vi.mock("../MetadataContent", () => ({ default: () => null }));
vi.mock("src/utils/utils", () => ({
  formatMs: () => "12 ms",
  formatStartTimeByRequiredFormat: () => "01/01/2026 - 00:00:00",
}));

const TRACE_ID = "shared-trace";
const SESSION_PROJECT = "11111111-1111-4111-8111-111111111111";
const OTHER_PROJECT = "22222222-2222-4222-8222-222222222222";
const FETCHED_PROJECT = "44444444-4444-4444-8444-444444444444";

function subject(systemMetrics = {}, observeId) {
  const path = observeId
    ? "/dashboard/observe/:observeId/sessions"
    : "/dashboard/users/:userId";
  const entry = observeId
    ? `/dashboard/observe/${observeId}/sessions`
    : "/dashboard/users/alice";
  return React.createElement(
    MemoryRouter,
    { initialEntries: [entry] },
    React.createElement(
      Routes,
      null,
      React.createElement(Route, {
        path,
        element: React.createElement(TraceCardRightSection, {
          traceId: TRACE_ID,
          systemMetrics,
        }),
      }),
    ),
  );
}

function openTrace() {
  fireEvent.click(screen.getByRole("button", { name: "View Trace" }));
}

function expectOpenProject(projectId) {
  expect(mocks.getTraceDetail).toHaveBeenLastCalledWith(TRACE_ID, {
    projectId,
  });
  expect(mocks.drawer.mock.lastCall[0]).toMatchObject({
    traceId: TRACE_ID,
    projectId,
    open: true,
  });
}

describe("TraceCardRightSection project identity", () => {
  beforeEach(() => {
    mocks.getTraceDetail.mockReset();
    mocks.drawer.mockReset();
    // A conflicting cached/fetched copy must never override the session pin.
    mocks.getTraceDetail.mockReturnValue({
      data: { trace: { project: FETCHED_PROJECT } },
    });
    mocks.drawer.mockReturnValue(null);
  });

  it("pins the cross-project lookup to the session project", () => {
    render(subject({ project_id: SESSION_PROJECT }));
    // A closed card still defers fetching until View Trace is clicked.
    expect(mocks.getTraceDetail.mock.lastCall[0]).toBeNull();
    expect(mocks.drawer.mock.lastCall[0].open).toBe(false);

    openTrace();

    expect(mocks.getTraceDetail).toHaveBeenLastCalledWith(TRACE_ID, {
      projectId: SESSION_PROJECT,
    });
  });

  it("keeps the drawer on the session project despite a conflicting fetched copy", () => {
    render(subject({ project_id: SESSION_PROJECT }));

    openTrace();

    expect(mocks.drawer.mock.lastCall[0]).toMatchObject({
      traceId: TRACE_ID,
      projectId: SESSION_PROJECT,
      open: true,
    });
  });

  it("prefers the session project over a conflicting observe route", () => {
    render(subject({ project_id: SESSION_PROJECT }, OTHER_PROJECT));

    openTrace();

    expectOpenProject(SESSION_PROJECT);
  });

  it("repins a reused trace ID when the selected session changes", () => {
    const view = render(subject({ project_id: SESSION_PROJECT }));
    openTrace();
    expectOpenProject(SESSION_PROJECT);

    view.rerender(subject({ project_id: OTHER_PROJECT }));

    expectOpenProject(OTHER_PROJECT);
  });

  it("retains the observe route fallback for older session responses", () => {
    render(subject({}, OTHER_PROJECT));

    openTrace();

    expectOpenProject(OTHER_PROJECT);
  });

  it("retains the fetched fallback when neither session nor route supplies a project", () => {
    render(subject());

    openTrace();

    expect(mocks.getTraceDetail).toHaveBeenLastCalledWith(TRACE_ID, {
      projectId: undefined,
    });
    expect(mocks.drawer.mock.lastCall[0]).toMatchObject({
      traceId: TRACE_ID,
      projectId: FETCHED_PROJECT,
      open: true,
    });
  });
});
