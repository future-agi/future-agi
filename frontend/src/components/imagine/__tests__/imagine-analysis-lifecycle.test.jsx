// @vitest-environment jsdom
import React from "react";
import {
  act,
  cleanup,
  fireEvent,
  render,
  renderHook,
  screen,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import axios from "src/utils/axios";
import ImagineTab from "../ImagineTab";
import useDynamicAnalysis, { runAnalysis } from "../useDynamicAnalysis";
import useImagineStore from "../useImagineStore";

const mocks = vi.hoisted(() => ({ create: vi.fn() }));
vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  endpoints: { imagineAnalysis: { trigger: "/analysis", poll: "/analysis" } },
}));
vi.mock("src/api/project/saved-views", () => ({
  useCreateSavedView: () => ({ mutate: mocks.create, isPending: false }),
}));
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: vi.fn(),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("../WidgetCanvas", () => ({ default: () => null }));
vi.mock("../ImagineChatPanel", () => ({ default: () => null }));
vi.mock("../SuggestedPrompts", () => ({ default: () => null }));
vi.mock("src/sections/falcon-ai/store/useFalconStore", () => ({
  default: { getState: () => ({ messages: [] }) },
}));
vi.mock("src/sections/falcon-ai/store/falconRequestGuard", () => ({
  captureFalconRequest: () => () => true,
}));
vi.mock("src/sections/falcon-ai/hooks/useFalconDeferredAction", () => ({
  default: () => vi.fn(),
}));
vi.mock("src/sections/falcon-ai/hooks/useFalconAPI", () => ({
  getConversation: vi.fn().mockResolvedValue({}),
}));

const widget = {
  id: "summary",
  type: "markdown",
  title: "Summary",
  dynamicAnalysis: { prompt: "Summarize trace errors" },
};
const traceData = { trace: { id: "trace-1" } };
const tabProps = {
  traceId: "trace-1",
  projectId: "project-1",
  traceData,
  savedViewId: null,
  readOnly: true,
};
const response = (analyses) => ({ data: { result: { analyses } } });
const flush = () => act(async () => {});
function useTestAnalysis() {
  return useDynamicAnalysis([widget], traceData, null, "trace-1", "project-1");
}

