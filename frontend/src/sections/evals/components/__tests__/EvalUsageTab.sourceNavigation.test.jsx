// The routed eval page (/dashboard/evaluations/:evalId) lists an eval's logs in
// its Usage tab. Opening an Observe log there offers View trace or View call
// for exactly the evaluated source. The real panel, source host, row and
// react-query run here; the list hooks, chart and the two viewers are stubbed.
import React from "react";
import PropTypes from "prop-types";
import { Drawer } from "@mui/material";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  cleanup,
  configure,
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
import EvalUsageTab from "../EvalUsageTab";

const h = vi.hoisted(() => ({
  org: "org-1",
  ws: "ws-1",
  logs: {},
  trace: vi.fn(),
  voice: vi.fn(),
  feedback: vi.fn(),
}));

vi.mock("../../hooks/useEvalUsage", () => ({
  useEvalUsageChart: () => ({
    data: undefined,
    isLoading: false,
    isFetching: false,
    isError: false,
    refresh: vi.fn(),
  }),
  useEvalUsageLogs: () => h.logs,
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ currentOrganizationId: h.org }),
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceId: h.ws }),
}));
vi.mock("src/utils/axios", async () => {
  const actual = await vi.importActual("src/utils/axios");
  return { ...actual, default: { ...actual.default, get: vi.fn() } };
});
vi.mock("src/components/iconify", () => ({ default: () => <span /> }));
vi.mock("src/sections/projects/DateTimeRangePicker", () => ({
  default: () => null,
}));
vi.mock("../UsageChart", () => ({ default: () => null }));
vi.mock("src/components/data-table", () => {
  // One button per listed log; clicking it is clicking the row.
  const MockDataTable = ({ data, onRowClick }) => (
    <div data-testid="usage-table">
      {data.map((row) => (
        <button key={row.id} type="button" onClick={() => onRowClick(row)}>
          {`Open ${row.input}`}
        </button>
      ))}
    </div>
  );
  MockDataTable.propTypes = {
    data: PropTypes.array,
    onRowClick: PropTypes.func,
  };
  return { DataTable: MockDataTable, DataTablePagination: () => null };
});
vi.mock("../../Helpers/evalUsageColumns", () => ({
  COLUMN_CONFIG_URL_PARAM: "columns",
  DATE_OPTION_TO_PERIOD: { "30D": "30d" },
  DEFAULT_COLUMN_CONFIG: [],
  ScoreCell: () => null,
  StatPill: () => null,
  columnConfigStorageKey: () => "eval-usage-columns",
  decodeColumnConfig: () => null,
  encodeColumnConfig: () => "",
  normalizeRow: (row) => row,
  periodForRange: () => "30d",
  useColumns: () => [],
}));
vi.mock("src/components/FormSearchField/FormSearchField", () => ({
  default: () => null,
}));
vi.mock("src/components/tooltip", () => ({
  default: ({ children }) => children,
}));
vi.mock("src/components/ColumnDropdown/ColumnDropdown", () => ({
  default: () => null,
}));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock(
  "src/sections/evals/EvalDetails/EvalsFeedback/AddEvalsFeedbackDrawer",
  () => ({
    default: (props) => {
      h.feedback(props);
      return null;
    },
  }),
);
vi.mock("src/sections/common/EvalsTasks/PartialInputWarningDetails", () => ({
  default: () => null,
}));
vi.mock("@monaco-editor/react", () => ({ default: () => null }));
vi.mock("src/hooks/use-debounce", () => ({ useDebounce: (value) => value }));
vi.mock("src/hooks/use-local-storage", () => ({
  getStorage: () => null,
  setStorage: vi.fn(),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "editor" }),
}));
vi.mock("src/utils/rolePermissionMapping", () => ({
  PERMISSIONS: { EDIT_CREATE_DELETE_EVALS: "edit" },
  RolePermission: { EVALS: { edit: { editor: true } } },
}));
vi.mock("src/components/traceDetail/TraceDetailDrawerV2", () => ({
  default: function TraceStub(props) {
    h.trace(props);
    return (
      <Drawer open variant="persistent" anchor="right">
        <div data-testid="trace-body">{props.initialSpanId}</div>
      </Drawer>
    );
  },
}));
vi.mock("src/components/VoiceDetailDrawerV2/VoiceDetailDrawerV2", () => ({
  default: function VoiceStub(props) {
    h.voice(props);
    return <div data-testid="voice-body">{props.data.trace_id}</div>;
  },
}));

