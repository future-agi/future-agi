import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";
import OrgConfigEditor from "./OrgConfigEditor";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  KEYS_URL,
  createFakeBackend,
} from "../budgets/admission/testing/serverFake";
let queryClient;

const { get, post, context, legacySave, stub } = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  legacySave: vi.fn(),
  context: { org: "org-a", canEdit: true },
  stub: () => ({ default: () => null }),
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
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ currentOrganizationId: context.org }),
}));
vi.mock("../hooks/useGatewayAdmin", () => ({
  useGatewayAdmin: () => ({ canEdit: context.canEdit }),
}));
vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "gw-1" }),
}));
vi.mock("../providers/hooks/useGatewayConfig", () => ({
  useGatewayConfig: () => ({
    data: { org_sections_enforced: ["budgets", "routing"] },
  }),
}));
vi.mock("../hooks/useConfigPatch", () => ({
  useConfigPatch: () => ({ mutate: legacySave, isPending: false }),
  buildConfigPatch: (_saved, draft) => draft,
  guardrailsForSave: (value) => value,
}));
vi.mock("./ProviderConfigTab", stub);
vi.mock("./GuardrailConfigTab", stub);
vi.mock("./RoutingConfigTab", stub);
vi.mock("./CacheConfigTab", stub);
vi.mock("./RateLimitingConfigTab", stub);
vi.mock("./BudgetsConfigTab", stub);
vi.mock("./SecurityConfigTab", stub);
vi.mock("./AlertingConfigTab", stub);
vi.mock("./ModelDatabaseConfigTab", stub);
vi.mock("./ModelMapConfigTab", stub);

const orgKey = (id, name = id, status = "active") => ({
  gateway_key_id: id,
  name,
  status,
  organization: "org-a",
});
const cap = (limit) => ({
  limit,
  period: "daily",
  activated_at: "2026-10-27T12:00:00Z",
});
function stored({
  enabled = true,
  agents = {},
  keyIds = {},
  routingAgents = {},
  routing = {},
  revision = 7,
} = {}) {
  return {
    organization: "org-a",
    version: 4,
    created_by: "creator-not-activator",
    budgets: {
      enabled: true,
      admission_caps_enabled: enabled,
      policy_revision: revision,
      fail_mode: "closed",
      no_max_tokens: "clamp",
      agents,
      key_ids: keyIds,
    },
    routing: { agents: routingAgents, ...routing },
  };
}
let backend;
function serve(options) {
  backend = createFakeBackend(options);
  get.mockImplementation(backend.get);
  post.mockImplementation(backend.post);
  return backend;
}
function editor() {
  return (
    <QueryClientProvider client={queryClient}>
      <OrgConfigEditor
        open
        onClose={vi.fn()}
        initialConfig={null}
        defaultTab="budgets"
      />
    </QueryClientProvider>
  );
}
async function openDraft() {
  fireEvent.click(await screen.findByTestId("admission-edit"));
  await waitFor(() =>
    expect(screen.getByTestId("admission-keys-status")).toHaveAttribute(
      "data-state",
      expect.stringMatching(/^(ready|error)$/),
    ),
  );
}
function addAgentCap(id, limit) {
  fireEvent.click(screen.getByRole("button", { name: "Add agent cap" }));
  fireEvent.change(screen.getByLabelText("Agent 1 identity"), {
    target: { value: id },
  });
  fireEvent.change(screen.getByLabelText("Agent 1 limit (USD)"), {
    target: { value: limit },
  });
}
function bindManually(index, key, agent) {
  fireEvent.click(screen.getByRole("button", { name: "Add binding" }));
  fireEvent.change(screen.getByLabelText(`Binding ${index} key ID`), {
    target: { value: key },
  });
  fireEvent.change(screen.getByLabelText(`Binding ${index} agent`), {
    target: { value: agent },
  });
}
// The acknowledgement, by its stable test id and role (its label is plain
// words and names no code).
const ackBox = () =>
  within(screen.getByTestId("admission-unbound-ack")).getByRole("checkbox");
beforeEach(() => {
  queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  get.mockReset();
  post.mockReset();
  legacySave.mockReset();
  context.org = "org-a";
  context.canEdit = true;
});

