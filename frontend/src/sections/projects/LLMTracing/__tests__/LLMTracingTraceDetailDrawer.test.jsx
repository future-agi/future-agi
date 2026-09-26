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

import LLMTracingTraceDetailDrawer from "../LLMTracingTraceDetailDrawer";

const lastDrawerProps = () => mocks.drawerProps.mock.lastCall[0];

// TraceDetailDrawerV2 reads useGetTraceDetail(traceId, projectId), which sends
// project_id only when it is given (src/api/project/__tests__/trace-detail).
// /dashboard/users/:userId renders LLMTracingView mode="user": the route has
// no observeId and the grid lists every project's rows, so the same trace id
// can appear once per project.
describe("LLMTracingTraceDetailDrawer project pin", () => {
  beforeEach(() => {
    mocks.drawerProps.mockClear();
    mocks.params = {};
    mocks.store = {
      traceDetailDrawerOpen: { traceId: "trace-1", filters: [] },
      setTraceDetailDrawerOpen: vi.fn(),
      visibleTraces: [{ traceId: "trace-1", projectId: "project-route" }],
    };
  });

  it("pins to the clicked row's project on a route without observeId", () => {
    mocks.params = { userId: "user-1" };
    mocks.store.traceDetailDrawerOpen = {
      traceId: "trace-1",
      projectId: "project-b",
      filters: [],
    };

    render(<LLMTracingTraceDetailDrawer />);

    expect(lastDrawerProps()).toMatchObject({
      traceId: "trace-1",
      open: true,
      projectId: "project-b",
    });
  });

  it("keeps the route project when the drawer state has no pin", () => {
    mocks.params = { observeId: "project-route" };

    render(<LLMTracingTraceDetailDrawer />);

    expect(lastDrawerProps().projectId).toBe("project-route");
  });

  it("steps prev/next by row, carrying each row's project", () => {
    mocks.params = { userId: "user-1" };
    const filters = [{ column_id: "user_id" }];
    mocks.store.traceDetailDrawerOpen = {
      traceId: "trace-1",
      projectId: "project-b",
      filters,
    };
    mocks.store.visibleTraces = [
      { traceId: "trace-1", projectId: "project-a" },
      { traceId: "trace-1", projectId: "project-b" },
      { traceId: "trace-2", projectId: "project-a" },
    ];

    render(<LLMTracingTraceDetailDrawer />);

    const props = lastDrawerProps();
    expect(props.hasPrev).toBe(true);
    expect(props.hasNext).toBe(true);
    act(() => props.onNext());
    expect(mocks.store.setTraceDetailDrawerOpen).toHaveBeenLastCalledWith({
      traceId: "trace-2",
      projectId: "project-a",
      filters,
    });
    act(() => props.onPrev());
    expect(mocks.store.setTraceDetailDrawerOpen).toHaveBeenLastCalledWith({
      traceId: "trace-1",
      projectId: "project-a",
      filters,
    });
  });
});
