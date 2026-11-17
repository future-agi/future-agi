// G2C-grid-03: exercise Trajectory directly with the real scoped query hooks.
// RED regression authored before the fix; execution is intentionally deferred.
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { h, Null } = vi.hoisted(() => ({
  h: { get: vi.fn(), post: vi.fn(), drawer: null, projectSupported: true },
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
      const fields = real.acceptedFields(definition).filter(
        (field) => field !== "project_id",
      );
      return h.projectSupported ? [...fields, "project_id"] : fields;
    },
  };
});

/* eslint-disable react/prop-types */
vi.mock("src/components/iconify", () => ({ default: Null }));
vi.mock("src/components/tooltip", () => ({
  default: ({ children }) => <>{children}</>,
}));
vi.mock("src/components/traceDetail/SpanTreeTimeline", () => ({
  default: ({ spans }) => (
    <div data-testid="span-tree">
      {spans.map((entry) => entry.observation_span.name).join(",")}
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
vi.mock("../SavedRowCopy", () => ({
  default: () => <div data-testid="saved-row-copy">Saved copy</div>,
}));
vi.mock("../itemHooks", () => ({
  useRowCells: () => ({ byType: {} }),
}));
/* eslint-enable react/prop-types */

import SourceTrajectoryTab from "../SourceTrajectoryTab";

const theme = createTheme({ palette: palette("light") });
const PROJECT = "00000000-0000-4000-8000-0000000000b1";
const SESSION = {
  type: "session",
  session_id: "5d6e7f80-0000-4000-8000-000000000002",
  project_id: PROJECT,
  expired: false,
};
const traces = Array.from({ length: 51 }, (_, index) => ({
  trace_id: `trace-${index + 1}`,
  input: `question-${index + 1}`,
  output: `answer-${index + 1}`,
}));
const page = (response, next) => ({ data: { result: { response, next } } });
const detail = (traceId) => ({
  data: {
    result: {
      observation_spans: [
        {
          observation_span: {
            id: `root-${traceId}`,
            name: `detail:${traceId}`,
            parent_span_id: null,
            start_time: "2026-10-28T10:00:00Z",
          },
          children: [],
        },
      ],
      summary: { total_spans: 1, total_latency_ms: 40 },
    },
  },
});
const clients = [];
const show = () => {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: Infinity, gcTime: Infinity },
    },
  });
  clients.push(client);
  return render(
    <QueryClientProvider client={client}>
      <ThemeProvider theme={theme}>
        <SourceTrajectoryTab
          datasetId="ds-1"
          row={{ row_id: "row-1", row_meta: { source: SESSION } }}
        />
      </ThemeProvider>
    </QueryClientProvider>,
  );
};
const expectSelectedTrace = (number) => {
  expect(
    screen.getByRole("tab", { name: `Trace ${number}`, selected: true }),
  ).toBeTruthy();
  expect(screen.getByTestId("span-tree").textContent).toBe(
    `detail:trace-${number}`,
  );
  expect(screen.queryByTestId("saved-row-copy")).toBeNull();
};
const expectPageRequests = (numbers) => {
  expect(h.post.mock.calls.map(([, body]) => body)).toEqual(
    numbers.map((number) => ({
      page_number: number,
      page_size: 50,
      ...(h.projectSupported ? { project_id: PROJECT } : {}),
    })),
  );
  h.post.mock.calls.forEach(([url, , config]) => {
    expect(url).toContain(`/tracer/trace-session/${SESSION.session_id}/query/`);
    expect(config.signal).toBeInstanceOf(AbortSignal);
  });
};

beforeEach(() => {
  h.projectSupported = true;
  h.drawer = null;
  h.get.mockReset();
  h.get.mockImplementation(async (url) =>
    detail(url.match(/\/tracer\/trace\/([^/]+)\//)?.[1]),
  );
  h.post.mockReset();
});
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
});