const TEMPLATE = "99999999-9999-4999-8999-999999999999";
const PROJECT = "33333333-3333-4333-8333-333333333333";
const TRACE = "22222222-2222-4222-8222-222222222222";
const VOICE_PROJECT = "55555555-5555-4555-8555-555555555555";
const VOICE_TRACE = "44444444-4444-4444-8444-444444444444";
// Collector span ids (16 hex): root -> child -> grandchild in one trace.
const ROOT_SPAN = "8ee58a04ec11dc74";
const CHILD_SPAN = "82970b1c0369b467";
const GRANDCHILD_SPAN = "4c60de46921c8fc9";
const VOICE_SPAN = "096436936191a409";
const LOGS = {
  root: "10000000-0000-4000-8000-000000000001",
  child: "10000000-0000-4000-8000-000000000002",
  grandchild: "10000000-0000-4000-8000-000000000003",
  voice: "10000000-0000-4000-8000-000000000004",
  deleted: "10000000-0000-4000-8000-000000000005",
  playground: "10000000-0000-4000-8000-000000000006",
};
const traceNav = (span) => ({
  status: "ready",
  kind: "trace",
  project_id: PROJECT,
  trace_id: TRACE,
  span_id: span,
  retryable: false,
});
const NAV = {
  [LOGS.root]: traceNav(ROOT_SPAN),
  [LOGS.child]: traceNav(CHILD_SPAN),
  [LOGS.grandchild]: traceNav(GRANDCHILD_SPAN),
  [LOGS.voice]: {
    status: "ready",
    kind: "voice_call",
    project_id: VOICE_PROJECT,
    trace_id: VOICE_TRACE,
    span_id: VOICE_SPAN,
    retryable: false,
  },
  [LOGS.deleted]: {
    status: "unavailable",
    kind: null,
    project_id: null,
    trace_id: null,
    span_id: null,
    retryable: false,
  },
};
const span = (id) => ({ id, trace: TRACE, project: PROJECT });
const traceData = {
  trace: { id: TRACE, project: PROJECT },
  observation_spans: [
    {
      observation_span: span(ROOT_SPAN),
      children: [
        {
          observation_span: { ...span(CHILD_SPAN), parent_span_id: ROOT_SPAN },
          children: [
            {
              observation_span: {
                ...span(GRANDCHILD_SPAN),
                parent_span_id: CHILD_SPAN,
              },
            },
          ],
        },
      ],
    },
  ],
};
const voiceData = {
  trace_id: VOICE_TRACE,
  project_id: VOICE_PROJECT,
  observation_span: [{ id: VOICE_SPAN, trace_id: VOICE_TRACE }],
};
const row = (key, extra = {}) => ({
  id: LOGS[key],
  input: `${key} log`,
  score: 1,
  result: "Passed",
  reason: `judged the ${key} span`,
  status: "success",
  source: "tracer",
  detail: { output: { reason: `judged the ${key} span` } },
  ...extra,
});
const table = () => [
  row("root"),
  row("child"),
  row("grandchild"),
  row("voice"),
  row("deleted"),
  row("playground", { source: "eval_playground" }),
];
const logsState = (rows = table()) => ({
  data: {
    table: rows,
    pagination: { total: rows.length },
    queryPending: false,
  },
  isLoading: false,
  isFetching: false,
  isError: false,
  refresh: vi.fn(),
});
const envelope = (result) => ({ data: { result } });
const UNAVAILABLE = "The source is unavailable or you do not have access.";

const enrichmentCalls = () =>
  axios.get.mock.calls.filter(
    ([url, config]) =>
      url === endpoints.develop.eval.getEvalLogs &&
      config?.params?.include_source_navigation === "true",
  );
const destinationCalls = () =>
  axios.get.mock.calls.filter(
    ([url]) => url !== endpoints.develop.eval.getEvalLogs,
  );

let user;
let client;
function mount() {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const tree = () => (
    <QueryClientProvider client={client}>
      <EvalUsageTab templateId={TEMPLATE} />
    </QueryClientProvider>
  );
  const view = render(tree());
  return { ...view, rerender: () => view.rerender(tree()) };
}
async function openLog(key) {
  await user.click(screen.getByRole("button", { name: `Open ${key} log` }));
  await screen.findByText(`judged the ${key} span`, { selector: "p" });
}
const position = (n) => screen.getByText(`${n} / ${table().length}`);
// The feedback drawer's data props (its onClose is a new function per render).
const feedbackData = ({
  open,
  selectedAddFeedback,
  output,
  evalsId,
  existingFeedback,
}) => ({
  open,
  selectedAddFeedback,
  output,
  evalsId,
  existingFeedback,
});

