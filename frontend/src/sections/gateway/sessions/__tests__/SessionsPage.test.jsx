import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HelmetProvider } from "react-helmet-async";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";

import SessionExplorerSection from "../SessionExplorerSection";
import { sessionStatus, sessionStatusHelp } from "../sessionStatus";
import { WORKSPACE_WRITE_ONLY } from "../../constants/gatewayAdmin";
import { formatLogTime } from "../../logs/logDates";
import { grid } from "../../logs/__tests__/fakeAgGrid";
import {
  chipNeeds,
  columnOf,
  cutHeaders,
  gridBudget1440,
  minimumGridWidth,
  minimumWidth,
} from "../../logs/__tests__/gridFit";

vi.mock("ag-grid-react", () => import("../../logs/__tests__/fakeAgGrid"));

const { mockGet, mockPost, snack } = vi.hoisted(() => ({
  mockGet: vi.fn(),
  mockPost: vi.fn(),
  snack: vi.fn(),
}));
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: (...args) => snack(...args),
}));
vi.mock("../../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "default", isLoading: false }),
}));
// Closing a session writes to the workspace: an org Owner or Admin writes to
// every workspace, anyone else where their role is Member or above
// (User.can_write_to_workspace). Levels: Viewer 1, Member 3, Admin 8, Owner 15.
// Every test reads as an Owner unless it sets the levels itself.
const access = vi.hoisted(() => ({ orgLevel: 15, wsLevel: null }));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: access.orgLevel }),
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ wsLevel: access.wsLevel }),
}));
vi.mock("src/utils/axios", () => ({
  default: {
    get: (...args) => mockGet(...args),
    post: (...args) => mockPost(...args),
    delete: vi.fn(),
  },
  endpoints: {
    gateway: {
      sessions: {
        list: "/agentcc/sessions/",
        detail: (id) => `/agentcc/sessions/${id}/`,
        requests: (id) => `/agentcc/sessions/${id}/requests/`,
        close: (id) => `/agentcc/sessions/${id}/close/`,
        delete: (id) => `/agentcc/sessions/${id}/`,
      },
    },
  },
}));

const session = (n, over = {}) => ({
  id: `row-${n}`,
  session_id: `conv-${String(n).padStart(3, "0")}`,
  name: `conv-${String(n).padStart(3, "0")}`,
  status: "active",
  metadata: {},
  created_at: "2026-10-20T09:38:16.807Z",
  updated_at: "2026-10-20T09:40:16.807Z",
  last_request_at: "2026-10-20T09:41:05.120Z",
  closed_reason: "",
  idle_timeout_seconds: 1800,
  stats: {
    request_count: 3,
    total_tokens: 54,
    total_cost: 0.00027,
    avg_latency_ms: 1385.0,
  },
  ...over,
});

const LIST = {
  count: 52,
  results: [
    session(12),
    session(11, {
      status: "closed",
      closed_reason: "idle",
      last_request_at: "2026-10-20T09:39:00.000Z",
      stats: {
        request_count: 1,
        total_tokens: 9,
        total_cost: 0,
        avg_latency_ms: 4,
      },
    }),
  ],
};
const REQUESTS = [
  {
    id: "log-1",
    request_id: "01M5CA",
    model: "gpt-4o",
    provider: "flaky-upstream",
    latency_ms: 1385,
    started_at: "2026-10-20T09:38:12.452Z",
    total_tokens: 54,
    cost: "0.000270",
    status_code: 200,
    is_error: false,
  },
  {
    id: "log-2",
    request_id: "01M5CB",
    model: "gpt-4o",
    provider: "flaky-upstream",
    latency_ms: 12,
    started_at: "2026-10-20T09:38:14.000Z",
    total_tokens: 0,
    cost: "0",
    status_code: 502,
    is_error: true,
  },
];

function answer(url) {
  if (url === "/agentcc/sessions/") return Promise.resolve({ data: LIST });
  if (url === "/agentcc/sessions/row-12/")
    return Promise.resolve({ data: { result: LIST.results[0] } });
  if (url === "/agentcc/sessions/row-12/requests/")
    return Promise.resolve({ data: { result: REQUESTS } });
  return Promise.reject(new Error(`unexpected ${url}`));
}

