// The Trajectory and Conversation tabs read only what the backend returns
// for the row's source. A live trace is read in the source's own project and
// "Open original" opens that same trace; an expired source, or a trace or
// session the tracer refuses (deleted since, or another tenant's), shows the
// copy saved at import and never a trace.
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { h, Null } = vi.hoisted(() => ({
  h: { get: null, post: null, drawer: null, projectSupported: false },
  Null: () => null,
}));

vi.mock("src/utils/axios", async (importOriginal) => {
  const real = await importOriginal();
  return {
    ...real,
    default: {
      ...real.default,
      get: (...args) => h.get(...args),
      post: (...args) => h.post(...args),
    },
  };
});
vi.mock("src/api/contracts/strictBody", async (importOriginal) => {
  const real = await importOriginal();
  return {
    ...real,
    acceptedFields: (definition) => {
      const fields = real.acceptedFields(definition);
      return h.projectSupported ? [...fields, "project_id"] : fields;
    },
  };
});
vi.mock("src/components/iconify", () => ({ default: Null }));
vi.mock("src/components/traceDetail/SpanTreeTimeline", () => ({
  default: ({ spans }) => (
    <div data-testid="span-tree">
      {spans.map((e) => e.observation_span.name).join(",")}
    </div>
  ),
}));
vi.mock("src/components/traceDetail/SpanDetailPane", () => ({
  default: Null,
}));
vi.mock("src/components/traceDetail/TraceDetailDrawerV2", () => ({
  default: (props) => {
    h.drawer = props;
    return null;
  },
}));
vi.mock("src/sections/common/ValueViewer/ValueViewer", () => ({
  default: Null,
}));
vi.mock("src/sections/common/ValueViewer/useValueViewMode", () => ({
  default: () => ["pretty", () => {}],
}));
vi.mock("../ItemTabStrip", () => ({
  default: ({ children }) => <div>{children}</div>,
}));
vi.mock("../itemHooks", () => {
  const columns = [
    { id: "c-in", name: "input", data_type: "text" },
    { id: "c-out", name: "output", data_type: "text" },
  ];
  const byName = { input: "saved question", output: "saved answer" };
  return {
    useRowCells: () => ({
      columns,
      byName,
      byType: { text: { column: columns[0], value: byName.input } },
      original: byName,
      metadata: {},
      originalMetadata: {},
      columnOf: (name) => columns.find((c) => c.name === name) ?? null,
      edited: () => false,
    }),
  };
});

import SourceTrajectoryTab from "../SourceTrajectoryTab";
import ConversationTab from "../ConversationTab";

const theme = createTheme({ palette: palette("light") });
const PROJECT = "00000000-0000-4000-8000-0000000000b1";
const TRACE = {
  type: "trace",
  label: "Trace 7e1a2b3c",
  project_id: PROJECT,
  trace_id: "7e1a2b3c-0000-4000-8000-000000000001",
  span_id: "1a2b3c4d5e6f7081",
  expired: false,
};
const SESSION = {
  type: "session",
  label: "Session 5d6e7f80",
  project_id: PROJECT,
  session_id: "5d6e7f80-0000-4000-8000-000000000002",
  expired: false,
};
const TRACE_DETAIL = {
  data: {
    status: true,
    result: {
      observation_spans: [
        {
          observation_span: {
            id: TRACE.span_id,
            name: "answer",
            parent_span_id: null,
            start_time: "2026-10-28T10:00:00Z",
          },
          children: [],
        },
      ],
      summary: { total_spans: 1, total_latency_ms: 40 },
    },
  },
};
const refused = (statusCode) =>
  Object.assign(new Error("refused"), { statusCode });

const rowWith = (source) => ({
  row_id: "R1",
  order: 0,
  row_meta: { source },
});
const show = (Tab, source) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ThemeProvider theme={theme}>
        <Tab datasetId="ds-1" rowId="R1" row={rowWith(source)} />
      </ThemeProvider>
    </QueryClientProvider>,
  );
};

beforeEach(() => {
  h.get = vi.fn(() => Promise.resolve(TRACE_DETAIL));
  h.post = vi.fn(() => Promise.resolve({ data: { result: { response: [] } } }));
  h.drawer = null;
  h.projectSupported = false;
});
afterEach(cleanup);

describe("Trajectory tab", () => {
  it("reads the live trace in the source's project and opens that same trace", async () => {
    show(SourceTrajectoryTab, TRACE);
    expect((await screen.findByTestId("span-tree")).textContent).toBe("answer");
    expect(h.get).toHaveBeenCalledTimes(1);
    const [url, config] = h.get.mock.calls[0];
    expect(url).toContain(`/tracer/trace/${TRACE.trace_id}/`);
    expect(config).toEqual({ params: { project_id: PROJECT } });
    expect(screen.queryByTestId("saved-row-copy")).toBeNull();

    fireEvent.click(screen.getByTestId("open-original"));
    expect(h.drawer).toMatchObject({
      traceId: TRACE.trace_id,
      projectId: PROJECT,
      initialSpanId: TRACE.span_id,
    });
  });

  it("shows the saved copy and reads no trace when the source expired", async () => {
    show(SourceTrajectoryTab, { ...TRACE, expired: true });
    expect(await screen.findByTestId("saved-row-copy")).toBeTruthy();
    expect(screen.getAllByTestId("saved-row-value")[0].textContent).toBe(
      "saved question",
    );
    expect(screen.getByTestId("trajectory-note").textContent).toContain(
      "copy saved at import",
    );
    expect(h.get).not.toHaveBeenCalled();
    expect(screen.queryByTestId("span-tree")).toBeNull();
    expect(screen.getByTestId("open-original").disabled).toBe(true);
  });

  it.each([400, 404])(
    "treats a %s from the trace read as expired: the saved copy, no spans",
    async (statusCode) => {
      h.get = vi.fn(() => Promise.reject(refused(statusCode)));
      show(SourceTrajectoryTab, TRACE);
      expect(await screen.findByTestId("saved-row-copy")).toBeTruthy();
      expect(screen.getByTestId("trajectory-note").textContent).toContain(
        "copy saved at import",
      );
      expect(screen.queryByTestId("span-tree")).toBeNull();
      expect(screen.getByTestId("open-original").disabled).toBe(true);
    },
  );
});