describe("agent caps need key bindings (FE-01)", () => {
  it("refuses agent caps with no binding and discloses that every execution key would be refused", async () => {
    serve({ config: null, keys: [orgKey("key_a1", "Primary")] });
    render(editor());
    await openDraft();
    addAgentCap("worker", "5");
    expect(screen.getByTestId("admission-activate")).toBeDisabled();
    expect(screen.getByTestId("admission-validation")).toHaveTextContent(
      /call_cap_unenforceable/,
    );
    expect(
      screen.getByTestId("admission-binding-disclosure"),
    ).toHaveTextContent(/402 call_cap_unenforceable/);
    expect(
      screen.getByTestId("admission-binding-disclosure"),
    ).toHaveTextContent(/every execution key/i);
    fireEvent.click(screen.getByTestId("admission-activate"));
    expect(backend.state.posts).toHaveLength(0);
  });
  it("lists the active keys that would be refused, needs an acknowledgment, and matches the gateway's real verdict", async () => {
    serve({
      config: null,
      keys: [
        orgKey("key_a1", "Primary"),
        orgKey("key_a2", "Batch"),
        orgKey("key_old", "Retired", "revoked"),
      ],
    });
    render(editor());
    await openDraft();
    addAgentCap("worker", "5");
    fireEvent.click(await screen.findByRole("button", { name: "Bind key_a1" }));
    fireEvent.change(screen.getByLabelText("Binding 1 agent"), {
      target: { value: "worker" },
    });
    const unbound = screen.getByTestId("admission-unbound-keys");
    expect(unbound).toHaveTextContent("key_a2");
    expect(unbound).toHaveTextContent("Batch");
    expect(unbound).not.toHaveTextContent("key_a1");
    expect(unbound).not.toHaveTextContent("key_old");
    expect(screen.getByTestId("admission-activate")).toBeDisabled();
    fireEvent.click(ackBox());
    expect(screen.getByTestId("admission-activate")).toBeEnabled();
    fireEvent.click(screen.getByTestId("admission-activate"));
    await waitFor(() => expect(backend.state.posts).toHaveLength(1));
    // What the form disclosed is what the gateway does with the saved policy.
    expect(backend.verdict("key_a1")).toEqual({
      admitted: true,
      agent: "worker",
    });
    expect(backend.verdict("key_a2")).toEqual({
      admitted: false,
      status: 402,
      code: "call_cap_unenforceable",
    });
  });
  it("withdraws the acknowledgment when the set of refused keys changes", async () => {
    serve({
      config: null,
      keys: [orgKey("key_a1"), orgKey("key_a2"), orgKey("key_a3")],
    });
    render(editor());
    await openDraft();
    addAgentCap("worker", "5");
    bindManually(1, "key_a1", "worker");
    bindManually(2, "key_a2", "worker");
    fireEvent.click(ackBox());
    expect(screen.getByTestId("admission-activate")).toBeEnabled();
    fireEvent.click(
      screen.getByRole("button", { name: "Remove binding 2 from draft" }),
    );
    expect(ackBox()).not.toBeChecked();
    expect(screen.getByTestId("admission-activate")).toBeDisabled();
    expect(screen.getByTestId("admission-unbound-keys")).toHaveTextContent(
      "key_a2",
    );
  });
  it("needs no acknowledgment once every active key is bound", async () => {
    serve({ config: null, keys: [orgKey("key_a1"), orgKey("key_a2")] });
    render(editor());
    await openDraft();
    addAgentCap("worker", "5");
    bindManually(1, "key_a1", "worker");
    bindManually(2, "key_a2", "worker");
    expect(screen.queryByRole("checkbox", { name: /I understand/ })).toBeNull();
    expect(screen.getByTestId("admission-activate")).toBeEnabled();
  });
  it("asks for an acknowledgment that says the key list could not be read", async () => {
    serve({
      config: null,
      keys: [],
      keysError: { statusCode: 503, message: "Unavailable" },
    });
    render(editor());
    await openDraft();
    expect(screen.getByTestId("admission-keys-status")).toHaveAttribute(
      "data-state",
      "error",
    );
    addAgentCap("worker", "5");
    bindManually(1, "key_a1", "worker");
    expect(screen.getByTestId("admission-activate")).toBeDisabled();
    expect(ackBox()).toBeInTheDocument();
    expect(
      screen.getByRole("checkbox", { name: /could not be read/ }),
    ).toBeInTheDocument();
    fireEvent.click(ackBox());
    expect(screen.getByTestId("admission-activate")).toBeEnabled();
  });
  it("does not over-block key caps alone: no binding, no acknowledgment, every key admitted", async () => {
    serve({ config: null, keys: [orgKey("key_a1"), orgKey("key_a2")] });
    render(editor());
    await openDraft();
    fireEvent.click(screen.getByRole("button", { name: "Add key cap" }));
    fireEvent.change(screen.getByLabelText("Key 1 identity"), {
      target: { value: "key_a1" },
    });
    fireEvent.change(screen.getByLabelText("Key 1 limit (USD)"), {
      target: { value: "2" },
    });
    expect(screen.queryByRole("checkbox", { name: /I understand/ })).toBeNull();
    expect(screen.queryByTestId("admission-validation")).toBeNull();
    fireEvent.click(screen.getByTestId("admission-activate"));
    await waitFor(() => expect(backend.state.posts).toHaveLength(1));
    expect(backend.state.posts[0].body.routing.agents).not.toHaveProperty(
      "key_bindings",
    );
    expect(backend.verdict("key_a1").admitted).toBe(true);
    expect(backend.verdict("key_a2").admitted).toBe(true);
  });
  it("refuses an unmatched agent identity but only warns about an uncapped agent", async () => {
    serve({ config: null, keys: [orgKey("key_a1"), orgKey("key_a2")] });
    render(editor());
    await openDraft();
    addAgentCap("worker", "5");
    bindManually(1, "key_a1", "workr");
    expect(screen.getByTestId("admission-validation")).toHaveTextContent(
      /worker/,
    );
    expect(screen.getByTestId("admission-activate")).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Binding 1 agent"), {
      target: { value: "worker" },
    });
    bindManually(2, "key_a2", "internal");
    expect(screen.getByTestId("admission-warnings")).toHaveTextContent(
      /internal/,
    );
    expect(screen.getByTestId("admission-warnings")).toHaveTextContent(
      /no agent cap/i,
    );
    fireEvent.click(screen.getByTestId("admission-activate"));
    await waitFor(() => expect(backend.state.posts).toHaveLength(1));
    expect(backend.state.posts[0].body.routing.agents.key_bindings).toEqual({
      key_a1: "worker",
      key_a2: "internal",
    });
    expect(backend.verdict("key_a2")).toEqual({
      admitted: true,
      agent: "internal",
    });
  });
  it("warns before saving when a rollout candidate does not repeat a new binding, then shows the server's refusal", async () => {
    serve({
      config: stored({
        agents: { worker: cap(5) },
        routingAgents: { key_bindings: { key_a1: "worker" } },
        routing: {
          rollout: {
            routing: { agents: { keyBindings: { key_a1: "worker" } } },
          },
        },
      }),
      keys: [orgKey("key_a1"), orgKey("key_a2")],
    });
    render(editor());
    await openDraft();
    bindManually(2, "key_a2", "worker");
    expect(screen.getByTestId("admission-warnings")).toHaveTextContent(
      /rollout/i,
    );
    expect(screen.getByTestId("admission-warnings")).toHaveTextContent(
      "key_a2",
    );
    fireEvent.click(screen.getByTestId("admission-activate"));
    await screen.findByText(/cannot drop or change stored bindings/);
    expect(backend.state.posts).toHaveLength(1);
    expect(screen.getByTestId("admission-notice")).toHaveTextContent(
      /nothing is retried automatically|review it before trying again/i,
    );
  });
  it("keeps Activate disabled while the organization's keys are still loading", async () => {
    const fake = serve({ config: null, keys: [] });
    let release;
    get.mockImplementation((url, options) =>
      url === KEYS_URL
        ? new Promise((resolve) => {
            release = () =>
              resolve({ data: { status: true, result: [orgKey("key_a1")] } });
          })
        : fake.get(url, options),
    );
    render(editor());
    fireEvent.click(await screen.findByTestId("admission-edit"));
    addAgentCap("worker", "5");
    bindManually(1, "key_a1", "worker");
    expect(screen.getByTestId("admission-keys-status")).toHaveAttribute(
      "data-state",
      "loading",
    );
    expect(screen.getByTestId("admission-activate")).toBeDisabled();
    await act(async () => release());
    await waitFor(() =>
      expect(screen.getByTestId("admission-activate")).toBeEnabled(),
    );
  });
  it("recovers when the key list is reloaded after a failed read", async () => {
    serve({
      config: null,
      keys: [orgKey("key_a1")],
      keysError: { statusCode: 503, message: "Unavailable" },
    });
    render(editor());
    await openDraft();
    expect(screen.getByTestId("admission-keys-status")).toHaveAttribute(
      "data-state",
      "error",
    );
    backend.state.keysError = null;
    fireEvent.click(screen.getByRole("button", { name: "Reload keys" }));
    await waitFor(() =>
      expect(screen.getByTestId("admission-keys-status")).toHaveAttribute(
        "data-state",
        "ready",
      ),
    );
  });
  it("does not read the organization's keys for a read-only role", async () => {
    serve({ config: stored(), keys: [orgKey("key_a1")] });
    context.canEdit = false;
    render(editor());
    await screen.findByTestId("admission-policy-current");
    expect(backend.state.gets.some((call) => call.url === KEYS_URL)).toBe(
      false,
    );
  });
  it("sends the organization header on the key read", async () => {
    serve({ config: stored(), keys: [orgKey("stable")] });
    render(editor());
    await openDraft();
    const read = backend.state.gets.find((call) => call.url === KEYS_URL);
    expect(read.options.headers).toEqual({ "X-Organization-Id": "org-a" });
  });
});

