import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  render,
  screen,
  waitFor,
  fireEvent,
  within,
} from "src/utils/test-utils";
import {
  bulkBody,
  bulkFiltersProblem,
  deliveryParams,
  scopedConfig,
  testOutcomeOf,
  webhookKeys,
} from "src/api/webhooks";
import DeliveriesCard from "../DeliveriesCard";
import TestResult from "../TestResult";
import WebhooksSection from "../WebhooksSection";

const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));

vi.mock("src/utils/axios", () => ({
  default: { get: mocks.get, post: mocks.post },
  endpoints: {
    integrations: { destinations: "/integrations/destinations/" },
    webhooks: {
      endpoints: "/webhooks/endpoints/",
      endpoint: (id) => `/webhooks/endpoints/${id}/`,
      endpointDeliveries: (id) => `/webhooks/endpoints/${id}/deliveries/`,
      deliveries: "/webhooks/deliveries/",
      delivery: (id) => `/webhooks/deliveries/${id}/`,
      redeliver: (id) => `/webhooks/deliveries/${id}/redeliver/`,
      redeliverBulk: "/webhooks/deliveries/redeliver/",
      preview: "/webhooks/preview/",
      policy: "/webhooks/policy/",
      workspacePolicy: (ws) => `/webhooks/policy/workspace/${ws}/`,
      egressIps: "/webhooks/egress-ips/",
      test: (id) => `/webhooks/endpoints/${id}/test/`,
    },
  },
}));
vi.mock("src/utils/utils", () => ({ copyToClipboard: vi.fn() }));
vi.mock("src/components/iconify", () => ({ default: () => null }));

const ok = (result) =>
  Promise.resolve({ status: 200, data: { status: true, result } });
const failedRow = {
  id: "dlv_1",
  status: "failed",
  event_type: "prompt.version.created",
  seq: 1,
  attempts_count: 1,
  is_test: false,
  created_at: new Date().toISOString(),
  last_attempt: { response_status: 500, duration_ms: 12 },
};

function withClient(ui) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={client}>{ui}</QueryClientProvider>;
}

const deliveryGets = () =>
  mocks.get.mock.calls.filter(([url]) => url.endsWith("/deliveries/"));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

describe("the request helpers", () => {
  it("turns a time window into a start time only when the request is made (review M1)", () => {
    const now = Date.UTC(2026, 9, 23, 12, 0, 0);
    expect(
      deliveryParams({ status: "", hours: "24", seq_from: "" }, now),
    ).toEqual({
      since: "2026-10-22T12:00:00.000Z",
    });
    // The query key holds the hours, so it is the same on every render.
    const filters = { status: "failed", hours: "1" };
    expect(webhookKeys.deliveries("whep_1", filters)).toEqual(
      webhookKeys.deliveries("whep_1", { ...filters }),
    );
  });

  it("bulk redelivery acts on the event type and time window shown (review S2)", () => {
    const now = Date.UTC(2026, 9, 23, 12, 0, 0);
    expect(
      bulkBody(
        "whep_1",
        { event_type: "prompt.label.changed", hours: "168" },
        now,
      ),
    ).toEqual({
      endpoint: "whep_1",
      status: "failed",
      event_type: "prompt.label.changed",
      since: "2026-10-16T12:00:00.000Z",
    });
    expect(bulkBody("whep_1", { event_type: "", hours: "" }, now)).toEqual({
      endpoint: "whep_1",
      status: "failed",
    });
  });

  it("bulk redelivery also takes the prompt or deployment and # filters (F5)", () => {
    expect(
      bulkBody("whep_1", {
        event_type: "",
        hours: "",
        subject: " prompt:p1 ",
        seq_from: "2",
        seq_to: "40",
      }),
    ).toEqual({
      endpoint: "whep_1",
      status: "failed",
      subject: "prompt:p1",
      seq_from: 2,
      seq_to: 40,
    });
    expect(bulkFiltersProblem({ subject: "prompt:p1", seq_from: "2" })).toBe(
      null,
    );
    expect(bulkFiltersProblem({ subject: "deployment:d-9" })).toBe(null);
    expect(bulkFiltersProblem({ subject: "prompt:" })).toMatch(
      /prompt:<id> or deployment:<id>/,
    );
    expect(bulkFiltersProblem({ subject: "p1" })).toMatch(/prompt:<id>/);
    expect(bulkFiltersProblem({ seq_to: "0" })).toMatch(/whole numbers/);
    expect(bulkFiltersProblem({ seq_from: "2.5" })).toMatch(/whole numbers/);
  });

  it("names the page's workspace in X-Workspace-Id (review M6)", () => {
    expect(scopedConfig("ws-b", { params: { a: 1 } })).toEqual({
      params: { a: 1 },
      headers: { "X-Workspace-Id": "ws-b" },
    });
    expect(scopedConfig(null, { params: { a: 1 } })).toEqual({
      params: { a: 1 },
    });
  });

  it("reads a settled test delivery as the test's answer (review S3)", () => {
    expect(testOutcomeOf({ status: "sending", attempts: [] })).toBeNull();
    expect(
      testOutcomeOf({
        status: "succeeded",
        attempts: [
          {
            response_status: 200,
            outcome: "success",
            duration_ms: 42,
            response_body_excerpt: "ok",
          },
        ],
      }),
    ).toEqual({
      status_code: 200,
      outcome: "success",
      latency_ms: 42,
      response_excerpt: "ok",
    });
  });
});