function renderPage() {
  return render(
    <HelmetProvider>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <ThemeProvider
          theme={createTheme({ palette: palette("light"), typography })}
        >
          <MemoryRouter initialEntries={["/dashboard/gateway/sessions"]}>
            <SessionExplorerSection />
          </MemoryRouter>
        </ThemeProvider>
      </QueryClientProvider>
    </HelmetProvider>,
  );
}

const listCalls = () =>
  mockGet.mock.calls
    .filter(([u]) => u === "/agentcc/sessions/")
    .map(([, cfg]) => cfg?.params || {});

beforeEach(() => {
  mockGet.mockReset();
  mockPost.mockReset();
  snack.mockReset();
  mockGet.mockImplementation(answer);
  access.orgLevel = 15;
  access.wsLevel = null;
});

describe("Sessions page", () => {
  it("lists every session with its numbers, not only the API's first 10", async () => {
    renderPage();
    expect(
      screen.getByRole("heading", { level: 1, name: "Sessions" }),
    ).toBeInTheDocument();
    const table = within(await screen.findByTestId("ag-grid"));
    await table.findByText("conv-012");
    expect(listCalls()[0]).toMatchObject({ limit: 100, page: 1 });
    const rows = table.getAllByRole("row").slice(1);
    expect(rows[0]).toHaveTextContent("Active");
    expect(rows[0]).toHaveTextContent("3");
    expect(rows[0]).toHaveTextContent("54");
    expect(rows[0]).toHaveTextContent("$0.00027");
    expect(rows[0]).toHaveTextContent("1.39 s");
    expect(rows[1]).toHaveTextContent("Closed");
    expect(screen.getByText("2 of 52 sessions")).toBeInTheDocument();
  });

  it("filters by status and search on the server", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("conv-012");
    await user.click(screen.getByRole("button", { name: "Closed" }));
    await waitFor(() =>
      expect(listCalls().at(-1)).toMatchObject({ status: "closed" }),
    );
    await user.type(
      screen.getByRole("textbox", { name: /Search sessions/ }),
      "conv-01",
    );
    await waitFor(() =>
      expect(listCalls().at(-1)).toMatchObject({
        status: "closed",
        search: "conv-01",
      }),
    );
  });

  it("closes an active session from its row menu and says so", async () => {
    const user = userEvent.setup();
    mockPost.mockResolvedValue({ data: { result: { status: "closed" } } });
    renderPage();
    await screen.findByText("conv-012");
    await user.click(
      screen.getByRole("button", { name: "Actions for session conv-012" }),
    );
    const item = await screen.findByRole("menuitem", { name: /Close session/ });
    // What closing does, said where it is done: a session a user closes
    // stays closed.
    expect(item).toHaveTextContent(
      "It stays closed when a request with this session id arrives. Those requests are still logged under it.",
    );
    await user.click(item);
    // It asks first, naming the session; Cancel closes nothing.
    let dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("Close session conv-012?");
    expect(dialog).toHaveTextContent(/permanent/);
    expect(mockPost).not.toHaveBeenCalled();
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(mockPost).not.toHaveBeenCalled();
    await user.click(
      screen.getByRole("button", { name: "Actions for session conv-012" }),
    );
    await user.click(
      await screen.findByRole("menuitem", { name: /Close session/ }),
    );
    dialog = screen.getByRole("dialog");
    await user.click(
      within(dialog).getByRole("button", { name: "Close session" }),
    );
    expect(mockPost).toHaveBeenCalledWith("/agentcc/sessions/row-12/close/");
    await waitFor(() =>
      expect(snack).toHaveBeenCalledWith("Session conv-012 closed", {
        variant: "success",
      }),
    );
  });

  // An idle session reads Closed but opens again on its next
  // request; closing it for good is still the user's to do. One closed by a
  // user stays closed, so it has nothing to close.
  it("closes an idle session for good from its row menu", async () => {
    const user = userEvent.setup();
    mockPost.mockResolvedValue({ data: { result: { status: "closed" } } });
    renderPage();
    await screen.findByText("conv-011");
    await user.click(
      screen.getByRole("button", { name: "Actions for session conv-011" }),
    );
    const item = await screen.findByRole("menuitem", {
      name: /Close for good/,
    });
    expect(item).toHaveTextContent(
      "It stays closed when a request with this session id arrives. Those requests are still logged under it.",
    );
    await user.click(item);
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("Close session conv-011 for good?");
    expect(dialog).toHaveTextContent(/permanent/);
    expect(mockPost).not.toHaveBeenCalled();
    await user.click(
      within(dialog).getByRole("button", { name: "Close for good" }),
    );
    expect(mockPost).toHaveBeenCalledWith("/agentcc/sessions/row-11/close/");
  });

  it("has no row menu on a session a user closed", async () => {
    mockGet.mockImplementation((url) =>
      url === "/agentcc/sessions/"
        ? Promise.resolve({
            data: {
              count: 2,
              results: [
                session(12),
                session(13, { status: "closed", closed_reason: "closed" }),
              ],
            },
          })
        : answer(url),
    );
    renderPage();
    await screen.findByText("conv-013");
    expect(
      screen.queryByRole("button", { name: "Actions for session conv-013" }),
    ).not.toBeInTheDocument();
    // A session a user closed reads Closed for good, never plain Closed.
    const rows = within(screen.getByTestId("ag-grid"))
      .getAllByRole("row")
      .slice(1);
    expect(rows[1]).toHaveTextContent("Closed for good");
    expect(rows[0]).not.toHaveTextContent("Closed");
    expect(
      screen.getByRole("button", { name: "Actions for session conv-012" }),
    ).toBeInTheDocument();
  });

  it("opens a session's requests in a drawer that links into Logs", async () => {
    const user = userEvent.setup();
    renderPage();
    const table = within(await screen.findByTestId("ag-grid"));
    await user.click(await table.findByText("conv-012"));
    const drawer = within(await screen.findByRole("dialog"));
    expect(
      drawer.getByRole("heading", { name: "conv-012" }),
    ).toBeInTheDocument();
    expect(drawer.getByRole("link", { name: "Open in Logs" })).toHaveAttribute(
      "href",
      "/dashboard/gateway/logs?session_id=conv-012",
    );
    const calls = within(
      await drawer.findByRole("list", { name: "Requests in this session" }),
    );
    const items = calls.getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(items[1]).toHaveTextContent("502");
    expect(within(items[0]).getByRole("link")).toHaveAttribute(
      "href",
      "/dashboard/gateway/logs?log=log-1",
    );
  });

  // The Falcon button covered the last rows' "..." menu.
  // The frame's room box (108 px after a 12 px gap) follows the grid.
  it("ends the grid clear of the Falcon button", async () => {
    renderPage();
    const page = await screen.findByTestId("gateway-sessions");
    const room = page.querySelector('[data-clearance="floating-button"]');
    expect(room).toHaveStyle({ height: "108px", flexShrink: "0" });
    expect(room.parentElement.lastElementChild).toBe(room);
  });

  it("shows an error with Retry", async () => {
    const user = userEvent.setup();
    mockGet.mockImplementation((url) =>
      url === "/agentcc/sessions/"
        ? Promise.reject({ message: "Gateway API unreachable" })
        : answer(url),
    );
    renderPage();
    expect(
      await screen.findByText("Sessions could not be loaded"),
    ).toBeInTheDocument();
    mockGet.mockImplementation(answer);
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("conv-012")).toBeInTheDocument();
  });

  it("explains how sessions appear when there are none", async () => {
    mockGet.mockImplementation((url) =>
      url === "/agentcc/sessions/"
        ? Promise.resolve({ data: { count: 0, results: [] } })
        : answer(url),
    );
    renderPage();
    expect(await screen.findByText("No sessions yet")).toBeInTheDocument();
    expect(screen.getByText(/x-agentcc-session-id/)).toBeInTheDocument();
  });
});

