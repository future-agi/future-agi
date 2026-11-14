import React from "react";
import {
  QueryClient,
  QueryClientProvider,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { Drawer } from "@mui/material";
import { SnackbarProvider } from "notistack";
import {
  act,
  cleanup,
  configure,
  fireEvent,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  afterAll,
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import { render } from "src/utils/test-utils";
import axios, { endpoints } from "src/utils/axios";
import { useGetTraceDetail } from "src/api/project/trace-detail";
import { useBulkCreateScores } from "src/api/scores/scores";
import DrawerHeader from "src/components/traceDetail/DrawerHeader";
import EvalLogSourceHost from "../EvalLogSourceHost";

const mocks = vi.hoisted(() => ({
  org: "org-1",
  ws: "ws-1",
  trace: vi.fn(),
  voice: vi.fn(),
  // A save the test renders inside the opened viewer.
  ViewerAction: null,
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ currentOrganizationId: mocks.org }),
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceId: mocks.ws }),
}));
vi.mock("src/utils/axios", async () => {
  const actual = await vi.importActual("src/utils/axios");
  return {
    ...actual,
    default: { ...actual.default, get: vi.fn(), post: vi.fn() },
  };
});
vi.mock("src/components/iconify", () => ({ default: () => <span /> }));
vi.mock("src/utils/Mixpanel", () => ({
  Events: {},
  PropertyName: {},
  trackEvent: vi.fn(),
}));
vi.mock("src/components/traceDetail/TraceDetailDrawerV2", () => ({
  default: function TraceStub(props) {
    const query = useGetTraceDetail(props.traceId, {
      projectId: props.projectId,
    });
    mocks.trace(props, useQueryClient());
    return (
      <Drawer
        open
        variant="persistent"
        anchor="right"
        PaperProps={{ sx: { width: "60vw" } }}
      >
        <DrawerHeader
          {...props}
          onOpenNewTab={props.hideOpenInNewTab ? undefined : vi.fn()}
        />
        <div data-testid="trace-body">{query.data?.trace?.id}</div>
        {mocks.ViewerAction && <mocks.ViewerAction />}
      </Drawer>
    );
  },
}));
vi.mock("src/components/VoiceDetailDrawerV2/VoiceDetailDrawerV2", () => ({
  default: function VoiceStub(props) {
    mocks.voice(props, useQueryClient());
    return (
      <div data-testid="voice-body">
        Call content
        {mocks.ViewerAction && <mocks.ViewerAction />}
      </div>
    );
  },
}));
const LOG = "11111111-1111-4111-8111-111111111111";
const TRACE = "22222222-2222-4222-8222-222222222222";
const PROJECT = "33333333-3333-4333-8333-333333333333";
const SPAN = "abcdef0123456789";
const ready = {
  status: "ready",
  kind: "trace",
  project_id: PROJECT,
  trace_id: TRACE,
  span_id: SPAN,
  retryable: false,
};
const envelope = (result) => ({ data: { result } });
const enriched = (nav) => envelope({ source_navigation: nav });
const traceData = {
  trace: { id: TRACE, project: PROJECT },
  observation_spans: [
    { observation_span: { id: SPAN, trace: TRACE, project: PROJECT } },
  ],
};
const voiceData = {
  trace_id: TRACE,
  project_id: PROJECT,
  observation_span: [{ id: SPAN, trace_id: TRACE }],
  id: "provider-is-not-trace",
  provider_call_id: "different-provider-id",
};
const unavailable = "The source is unavailable or you do not have access.";
// What the host reports while this log's trace or call is open.
const SCOPE = JSON.stringify(["org-1", "ws-1", LOG]);
let client;
let nav;
let destination;
let user;
let openSpy;
let pushSpy;
let replaceSpy;
const destinationCalls = () =>
  axios.get.mock.calls.filter(
    ([url]) => url !== endpoints.develop.eval.getEvalLogs,
  );
const enrichmentCalls = () =>
  axios.get.mock.calls.filter(
    ([, config]) => config?.params?.include_source_navigation === "true",
  );