describe("DeliveriesCard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.get.mockImplementation(() =>
      ok({ results: [failedRow], next_cursor: null }),
    );
    mocks.post.mockImplementation((_url, body) =>
      ok({
        count: 1,
        queued: body?.dry_run ? 0 : 1,
        expired: 0,
        dry_run: !!body?.dry_run,
      }),
    );
  });

  const card = () =>
    withClient(
      <DeliveriesCard
        endpoint={{
          id: "whep_1",
          org_wide: false,
          url_host: "h.ai",
          can_manage: true,
        }}
        access={{ can_admin: true, org_admin: false }}
      />,
    );

  it("a time window reads the log once, not in a loop (review M1)", async () => {
    render(card());
    await waitFor(() => expect(deliveryGets().length).toBeGreaterThan(0));
    fireEvent.mouseDown(screen.getByRole("combobox", { name: /^Time/ }));
    fireEvent.click(
      within(await screen.findByRole("listbox")).getByText("Last hour"),
    );
    await waitFor(() =>
      expect(deliveryGets().some(([, cfg]) => cfg?.params?.since)).toBe(true),
    );
    const [, cfg] = deliveryGets().find(([, c]) => c?.params?.since);
    expect(cfg.params).not.toHaveProperty("hours");
    expect(Date.now() - Date.parse(cfg.params.since)).toBeGreaterThan(3500_000);
    expect(Date.now() - Date.parse(cfg.params.since)).toBeLessThan(3700_000);
    const settled = deliveryGets().length;
    await sleep(600);
    expect(deliveryGets().length - settled).toBeLessThanOrEqual(1);
  });

  it("Redeliver failed (N) counts, confirms and sends the filtered set (review S2)", async () => {
    render(card());
    await screen.findByRole("button", { name: "Redeliver failed (1)" });
    fireEvent.mouseDown(screen.getByRole("combobox", { name: /^Event/ }));
    fireEvent.click(
      within(await screen.findByRole("listbox")).getByText(
        "prompt.version.created",
      ),
    );
    await waitFor(() =>
      expect(
        mocks.post.mock.calls.some(
          ([, body]) =>
            body.event_type === "prompt.version.created" && body.dry_run,
        ),
      ).toBe(true),
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "Redeliver failed (1)" }),
    );
    const dialog = await screen.findByRole("dialog", {
      name: "Redeliver failed (1)",
    });
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Redeliver 1" }),
    );
    await waitFor(() =>
      expect(
        mocks.post.mock.calls.some(([, body]) => body.dry_run === false),
      ).toBe(true),
    );
    const real = mocks.post.mock.calls.find(
      ([, body]) => body.dry_run === false,
    )[1];
    expect(real).toEqual({
      endpoint: "whep_1",
      status: "failed",
      event_type: "prompt.version.created",
      dry_run: false,
    });
  });

  it("redelivers in bulk on the prompt or deployment and # filters too (F5)", async () => {
    render(card());
    await screen.findByRole("button", { name: "Redeliver failed (1)" });
    fireEvent.change(
      screen.getByRole("textbox", { name: "Prompt or deployment" }),
      { target: { value: "prompt:p1" } },
    );
    fireEvent.change(screen.getByRole("spinbutton", { name: "# from" }), {
      target: { value: "1" },
    });
    await waitFor(() =>
      expect(
        mocks.post.mock.calls.some(
          ([, body]) =>
            body.dry_run && body.subject === "prompt:p1" && body.seq_from === 1,
        ),
      ).toBe(true),
    );
    const button = await screen.findByRole("button", {
      name: "Redeliver failed (1)",
    });
    expect(button).toBeEnabled();
    fireEvent.click(button);
    const dialog = await screen.findByRole("dialog", {
      name: "Redeliver failed (1)",
    });
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Redeliver 1" }),
    );
    await waitFor(() =>
      expect(
        mocks.post.mock.calls.some(([, body]) => body.dry_run === false),
      ).toBe(true),
    );
    expect(
      mocks.post.mock.calls.find(([, body]) => body.dry_run === false)[1],
    ).toEqual({
      endpoint: "whep_1",
      status: "failed",
      subject: "prompt:p1",
      seq_from: 1,
      dry_run: false,
    });
  });

  it("reads the prompt or deployment once typing pauses, and never a half-typed one (WH-3 review S10)", async () => {
    render(card());
    await screen.findByRole("button", { name: "Redeliver failed (1)" });
    const field = screen.getByRole("textbox", { name: "Prompt or deployment" });
    for (const value of [
      "p",
      "pr",
      "prompt",
      "prompt:",
      "prompt:p",
      "prompt:p1",
    ])
      fireEvent.change(field, { target: { value } });
    await waitFor(() =>
      expect(
        deliveryGets().some(([, cfg]) => cfg?.params?.subject === "prompt:p1"),
      ).toBe(true),
    );
    const subjects = deliveryGets()
      .map(([, cfg]) => cfg?.params?.subject)
      .filter(Boolean);
    expect(subjects.every((v) => v === "prompt:p1")).toBe(true);
    // A malformed value settles with a hint and is never sent: the log keeps its last view.
    fireEvent.change(field, { target: { value: "prompt:" } });
    expect(
      await screen.findByText("Use prompt:<id> or deployment:<id>"),
    ).toBeInTheDocument();
    await sleep(300);
    expect(
      deliveryGets().some(([, cfg]) => cfg?.params?.subject === "prompt:"),
    ).toBe(false);
  });

  it("waits while a prompt or deployment is still being typed", async () => {
    render(card());
    await screen.findByRole("button", { name: "Redeliver failed (1)" });
    fireEvent.change(
      screen.getByRole("textbox", { name: "Prompt or deployment" }),
      { target: { value: "prompt:" } },
    );
    expect(
      await screen.findByRole("button", { name: "Redeliver failed (0)" }),
    ).toBeDisabled();
  });
});

