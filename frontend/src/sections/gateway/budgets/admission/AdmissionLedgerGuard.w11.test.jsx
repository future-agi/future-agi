import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import AdmissionCapEditor from "./AdmissionCapEditor";

// W11 (F9): daily admission caps without the gateway's spend ledger turn every
// gateway request into a 503. The org-config read carries the runtime
// `admission_cap_ledger` status; anything but "ready" (a missing field is an
// older backend: unknown) must explain why and block only switching caps on.
const { get, post, toast, context } = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  toast: vi.fn(),
  context: { org: "org-a", canEdit: true },
}));
vi.mock("src/utils/axios", () => ({
  default: { get, post },
  endpoints: {
    gateway: {
      orgConfig: {
        active: "/agentcc/org-configs/active/",
        create: "/agentcc/org-configs/",
      },
      apiKeys: "/agentcc/api-keys/",
    },
  },
}));
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: toast,
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ currentOrganizationId: context.org }),
}));
vi.mock("../../hooks/useGatewayAdmin", () => ({
  useGatewayAdmin: () => ({ canEdit: context.canEdit }),
}));

const KEYS_URL = "/agentcc/api-keys/";
// The contract's wording, shown verbatim.
const LEDGER_TEXT =
  "Daily caps can't be turned on in this release. Budgets in Gateway > Budgets still limit spend.";
// Caps already on read what caps on does instead.
const CAPS_ON_TEXT =
  "Daily caps are on. A key under a cap can make only non-streaming chat requests, and its requests over the day's cap are refused. Streaming and Gemini requests from capped keys are refused, and native message and response requests are refused for every key while daily caps are on.";
const MISSING = Symbol("field missing");
const REFUSAL = {
  statusCode: 409,
  code: "admission_cap_ledger_unavailable",
  message: LEDGER_TEXT,
  ledger_status: "absent",
};

const cap = (limit) => ({
  limit,
  period: "daily",
  activated_at: "2026-11-01T00:00:00Z",
});
function withLedger(config, ledger) {
  return ledger === MISSING
    ? config
    : { ...config, admission_cap_ledger: ledger };
}
function capsOff(ledger, revision = 5) {
  return withLedger(
    {
      organization: "org-a",
      version: 3,
      budgets: {
        enabled: true,
        admission_caps_enabled: false,
        policy_revision: revision,
      },
      routing: { agents: {} },
    },
    ledger,
  );
}
function capsOn(ledger, revision = 5) {
  return withLedger(
    {
      organization: "org-a",
      version: 3,
      budgets: {
        enabled: true,
        admission_caps_enabled: true,
        policy_revision: revision,
        fail_mode: "closed",
        no_max_tokens: "clamp",
        agents: { worker: cap(5) },
        key_ids: { stable: cap(2) },
      },
      routing: {
        agents: {
          key_bindings: { stable: "worker" },
          unpriced_under_cap: "reject",
        },
      },
    },
    ledger,
  );
}
const response = (result) => ({ data: { status: true, result } });

let queryClient;
let current;
// Beside `result` on the active read (an org with no saved config reads as
// result null with the status next to it), and a failing current read.
let envelope;
let readFails;
function readsAs(config) {
  current = config;
}
function panel() {
  return (
    <QueryClientProvider client={queryClient}>
      <AdmissionCapEditor />
    </QueryClientProvider>
  );
}
async function loaded() {
  await screen.findByTestId("admission-policy-current");
}
async function keysSettled() {
  await waitFor(() =>
    expect(screen.getByTestId("admission-keys-status")).toHaveAttribute(
      "data-state",
      "ready",
    ),
  );
}
async function openDraft() {
  fireEvent.click(screen.getByTestId("admission-edit"));
  await keysSettled();
}
// Caps off and no ready ledger: the action that turns caps on is shown,
// disabled, opens no draft, reads no keys and sends nothing.
function expectSwitchDisabled() {
  const entry = screen.getByTestId("admission-edit");
  expect(entry).toBeDisabled();
  fireEvent.click(entry);
  expect(screen.queryByTestId("admission-draft")).toBeNull();
  expect(get.mock.calls.some(([url]) => url === KEYS_URL)).toBe(false);
  expect(post).not.toHaveBeenCalled();
}
function addKeyCap(id, limit) {
  fireEvent.click(screen.getByRole("button", { name: "Add key cap" }));
  fireEvent.change(screen.getByLabelText("Key 1 identity"), {
    target: { value: id },
  });
  fireEvent.change(screen.getByLabelText("Key 1 limit (USD)"), {
    target: { value: limit },
  });
}

