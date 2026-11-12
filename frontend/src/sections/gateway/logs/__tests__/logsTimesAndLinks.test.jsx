import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "src/utils/test-utils";
import RequestDetailDrawer from "../RequestDetailDrawer";
import RequestTable from "../RequestTable";
import { REQUEST_TIME_WINDOWS, activeTimeWindow } from "../explorerFilters";

// Where a Logs time is read it carries its zone and its age; the drawer's
// Identity links sit on their own line; the time presets run to 30 days.

const detail = vi.hoisted(() => ({
  state: { data: null, isLoading: false, error: null, refetch: () => {} },
}));
const logs = vi.hoisted(() => ({ rows: [] }));

vi.mock("../hooks/useRequestDetail", () => ({ default: () => detail.state }));
vi.mock("../hooks/useRequestLogs", () => ({
  default: () => ({
    data: { results: logs.rows, count: logs.rows.length },
    isLoading: false,
    error: null,
    refetch: () => {},
  }),
}));
vi.mock("ag-grid-react", () => import("./fakeAgGrid"));
vi.mock("../../guardrails/FeedbackWidget", () => ({
  default: () => <div>Feedback widget</div>,
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("../../shell/page-status", async (importOriginal) => ({
  ...(await importOriginal()),
  isGatewayPageHidden: () => false,
}));

afterEach(() => vi.restoreAllMocks());

const ZONE = Intl.DateTimeFormat().resolvedOptions().timeZone;
const THREE_HOURS_AGO = new Date(Date.now() - 3 * 60 * 60 * 1000 - 5000);

const LOG = {
  id: "log-1",
  request_id: "01M74YH0111AAH49XKQWGW0PJA",
  model: "liquid/lfm-3-1.7b",
  resolved_model: "liquid/lfm-3-1.7b",
  provider: "openrouter",
  status_code: 200,
  latency_ms: 590,
  started_at: THREE_HOURS_AGO.toISOString(),
  input_tokens: 23,
  output_tokens: 50,
  total_tokens: 73,
  cost: "0.000013",
  is_stream: false,
  is_error: false,
  error_message: "",
  cache_hit: false,
  fallback_used: false,
  guardrail_triggered: false,
  api_key_id: "key_2e455439c4aa48219ecde97e38fbed67",
  user_id: "walker@example.com",
  session_id: "sess-0015",
  billing_mode: "managed",
  metadata: { billing_mode: "managed", auth_key_name: "checkout-service" },
  request_headers: {},
  response_headers: {},
  attempts: [],
  billing: { mode: "managed", billed: "0.000013", cost_source: "catalog" },
  agent: {},
  routing: {},
};

// "Nov 11, 2026 02:54:31.370 <zone>, 3 hours ago"
const EXACT = new RegExp(
  `^[A-Z][a-z]{2} \\d{2}, \\d{4} \\d{2}:\\d{2}:\\d{2}\\.\\d{3} ${ZONE.replace(/[/+]/g, "\\$&")}, 3 hours ago$`,
);

const openDrawer = (log) => {
  detail.state = {
    data: { result: log },
    isLoading: false,
    error: null,
    refetch: () => {},
  };
  return render(<RequestDetailDrawer open logId={log.id} onClose={vi.fn()} />);
};

describe("Logs times", () => {
  it("a row's time shows its exact instant with the zone and its age on hover", () => {
    logs.rows = [LOG];
    render(
      <RequestTable
        filters={{}}
        setFilter={() => {}}
        setFilters={() => {}}
        onSelectLog={() => {}}
      />,
    );
    const [row] = within(screen.getByTestId("ag-grid"))
      .getAllByRole("row")
      .slice(1);
    const cell = within(row).getByText(/\d{2}:\d{2}:\d{2}\.\d{3}$/);
    expect(cell.getAttribute("title")).toMatch(EXACT);
  });

  it("the drawer's time says how long ago it was, with the exact instant and zone on hover", () => {
    openDrawer(LOG);
    const time = screen.getByText(/\(3 hours ago\)/);
    expect(time.getAttribute("title")).toMatch(EXACT);
  });
});

describe("Request drawer Identity links", () => {
  it("puts each link on its own line, after the key or session it filters by", () => {
    openDrawer(LOG);
    for (const name of [
      "All requests with this key",
      "All requests in this session",
    ]) {
      const link = screen.getByRole("link", { name });
      const line = link.parentElement;
      expect(line.tagName).toBe("SPAN");
      expect(line).toHaveTextContent(new RegExp(`^${name}$`));
    }
  });
});

describe("Logs time presets", () => {
  it("run to 30 days, as the widest preset", () => {
    expect(REQUEST_TIME_WINDOWS.at(-1)).toEqual({
      label: "Last 30 days",
      minutes: 30 * 24 * 60,
    });
    const now = new Date("2026-11-11T12:00:00.000Z");
    expect(
      activeTimeWindow({
        startedAfter: new Date(
          now.getTime() - 30 * 24 * 60 * 60000,
        ).toISOString(),
        startedBefore: now.toISOString(),
      }),
    ).toBe(30 * 24 * 60);
  });
});
