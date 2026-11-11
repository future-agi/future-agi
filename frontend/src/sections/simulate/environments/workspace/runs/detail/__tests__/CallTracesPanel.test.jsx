import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "src/utils/test-utils";

const mocks = vi.hoisted(() => ({
  store: {},
  drawerProps: vi.fn(),
}));

vi.mock("src/sections/projects/LLMTracing/states", () => ({
  useLLMTracingStoreShallow: (selector) => selector(mocks.store),
}));

vi.mock("src/sections/projects/LLMTracing/LLMTracingTraceDetailDrawer", () => ({
  default: (props) => {
    mocks.drawerProps(props);
    return <div data-testid="trace-drawer" />;
  },
}));

import CallTracesPanel from "../CallTracesPanel";
import { CALL_TRACES_COPY } from "../callTraces.constants";

const TRACES = [
  {
    traceId: "trace-2",
    name: "book_ride",
    startTime: "2026-10-15T10:02:00Z",
    status: "OK",
    latencyMs: 1830,
  },
  {
    traceId: "trace-1",
    name: "verify_identity",
    startTime: "2026-10-15T10:01:00Z",
    status: "ERROR",
    latencyMs: 420,
  },
];

const renderPanel = (props) =>
  render(
    <CallTracesPanel traces={TRACES} projectId="hidden-project" {...props} />,
  );

describe("CallTracesPanel", () => {
  beforeEach(() => {
    mocks.drawerProps.mockReset();
    mocks.store = {
      setTraceDetailDrawerOpen: vi.fn(),
      setVisibleTraces: vi.fn(),
      resetStates: vi.fn(),
    };
  });

  it("lists the call's traces and keeps the drawer's prev/next to them", () => {
    renderPanel();

    expect(screen.getByText("book_ride")).toBeInTheDocument();
    expect(screen.getByText("verify_identity")).toBeInTheDocument();
    expect(screen.getByText("1.8s")).toBeInTheDocument();
    expect(mocks.store.setVisibleTraces).toHaveBeenLastCalledWith([
      { traceId: "trace-2", projectId: "hidden-project" },
      { traceId: "trace-1", projectId: "hidden-project" },
    ]);
  });

  it("opens the read-only trace drawer on the answer's project, for the run", () => {
    renderPanel({ testExecutionId: "run-1" });
    fireEvent.click(screen.getByText("verify_identity"));

    expect(mocks.store.setTraceDetailDrawerOpen).toHaveBeenCalledWith({
      traceId: "trace-1",
      projectId: "hidden-project",
      testExecutionId: "run-1",
    });
    expect(screen.getByTestId("trace-drawer")).toBeInTheDocument();
    expect(mocks.drawerProps.mock.lastCall[0]).toMatchObject({
      readOnly: true,
    });
  });

  it("clears the trace drawer state and its URL key when it unmounts", () => {
    const { unmount } = renderPanel();
    expect(mocks.store.resetStates).not.toHaveBeenCalled();
    expect(mocks.store.setTraceDetailDrawerOpen).not.toHaveBeenCalled();

    unmount();

    expect(mocks.store.resetStates).toHaveBeenCalledTimes(1);
    expect(mocks.store.setTraceDetailDrawerOpen).toHaveBeenCalledWith(null);
  });

  it("tells its owner while it is mounted", () => {
    const onMountedChange = vi.fn();
    const { unmount } = renderPanel({ onMountedChange });
    expect(onMountedChange.mock.calls).toEqual([[true]]);

    unmount();

    expect(onMountedChange.mock.calls).toEqual([[true], [false]]);
  });

  it("names a trace whose root span is not stored yet by its id", () => {
    renderPanel({
      traces: [
        {
          traceId: "trace-9",
          name: null,
          startTime: null,
          status: null,
          latencyMs: null,
        },
      ],
    });

    expect(screen.getByText("Root span not stored yet")).toBeInTheDocument();
    expect(screen.getByText("trace-9")).toBeInTheDocument();
  });

  it("says only that the call has no trace", () => {
    const { container } = renderPanel({ traces: [] });

    expect(screen.getByText("No trace for this call")).toBeInTheDocument();
    expect(container.textContent).toBe("No trace for this call");
    expect(mocks.store.setVisibleTraces).toHaveBeenLastCalledWith([]);
  });

  it("says when the traces could not be read", () => {
    renderPanel({ traces: [], projectId: null, isError: true });

    expect(
      screen.getByText("Couldn't load this call's traces."),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("trace-drawer")).toBeNull();
  });

  it("keeps the rows it has when a background refetch fails", () => {
    renderPanel({ isError: true });

    expect(screen.getByText("book_ride")).toBeInTheDocument();
    expect(screen.queryByText(CALL_TRACES_COPY.error)).not.toBeInTheDocument();
  });

  it("shows the error words only when there is nothing to show", () => {
    renderPanel({ isError: true, traces: [] });

    expect(screen.getByText(CALL_TRACES_COPY.error)).toBeInTheDocument();
  });
});