// Close session and Close for good write to the workspace, so the row menu
// offers them to an Owner, an Admin and a Member. A Viewer, or a Member with
// view-only access to this workspace, still reads what closing does, sees the
// item disabled with the role it needs, and nothing is sent.
describe("Sessions close by role", () => {
  const openRowMenu = async (user, id) => {
    await screen.findByText(id);
    await user.click(
      screen.getByRole("button", { name: `Actions for session ${id}` }),
    );
  };

  it.each([
    ["a Viewer", "Close session", "conv-012", 1, null],
    ["a Viewer", "Close for good", "conv-011", 1, null],
    [
      "a Member with view-only access to this workspace",
      "Close session",
      "conv-012",
      3,
      1,
    ],
    [
      "a Member with view-only access to this workspace",
      "Close for good",
      "conv-011",
      3,
      1,
    ],
  ])(
    "%s sees %s on %s disabled with the role it needs",
    async (_who, label, id, orgLevel, wsLevel) => {
      access.orgLevel = orgLevel;
      access.wsLevel = wsLevel;
      const user = userEvent.setup();
      renderPage();
      await openRowMenu(user, id);
      const item = await screen.findByRole("menuitem", {
        name: new RegExp(`^${label}`),
      });
      expect(item).toHaveAttribute("aria-disabled", "true");
      expect(
        item.closest(`[aria-label="${WORKSPACE_WRITE_ONLY}"]`),
      ).not.toBeNull();
      expect(WORKSPACE_WRITE_ONLY).toBe("Owner, Admin or Member only");
      expect(item).toHaveTextContent(
        "It stays closed when a request with this session id arrives. Those requests are still logged under it.",
      );
      // Even a click that reaches the item asks nothing and sends nothing.
      fireEvent.click(item);
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      expect(mockPost).not.toHaveBeenCalled();
    },
  );

  it.each([
    ["a Member", 3, 3],
    ["a Member whose workspace level is not loaded", 3, null],
    ["an Admin", 8, 1],
    ["an Owner", 15, null],
  ])(
    "%s closes an active session from its row menu",
    async (_who, orgLevel, wsLevel) => {
      access.orgLevel = orgLevel;
      access.wsLevel = wsLevel;
      const user = userEvent.setup();
      mockPost.mockResolvedValue({ data: { result: { status: "closed" } } });
      renderPage();
      await openRowMenu(user, "conv-012");
      const item = await screen.findByRole("menuitem", {
        name: /Close session/,
      });
      expect(item).not.toHaveAttribute("aria-disabled", "true");
      expect(item.closest(`[aria-label="${WORKSPACE_WRITE_ONLY}"]`)).toBeNull();
      await user.click(item);
      const dialog = screen.getByRole("dialog");
      expect(dialog).toHaveTextContent("Close session conv-012?");
      await user.click(
        within(dialog).getByRole("button", { name: "Close session" }),
      );
      expect(mockPost).toHaveBeenCalledWith("/agentcc/sessions/row-12/close/");
    },
  );

  it("a Member closes an idle session for good from its row menu", async () => {
    access.orgLevel = 3;
    access.wsLevel = 3;
    const user = userEvent.setup();
    mockPost.mockResolvedValue({ data: { result: { status: "closed" } } });
    renderPage();
    await openRowMenu(user, "conv-011");
    const item = await screen.findByRole("menuitem", {
      name: /Close for good/,
    });
    expect(item).not.toHaveAttribute("aria-disabled", "true");
    await user.click(item);
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("Close session conv-011 for good?");
    await user.click(
      within(dialog).getByRole("button", { name: "Close for good" }),
    );
    expect(mockPost).toHaveBeenCalledWith("/agentcc/sessions/row-11/close/");
  });
});

