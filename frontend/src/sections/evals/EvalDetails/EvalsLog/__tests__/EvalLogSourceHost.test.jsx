import React from "react";
import {
  QueryClient,
  QueryClientProvider,
  useQueryClient,
} from "@tanstack/react-query";
import { Drawer } from "@mui/material";
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
import DrawerHeader from "src/components/traceDetail/DrawerHeader";
import EvalLogSourceHost from "../EvalLogSourceHost";
import LogsDrawer from "../LogsDrawer";

const mocks = vi.hoisted(() => ({
  org: "org-1",
  ws: "ws-1",
  trace: vi.fn(),
  voice: vi.fn(),
  feedback: vi.fn(),
  feedbackMutation: vi.fn(),
  refreshGrid: vi.fn(),
  closeLog: vi.fn(),
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ currentOrganizationId: mocks.org }),
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceId: mocks.ws }),
}));
vi.mock("src/utils/axios", async () => {
  const actual = await vi.importActual("src/utils/axios");
  return { ...actual, default: { ...actual.default, get: vi.fn() } };
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
      </Drawer>
    );
  },
}));
vi.mock("src/components/VoiceDetailDrawerV2/VoiceDetailDrawerV2", () => ({
  default: function VoiceStub(props) {
    mocks.voice(props, useQueryClient());
    return <div data-testid="voice-body">Call content</div>;
  },
}));
vi.mock("../LogDrawerRight", () => ({
  default: ({ output, addFeedbackClick }) => (
    <div>
      <span data-testid="eval-output">{JSON.stringify(output)}</span>
      <button onClick={addFeedbackClick}>Add feedback</button>
    </div>
  ),
}));
vi.mock("src/sections/common/DatapointCard", () => ({
  default: ({ value }) => <div>{value.cellValue}</div>,
}));
vi.mock("../../EvalsFeedback/AddEvalsFeedbackDrawer", () => ({
  default: (props) => {
    mocks.feedback(props);
    return props.open ? (
      <button onClick={mocks.feedbackMutation}>Submit feedback</button>
    ) : null;
  },
}));