function mount(props = {}) {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const content = (nextProps) => (
    <EvalLogSourceHost logId={LOG} {...nextProps} />
  );
  // The app shows toasts through notistack's provider.
  const tree = (nextProps) => (
    <SnackbarProvider>
      <QueryClientProvider client={client}>
        {content(nextProps)}
      </QueryClientProvider>
    </SnackbarProvider>
  );
  const view = render(tree(props));
  return {
    ...view,
    rerender: (nextProps = props) => view.rerender(tree(nextProps)),
  };
}
// Annotate's save inside the viewer (AnnotationSidebarContent): the real
// useBulkCreateScores, called with onSuccess only. The hook shows its own
// generic toast on failure; the client's toast adds the server's message.
function SaveScore() {
  const { mutate } = useBulkCreateScores();
  return (
    <button
      type="button"
      onClick={() =>
        mutate(
          {
            sourceType: "observation_span",
            sourceId: SPAN,
            scores: [{ label_id: "label-1", value: 1 }],
            projectId: PROJECT,
          },
          { onSuccess: () => {} },
        )
      }
    >
      Save score
    </button>
  );
}
// Add to dataset's save inside the viewer (AddNewDataset): an inline
// mutation with onSuccess only and no meta, so only the client reports a
// failure.
function SaveToNewDataset() {
  const { mutate } = useMutation({
    mutationFn: (payload) =>
      axios.post(endpoints.project.addNewDataset, payload),
    onSuccess: () => {},
  });
  return (
    <button type="button" onClick={() => mutate({ new_dataset_name: "eval" })}>
      Add to new dataset
    </button>
  );
}
const PLAIN_READ = "/tracer/viewer-read/plain/";
const META_READ = "/tracer/viewer-read/meta/";
// Two reads a viewer might make: one with no meta, one with its own meta.
function ViewerReads() {
  useQuery({
    queryKey: ["viewer-read", "plain"],
    queryFn: () => axios.get(PLAIN_READ),
  });
  useQuery({
    queryKey: ["viewer-read", "meta"],
    queryFn: () => axios.get(META_READ),
    meta: { source: "viewer" },
  });
  return null;
}
async function activate(kind = "trace") {
  const button = await screen.findByRole("button", {
    name: kind === "trace" ? "View trace" : "View call",
    exact: true,
  });
  await user.click(button);
  return button;
}