// 52 of 52 green "Active" since yesterday, an empty Name
// column, and a close icon on every row.
describe("Sessions status, Name and close", () => {
  it("shows the status as the backend reads it, not as a green health claim", () => {
    // The backend reads a session closed once its idle timeout passes, so
    // Active means "had a request lately"; still the neutral chip.
    expect(sessionStatus("active")).toEqual({
      tone: "neutral",
      label: "Active",
      detail: "",
    });
    expect(sessionStatus("closed")).toMatchObject({
      tone: "neutral",
      label: "Closed",
    });
  });

  it("says in the Status header when a session closes", async () => {
    renderPage();
    await screen.findByText("conv-012");
    const status = grid.props.columnDefs.find((c) => c.colId === "status");
    expect(status.headerTooltip).toBe(
      "A session closes after 30 minutes with no request, or when someone closes it. A new request with its id opens an idle session again.",
    );
  });

  // Sessions close after their idle timeout (the API says
  // why in closed_reason) and the list has a Last request column.
  it("says why a session closed", () => {
    expect(sessionStatus("closed", "idle", 1800)).toEqual({
      tone: "neutral",
      label: "Closed",
      detail:
        "No request for 30 minutes. A new request with this session id opens it again.",
    });
    // The console's Close and the API's close both set it, and it stays
    // closed: its own status, distinct from an idle close.
    expect(sessionStatus("closed", "closed")).toEqual({
      tone: "neutral",
      label: "Closed for good",
      detail:
        "Closed by a user. It stays closed when a request with this session id arrives.",
    });
    expect(sessionStatus("active", "").detail).toBe("");
    expect(sessionStatusHelp(3600)).toMatch(/^A session closes after 1 hour /);
    expect(sessionStatusHelp(undefined)).toMatch(
      /^A session closes after its idle timeout /,
    );
  });

  it("shows each session's last request in its own column", async () => {
    renderPage();
    const table = within(await screen.findByTestId("ag-grid"));
    await table.findByText("conv-012");
    expect(
      table.getByRole("columnheader", { name: "Last request" }),
    ).toBeInTheDocument();
    const rows = table.getAllByRole("row").slice(1);
    expect(rows[0]).toHaveTextContent(
      formatLogTime(LIST.results[0].last_request_at),
    );
    expect(rows[1]).toHaveTextContent(
      formatLogTime(LIST.results[1].last_request_at),
    );
  });

  it("puts the reason on a closed session's chip", async () => {
    renderPage();
    const table = within(await screen.findByTestId("ag-grid"));
    await table.findByText("conv-011");
    expect(
      table.getByTitle(
        "No request for 30 minutes. A new request with this session id opens it again.",
      ),
    ).toHaveTextContent("Closed");
  });

  it("shows the last request in the session drawer", async () => {
    const user = userEvent.setup();
    renderPage();
    const table = within(await screen.findByTestId("ag-grid"));
    await user.click(await table.findByText("conv-012"));
    const summary = within(
      await screen.findByRole("list", { name: "Session summary" }),
    );
    expect(summary.getByText("Last request").parentElement).toHaveTextContent(
      formatLogTime(LIST.results[0].last_request_at),
    );
  });

  it("hides the Name column when no session has a name of its own", async () => {
    renderPage();
    const table = within(await screen.findByTestId("ag-grid"));
    await table.findByText("conv-012");
    expect(
      table.queryByRole("columnheader", { name: "Name" }),
    ).not.toBeInTheDocument();
  });

  it("shows the Name column when a session has a name of its own", async () => {
    mockGet.mockImplementation((url) =>
      url === "/agentcc/sessions/"
        ? Promise.resolve({
            data: {
              count: 1,
              results: [session(12, { name: "Checkout assistant" })],
            },
          })
        : answer(url),
    );
    renderPage();
    const table = within(await screen.findByTestId("ag-grid"));
    expect(await table.findByText("Checkout assistant")).toBeInTheDocument();
    expect(
      table.getByRole("columnheader", { name: "Name" }),
    ).toBeInTheDocument();
  });

  it("no row renders a bare close IconButton", async () => {
    renderPage();
    const table = within(await screen.findByTestId("ag-grid"));
    await table.findByText("conv-012");
    expect(
      table.queryByRole("button", { name: /^Close session/ }),
    ).not.toBeInTheDocument();
  });
});