describe("Trajectory session pagination", () => {
  it.each([true, false])(
    "loads trace 51 directly and reads its scoped detail (project body supported: %s)",
    async (projectSupported) => {
      h.projectSupported = projectSupported;
      let resolveNext;
      const nextPage = new Promise((resolve) => {
        resolveNext = resolve;
      });
      h.post
        .mockResolvedValueOnce(page(traces.slice(0, 50), true))
        .mockReturnValueOnce(nextPage);
      show();

      expect(await screen.findAllByRole("tab")).toHaveLength(50);
      expectPageRequests([0]);
      fireEvent.click(screen.getByRole("tab", { name: "Trace 17" }));
      await waitFor(() => expectSelectedTrace(17));
      fireEvent.click(screen.getByRole("button", { name: "Load more traces" }));

      const loading = await screen.findByRole("button", {
        name: /Loading traces/,
      });
      expect(loading.disabled).toBe(true);
      expect(screen.getAllByRole("tab")).toHaveLength(50);
      expectSelectedTrace(17);
      fireEvent.click(loading);
      expectPageRequests([0, 1]);

      await act(async () => resolveNext(page(traces.slice(50), false)));
      await screen.findByRole("tab", { name: "Trace 51" });
      expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual(
        traces.map((_, index) => `Trace ${index + 1}`),
      );
      expectSelectedTrace(17);
      expect(
        screen.queryByRole("button", { name: /(?:Load more|Loading|Retry loading) traces/ }),
      ).toBeNull();

      fireEvent.click(screen.getByRole("tab", { name: "Trace 51" }));
      await waitFor(() => expectSelectedTrace(51));
      expect(h.get).toHaveBeenLastCalledWith(
        expect.stringContaining("/tracer/trace/trace-51/"),
        { params: { project_id: PROJECT } },
      );
      h.get.mock.calls.forEach(([, config]) => {
        expect(config).toEqual({ params: { project_id: PROJECT } });
      });
      fireEvent.click(screen.getByTestId("open-original"));
      expect(h.drawer).toMatchObject({
        traceId: "trace-51",
        projectId: PROJECT,
        open: true,
      });
      expectPageRequests([0, 1]);
    },
  );

  it.each([
    ["server failure", Object.assign(new Error("unavailable"), { statusCode: 503 })],
    ["next-page refusal", Object.assign(new Error("refused"), { statusCode: 404 })],
    ["network failure", new Error("Network Error")],
  ])("keeps the selected trace and retries page 1 after a %s", async (_label, error) => {
    h.post
      .mockResolvedValueOnce(page(traces.slice(0, 50), true))
      .mockRejectedValueOnce(error)
      .mockResolvedValueOnce(page(traces.slice(50), false));
    show();
    await screen.findByRole("tab", { name: "Trace 17" });
    fireEvent.click(screen.getByRole("tab", { name: "Trace 17" }));
    await waitFor(() => expectSelectedTrace(17));
    fireEvent.click(screen.getByRole("button", { name: "Load more traces" }));

    const retry = await screen.findByRole("button", { name: "Retry loading traces" });
    expect(screen.getByRole("alert").textContent).toContain(
      "The traces already loaded are kept.",
    );
    expect(screen.getAllByRole("tab")).toHaveLength(50);
    expectSelectedTrace(17);
    expect(screen.getByTestId("open-original").disabled).toBe(false);
    expectPageRequests([0, 1]);

    fireEvent.click(retry);
    await screen.findByRole("tab", { name: "Trace 51" });
    expect(screen.getAllByRole("tab")).toHaveLength(51);
    expectSelectedTrace(17);
    expectPageRequests([0, 1, 1]);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(
      screen.queryByRole("button", { name: /(?:Load more|Loading|Retry loading) traces/ }),
    ).toBeNull();
  });

  it("offers pagination even when the first page contains only one trace", async () => {
    h.post
      .mockResolvedValueOnce(page(traces.slice(0, 1), true))
      .mockResolvedValueOnce(page(traces.slice(1, 2), false));
    show();
    fireEvent.click(await screen.findByRole("button", { name: "Load more traces" }));
    await screen.findByRole("tab", { name: "Trace 2" });
    expect(screen.getAllByRole("tab")).toHaveLength(2);
    expectPageRequests([0, 1]);
    expect(screen.queryByRole("button", { name: "Load more traces" })).toBeNull();
  });

  it("does not offer another page when the first response has next=false", async () => {
    h.post.mockResolvedValueOnce(page(traces.slice(0, 50), false));
    show();
    expect(await screen.findAllByRole("tab")).toHaveLength(50);
    expect(screen.queryByRole("button", { name: "Load more traces" })).toBeNull();
    expectPageRequests([0]);
  });

  it.each([404, 503])("still shows the saved copy after an initial HTTP %s failure", async (statusCode) => {
    h.post.mockRejectedValueOnce(Object.assign(new Error("unavailable"), { statusCode }));
    show();
    expect(await screen.findByTestId("saved-row-copy")).toBeTruthy();
    expect(screen.queryByRole("tab")).toBeNull();
    expect(screen.queryByRole("button", { name: "Retry loading traces" })).toBeNull();
    expect(h.get).not.toHaveBeenCalled();
  });
});
