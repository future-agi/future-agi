// @vitest-environment jsdom
import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import axios, { endpoints } from "src/utils/axios";
import ImagineTab from "../ImagineTab";
import WidgetRenderer from "../WidgetRenderer";
import useImagineStore from "../useImagineStore";

vi.mock("src/utils/axios", () => ({
  default: { post: vi.fn(), get: vi.fn() },
  endpoints: {
    imagineAnalysis: {
      trigger: "/tracer/imagine-analysis/",
      poll: "/tracer/imagine-analysis/",
    },
  },
}));
vi.mock("src/api/project/saved-views", () => ({
  useCreateSavedView: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: vi.fn(),
}));
vi.mock("src/sections/falcon-ai/store/falconRequestGuard", () => ({
  captureFalconRequest: () => () => true,
}));
vi.mock("src/sections/falcon-ai/store/useFalconStore", () => ({
  default: { getState: () => ({ messages: [] }) },
}));
vi.mock("src/sections/falcon-ai/hooks/useFalconDeferredAction", () => ({
  default: () => vi.fn(),
}));
vi.mock("src/sections/falcon-ai/hooks/useFalconAPI", () => ({
  getConversation: vi.fn(),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("../ImagineChatPanel", () => ({ default: () => null }));
vi.mock("../SuggestedPrompts", () => ({ default: () => null }));
// Keep ImagineTab -> WidgetCanvas -> useDynamicAnalysis real; only stub the
// visual leaf, whose Rerun callback is exercised below.
vi.mock("../WidgetRenderer", () => ({ default: vi.fn(() => null) }));

const projectId = "11111111-1111-4111-8111-111111111111";
const otherProjectId = "22222222-2222-4222-8222-222222222222";
const savedViewId = "33333333-3333-4333-8333-333333333333";
const traceId = "pg-trace-1";
const widget = {
  id: "dynamic-summary",
  type: "markdown",
  dynamicAnalysis: { prompt: "Summarize this trace" },
};
const savedWidgets = [widget];
const traceData = { spans: [], summary: {} };
const routes = [
  ["cross-project Users", "/dashboard/users/customer-1?userTab=traces"],
  ["owning project", `/dashboard/observe/${projectId}/traces`],
  ["different URL project", `/dashboard/observe/${otherProjectId}/traces`],
];
const response = (analyses) => ({ data: { result: { analyses } } });
// The hook restores with GET first and dispatches after a microtask.
const flush = () => act(async () => {});

function savedTab(props = {}) {
  return (
    <ImagineTab
      traceId={traceId}
      projectId={projectId}
      traceData={traceData}
      savedViewId={savedViewId}
      savedWidgets={savedWidgets}
      readOnly
      {...props}
    />
  );
}

function expectProjectRequest(force) {
  expect(axios.post).toHaveBeenCalledTimes(1);
  expect(axios.post).toHaveBeenCalledWith(endpoints.imagineAnalysis.trigger, {
    saved_view_id: savedViewId,
    trace_id: traceId,
    project_id: projectId,
    force,
    widgets: [{ widget_id: widget.id, prompt: widget.dynamicAnalysis.prompt }],
  });
}

async function rerun() {
  const { onRunAnalysis, widget: renderedWidget } =
    WidgetRenderer.mock.lastCall[0];
  let started;
  // A click commits React's re-render before the POST can settle, so the hook
  // joins the in-flight rerun. A synchronous act keeps that order, as
  // fireEvent.click does; an async act lets the mocked POST settle first and
  // the hook then dispatches the widget a second time.
  act(() => {
    started = onRunAnalysis(renderedWidget);
  });
  await flush();
  return started;
}

describe("W10-TRACING-OTHER-L06: Imagine analysis project context", () => {
  let originalUrl;

  beforeEach(() => {
    originalUrl = window.location.href;
    vi.useFakeTimers();
    vi.clearAllMocks();
    useImagineStore.getState().reset();
    axios.post.mockResolvedValue(response([]));
    axios.get.mockResolvedValue(response([]));
    window.history.replaceState({}, "", routes[0][1]);
  });

  afterEach(() => {
    cleanup();
    useImagineStore.getState().reset();
    vi.useRealTimers();
    window.history.replaceState({}, "", originalUrl);
  });

  it.each(routes)(
    "automatic analysis uses the drawer project on %s",
    async (_name, url) => {
      window.history.replaceState({}, "", url);
      render(savedTab());
      await flush();

      expectProjectRequest(false);
    },
  );

  it.each(routes)("Rerun uses the drawer project on %s", async (_name, url) => {
    window.history.replaceState({}, "", url);
    // Rerun is offered once a result exists; seed one so no auto-run starts.
    useImagineStore.getState().setAnalysis(traceId, widget.id, "Old analysis");
    render(savedTab());
    await flush();
    expect(axios.post).not.toHaveBeenCalled();

    expect(await rerun()).toBe(true);

    expectProjectRequest(true);
  });

  it("waits for project context and triggers when it arrives", async () => {
    const { rerender } = render(savedTab({ projectId: undefined }));
    await flush();
    expect(axios.post).not.toHaveBeenCalled();

    rerender(savedTab());
    await flush();

    expectProjectRequest(false);
  });

  it("does not substitute the URL project when drawer context is missing", async () => {
    window.history.replaceState(
      {},
      "",
      `/dashboard/observe/${otherProjectId}/traces`,
    );
    render(savedTab({ projectId: undefined }));
    await flush();

    expect(axios.post).not.toHaveBeenCalled();
  });

  it("rejects Rerun without project context without clearing cached analysis", async () => {
    window.history.replaceState(
      {},
      "",
      `/dashboard/observe/${otherProjectId}/traces`,
    );
    useImagineStore
      .getState()
      .setAnalysis(traceId, widget.id, "Cached analysis");
    render(savedTab({ projectId: undefined }));
    await flush();

    expect(await rerun()).toBe(false);

    expect(axios.post).not.toHaveBeenCalled();
    expect(useImagineStore.getState().getAnalysis(traceId, widget.id)).toBe(
      "Cached analysis",
    );
  });
});