beforeEach(() => {
  queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  get.mockReset();
  post.mockReset();
  toast.mockReset();
  context.org = "org-a";
  context.canEdit = true;
  envelope = {};
  readFails = null;
  get.mockImplementation(async (url) => {
    if (url === KEYS_URL)
      return {
        data: {
          status: true,
          result: [
            {
              gateway_key_id: "stable",
              name: "Stable key",
              status: "active",
              organization: "org-a",
            },
          ],
        },
      };
    if (readFails) throw readFails;
    return { data: { ...response(current).data, ...envelope } };
  });
  readsAs(capsOff("ready"));
});

describe("daily admission caps need the gateway's spend ledger (W11)", () => {
  it.each([
    ["absent", () => capsOff("absent")],
    ["unavailable", () => capsOff("unavailable")],
    ["unknown", () => capsOff("unknown")],
    ["missing from an older backend's read", () => capsOff(MISSING)],
    ["missing because no config is saved yet", () => null],
  ])(
    "ledger %s: explains why and blocks only switching caps on",
    async (_name, config) => {
      readsAs(config());
      render(panel());
      await loaded();
      expect(screen.getByText(LEDGER_TEXT)).toBeInTheDocument();
      // True on cloud and self-hosted: it blames no setup and names no one to ask.
      expect(
        screen.getByTestId("admission-ledger-notice"),
      ).not.toHaveTextContent(/deployment|administrator/i);
      // The switch that turns caps on is shown, disabled; nothing is sent.
      expectSwitchDisabled();
    },
  );

  it.each([
    ["absent", "absent"],
    ["missing", MISSING],
  ])(
    "ledger %s with caps already on: the reason shows and limit edits still save",
    async (_name, ledger) => {
      readsAs(capsOn(ledger));
      render(panel());
      await loaded();
      expect(screen.getByText(CAPS_ON_TEXT)).toBeInTheDocument();
      expect(screen.queryByText(LEDGER_TEXT)).toBeNull();
      expect(screen.getByTestId("admission-deactivate")).toBeEnabled();
      await openDraft();
      fireEvent.change(screen.getByLabelText("Agent 1 limit (USD)"), {
        target: { value: "6" },
      });
      expect(screen.getByTestId("admission-activate")).toBeEnabled();
      post.mockResolvedValue(
        response({ ...capsOn(ledger, 6), gateway_synced: true }),
      );
      readsAs(capsOn(ledger, 6));
      fireEvent.click(screen.getByTestId("admission-activate"));
      await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
      expect(post.mock.calls[0][1].budgets).toMatchObject({
        admission_caps_enabled: true,
        policy_revision: 5,
      });
      expect(post.mock.calls[0][1].budgets.agents.worker.limit).toBe(6);
    },
  );

  it("never blocks turning caps off when the ledger is absent", async () => {
    readsAs(capsOn("absent"));
    render(panel());
    await loaded();
    fireEvent.click(screen.getByTestId("admission-deactivate"));
    post.mockResolvedValue(response(capsOff("absent", 6)));
    readsAs(capsOff("absent", 6));
    fireEvent.click(screen.getByRole("button", { name: "Deactivate caps" }));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(post.mock.calls[0][1].budgets).toMatchObject({
      admission_caps_enabled: false,
      policy_revision: 5,
    });
    await screen.findByText("Daily caps inactive");
  });

  it("ledger ready: no notice, and switching caps on saves as before", async () => {
    render(panel());
    await loaded();
    expect(screen.queryByText(LEDGER_TEXT)).toBeNull();
    await openDraft();
    addKeyCap("stable", "2");
    expect(screen.getByTestId("admission-activate")).toBeEnabled();
    post.mockResolvedValue(
      response({ ...capsOn("ready", 6), gateway_synced: true }),
    );
    readsAs(capsOn("ready", 6));
    fireEvent.click(screen.getByTestId("admission-activate"));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    const sent = post.mock.calls[0][1];
    expect(sent.budgets).toMatchObject({
      admission_caps_enabled: true,
      policy_revision: 5,
    });
    expect(sent.budgets.key_ids.stable.limit).toBe(2);
    // Runtime state is read-only: it never goes back on a write.
    expect(sent).not.toHaveProperty("admission_cap_ledger");
    expect(sent.budgets).not.toHaveProperty("admission_cap_ledger");
  });

  it("a save refused for the ledger shows the plain reason inline and keeps the draft", async () => {
    render(panel());
    await loaded();
    await openDraft();
    addKeyCap("stable", "2");
    fireEvent.change(screen.getByLabelText("Per-run ceiling (USD)"), {
      target: { value: "1.5" },
    });
    post.mockRejectedValue(REFUSAL);
    // The read after the refusal may still carry a cached "ready".
    fireEvent.click(screen.getByTestId("admission-activate"));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(await screen.findByText(LEDGER_TEXT)).toBeInTheDocument();
    expect(await screen.findByLabelText("Key 1 identity")).toHaveValue(
      "stable",
    );
    expect(screen.getByLabelText("Key 1 limit (USD)")).toHaveValue(2);
    expect(screen.getByLabelText("Per-run ceiling (USD)")).toHaveValue(1.5);
    await keysSettled();
    expect(screen.getByTestId("admission-activate")).toBeDisabled();
    expect(screen.queryByText(/Something went wrong/i)).toBeNull();
    expect(screen.queryByText(/Stale draft discarded/)).toBeNull();
    expect(screen.queryByText(/admission_cap_ledger_unavailable/)).toBeNull();
    expect(toast).not.toHaveBeenCalled();
    expect(post).toHaveBeenCalledTimes(1);
  });

  it("does not bring a refused draft back over a policy that changed meanwhile", async () => {
    render(panel());
    await loaded();
    await openDraft();
    addKeyCap("stable", "2");
    post.mockRejectedValue(REFUSAL);
    readsAs(capsOff("ready", 6));
    fireEvent.click(screen.getByTestId("admission-activate"));
    expect(await screen.findByText(LEDGER_TEXT)).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByTestId("admission-policy-current")).toHaveTextContent(
        "Revision 6",
      ),
    );
    expect(screen.queryByTestId("admission-draft")).toBeNull();
    expect(post).toHaveBeenCalledTimes(1);
  });

  it("keeps a viewer read-only: the reason shows and no control is added", async () => {
    context.canEdit = false;
    readsAs(capsOff("absent"));
    render(panel());
    await loaded();
    expect(
      screen.getByText(/Only an Owner or Admin can activate/),
    ).toBeInTheDocument();
    expect(screen.getByText(LEDGER_TEXT)).toBeInTheDocument();
    expect(screen.queryByTestId("admission-edit")).toBeNull();
    expect(screen.queryByTestId("admission-deactivate")).toBeNull();
    expect(screen.queryByTestId("admission-activate")).toBeNull();
    expect(get.mock.calls.some(([url]) => url === KEYS_URL)).toBe(false);
    expect(post).not.toHaveBeenCalled();
  });
});