const LOG = "11111111-1111-4111-8111-111111111111";
const TRACE = "22222222-2222-4222-8222-222222222222";
const PROJECT = "33333333-3333-4333-8333-333333333333";
const SPAN = "44444444-4444-4444-8444-444444444444";
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
  observation_spans: [{ observation_span: { id: SPAN, trace: TRACE } }],
};
const voiceData = {
  trace_id: TRACE,
  id: "provider-is-not-trace",
  provider_call_id: "different-provider-id",
};
const baseLog = {
  log_id: LOG,
  evaluation_id: LOG,
  trace_id: "copy-only-trace",
  span_id: "copy-only-span",
  source: "Trace",
  output: { score: 0.3, reason: "Stored reason" },
  required_keys: ["prompt"],
  values: { prompt: "Stored input" },
};
const unavailable = "The source is unavailable or you do not have access.";
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
function mount(logs = false, props = {}) {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const content = (nextProps) =>
    logs ? (
      <LogsDrawer
        open
        selectedRow={{ logId: LOG, order: "desc" }}
        evalsId="eval-1"
        onClose={mocks.closeLog}
        refreshGrid={mocks.refreshGrid}
        {...nextProps}
      />
    ) : (
      <EvalLogSourceHost logId={LOG} {...nextProps} />
    );
  const view = render(
    <QueryClientProvider client={client}>{content(props)}</QueryClientProvider>,
  );
  return {
    ...view,
    rerender: (nextProps = props) =>
      view.rerender(
        <QueryClientProvider client={client}>
          {content(nextProps)}
        </QueryClientProvider>,
      ),
  };
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
    nav = ready;
    destination = traceData;
    user = userEvent.setup();
    openSpy = vi.spyOn(window, "open").mockImplementation(() => null);
    pushSpy = vi.spyOn(window.history, "pushState");
    replaceSpy = vi.spyOn(window.history, "replaceState");
    axios.get.mockImplementation(async (url, config) => {
      if (url === endpoints.develop.eval.getEvalLogs)
        return config.params.include_source_navigation
          ? enriched(nav)
          : envelope(baseLog);
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
        { observation_span: { id: fresh.span_id, trace: TRACE } },
      ],
    };
    await act(async () => resolve(enriched(fresh)));
    await screen.findByTestId("trace-body");
    expect(mocks.trace.mock.lastCall[0]).toMatchObject({
      open: true,
      traceId: TRACE,
      projectId: fresh.project_id,
      initialSpanId: fresh.span_id,
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
        { params: { trace_id: TRACE, project_id: PROJECT } },
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
      mount(true);
      const trigger = await activate(kind);
      await screen.findByTestId(kind === "trace" ? "trace-body" : "voice-body");
      expect(screen.getByRole("dialog")).toContainElement(
        document.activeElement,
      );
      expect(
        screen.queryByRole("button", { name: "Add feedback" }),
      ).not.toBeInTheDocument();
      await user.keyboard("{Escape}");
      await waitFor(() =>
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
      );
      expect(trigger).toHaveFocus();
      expect(mocks.closeLog).not.toHaveBeenCalled();
      expect(mocks.refreshGrid).not.toHaveBeenCalled();
      expect(enrichmentCalls()).toHaveLength(2);
      expect(screen.getByTestId("eval-output")).toHaveTextContent(
        JSON.stringify(baseLog.output),
      );
      await user.keyboard("{Escape}");
      expect(mocks.closeLog).toHaveBeenCalledOnce();
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

  it("S09 preserves base output, input and feedback props throughout source retry/open/close", async () => {
    nav = { ...ready, kind: "voice_call" };
    destination = { statusCode: 503 };
    mount(true);
    await screen.findByRole("button", { name: "View call" });
    const feedbackProps = mocks.feedback.mock.lastCall[0];
    await activate("voice_call");
    destination = voiceData;
    await user.click(await screen.findByRole("button", { name: "Retry" }));
    await screen.findByTestId("voice-body");
    await user.click(
      screen.getByRole("button", { name: "Back to evaluation" }),
    );
    expect(screen.getByTestId("eval-output")).toHaveTextContent(
      JSON.stringify(baseLog.output),
    );
    expect(screen.getByText("Stored input")).toBeInTheDocument();
    expect(mocks.feedback.mock.lastCall[0]).toEqual(feedbackProps);
    expect(feedbackProps.selectedAddFeedback).toEqual({
      id: LOG,
      ...baseLog.output,
    });
    expect(mocks.feedbackMutation).not.toHaveBeenCalled();
    expect(mocks.refreshGrid).not.toHaveBeenCalled();
    expect(
      axios.get.mock.calls.filter(
        ([, config]) => config?.params?.source === "logs",
      ),
    ).toHaveLength(1);
    await user.click(screen.getByRole("button", { name: "Add feedback" }));
    expect(mocks.feedback.mock.lastCall[0].open).toBe(true);
  });

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
            ? "This log's source cannot be identified uniquely."
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

  it("S09 keeps the evaluation when only the enrichment request is denied", async () => {
    axios.get.mockImplementation(async (_url, config) => {
      if (config?.params?.include_source_navigation) throw { statusCode: 403 };
      return envelope(baseLog);
    });
    mount(true);
    await screen.findByText(unavailable);
    expect(screen.getByTestId("eval-output")).toBeInTheDocument();
    expect(screen.getByText("Stored input")).toBeInTheDocument();
    expect(screen.queryByText("Evaluation log is unavailable.")).not.toBeInTheDocument();
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

  it("S09 waits for the base log and does not enrich CE empty results or closed rows", async () => {
    const view = mount(true, { open: false });
    expect(axios.get).not.toHaveBeenCalled();
    axios.get.mockResolvedValue(envelope([]));
    view.rerender({ open: true });
    await waitFor(() => expect(axios.get).toHaveBeenCalledOnce());
    expect(enrichmentCalls()).toHaveLength(0);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("S07 partitions base evaluation content and cancels its old request on scope change", async () => {
    let resolve;
    axios.get.mockImplementationOnce(
      () =>
        new Promise((r) => {
          resolve = r;
        }),
    );
    const view = mount(true);
    const signal = axios.get.mock.calls[0][1].signal;
    mocks.ws = "ws-2";
    axios.get.mockResolvedValue(envelope([]));
    view.rerender();
    expect(signal.aborted).toBe(true);
    await act(async () => resolve(envelope(baseLog)));
    expect(screen.queryByTestId("eval-output")).not.toBeInTheDocument();
    expect(enrichmentCalls()).toHaveLength(0);
    expect(
      client.getQueryCache().find({
        queryKey: ["evalslogsData", "org-1", "ws-2", LOG],
        exact: true,
      }),
    ).toBeDefined();
  });
});
