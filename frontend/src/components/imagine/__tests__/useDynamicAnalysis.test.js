import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import axios from "src/utils/axios";
import useImagineStore from "../useImagineStore";
import useDynamicAnalysis, {
  resolveProjectId,
  triggerAnalysis,
} from "../useDynamicAnalysis";

vi.mock("src/utils/axios", () => ({
  default: {
    post: vi.fn(),
    get: vi.fn(),
  },
  endpoints: {
    imagineAnalysis: {
      trigger: "/tracer/imagine-analysis/",
      poll: "/tracer/imagine-analysis/",
    },
  },
}));

const widget = {
  id: "analysis-widget",
  dynamicAnalysis: { prompt: "Explain this trace." },
};

describe("useDynamicAnalysis helpers", () => {
  beforeEach(() => {
    vi.useRealTimers();
    vi.clearAllMocks();
    useImagineStore.getState().reset();
    window.history.pushState(
      {},
      "Test",
      "/dashboard/observe/project-from-url/traces",
    );
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("resolves project id from explicit prop before trace data or URL", () => {
    expect(
      resolveProjectId("explicit-project", {
        project_id: "trace-project",
        trace: { project_id: "nested-project" },
      }),
    ).toBe("explicit-project");
  });

  it("resolves project id from trace data before URL fallback", () => {
    expect(
      resolveProjectId(null, { trace: { project_id: "trace-project" } }),
    ).toBe("trace-project");
  });

  it("stores a visible failure when analysis has no saved view", async () => {
    const started = await triggerAnalysis(
      [widget],
      "trace-1",
      null,
      "project-1",
    );

    expect(started).toBe(false);
    expect(axios.post).not.toHaveBeenCalled();
    expect(
      useImagineStore.getState().getAnalysis("trace-1", "analysis-widget"),
    ).toContain("saved Imagine view");
  });

  it("stores failed trigger responses so widgets stop loading", async () => {
    axios.post.mockResolvedValueOnce({
      data: {
        result: {
          analyses: [
            {
              widget_id: "analysis-widget",
              status: "failed",
              error: "temporal unavailable",
            },
          ],
        },
      },
    });

    const started = await triggerAnalysis(
      [widget],
      "trace-1",
      "view-1",
      "project-1",
    );

    expect(started).toBe(true);
    expect(
      useImagineStore.getState().getAnalysis("trace-1", "analysis-widget"),
    ).toContain("temporal unavailable");
  });

  it("stores completed trigger responses and lets backend infer missing project id", async () => {
    axios.post.mockResolvedValueOnce({
      data: {
        result: {
          analyses: [
            {
              widget_id: "analysis-widget",
              status: "completed",
              content: "Analysis complete",
            },
          ],
        },
      },
    });

    const started = await triggerAnalysis([widget], "trace-1", "view-1", null);

    expect(started).toBe(true);
    expect(axios.post).toHaveBeenCalledWith("/tracer/imagine-analysis/", {
      saved_view_id: "view-1",
      trace_id: "trace-1",
      widgets: [
        {
          widget_id: "analysis-widget",
          prompt: "Explain this trace.",
        },
      ],
    });
    expect(
      useImagineStore.getState().getAnalysis("trace-1", "analysis-widget"),
    ).toBe("Analysis complete");
  });

  it("rerun starts polling and stores completed poll results", async () => {
    vi.useFakeTimers();
    useImagineStore.getState().setSavedViewId("view-1");

    axios.post.mockResolvedValueOnce({
      data: {
        result: {
          analyses: [
            {
              widget_id: "analysis-widget",
              status: "running",
            },
          ],
        },
      },
    });
    axios.get.mockResolvedValueOnce({
      data: {
        result: {
          analyses: [
            {
              widget_id: "analysis-widget",
              status: "completed",
              content: "Polled result",
            },
          ],
        },
      },
    });

    const { result } = renderHook(() =>
      useDynamicAnalysis([], { project_id: "project-1" }, null, "trace-1"),
    );

    await act(async () => {
      expect(result.current(widget)).toBe(true);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(axios.post).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
      await Promise.resolve();
    });

    expect(axios.get).toHaveBeenCalledWith("/tracer/imagine-analysis/", {
      params: { saved_view_id: "view-1", trace_id: "trace-1" },
    });
    expect(
      useImagineStore.getState().getAnalysis("trace-1", "analysis-widget"),
    ).toBe("Polled result");
  });
});