// r2: an org with no saved config reads as result null, with the status
// beside it on the read; the saved config's own status comes first.
describe("the spend ledger status beside the read (W11 r2)", () => {
  it("ledger ready with no config saved yet: no notice, and the first policy activates", async () => {
    readsAs(null);
    envelope = { admission_cap_ledger: "ready" };
    render(panel());
    await loaded();
    expect(screen.queryByText(LEDGER_TEXT)).toBeNull();
    await openDraft();
    addKeyCap("stable", "2");
    expect(screen.getByTestId("admission-activate")).toBeEnabled();
    post.mockResolvedValue(
      response({ ...capsOn("ready", 1), gateway_synced: true }),
    );
    readsAs(capsOn("ready", 1));
    fireEvent.click(screen.getByTestId("admission-activate"));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    const sent = post.mock.calls[0][1];
    expect(sent.budgets).toMatchObject({
      admission_caps_enabled: true,
      policy_revision: 0,
    });
    expect(sent.budgets.key_ids.stable.limit).toBe(2);
    expect(sent).not.toHaveProperty("admission_cap_ledger");
    expect(sent.budgets).not.toHaveProperty("admission_cap_ledger");
  });

  it.each(["absent", "unavailable", "unknown"])(
    "ledger %s with no config saved yet: explains why and blocks only switching caps on",
    async (ledger) => {
      readsAs(null);
      envelope = { admission_cap_ledger: ledger };
      render(panel());
      await loaded();
      expect(screen.getByText(LEDGER_TEXT)).toBeInTheDocument();
      expectSwitchDisabled();
    },
  );

  it("a saved config's own status outranks the one beside it", async () => {
    readsAs(capsOff("absent"));
    envelope = { admission_cap_ledger: "ready" };
    render(panel());
    await loaded();
    expect(screen.getByText(LEDGER_TEXT)).toBeInTheDocument();
    expectSwitchDisabled();
  });

  it("a saved config without the field takes the status beside it", async () => {
    readsAs(capsOff(MISSING));
    envelope = { admission_cap_ledger: "ready" };
    render(panel());
    await loaded();
    expect(screen.queryByText(LEDGER_TEXT)).toBeNull();
    await openDraft();
    addKeyCap("stable", "2");
    expect(screen.getByTestId("admission-activate")).toBeEnabled();
  });

  it("keeps the reason and the edits when the read after a ledger refusal fails", async () => {
    render(panel());
    await loaded();
    await openDraft();
    addKeyCap("stable", "2");
    fireEvent.change(screen.getByLabelText("Per-run ceiling (USD)"), {
      target: { value: "1.5" },
    });
    post.mockRejectedValue(REFUSAL);
    readFails = { statusCode: 503, message: "Service unavailable" };
    fireEvent.click(screen.getByTestId("admission-activate"));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    // The read's own failure may show beside the reason, never instead of it.
    expect(await screen.findByTestId("admission-error")).toBeInTheDocument();
    expect(await screen.findByText(LEDGER_TEXT)).toBeInTheDocument();
    expect(screen.getByLabelText("Key 1 identity")).toHaveValue("stable");
    expect(screen.getByLabelText("Key 1 limit (USD)")).toHaveValue(2);
    expect(screen.getByLabelText("Per-run ceiling (USD)")).toHaveValue(1.5);
    // Still the revision the edits were based on, not a blank read's 0.
    expect(screen.getByText(/based on revision 5/)).toBeInTheDocument();
    await keysSettled();
    expect(screen.getByTestId("admission-activate")).toBeDisabled();
    fireEvent.click(screen.getByTestId("admission-activate"));
    expect(post).toHaveBeenCalledTimes(1);
    expect(toast).not.toHaveBeenCalled();
    expect(screen.queryByText(/Something went wrong/i)).toBeNull();
    expect(screen.queryByText(/Stale draft discarded/)).toBeNull();
    expect(screen.queryByText(/admission_cap_ledger_unavailable/)).toBeNull();
  });
});
