import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import AdmissionCapEditor from "./AdmissionCapEditor";
import { KEYS_URL, createFakeBackend } from "./testing/serverFake";

// No gateway reports the spend ledger that switching daily admission caps on
// needs, so the server refuses that switch. The console shows the switch,
// disabled, with one true sentence beside it, and sends nothing. A stored "on"
// policy stays shown as on, says what caps on does, and can still be edited or
// turned off.
const { get, post, toast, context } = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  toast: vi.fn(),
  context: { canEdit: true },
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
  useOrganization: () => ({ currentOrganizationId: "org-a" }),
}));
vi.mock("../../hooks/useGatewayAdmin", () => ({
  useGatewayAdmin: () => ({ canEdit: context.canEdit }),
}));

const SENTENCE =
  "Daily caps can't be turned on in this release. Budgets in Gateway > Budgets still limit spend.";
const CAPS_ON =
  "Daily caps are on. A key under a cap can make only non-streaming chat requests, and its requests over the day's cap are refused. Streaming and Gemini requests from capped keys are refused, and native message and response requests are refused for every key while daily caps are on.";
const NOT_READY = [
  ["absent", "absent"],
  ["unavailable", "unavailable"],
  ["unknown", "unknown"],
  ["not reported", undefined],
];
const ANY_LEDGER = [...NOT_READY, ["ready", "ready"]];