beforeEach(() => {
  vi.resetAllMocks();
  useImagineStore.getState().reset();
  window.history.replaceState({}, "", "/dashboard/observe/project-1");
  axios.get.mockResolvedValue(response([]));
  axios.post.mockResolvedValue(
    response([{ widget_id: "summary", status: "running" }]),
  );
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("unsaved Imagine identity", () => {
  it("creates a personal draft once and binds its returned identity", async () => {
    useImagineStore.getState().replaceAll([widget]);
    const { rerender } = render(<ImagineTab {...tabProps} readOnly={false} />);
    expect(mocks.create).toHaveBeenCalledTimes(1);
    expect(mocks.create.mock.calls[0][0]).toMatchObject({
      project_id: "project-1",
      tab_type: "imagine",
      visibility: "personal",
      config: { widgets: [widget] },
    });
    rerender(<ImagineTab {...tabProps} readOnly={false} />);
    expect(mocks.create).toHaveBeenCalledTimes(1);
    await act(async () => {
      mocks.create.mock.calls[0][1].onSuccess({
        data: { result: { id: "draft-1" } },
      });
    });
    expect(useImagineStore.getState()._savedViewId).toBe("draft-1");
  });

  it("does not create duplicate drafts during strict effect replay", async () => {
    useImagineStore.getState().replaceAll([widget]);
    render(
      <React.StrictMode>
        <ImagineTab {...tabProps} readOnly={false} />
      </React.StrictMode>,
    );
    expect(mocks.create).toHaveBeenCalledTimes(1);
    await act(async () => {
      mocks.create.mock.calls[0][1].onSuccess({
        data: { result: { id: "strict-draft" } },
      });
    });
    expect(useImagineStore.getState()._savedViewId).toBe("strict-draft");
  });

  it("binds explicit save without reopening the tab", async () => {
    useImagineStore
      .getState()
      .replaceAll([{ id: "plain", type: "markdown", title: "Plain" }]);
    render(<ImagineTab {...tabProps} />);
    fireEvent.click(screen.getByRole("button", { name: "Save View" }));
    fireEvent.click(screen.getByRole("button", { name: "Save view" }));
    await act(async () => {
      mocks.create.mock.calls[0][1].onSuccess({
        data: { result: { id: "saved-1" } },
      });
    });
    expect(useImagineStore.getState()._savedViewId).toBe("saved-1");
  });

  it("surfaces a draft creation failure instead of an indefinite skeleton", async () => {
    useImagineStore.getState().replaceAll([widget]);
    render(<ImagineTab {...tabProps} readOnly={false} />);
    expect(mocks.create).toHaveBeenCalledTimes(1);
    await act(async () => {
      mocks.create.mock.calls[0][1].onError(new Error("offline"));
    });
    expect(
      useImagineStore.getState().getAnalysis("trace-1", "summary"),
    ).toMatch(/save view/i);
    expect(mocks.create).toHaveBeenCalledTimes(1);
  });

  it("does not bind a draft response after unmount", async () => {
    useImagineStore.getState().replaceAll([widget]);
    const { unmount } = render(<ImagineTab {...tabProps} readOnly={false} />);
    expect(mocks.create).toHaveBeenCalledTimes(1);
    const callbacks = mocks.create.mock.calls[0][1];
    unmount();
    await act(async () =>
      callbacks.onSuccess({ data: { result: { id: "old-draft" } } }),
    );
    expect(useImagineStore.getState()._savedViewId).toBeNull();
  });
});

describe("dynamic analysis execution", () => {
  it("never dispatches a null saved-view identity", () => {
    expect(runAnalysis(widget, "trace-1", "project-1")).toBe(false);
    expect(axios.post).not.toHaveBeenCalled();
  });

  it("waits for binding and lets only the hook own initial dispatch", async () => {
    renderHook(useTestAnalysis);
    expect(
      runAnalysis({ ...widget, _needsAnalysis: true }, "trace-1", "project-1"),
    ).toBe(false);
    expect(axios.post).not.toHaveBeenCalled();
    await act(async () => useImagineStore.getState().setSavedViewId("draft-1"));
    await flush();
    expect(axios.post).toHaveBeenCalledTimes(1);
    expect(axios.post.mock.calls[0][1]).toMatchObject({
      saved_view_id: "draft-1",
    });
    expect(
      runAnalysis({ ...widget, _needsAnalysis: true }, "trace-1", "project-1"),
    ).toBe(false);
    expect(axios.post).toHaveBeenCalledTimes(1);
  });

  it("restores a completed result with GET without executing again", async () => {
    useImagineStore.getState().setSavedViewId("saved-1");
    axios.get.mockResolvedValue(
      response([
        { widget_id: "summary", status: "completed", content: "Saved summary" },
      ]),
    );
    renderHook(useTestAnalysis);
    await flush();
    expect(useImagineStore.getState().getAnalysis("trace-1", "summary")).toBe(
      "Saved summary",
    );
    expect(axios.post).not.toHaveBeenCalled();
  });

  it("reopens an already-stale versioned running analysis through an ordinary POST", async () => {
    vi.useFakeTimers();
    useImagineStore.getState().setSavedViewId("saved-1");
    // GET retains running for imagine-pb1- rows but exposes neither their
    // workflow ID nor updated_at. Only POST can restart a row older than 120s.
    const running = {
      id: "6df91937-802b-4a3c-90fa-0c8c4e6bf438",
      widget_id: "summary",
      status: "running",
      content: null,
      error: null,
    };
    let restarted = false;
    axios.get.mockImplementation(async () =>
      response([
        restarted
          ? { ...running, status: "completed", content: "Recovered summary" }
          : running,
      ]),
    );
    axios.post.mockImplementation(async () => {
      restarted = true;
      return response([running]);
    });
    renderHook(useTestAnalysis);
    await flush();
    expect(axios.get).toHaveBeenCalledTimes(1);
    expect(axios.post).toHaveBeenCalledTimes(1);
    expect(axios.get.mock.invocationCallOrder[0]).toBeLessThan(
      axios.post.mock.invocationCallOrder[0],
    );
    expect(axios.post).toHaveBeenCalledWith("/analysis", {
      saved_view_id: "saved-1",
      trace_id: "trace-1",
      project_id: "project-1",
      force: false,
      widgets: [
        { widget_id: "summary", prompt: widget.dynamicAnalysis.prompt },
      ],
    });
    await act(async () => vi.advanceTimersByTimeAsync(3000));
    expect(useImagineStore.getState().getAnalysis("trace-1", "summary")).toBe(
      "Recovered summary",
    );
    expect(axios.post).toHaveBeenCalledTimes(1);
  });

  it("checks an active running analysis once and leaves deduplication to POST", async () => {
    vi.useFakeTimers();
    useImagineStore.getState().setSavedViewId("saved-1");
    axios.get.mockResolvedValue(
      response([
        { widget_id: "summary", status: "running", content: null, error: null },
      ]),
    );
    renderHook(useTestAnalysis);
    await flush();
    expect(axios.get).toHaveBeenCalledTimes(1);
    expect(axios.post).toHaveBeenCalledTimes(1);
    expect(axios.post.mock.calls[0][1]).toMatchObject({ force: false });
    await act(async () => vi.advanceTimersByTimeAsync(9000));
    expect(axios.get).toHaveBeenCalledTimes(4);
    expect(axios.post).toHaveBeenCalledTimes(1);
    expect(
      useImagineStore.getState().getAnalysis("trace-1", "summary"),
    ).toBeNull();
  });

  it("shows dispatch errors in the widget cache", async () => {
    useImagineStore.getState().setSavedViewId("saved-1");
    axios.post.mockRejectedValue(new Error("offline"));
    renderHook(useTestAnalysis);
    await flush();
    expect(
      useImagineStore.getState().getAnalysis("trace-1", "summary"),
    ).toMatch(/failed.*rerun/i);
  });

  it("shows an API dispatch failure returned with HTTP success", async () => {
    useImagineStore.getState().setSavedViewId("saved-1");
    axios.post.mockResolvedValue(
      response([{ widget_id: "summary", status: "failed" }]),
    );
    renderHook(useTestAnalysis);
    await flush();
    expect(
      useImagineStore.getState().getAnalysis("trace-1", "summary"),
    ).toMatch(/failed.*rerun/i);
  });

  it("keeps polling when the first result list is empty", async () => {
    vi.useFakeTimers();
    useImagineStore.getState().setSavedViewId("saved-1");
    axios.get
      .mockResolvedValueOnce(response([]))
      .mockResolvedValueOnce(response([]))
      .mockResolvedValue(
        response([
          {
            widget_id: "summary",
            status: "completed",
            content: "Delayed result",
          },
        ]),
      );
    renderHook(useTestAnalysis);
    await flush();
    await act(async () => vi.advanceTimersByTimeAsync(6000));
    expect(useImagineStore.getState().getAnalysis("trace-1", "summary")).toBe(
      "Delayed result",
    );
    expect(axios.post).toHaveBeenCalledTimes(1);
  });

  it("forces rerun and polls only after dispatch settles", async () => {
    vi.useFakeTimers();
    useImagineStore.getState().setSavedViewId("saved-1");
    useImagineStore.getState().setAnalysis("trace-1", "summary", "Old summary");
    let finishDispatch;
    axios.post.mockImplementation(
      () =>
        new Promise((resolve) => {
          finishDispatch = resolve;
        }),
    );
    axios.get
      .mockResolvedValueOnce(
        response([{ widget_id: "summary", status: "running" }]),
      )
      .mockResolvedValue(
        response([
          {
            widget_id: "summary",
            status: "completed",
            content: "Fresh summary",
          },
        ]),
      );
    renderHook(useTestAnalysis);
    await act(async () =>
      expect(runAnalysis(widget, "trace-1", "project-1")).toBe(true),
    );
    expect(axios.post.mock.calls[0][1]).toMatchObject({
      saved_view_id: "saved-1",
      force: true,
    });
    expect(axios.get).not.toHaveBeenCalled();
    await act(async () =>
      finishDispatch(response([{ widget_id: "summary", status: "running" }])),
    );
    await act(async () => vi.advanceTimersByTimeAsync(3000));
    expect(useImagineStore.getState().getAnalysis("trace-1", "summary")).toBe(
      "Fresh summary",
    );
    expect(axios.post).toHaveBeenCalledTimes(1);
  });
});
