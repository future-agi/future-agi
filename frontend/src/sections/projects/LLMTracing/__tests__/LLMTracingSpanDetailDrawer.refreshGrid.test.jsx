import React from "react";
import { act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
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

// SpanGrid renders <LLMTracingSpanDetailDrawer refreshGrid={refreshGrid} />.
// A saved annotation in TraceDetailDrawerV2 calls refreshParentGrid so the
// AG Grid server-side row cache reloads the span row's annotation column;
// the Traces tab wrapper already forwards it.
describe("LLMTracingSpanDetailDrawer grid refresh after annotation", () => {
  beforeEach(() => {
    mocks.drawerProps.mockClear();
    mocks.params = { observeId: "project-1" };
    mocks.store = {
      spanDetailDrawerOpen: {
        trace_id: "trace-1",
        span_id: "span-1",
        filters: [],
        fromSpansView: true,
      },
      setSpanDetailDrawerOpen: vi.fn(),
      visibleTraces: [{ traceId: "trace-1", projectId: "project-1" }],
    };
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("forwards refreshGrid to the drawer as refreshParentGrid", () => {
    const refreshGrid = vi.fn();

    render(<LLMTracingSpanDetailDrawer refreshGrid={refreshGrid} />);

    expect(lastDrawerProps().refreshParentGrid).toEqual(expect.any(Function));
    act(() => lastDrawerProps().refreshParentGrid());
    expect(refreshGrid).toHaveBeenCalledTimes(1);
    expect(refreshGrid).toHaveBeenCalledWith();
  });

  it("refreshes the Spans grid when the drawer reports a saved annotation", () => {
    const refreshGrid = vi.fn();

    render(<LLMTracingSpanDetailDrawer refreshGrid={refreshGrid} />);

    act(() => lastDrawerProps().refreshParentGrid());
    expect(refreshGrid).toHaveBeenCalledTimes(1);
  });

  // OBS-SW-192: the score reaches the list API through CDC up to ~8 s after
  // the save, so the first refresh can still read "-". Grid auto-refresh is
  // off by default, so the drawer must re-read on its own, then stop.
  it("re-reads the Spans grid after the score's CDC copy can land, then stops", () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const refreshGrid = vi.fn();

    render(<LLMTracingSpanDetailDrawer refreshGrid={refreshGrid} />);

    act(() => lastDrawerProps().refreshParentGrid());
    expect(refreshGrid).toHaveBeenCalledTimes(1);
    expect(refreshGrid).toHaveBeenLastCalledWith();

    act(() => {
      vi.advanceTimersByTime(8000);
    });
    const refreshesBy8s = refreshGrid.mock.calls.length;
    act(() => {
      vi.advanceTimersByTime(2000);
    });
    const refreshesBy10s = refreshGrid.mock.calls.length;
    expect(refreshesBy10s).toBeGreaterThan(refreshesBy8s);
    // Follow-ups keep the current rows on screen instead of purging.
    expect(refreshGrid.mock.calls.slice(1)).toEqual(
      Array(refreshesBy10s - 1).fill([false]),
    );

    act(() => {
      vi.advanceTimersByTime(60000);
    });
    expect(refreshGrid).toHaveBeenCalledTimes(refreshesBy10s);
  });

  it("restarts the follow-ups on a second save instead of stacking them", () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const refreshGrid = vi.fn();

    render(<LLMTracingSpanDetailDrawer refreshGrid={refreshGrid} />);

    act(() => lastDrawerProps().refreshParentGrid());
    act(() => {
      vi.advanceTimersByTime(70000);
    });
    const refreshesPerSave = refreshGrid.mock.calls.length;
    expect(refreshesPerSave).toBeGreaterThan(1);

    refreshGrid.mockClear();
    act(() => lastDrawerProps().refreshParentGrid());
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    act(() => lastDrawerProps().refreshParentGrid());
    act(() => {
      vi.advanceTimersByTime(70000);
    });
    expect(refreshGrid).toHaveBeenCalledTimes(refreshesPerSave + 1);
  });

  it("stops the follow-ups when the Spans grid unmounts", () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const refreshGrid = vi.fn();

    const { unmount } = render(
      <LLMTracingSpanDetailDrawer refreshGrid={refreshGrid} />,
    );

    act(() => lastDrawerProps().refreshParentGrid());
    expect(refreshGrid).toHaveBeenCalledTimes(1);
    unmount();
    act(() => {
      vi.advanceTimersByTime(70000);
    });
    expect(refreshGrid).toHaveBeenCalledTimes(1);
  });
});