// Started read the session row's created_at, the time an
// ingestion batch stored it, so a batch of sessions shared one start and some
// started after their last request. It reads the API's first_request_at,
// and created_at only for a session with no request.
describe("Sessions Started is the first request", () => {
  const STORED = "2026-10-23T01:01:23.685Z";
  const BATCH = {
    count: 3,
    results: [
      session(29, {
        created_at: STORED,
        first_request_at: "2026-10-23T01:00:41.120Z",
        last_request_at: "2026-10-23T01:01:20.993Z",
      }),
      session(28, {
        created_at: STORED,
        first_request_at: "2026-10-23T01:00:55.500Z",
        last_request_at: "2026-10-23T01:01:01.000Z",
      }),
      session(27, {
        created_at: "2026-10-23T00:58:00.000Z",
        first_request_at: null,
        last_request_at: null,
      }),
    ],
  };

  beforeEach(() => {
    mockGet.mockImplementation((url) => {
      if (url === "/agentcc/sessions/") return Promise.resolve({ data: BATCH });
      if (url === "/agentcc/sessions/row-29/")
        return Promise.resolve({ data: { result: BATCH.results[0] } });
      if (url === "/agentcc/sessions/row-29/requests/")
        return Promise.resolve({ data: { result: [] } });
      return Promise.reject(new Error(`unexpected ${url}`));
    });
  });

  it("shows each session's first request under Started, never the batch time", async () => {
    renderPage();
    const table = within(await screen.findByTestId("ag-grid"));
    await table.findByText("conv-029");
    const started = grid.props.columnDefs.find(
      (c) => c.headerName === "Started",
    );
    const read = (row) => started.valueGetter({ data: row });
    expect(read(BATCH.results[0])).toBe("2026-10-23T01:00:41.120Z");
    expect(read(BATCH.results[1])).toBe("2026-10-23T01:00:55.500Z");
    // No request yet: when the gateway stored it is all there is.
    expect(read(BATCH.results[2])).toBe("2026-10-23T00:58:00.000Z");
    expect(started.sort).toBe("desc");
    expect(table.queryByText(formatLogTime(STORED))).not.toBeInTheDocument();
    expect(
      table.getByText(formatLogTime("2026-10-23T01:00:41.120Z")),
    ).toBeInTheDocument();
  });

  it("starts the session drawer at the first request", async () => {
    const user = userEvent.setup();
    renderPage();
    const table = within(await screen.findByTestId("ag-grid"));
    await user.click(await table.findByText("conv-029"));
    const summary = within(
      await screen.findByRole("list", { name: "Session summary" }),
    );
    expect(summary.getByText("Started").parentElement).toHaveTextContent(
      formatLogTime("2026-10-23T01:00:41.120Z"),
    );
    expect(summary.getByText("Started").parentElement).not.toHaveTextContent(
      formatLogTime(STORED),
    );
  });
});