const cap = (limit) => ({
  limit,
  period: "daily",
  activated_at: "2026-11-01T00:00:00Z",
});
function capsOff({ runCeiling = false } = {}) {
  return {
    organization: "org-a",
    version: 3,
    budgets: {
      enabled: true,
      admission_caps_enabled: false,
      policy_revision: 5,
    },
    routing: { agents: runCeiling ? { max_run_usd: 3 } : {} },
  };
}
function capsOn() {
  return {
    organization: "org-a",
    version: 3,
    budgets: {
      enabled: true,
      admission_caps_enabled: true,
      policy_revision: 5,
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
  };
}

let queryClient;
let backend;
function serve({ ledger, ...options }) {
  backend = createFakeBackend({
    keys: [
      {
        gateway_key_id: "stable",
        name: "Stable key",
        status: "active",
        organization: "org-a",
      },
    ],
    ledger,
    ...options,
  });
  // The fake reads a missing status as "ready"; "not reported" means the read
  // carries no status at all.
  if (ledger === undefined) backend.state.ledger = undefined;
  get.mockImplementation(backend.get);
  post.mockImplementation(backend.post);
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
const keyReads = () => backend.state.gets.filter(({ url }) => url === KEYS_URL);

// The switch is shown, cannot be used, and says why in one sentence that
// screen readers hear with it.
function expectDisabledWithSentence(label) {
  const entry = screen.getByTestId("admission-edit");
  expect(entry).toBeVisible();
  expect(entry).toHaveTextContent(label);
  expect(entry).toBeDisabled();
  expect(screen.getAllByText(SENTENCE)).toHaveLength(1);
  const notice = screen.getByTestId("admission-ledger-notice");
  expect(notice).toHaveTextContent(SENTENCE);
  expect(notice.id).not.toBe("");
  expect(entry.getAttribute("aria-describedby") || "").toContain(notice.id);
  return entry;
}

beforeEach(() => {
  queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  get.mockReset();
  post.mockReset();
  toast.mockReset();
  context.canEdit = true;
});

describe("daily admission caps can't be switched on in this release", () => {
  it.each(NOT_READY)(
    "caps off, ledger %s: the switch is visible and disabled, with the sentence beside it",
    async (_name, ledger) => {
      serve({ config: capsOff(), ledger });
      render(panel());
      await loaded();
      expect(screen.getByText("Daily caps inactive")).toBeInTheDocument();
      const entry = expectDisabledWithSentence("Create activation draft");
      fireEvent.click(entry);
      expect(screen.queryByTestId("admission-draft")).toBeNull();
      expect(screen.queryByTestId("admission-activate")).toBeNull();
      expect(backend.state.posts).toHaveLength(0);
      expect(post).not.toHaveBeenCalled();
      expect(keyReads()).toHaveLength(0);
      expect(toast).not.toHaveBeenCalled();
    },
  );

  it("no config saved yet, ledger absent: the same disabled switch and sentence", async () => {
    serve({ config: null, ledger: "absent" });
    render(panel());
    await loaded();
    const entry = expectDisabledWithSentence("Create activation draft");
    fireEvent.click(entry);
    expect(screen.queryByTestId("admission-draft")).toBeNull();
    expect(post).not.toHaveBeenCalled();
    expect(keyReads()).toHaveLength(0);
  });

  it("caps off with a stored run ceiling: Edit and activate is disabled too, Deactivate stays", async () => {
    serve({ config: capsOff({ runCeiling: true }), ledger: "absent" });
    render(panel());
    await loaded();
    const entry = expectDisabledWithSentence("Edit and activate caps");
    fireEvent.click(entry);
    expect(screen.queryByTestId("admission-draft")).toBeNull();
    expect(screen.getByTestId("admission-deactivate")).toBeEnabled();
    expect(post).not.toHaveBeenCalled();
  });

  it("the sentence says nothing about Redis or adding a spend ledger", async () => {
    serve({ config: capsOff(), ledger: "absent" });
    render(panel());
    await loaded();
    const section = screen.getByTestId("admission-cap-policy");
    expect(section).not.toHaveTextContent(/redis/i);
    expect(section).not.toHaveTextContent(/spend ledger/i);
    expect(section).not.toHaveTextContent(/run your own gateway/i);
  });

  it("a viewer sees the same sentence and no control", async () => {
    context.canEdit = false;
    serve({ config: capsOff(), ledger: "absent" });
    render(panel());
    await loaded();
    expect(screen.getAllByText(SENTENCE)).toHaveLength(1);
    expect(screen.queryByTestId("admission-edit")).toBeNull();
    expect(post).not.toHaveBeenCalled();
  });

  it("ledger ready: the switch stays enabled and no sentence shows", async () => {
    serve({ config: capsOff(), ledger: "ready" });
    render(panel());
    await loaded();
    expect(screen.queryByText(SENTENCE)).toBeNull();
    const entry = screen.getByTestId("admission-edit");
    expect(entry).toBeEnabled();
    expect(entry).not.toHaveAttribute("aria-describedby");
    fireEvent.click(entry);
    expect(await screen.findByTestId("admission-draft")).toBeInTheDocument();
  });
});

describe("a stored on policy is kept and shown on", () => {
  it.each(ANY_LEDGER)(
    "caps on, ledger %s: still shown on, with the sentence, and nothing is written",
    async (_name, ledger) => {
      serve({ config: capsOn(), ledger });
      render(panel());
      await loaded();
      expect(
        screen.getByText("Daily caps active in saved policy"),
      ).toBeInTheDocument();
      expect(screen.getByTestId("admission-policy-current")).toHaveTextContent(
        "worker",
      );
      expect(screen.getAllByText(CAPS_ON)).toHaveLength(1);
      expect(screen.queryByText(SENTENCE)).toBeNull();
      // Turning caps off and editing caps already on stay available: the
      // server allows both without the spend ledger.
      expect(screen.getByTestId("admission-deactivate")).toBeEnabled();
      expect(screen.getByTestId("admission-edit")).toBeEnabled();
      await waitFor(() => expect(backend.state.gets.length).toBeGreaterThan(0));
      expect(post).not.toHaveBeenCalled();
      expect(backend.state.config.budgets.admission_caps_enabled).toBe(true);
    },
  );

  it("caps on, ledger absent: a limit edit saves and keeps caps on", async () => {
    serve({ config: capsOn(), ledger: "absent" });
    render(panel());
    await loaded();
    fireEvent.click(screen.getByTestId("admission-edit"));
    await waitFor(() =>
      expect(screen.getByTestId("admission-keys-status")).toHaveAttribute(
        "data-state",
        "ready",
      ),
    );
    fireEvent.change(screen.getByLabelText("Agent 1 limit (USD)"), {
      target: { value: "6" },
    });
    expect(screen.getByTestId("admission-activate")).toBeEnabled();
    fireEvent.click(screen.getByTestId("admission-activate"));
    await waitFor(() => expect(backend.state.posts).toHaveLength(1));
    expect(backend.state.posts[0].body.budgets).toMatchObject({
      admission_caps_enabled: true,
      policy_revision: 5,
    });
    await waitFor(() =>
      expect(screen.getByTestId("admission-policy-current")).toHaveTextContent(
        "Revision 6",
      ),
    );
    expect(
      screen.getByText("Daily caps active in saved policy"),
    ).toBeInTheDocument();
  });
});
