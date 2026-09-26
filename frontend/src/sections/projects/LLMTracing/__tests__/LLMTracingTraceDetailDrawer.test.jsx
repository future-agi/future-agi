import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "src/utils/test-utils";

const mocks = vi.hoisted(() => ({
  params: {},
  drawerProps: vi.fn(),
  store: {
    traceDetailDrawerOpen: { traceId: "trace-1", filters: [] },
    setTraceDetailDrawerOpen: vi.fn(),
    visibleTraceIds: ["trace-1"],
  },
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

// TraceDetailDrawerV2 reads useGetTraceDetail(traceId, projectId), which sends
// project_id only when it is given (src/api/project/__tests__/trace-detail).
describe("LLMTracingTraceDetailDrawer project pin", () => {
  beforeEach(() => {
    mocks.drawerProps.mockClear();
    mocks.params = {};
  });

  it("pins to the grid's project on a route without observeId", () => {
    render(<LLMTracingTraceDetailDrawer projectId="project-selected" />);

    expect(mocks.drawerProps.mock.lastCall[0]).toMatchObject({
      traceId: "trace-1",
      open: true,
      projectId: "project-selected",
    });
  });

  it("keeps the route project when the grid does not pass one", () => {
    mocks.params = { observeId: "project-route" };

    render(<LLMTracingTraceDetailDrawer />);

    expect(mocks.drawerProps.mock.lastCall[0].projectId).toBe("project-route");
  });
});