// An unpriced request is stored at cost 0, so a session
// of unpriced requests read "$0.00". The API counts each session's unpriced
// requests: none priced reads an em dash titled No price found, as a Logs
// row does; some reads the priced part as a floor. Grid and drawer alike.
describe("Sessions cost over requests with no price", () => {
  const UNPRICED = {
    count: 2,
    results: [
      session(41, {
        stats: {
          request_count: 2,
          total_tokens: 1604,
          total_cost: 0,
          unpriced_requests: 2,
          avg_latency_ms: 900,
        },
      }),
      session(40, {
        stats: {
          request_count: 2,
          total_tokens: 1604,
          total_cost: 0.005,
          unpriced_requests: 1,
          avg_latency_ms: 900,
        },
      }),
    ],
  };

  beforeEach(() => {
    mockGet.mockImplementation((url) => {
      if (url === "/agentcc/sessions/")
        return Promise.resolve({ data: UNPRICED });
      if (url === "/agentcc/sessions/row-41/")
        return Promise.resolve({ data: { result: UNPRICED.results[0] } });
      if (url === "/agentcc/sessions/row-41/requests/")
        return Promise.resolve({ data: { result: [] } });
      return Promise.reject(new Error(`unexpected ${url}`));
    });
  });

  it("reads a session's cost as unknown or a floor in the grid, never $0.00", async () => {
    renderPage();
    const table = within(await screen.findByTestId("ag-grid"));
    await table.findByText("conv-041");
    const [none, some] = table.getAllByRole("row").slice(1);
    expect(none).not.toHaveTextContent("$0.00");
    expect(within(none).getByTitle("No price found")).toHaveTextContent(/^—$/);
    // Release-lead 595: the bound is exact, not the cell format's two figures.
    expect(
      within(some).getByTitle(
        "1 of 2 requests had no price found, so this total counts only the priced ones.",
      ),
    ).toHaveTextContent(/^≥\s\$0\.005$/);
  });

  it("reads the drawer's Cost as unknown when no request had a price", async () => {
    const user = userEvent.setup();
    renderPage();
    const table = within(await screen.findByTestId("ag-grid"));
    await user.click(await table.findByText("conv-041"));
    const summary = within(
      await screen.findByRole("list", { name: "Session summary" }),
    );
    const cost = summary.getByText("Cost").parentElement;
    expect(cost).not.toHaveTextContent("$0.00");
    expect(within(cost).getByTitle("No price found")).toHaveTextContent(/^—$/);
  });
});

describe("Sessions grid widths", () => {
  it("cuts off no header at its minimum width and fits at 1440 px", async () => {
    renderPage();
    await within(await screen.findByTestId("ag-grid")).findByText("conv-012");
    expect(cutHeaders(grid.props)).toEqual([]);
    expect(minimumGridWidth(grid.props)).toBeLessThanOrEqual(gridBudget1440());
  });

  it("shows the Closed for good status whole at the column's minimum width", async () => {
    renderPage();
    await within(await screen.findByTestId("ag-grid")).findByText("conv-012");
    expect(minimumWidth(columnOf(grid.props, "status"))).toBeGreaterThanOrEqual(
      chipNeeds("Closed for good"),
    );
  });
});