describe("Usage tab: View trace / View call on an opened eval log", () => {
  configure({ asyncUtilTimeout: 5000 });
  afterAll(() => configure({ asyncUtilTimeout: 1000 }));
  beforeEach(() => {
    vi.clearAllMocks();
    user = userEvent.setup();
    h.org = "org-1";
    h.ws = "ws-1";
    h.logs = logsState();
    axios.get.mockImplementation(async (url, config) => {
      if (url === endpoints.develop.eval.getEvalLogs)
        return envelope({
          log_id: config.params.log_id,
          source_navigation: NAV[config.params.log_id],
        });
      if (url === endpoints.project.getTrace(TRACE)) return envelope(traceData);
      if (url === endpoints.project.getVoiceCallDetail)
        return envelope(voiceData);
      throw new Error(`unexpected request ${url}`);
    });
  });
  afterEach(() => {
    cleanup();
    client?.clear();
  });

  it.each([
    ["root", ROOT_SPAN],
    ["child", CHILD_SPAN],
    ["grandchild", GRANDCHILD_SPAN],
  ])(
    "the %s span's log offers View trace and opens exactly that span",
    async (key, spanId) => {
      mount();
      await openLog(key);
      await user.click(
        await screen.findByRole("button", { name: "View trace" }),
      );
      const dialog = await screen.findByRole("dialog");
      expect(await within(dialog).findByTestId("trace-body")).toHaveTextContent(
        spanId,
      );
      expect(h.trace.mock.lastCall[0]).toMatchObject({
        traceId: TRACE,
        projectId: PROJECT,
        initialSpanId: spanId,
        hideOpenInNewTab: true,
      });
      // The opt-in is asked for this log only: once to show, once on click.
      expect(enrichmentCalls().map(([, c]) => c.params)).toEqual([
        { log_id: LOGS[key], include_source_navigation: "true" },
        { log_id: LOGS[key], include_source_navigation: "true" },
      ]);
      expect(destinationCalls().map(([url, c]) => [url, c.params])).toEqual([
        [endpoints.project.getTrace(TRACE), { project_id: PROJECT }],
      ]);
    },
  );

  it("the voice call's log offers View call and opens that call only", async () => {
    mount();
    await openLog("voice");
    await user.click(await screen.findByRole("button", { name: "View call" }));
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByTestId("voice-body")).toHaveTextContent(
      VOICE_TRACE,
    );
    expect(h.voice.mock.lastCall[0]).toMatchObject({
      embedded: true,
      data: {
        trace_id: VOICE_TRACE,
        project_id: VOICE_PROJECT,
        module: "project",
      },
    });
    expect(destinationCalls().map(([url, c]) => [url, c.params])).toEqual([
      [
        endpoints.project.getVoiceCallDetail,
        {
          trace_id: VOICE_TRACE,
          project_id: VOICE_PROJECT,
          expected_span_id: VOICE_SPAN,
        },
      ],
    ]);
    expect(h.trace).not.toHaveBeenCalled();
  });

  it("a deleted source shows the unavailable state with no link, and the log closes", async () => {
    mount();
    await openLog("deleted");
    expect(await screen.findByText(UNAVAILABLE)).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /View (trace|call)/ }),
    ).not.toBeInTheDocument();
    expect(screen.queryAllByRole("link")).toHaveLength(0);
    expect(destinationCalls()).toHaveLength(0);
    // The evaluation itself stays readable.
    expect(screen.getByText("judged the deleted span", { selector: "p" }));
    await user.click(screen.getByRole("button", { name: "Close log" }));
    await waitFor(() =>
      expect(
        screen.queryByText("judged the deleted span", { selector: "p" }),
      ).not.toBeInTheDocument(),
    );
  });

  it("a non-Observe log shows no source row and asks for no source", async () => {
    mount();
    await openLog("playground");
    expect(screen.getByText("Playground")).toBeInTheDocument();
    expect(screen.queryByText("Checking source...")).not.toBeInTheDocument();
    expect(screen.queryByText("Source:")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /View (trace|call)/ }),
    ).not.toBeInTheDocument();
    expect(axios.get).not.toHaveBeenCalled();
  });

  it.each(["Back to evaluation", "Close"])(
    "%s returns to the same log with its evaluation and feedback untouched",
    async (name) => {
      mount();
      await openLog("child");
      const feedbackProps = feedbackData(h.feedback.mock.lastCall[0]);
      const trigger = await screen.findByRole("button", { name: "View trace" });
      await user.click(trigger);
      await screen.findByTestId("trace-body");
      await user.click(
        within(screen.getByRole("dialog")).getByRole("button", {
          name,
          exact: true,
        }),
      );
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      expect(position(2)).toBeInTheDocument();
      expect(screen.getByText("judged the child span", { selector: "p" }));
      expect(
        screen.getByRole("button", { name: "Add Feedback" }),
      ).toBeEnabled();
      expect(feedbackData(h.feedback.mock.lastCall[0])).toEqual(feedbackProps);
      expect(feedbackProps).toMatchObject({
        open: false,
        selectedAddFeedback: { id: LOGS.child },
        evalsId: TEMPLATE,
      });
      expect(trigger).toHaveFocus();
    },
  );

  it("while a trace is open, j/k and Escape belong to it; then Escape closes the log", async () => {
    mount();
    await openLog("root");
    await user.click(await screen.findByRole("button", { name: "View trace" }));
    await screen.findByTestId("trace-body");
    expect(position(1)).toBeInTheDocument();

    await user.keyboard("j");
    await user.keyboard("k");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(h.trace.mock.lastCall[0].initialSpanId).toBe(ROOT_SPAN);

    await user.keyboard("{Escape}");
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    // Only the trace closed: the same log is still open.
    expect(position(1)).toBeInTheDocument();
    expect(screen.getByText("judged the root span", { selector: "p" }));

    // With the trace closed the panel's own shortcuts work again.
    await user.keyboard("j");
    expect(position(2)).toBeInTheDocument();
    await user.keyboard("{Escape}");
    await waitFor(() =>
      expect(screen.queryByText(/ \/ 6$/)).not.toBeInTheDocument(),
    );
  });

  it("leaves a key that another handler already took to that handler", async () => {
    mount();
    await openLog("root");
    expect(position(1)).toBeInTheDocument();
    // Stands in for the open trace's own Escape (DrawerHeader's document
    // listener), which marks the key handled before it reaches window.
    const takeKey = (e) => e.preventDefault();
    document.addEventListener("keydown", takeKey);
    try {
      await user.keyboard("j");
      expect(position(1)).toBeInTheDocument();
      await user.keyboard("{Escape}");
      expect(position(1)).toBeInTheDocument();
      expect(
        screen.getByText("judged the root span", { selector: "p" }),
      ).toBeInTheDocument();
    } finally {
      document.removeEventListener("keydown", takeKey);
    }
    // Keys nothing else took still drive the panel.
    await user.keyboard("j");
    expect(position(2)).toBeInTheDocument();
    await user.keyboard("{Escape}");
    await waitFor(() =>
      expect(screen.queryByText(/ \/ 6$/)).not.toBeInTheDocument(),
    );
  });

  it("keeps an open trace while the Usage list refreshes the same rows", async () => {
    const view = mount();
    await openLog("grandchild");
    await user.click(await screen.findByRole("button", { name: "View trace" }));
    const body = await screen.findByTestId("trace-body");
    h.logs = logsState();
    await act(async () => view.rerender());
    expect(body.isConnected).toBe(true);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(enrichmentCalls()).toHaveLength(2);
  });

  it.each(["org", "ws"])(
    "an %s switch with a trace open hands j/k and Escape back to the panel",
    async (field) => {
      const view = mount();
      await openLog("root");
      await user.click(
        await screen.findByRole("button", { name: "View trace" }),
      );
      await screen.findByTestId("trace-body");
      h[field] = `${field}-2`;
      await act(async () => view.rerender());
      // The host remounted for the new scope; its trace is gone.
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      await screen.findByRole("button", { name: "View trace" });
      await user.keyboard("j");
      expect(position(2)).toBeInTheDocument();
      await user.keyboard("{Escape}");
      await waitFor(() =>
        expect(screen.queryByText(/ \/ 6$/)).not.toBeInTheDocument(),
      );
    },
  );

  it("a trace dropped by a list change leaves the log's shortcuts working when it returns", async () => {
    const view = mount();
    await openLog("root");
    await user.click(await screen.findByRole("button", { name: "View trace" }));
    await screen.findByTestId("trace-body");
    // A refresh puts another log at the opened position: the host remounts
    // for that log and the root's trace closes without a Close click.
    const [root, child, ...rest] = table();
    h.logs = logsState([child, root, ...rest]);
    await act(async () => view.rerender());
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByText("judged the child span", { selector: "p" }));
    // The next refresh brings the root log back to that position.
    h.logs = logsState();
    await act(async () => view.rerender());
    expect(screen.getByText("judged the root span", { selector: "p" }));
    await screen.findByRole("button", { name: "View trace" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await user.keyboard("j");
    expect(position(2)).toBeInTheDocument();
    expect(screen.getByText("judged the child span", { selector: "p" }));
  });
});