describe("read-back of a policy that cannot be enforced", () => {
  it("warns when stored agent caps have no key binding", async () => {
    serve({
      config: stored({ agents: { worker: cap(5) } }),
      keys: [orgKey("key_a1")],
    });
    render(editor());
    await screen.findByTestId("admission-policy-current");
    expect(screen.getByTestId("admission-bindings-missing")).toHaveTextContent(
      /402 call_cap_unenforceable/,
    );
  });
  it("shows the stored bindings from either spelling", async () => {
    serve({
      config: stored({
        agents: { worker: cap(5) },
        routingAgents: { keyBindings: { key_a1: "worker" } },
      }),
      keys: [orgKey("key_a1")],
    });
    render(editor());
    await screen.findByTestId("admission-policy-current");
    expect(screen.getByTestId("admission-bindings-current")).toHaveTextContent(
      "key_a1",
    );
    expect(screen.queryByTestId("admission-bindings-missing")).toBeNull();
  });
});

describe("stored run-ceiling aliases (MONEY-01)", () => {
  const aliased = (agents, over = {}) =>
    stored({
      agents: { worker: cap(5) },
      routingAgents: { keyBindings: { key_a1: "worker" }, ...agents },
      ...over,
    });
  it.each([
    ["maxRunUSD", { maxRunUSD: 0.1 }, "$0.1"],
    ["maxRunUsd", { maxRunUsd: 0.2 }, "$0.2"],
    ["maxRunUsd over maxRunUSD", { maxRunUsd: 0.2, maxRunUSD: 0.1 }, "$0.2"],
  ])(
    "shows %s as the run ceiling, then a blank run field removes every spelling with CAS",
    async (_name, agents, shown) => {
      serve({ config: aliased(agents), keys: [orgKey("key_a1")] });
      render(editor());
      await screen.findByTestId("admission-policy-current");
      expect(screen.getByTestId("admission-enforced-run")).toHaveTextContent(
        shown,
      );
      await openDraft();
      expect(screen.getByLabelText("Per-run ceiling (USD)")).toHaveValue(
        Number(shown.slice(1)),
      );
      fireEvent.change(screen.getByLabelText("Per-run ceiling (USD)"), {
        target: { value: "" },
      });
      fireEvent.click(screen.getByTestId("admission-activate"));
      await waitFor(() => expect(backend.state.posts).toHaveLength(1));
      const sent = backend.state.posts[0].body;
      expect(sent.budgets.policy_revision).toBe(7);
      for (const spelling of ["max_run_usd", "maxRunUsd", "maxRunUSD"])
        expect(sent.routing.agents).not.toHaveProperty(spelling);
      await waitFor(() =>
        expect(screen.queryByTestId("admission-enforced-run")).toBeNull(),
      );
    },
  );
  it("offers deactivation for a run-only alias policy and removes the alias with CAS", async () => {
    serve({
      config: stored({
        enabled: false,
        routingAgents: { maxRunUsd: 0.2, maxRunUSD: 0.1 },
        revision: 4,
      }),
      keys: [],
    });
    render(editor());
    fireEvent.click(await screen.findByTestId("admission-deactivate"));
    fireEvent.click(screen.getByRole("button", { name: "Deactivate caps" }));
    await waitFor(() => expect(backend.state.posts).toHaveLength(1));
    const sent = backend.state.posts[0].body;
    expect(sent.budgets).toMatchObject({
      policy_revision: 4,
      admission_caps_enabled: false,
    });
    for (const spelling of ["max_run_usd", "maxRunUsd", "maxRunUSD"])
      expect(sent.routing.agents).not.toHaveProperty(spelling);
    await waitFor(() =>
      expect(screen.queryByTestId("admission-enforced-run")).toBeNull(),
    );
  });
  it("confirmed deactivation keeps alias-spelled bindings and removes the run alias", async () => {
    serve({
      config: aliased({ maxRunUSD: 0.1 }),
      keys: [orgKey("key_a1")],
    });
    render(editor());
    fireEvent.click(await screen.findByTestId("admission-deactivate"));
    fireEvent.click(screen.getByRole("button", { name: "Deactivate caps" }));
    await waitFor(() => expect(backend.state.posts).toHaveLength(1));
    expect(backend.state.posts[0].body.routing.agents).toEqual({
      key_bindings: { key_a1: "worker" },
    });
    await screen.findByText("Daily caps inactive");
  });
});