describe("session source project", () => {
  it.each([
    ["Conversation", ConversationTab],
    ["Trajectory", SourceTrajectoryTab],
  ])("passes the record project from the %s tab", async (_name, Tab) => {
    h.projectSupported = true;
    h.post = vi.fn(async () => ({
      data: {
        result: {
          response: [{ trace_id: TRACE.trace_id, output: "answer" }],
          next: false,
        },
      },
    }));
    show(Tab, SESSION);
    await screen.findByText("answer");
    expect(h.post).toHaveBeenCalledWith(
      expect.stringContaining(
        `/tracer/trace-session/${SESSION.session_id}/query/`,
      ),
      { page_number: 0, page_size: 50, project_id: PROJECT },
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
  });
});

describe("Conversation tab of a session import", () => {
  it("shows the session's turns in order while the session is live", async () => {
    h.post = vi.fn(() =>
      Promise.resolve({
        data: {
          result: {
            response: [
              {
                trace_id: "t-1",
                input: "Where is my parcel?",
                output: "On its way.",
              },
              { trace_id: "t-2", input: "When?", output: "Tomorrow." },
            ],
          },
        },
      }),
    );
    show(ConversationTab, SESSION);
    const turns = await screen.findAllByTestId("session-turn");
    expect(turns.map((t) => t.textContent)).toEqual([
      "Turn 1UserWhere is my parcel?AgentOn its way.",
      "Turn 2UserWhen?AgentTomorrow.",
    ]);
    expect(h.post).toHaveBeenCalledTimes(1);
    expect(h.post.mock.calls[0][0]).toContain(
      `/tracer/trace-session/${SESSION.session_id}/query/`,
    );
    expect(screen.queryByTestId("saved-row-copy")).toBeNull();
  });

  it("loads the persisted turn after the first page instead of truncating the session", async () => {
    const persisted = Array.from({ length: 51 }, (_, i) => ({
      trace_id: `trace-${i + 1}`,
      input: `question-${i + 1}`,
      output: `answer-${i + 1}`,
    }));
    h.post = vi.fn(async (_url, { page_number, page_size }) => ({
      data: {
        result: {
          response: persisted.slice(
            page_number * page_size,
            (page_number + 1) * page_size,
          ),
          next: (page_number + 1) * page_size < persisted.length,
        },
      },
    }));
    show(ConversationTab, SESSION);
    expect(await screen.findAllByTestId("session-turn")).toHaveLength(50);
    fireEvent.click(screen.getByRole("button", { name: "Load more turns" }));
    expect(await screen.findByText("answer-51")).toBeTruthy();
    expect(
      screen.getAllByTestId("session-turn").map((t) => t.textContent),
    ).toEqual(
      persisted.map((t, i) => `Turn ${i + 1}User${t.input}Agent${t.output}`),
    );
    expect(h.post.mock.calls.map(([, body]) => body)).toEqual([
      { page_number: 0, page_size: 50 },
      { page_number: 1, page_size: 50 },
    ]);
    expect(
      screen.queryByRole("button", { name: "Load more turns" }),
    ).toBeNull();
  });

  it.each([refused(503), new Error("Network Error")])(
    "keeps the loaded turns and offers retry when the next page fails: %s",
    async (error) => {
      h.post = vi
        .fn()
        .mockResolvedValueOnce({
          data: {
            result: {
              response: [{ trace_id: "t1", input: "first", output: "kept" }],
              next: true,
            },
          },
        })
        .mockRejectedValueOnce(error)
        .mockResolvedValueOnce({
          data: {
            result: {
              response: [
                { trace_id: "t2", input: "second", output: "recovered" },
              ],
              next: false,
            },
          },
        });
      show(ConversationTab, SESSION);
      await screen.findByText("kept");
      fireEvent.click(screen.getByRole("button", { name: "Load more turns" }));
      const retry = await screen.findByRole("button", {
        name: "Retry loading turns",
      });
      expect(screen.getByText("kept")).toBeTruthy();
      expect(screen.queryByTestId("saved-row-copy")).toBeNull();
      fireEvent.click(retry);
      await screen.findByText("recovered");
      expect(h.post.mock.calls.map(([, body]) => body.page_number)).toEqual([
        0, 1, 1,
      ]);
    },
  );

  it("shows the saved copy and reads no session once the session expired", async () => {
    show(ConversationTab, { ...SESSION, expired: true });
    expect(await screen.findByTestId("saved-row-copy")).toBeTruthy();
    expect(
      screen.getByTestId("conversation-expired-note").textContent,
    ).toContain("copy saved at import");
    expect(h.post).not.toHaveBeenCalled();
    expect(screen.queryByTestId("session-turn")).toBeNull();
  });

  it("shows the saved copy when the session read is refused", async () => {
    h.post = vi.fn(() => Promise.reject(refused(400)));
    show(ConversationTab, SESSION);
    expect(await screen.findByTestId("saved-row-copy")).toBeTruthy();
    expect(screen.queryByTestId("session-turn")).toBeNull();
  });
});
