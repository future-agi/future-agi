import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render } from "src/utils/test-utils";

const mocks = vi.hoisted(() => ({
  drawerProps: vi.fn(),
  store: {
    traceDetailDrawerOpen: { traceId: "trace-1", projectId: "project-1" },
    setTraceDetailDrawerOpen: vi.fn(),
    visibleTraces: [{ traceId: "trace-1", projectId: "project-1" }],
  },
}));

vi.mock("react-router", async (importOriginal) => ({
  ...(await importOriginal()),
  useParams: () => ({}),
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

describe("LLMTracingTraceDetailDrawer read-only", () => {
  it("passes readOnly through to the drawer, off by default", () => {
    render(<LLMTracingTraceDetailDrawer />);
    expect(mocks.drawerProps.mock.lastCall[0]).toMatchObject({
      readOnly: false,
    });

    render(<LLMTracingTraceDetailDrawer readOnly />);
    expect(mocks.drawerProps.mock.lastCall[0]).toMatchObject({
      readOnly: true,
      traceId: "trace-1",
      projectId: "project-1",
    });
  });
});