describe("eval-owned source host", () => {
  configure({ asyncUtilTimeout: 5000 });
  afterAll(() => configure({ asyncUtilTimeout: 1000 }));
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.org = "org-1";
    mocks.ws = "ws-1";
    mocks.ViewerAction = null;
    nav = ready;
    destination = traceData;
    user = userEvent.setup();
    openSpy = vi.spyOn(window, "open").mockImplementation(() => null);
    pushSpy = vi.spyOn(window.history, "pushState");
    replaceSpy = vi.spyOn(window.history, "replaceState");
    axios.get.mockImplementation(async (url) => {
      // The host only ever asks for the opt-in enrichment.
      if (url === endpoints.develop.eval.getEvalLogs) return enriched(nav);
      if (destination instanceof Error || destination?.statusCode)
        throw destination;
      return envelope(destination);
    });
  });
  afterEach(() => {
    cleanup();
    client?.clear();
    vi.restoreAllMocks();
  });

  it("S01 revalidates before opening and pins the fresh trace/project/span instead of chips", async () => {
    mount();
    await screen.findByRole("button", { name: "View trace" });
    const fresh = {
      ...ready,
      project_id: "fresh-project",
      span_id: "fresh-span",
    };
    let resolve;
    axios.get.mockImplementationOnce(
      () =>
        new Promise((r) => {
          resolve = r;
        }),
    );
    await activate();
    expect(enrichmentCalls()).toHaveLength(2);
    expect(destinationCalls()).toHaveLength(0);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    destination = {
      trace: { id: TRACE, project: fresh.project_id },
      observation_spans: [
        {
          observation_span: {
            id: fresh.span_id,
            trace: TRACE,
            project: fresh.project_id,
          },
        },
      ],
    };
    await act(async () => resolve(enriched(fresh)));
    await screen.findByTestId("trace-body");
    expect(mocks.trace.mock.lastCall[0]).toMatchObject({
      open: true,
      traceId: TRACE,
      projectId: fresh.project_id,
      initialSpanId: fresh.span_id,
      // A saved Observe display filter must not hide the evaluated span.
      keepInitialSpanVisible: true,
      disablePortal: true,
      hideOpenInNewTab: true,
      hasPrev: false,
      hasNext: false,
    });
    expect(destinationCalls()).toHaveLength(1);
    expect(destinationCalls()[0]).toEqual([
      endpoints.project.getTrace(TRACE),
      { params: { project_id: fresh.project_id } },
    ]);
  });

  it("S02 opens only the Observe voice read with canonical identity and embedded body", async () => {
    nav = { ...ready, kind: "voice_call" };
    destination = voiceData;
    mount();
    await activate("voice_call");
    await screen.findByTestId("voice-body");
    expect(destinationCalls()).toEqual([
      [
        endpoints.project.getVoiceCallDetail,
        {
          params: {
            trace_id: TRACE,
            project_id: PROJECT,
            expected_span_id: SPAN,
          },
        },
      ],
    ]);
    expect(mocks.voice.mock.lastCall[0]).toMatchObject({
      embedded: true,
      data: { trace_id: TRACE, project_id: PROJECT, module: "project" },
    });
    expect(mocks.voice.mock.lastCall[0]).not.toHaveProperty("hiddenActionIds");
    expect(mocks.trace).not.toHaveBeenCalled();
    expect(
      axios.get.mock.calls.some(([url]) => /simulate|provider/.test(url)),
    ).toBe(false);
  });

  it("R1-02 opens the exact evaluated non-root collector span in a nested trace", async () => {
    destination = {
      ...traceData,
      observation_spans: [
        {
          observation_span: {
            id: "0123456789abcdef",
            trace: TRACE,
            project: PROJECT,
          },
          children: [
            {
              observation_span: {
                id: "fedcba9876543210",
                trace: TRACE,
                project: PROJECT,
              },
              children: traceData.observation_spans,
            },
          ],
        },
      ],
    };
    mount();
    await activate();
    await screen.findByTestId("trace-body");
    expect(mocks.trace.mock.lastCall[0].initialSpanId).toBe(SPAN);
  });

  it("R2-05 retains reachable host chrome when a successful refresh loses the span", async () => {
    mount();
    await activate();
    await screen.findByTestId("trace-body");
    await waitFor(() =>
      expect(
        screen
          .getByRole("button", { name: "Back to evaluation" })
          .closest(".MuiDrawer-paper"),
      ).not.toBeNull(),
    );
    const destinationClient = mocks.trace.mock.lastCall[1];
    destination = { ...traceData, observation_spans: [] };
    await act(async () => destinationClient.refetchQueries());
    await screen.findByText("This log's source reference is invalid.");
    expect(
      screen.getByRole("button", { name: "Back to evaluation" }),
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Close", exact: true }),
    ).toBeVisible();
    expect(getComputedStyle(screen.getByRole("dialog")).pointerEvents).not.toBe(
      "none",
    );
    await user.click(
      screen.getByRole("button", { name: "Back to evaluation" }),
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("R1-04 never paints a voice response missing the evaluated span after Retry", async () => {
    nav = { ...ready, kind: "voice_call" };
    destination = { statusCode: 503 };
    mount();
    await activate("voice_call");
    const retry = await screen.findByRole("button", { name: "Retry" });
    destination = {
      ...voiceData,
      observation_span: [{ id: "surviving-root", trace_id: TRACE }],
    };
    nav = { status: "unavailable" };
    await user.click(retry);
    await screen.findByText(unavailable);
    expect(mocks.voice).not.toHaveBeenCalled();
    expect(destinationCalls()).toHaveLength(2);
    expect(destinationCalls()[1][1].params.expected_span_id).toBe(SPAN);
  });

  it("R2-03 never paints a call whose destination read reports changed root membership", async () => {
    nav = { ...ready, kind: "voice_call" };
    destination = { statusCode: 409 };
    mount();
    await activate("voice_call");
    await screen.findByText("This log's source reference is invalid.");
    expect(mocks.voice).not.toHaveBeenCalled();
    expect(enrichmentCalls()).toHaveLength(3);
  });

  it("S03 refuses a revoked ready action and focuses the fresh status", async () => {
    mount();
    await screen.findByRole("button", { name: "View trace" });
    nav = { status: "unavailable" };
    await activate();
    expect(await screen.findByText(unavailable)).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveFocus();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(destinationCalls()).toHaveLength(0);
  });

  it.each([403, 404])(
    "S04 conceals unavailable voice destination (%s)",
    async (statusCode) => {
      nav = { ...ready, kind: "voice_call" };
      destination = { statusCode };
      mount();
      await activate("voice_call");
      expect(await screen.findByText(unavailable)).toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: "Retry" }),
      ).not.toBeInTheDocument();
      expect(mocks.voice).not.toHaveBeenCalled();
      expect(mocks.trace).not.toHaveBeenCalled();
    },
  );

  it("S05 retries the same voice destination once without automatic retries or cached content", async () => {
    nav = { ...ready, kind: "voice_call" };
    destination = { statusCode: 503 };
    mount();
    client.setQueryData(
      ["voiceCallDetail", TRACE, PROJECT],
      envelope(voiceData),
    );
    await activate("voice_call");
    const retry = await screen.findByRole("button", { name: "Retry" });
    expect(mocks.voice).not.toHaveBeenCalled();
    expect(destinationCalls()).toHaveLength(1);
    destination = voiceData;
    fireEvent.click(retry);
    fireEvent.click(retry);
    await screen.findByTestId("voice-body");
    expect(destinationCalls()).toHaveLength(2);
    expect(destinationCalls()[0]).toEqual(destinationCalls()[1]);
  });

  it.each(["trace", "voice_call"])(
    "S06 Escape closes only the %s host and restores trigger focus",
    async (kind) => {
      nav = { ...ready, kind };
      destination = kind === "trace" ? traceData : voiceData;
      const onSourceOpenChange = vi.fn();
      mount({ onSourceOpenChange });
      const trigger = await activate(kind);
      await screen.findByTestId(kind === "trace" ? "trace-body" : "voice-body");
      expect(screen.getByRole("dialog")).toContainElement(
        document.activeElement,
      );
      expect(onSourceOpenChange).toHaveBeenLastCalledWith(SCOPE);
      await user.keyboard("{Escape}");
      await waitFor(() =>
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
      );
      expect(trigger).toHaveFocus();
      expect(onSourceOpenChange).toHaveBeenLastCalledWith(null);
      expect(enrichmentCalls()).toHaveLength(2);
    },
  );

  it.each(["org", "ws", "log"])(
    "S07 closes synchronously on %s changes and ignores late destination data",
    async (field) => {
      nav = { ...ready, kind: "voice_call" };
      let resolve;
      const originalGet = axios.get.getMockImplementation();
      axios.get.mockImplementation((url, config) =>
        url === endpoints.project.getVoiceCallDetail
          ? new Promise((r) => {
              resolve = r;
            })
          : originalGet(url, config),
      );
      const view = mount();
      await activate("voice_call");
      await screen.findByRole("dialog");
      if (field !== "log") mocks[field] = "new-scope";
      view.rerender({ logId: field === "log" ? "new-log" : LOG });
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      await act(async () => resolve(envelope(voiceData)));
      expect(mocks.voice).not.toHaveBeenCalled();
      await screen.findByRole("button", { name: "View call" });
      expect(enrichmentCalls()).toHaveLength(3);
      axios.get.mockImplementation(originalGet);
      destination = voiceData;
      await activate("voice_call");
      await screen.findByTestId("voice-body");
      expect(enrichmentCalls()).toHaveLength(4);
      expect(destinationCalls()).toHaveLength(2);
    },
  );

  it.each(["trace", "voice_call"])(
    "S08 keeps %s navigation drawer-only without history writes",
    async (kind) => {
      nav = { ...ready, kind };
      destination = kind === "trace" ? traceData : voiceData;
      mount();
      const location = window.location.href;
      await activate(kind);
      await screen.findByTestId(kind === "trace" ? "trace-body" : "voice-body");
      expect(
        screen.queryByLabelText(/open in new tab/i),
      ).not.toBeInTheDocument();
      expect(screen.queryAllByRole("link")).toHaveLength(0);
      await user.click(
        screen.getByRole("button", { name: "Back to evaluation", exact: true }),
      );
      expect(openSpy).not.toHaveBeenCalled();
      expect(pushSpy).not.toHaveBeenCalled();
      expect(replaceSpy).not.toHaveBeenCalled();
      expect(window.location.href).toBe(location);
    },
  );

  it.each(["trace", "voice_call"])(
    "S10 keeps host Close focusable at 360px for %s",
    async (kind) => {
      vi.stubGlobal("innerWidth", 360);
      nav = { ...ready, kind };
      destination = kind === "trace" ? traceData : voiceData;
      mount();
      await activate(kind);
      await screen.findByTestId(kind === "trace" ? "trace-body" : "voice-body");
      const close = within(screen.getByRole("dialog")).getByRole("button", {
        name: "Close",
        exact: true,
      });
      act(() => close.focus());
      expect(close).toHaveFocus();
      await user.keyboard("{Enter}");
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      vi.unstubAllGlobals();
    },
  );

  it.each(["unavailable", "ambiguous_reference", "ready"])(
    "S05 revalidates a generic trace 400 and renders %s correctly",
    async (status) => {
      axios.get.mockImplementation(async (url) => {
        if (url === endpoints.develop.eval.getEvalLogs)
          return enriched(
            enrichmentCalls().length >= 3 && status !== "ready"
              ? { status }
              : ready,
          );
        throw { statusCode: 400 };
      });
      mount();
      await activate();
      const expected =
        status === "unavailable"
          ? unavailable
          : status === "ambiguous_reference"
            ? "Can't tell which trace or call this evaluation ran on."
            : "Source details could not be loaded. Try again.";
      expect(await screen.findByText(expected)).toBeInTheDocument();
      expect(enrichmentCalls()).toHaveLength(3);
      expect(mocks.trace).not.toHaveBeenCalled();
    },
  );

  it("S04 rejects a voice payload whose trace differs from the pinned identity", async () => {
    nav = { ...ready, kind: "voice_call" };
    destination = { ...voiceData, trace_id: "different-trace" };
    mount();
    await activate("voice_call");
    expect(
      await screen.findByText("This log's source reference is invalid."),
    ).toBeInTheDocument();
    expect(mocks.voice).not.toHaveBeenCalled();
  });

  it("S04 rejects a voice destination project mismatch", async () => {
    nav = { ...ready, kind: "voice_call" };
    destination = { ...voiceData, project_id: "other-project" };
    mount();
    await activate("voice_call");
    expect(
      await screen.findByText("This log's source reference is invalid."),
    ).toBeInTheDocument();
    expect(mocks.voice).not.toHaveBeenCalled();
  });

  it("S04 revalidates a changed trace destination kind without switching viewers", async () => {
    destination = {
      ...traceData,
      observation_spans: [
        {
          observation_span: {
            id: SPAN,
            trace: TRACE,
            parent_span_id: null,
            observation_type: "conversation",
          },
        },
      ],
    };
    mount();
    await activate();
    expect(
      await screen.findByText("This log's source reference is invalid."),
    ).toBeInTheDocument();
    expect(enrichmentCalls()).toHaveLength(3);
    expect(mocks.trace).not.toHaveBeenCalled();
    expect(mocks.voice).not.toHaveBeenCalled();
  });

  it("S04 returns to the unavailable status instead of the old ready action after destination denial", async () => {
    nav = { ...ready, kind: "voice_call" };
    destination = { statusCode: 403 };
    mount();
    await activate("voice_call");
    await screen.findByText(unavailable);
    await user.click(
      screen.getByRole("button", { name: "Back to evaluation" }),
    );
    expect(
      screen.queryByRole("button", { name: "View call" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(unavailable);
    expect(screen.getByRole("status")).toHaveFocus();
  });

  it.each(["trace", "project", "span"])(
    "S04 revalidates a mismatched trace destination %s without painting it",
    async (field) => {
      destination = { ...traceData, trace: { ...traceData.trace } };
      if (field === "trace") destination.trace.id = "other-trace";
      if (field === "project") destination.trace.project = "other-project";
      if (field === "span") destination.observation_spans = [];
      mount();
      await activate();
      expect(
        await screen.findByText("This log's source reference is invalid."),
      ).toBeInTheDocument();
      expect(enrichmentCalls()).toHaveLength(3);
      expect(mocks.trace).not.toHaveBeenCalled();
    },
  );

  it("S09 shows a denied enrichment request as unavailable, with no link", async () => {
    axios.get.mockImplementation(async () => {
      throw { statusCode: 403 };
    });
    mount();
    await screen.findByText(unavailable);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.queryAllByRole("link")).toHaveLength(0);
    expect(destinationCalls()).toHaveLength(0);
  });

  it("S07 cannot reopen after unmount during activation", async () => {
    const view = mount();
    await screen.findByRole("button", { name: "View trace" });
    let resolve;
    axios.get.mockImplementationOnce(
      () =>
        new Promise((r) => {
          resolve = r;
        }),
    );
    await activate();
    const signal = enrichmentCalls()[1][1].signal;
    view.unmount();
    expect(signal.aborted).toBe(true);
    await act(async () => resolve(enriched(ready)));
    expect(destinationCalls()).toHaveLength(0);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it.each(["org", "ws", "log"])(
    "B1 reports the opened scope from opening and closing, and nothing when a %s change remounts it",
    async (field) => {
      const onSourceOpenChange = vi.fn();
      const view = mount({ onSourceOpenChange });
      await screen.findByRole("button", { name: "View trace" });
      expect(onSourceOpenChange).not.toHaveBeenCalled();
      await activate();
      await screen.findByTestId("trace-body");
      expect(onSourceOpenChange.mock.calls).toEqual([[SCOPE]]);
      await user.click(
        within(screen.getByRole("dialog")).getByRole("button", {
          name: "Close",
          exact: true,
        }),
      );
      await waitFor(() =>
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
      );
      expect(onSourceOpenChange.mock.calls).toEqual([[SCOPE], [null]]);
      await activate();
      await screen.findByTestId("trace-body");
      // The page compares this scope with its opened log, so a remount that
      // drops the open trace needs no report.
      if (field !== "log") mocks[field] = "new-scope";
      view.rerender({
        onSourceOpenChange,
        logId: field === "log" ? "new-log" : LOG,
      });
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      await screen.findByRole("button", { name: "View trace" });
      expect(onSourceOpenChange.mock.calls).toEqual([[SCOPE], [null], [SCOPE]]);
    },
  );

  it.each(["trace", "voice_call"])(
    "S3 a failed save inside the opened %s shows the app's error toast",
    async (kind) => {
      nav = { ...ready, kind };
      destination = kind === "trace" ? traceData : voiceData;
      mocks.ViewerAction = SaveScore;
      axios.post.mockRejectedValueOnce({
        statusCode: 400,
        result: "This label is archived.",
      });
      mount();
      await activate(kind);
      await screen.findByTestId(kind === "trace" ? "trace-body" : "voice-body");
      await user.click(screen.getByRole("button", { name: "Save score" }));
      expect(
        await screen.findByText("This label is archived."),
      ).toBeInTheDocument();
      // The hook's own generic toast stays alongside it, as in Observe.
      expect(
        screen.getByText("Failed to save annotations"),
      ).toBeInTheDocument();
      expect(axios.post).toHaveBeenCalledTimes(1);
      expect(axios.post.mock.calls[0][0]).toMatch(
        /\/model-hub\/scores\/bulk\/$/,
      );
      // The viewer stays open behind the toast.
      expect(screen.getByRole("dialog")).toBeInTheDocument();
    },
  );

  it.each(["trace", "voice_call"])(
    "S3 a failed Add to dataset inside the opened %s, which reports nothing itself, shows the app's error toast",
    async (kind) => {
      nav = { ...ready, kind };
      destination = kind === "trace" ? traceData : voiceData;
      mocks.ViewerAction = SaveToNewDataset;
      axios.post.mockRejectedValueOnce({
        statusCode: 400,
        result: "A dataset with this name already exists in your organization.",
      });
      mount();
      await activate(kind);
      await screen.findByTestId(kind === "trace" ? "trace-body" : "voice-body");
      await user.click(
        screen.getByRole("button", { name: "Add to new dataset" }),
      );
      expect(
        await screen.findByText(
          "A dataset with this name already exists in your organization.",
        ),
      ).toBeInTheDocument();
      expect(axios.post).toHaveBeenCalledTimes(1);
      expect(axios.post.mock.calls[0][0]).toBe(endpoints.project.addNewDataset);
      expect(screen.getByRole("dialog")).toBeInTheDocument();
    },
  );

  it("a failed viewer read toasts only when the query sets its own meta", async () => {
    mocks.ViewerAction = ViewerReads;
    const base = axios.get.getMockImplementation();
    axios.get.mockImplementation(async (url, config) => {
      if (url === PLAIN_READ)
        throw { statusCode: 404, result: "Plain viewer read failed." };
      if (url === META_READ)
        throw { statusCode: 404, result: "Viewer read with meta failed." };
      return base(url, config);
    });
    mount();
    await activate();
    await screen.findByTestId("trace-body");
    expect(
      await screen.findByText("Viewer read with meta failed."),
    ).toBeInTheDocument();
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    expect(axios.get.mock.calls.some(([url]) => url === PLAIN_READ)).toBe(true);
    expect(
      screen.queryByText("Plain viewer read failed."),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it.each(["trace", "voice_call"])(
    "S3 keeps the host's own failed %s read in its row, with no toast",
    async (kind) => {
      nav = { ...ready, kind };
      destination = { statusCode: 404, result: "Trace not found." };
      mount();
      await activate(kind);
      expect(await screen.findByText(unavailable)).toBeInTheDocument();
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 50));
      });
      expect(screen.queryByText("Trace not found.")).not.toBeInTheDocument();
      expect(
        screen.queryByText("Something went wrong"),
      ).not.toBeInTheDocument();
    },
  );
});
