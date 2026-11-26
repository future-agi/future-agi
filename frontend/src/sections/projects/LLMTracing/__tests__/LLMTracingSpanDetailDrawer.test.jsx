import React from "react";
import { act } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "src/utils/test-utils";

const mocks = vi.hoisted(() => ({
  params: {},
  drawerProps: vi.fn(),
  store: {},
}));

vi.mock("react-router", async (importOriginal) => ({
  ...(await importOriginal()),
  useParams: () => mocks.params,
}));

vi.mock("../states", () => ({
  useLLMTracingStoreShallow: (selector) => selector(mocks.store),
}));

vi.mock("src/components/traceDetail/TraceDetailDrawerV2", () => ({
  default: (props) => {
    mocks.drawerProps(props);
    return null;
  },
}));

import LLMTracingSpanDetailDrawer from "../LLMTracingSpanDetailDrawer";

const lastDrawerProps = () => mocks.drawerProps.mock.lastCall[0];

// The span list on /dashboard/users/:userId (LLMTracingView mode="user") has
// no route observeId and lists spans from every project; the drawer reads
// the span's trace by id, which is not unique across projects.
describe("LLMTracingSpanDetailDrawer project pin", () => {
  beforeEach(() => {
    mocks.drawerProps.mockClear();
    mocks.params = {};
    mocks.store = {
      spanDetailDrawerOpen: {
        trace_id: "trace-1",
        span_id: "span-1",
        filters: [],
        fromSpansView: true,
      },
      setSpanDetailDrawerOpen: vi.fn(),
      visibleTraces: [{ traceId: "trace-1", projectId: "project-route" }],
    };
  });

  it("pins to the clicked span's project on a route without observeId", () => {
    mocks.params = { userId: "user-1" };
    mocks.store.spanDetailDrawerOpen = {
      ...mocks.store.spanDetailDrawerOpen,
      project_id: "project-b",
    };

    render(<LLMTracingSpanDetailDrawer />);

    expect(lastDrawerProps()).toMatchObject({
      traceId: "trace-1",
      initialSpanId: "span-1",
      projectId: "project-b",
    });
  });

  it("keeps the route project when the drawer state has no pin", () => {
    mocks.params = { observeId: "project-route" };

    render(<LLMTracingSpanDetailDrawer />);

    expect(lastDrawerProps().projectId).toBe("project-route");
  });

  it("steps to the next trace row with that row's project", () => {
    mocks.params = { userId: "user-1" };
    mocks.store.spanDetailDrawerOpen = {
      ...mocks.store.spanDetailDrawerOpen,
      project_id: "project-b",
    };
    mocks.store.visibleTraces = [
      { traceId: "trace-1", projectId: "project-a" },
      { traceId: "trace-1", projectId: "project-b" },
      { traceId: "trace-2", projectId: "project-a" },
    ];

    render(<LLMTracingSpanDetailDrawer />);

    act(() => lastDrawerProps().onNext());
    expect(mocks.store.setSpanDetailDrawerOpen).toHaveBeenLastCalledWith(
      expect.objectContaining({
        trace_id: "trace-2",
        project_id: "project-a",
        span_id: null,
      }),
    );
  });
});