describe("TestResult", () => {
  beforeEach(() => vi.clearAllMocks());

  it("follows a 202 test until its delivery settles (review S3)", async () => {
    let reads = 0;
    mocks.get.mockImplementation((url) => {
      if (url === "/webhooks/deliveries/")
        return ok({ results: [{ id: "dlv_9" }] });
      reads += 1;
      return ok(
        reads < 2
          ? { status: "sending", attempts: [] }
          : {
              status: "succeeded",
              attempts: [
                {
                  n: 1,
                  response_status: 200,
                  outcome: "success",
                  duration_ms: 42,
                },
              ],
            },
      );
    });
    render(
      withClient(
        <TestResult
          status={202}
          result={{ event_id: "evt_9", delivery_id: null }}
        />,
      ),
    );
    expect(screen.getByRole("status").textContent).toMatch(
      /^The test is on its way/,
    );
    await waitFor(
      () =>
        expect(screen.getByRole("status").textContent).toBe(
          "Your endpoint answered 200 in 42 ms.",
        ),
      { timeout: 6000 },
    );
    expect(mocks.get).toHaveBeenCalledWith("/webhooks/deliveries/", {
      params: { event: "evt_9", limit: 1 },
    });
    expect(mocks.get).toHaveBeenCalledWith("/webhooks/deliveries/dlv_9/", {});
  });
});

describe("WebhooksSection on a workspace's settings page (review M6)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.get.mockImplementation((url, cfg) => {
      if (url === "/integrations/destinations/") {
        return ok({
          destinations: [],
          webhooks: {
            available: true,
            reasons: [],
            can_view: true,
            can_manage: true,
            can_admin: true,
            org_admin: true,
            workspace_id: cfg?.headers?.["X-Workspace-Id"] || "ws-session",
          },
        });
      }
      if (url === "/webhooks/endpoints/") return ok({ results: [] });
      if (url.startsWith("/webhooks/policy/")) {
        return ok({
          allowed_hosts: [],
          members_can_manage: false,
          full_payload_allowed: false,
        });
      }
      return ok({ hosted: false, regions: [] });
    });
  });

  it("lists and adds in the page's workspace, not the session's", async () => {
    render(withClient(<WebhooksSection workspaceRouteId="ws-b" />));
    expect(
      await screen.findByText("Webhook policy for this workspace"),
    ).toBeTruthy();
    const registry = mocks.get.mock.calls.find(
      ([u]) => u === "/integrations/destinations/",
    );
    expect(registry[1].headers).toEqual({ "X-Workspace-Id": "ws-b" });
    await waitFor(() =>
      expect(
        mocks.get.mock.calls.some(([u]) => u === "/webhooks/endpoints/"),
      ).toBe(true),
    );
    const list = mocks.get.mock.calls.find(
      ([u]) => u === "/webhooks/endpoints/",
    );
    expect(list[1]).toEqual({
      params: { workspace: "ws-b" },
      headers: { "X-Workspace-Id": "ws-b" },
    });
    expect(
      mocks.get.mock.calls.some(
        ([u]) => u === "/webhooks/policy/workspace/ws-b/",
      ),
    ).toBe(true);
  });

  it("on the org page uses the session's workspace", async () => {
    render(withClient(<WebhooksSection />));
    expect(await screen.findByText("Webhook policy")).toBeTruthy();
    const registry = mocks.get.mock.calls.find(
      ([u]) => u === "/integrations/destinations/",
    );
    expect(registry[1]).toEqual({});
    await waitFor(() =>
      expect(
        mocks.get.mock.calls.some(([u]) => u === "/webhooks/endpoints/"),
      ).toBe(true),
    );
    const list = mocks.get.mock.calls.find(
      ([u]) => u === "/webhooks/endpoints/",
    );
    expect(list[1]).toEqual({ params: { workspace: "ws-session" } });
  });
});