describe("effective micro-dollar threshold (MONEY-02)", () => {
  it("rejects more than six decimals with the stated contract and shows the enforced micro-dollars", async () => {
    serve({ config: null, keys: [orgKey("key_a1")] });
    render(editor());
    await openDraft();
    fireEvent.click(screen.getByRole("button", { name: "Add key cap" }));
    fireEvent.change(screen.getByLabelText("Key 1 identity"), {
      target: { value: "key_a1" },
    });
    expect(screen.getByTestId("admission-amount-contract")).toHaveTextContent(
      /six decimal places/i,
    );
    fireEvent.change(screen.getByLabelText("Key 1 limit (USD)"), {
      target: { value: "0.0000009" },
    });
    expect(screen.getByTestId("admission-validation")).toHaveTextContent(
      /six decimal places/i,
    );
    expect(screen.getByTestId("admission-activate")).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Key 1 limit (USD)"), {
      target: { value: "0.000249" },
    });
    expect(screen.queryByTestId("admission-validation")).toBeNull();
    expect(
      screen.getByTestId("admission-effective-key_ids-0"),
    ).toHaveTextContent("248 micro-dollars");
    expect(
      screen.getByTestId("admission-effective-key_ids-0"),
    ).toHaveTextContent("$0.000248");
  });
  it("shows the threshold the gateway enforces for stored agent, key and run amounts", async () => {
    serve({
      config: stored({
        agents: { worker: cap(0.0000009) },
        keyIds: { key_a1: cap(5) },
        routingAgents: { key_bindings: { key_a1: "worker" }, max_run_usd: 0.5 },
      }),
      keys: [orgKey("key_a1")],
    });
    render(editor());
    await screen.findByTestId("admission-policy-current");
    const agent = screen.getByTestId("admission-enforced-agent-worker");
    expect(agent).toHaveTextContent("0 micro-dollars");
    expect(agent).toHaveTextContent(/below one micro-dollar|rounded down/i);
    expect(
      screen.getByTestId("admission-enforced-key-key_a1"),
    ).toHaveTextContent("5,000,000 micro-dollars");
    expect(screen.getByTestId("admission-enforced-run")).toHaveTextContent(
      "500,000 micro-dollars",
    );
  });
});
