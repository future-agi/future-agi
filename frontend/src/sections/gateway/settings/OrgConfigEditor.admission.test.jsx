import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import OrgConfigEditor from "./OrgConfigEditor";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createFakeBackend } from "../budgets/admission/testing/serverFake";
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

const active = (revision = 7, organization = "org-a") => ({
  organization,
  version: 4,
  created_by: "creator-not-activator",
  created_at: "2026-10-27T12:00:00Z",
  budgets: {
    enabled: true,
    admission_caps_enabled: true,
    policy_revision: revision,
    agents: {
      worker: {
        limit: 5,
        period: "daily",
        activated_at: "2026-10-27T12:00:00Z",
      },
    },
    key_ids: {
      stable: {
        limit: 2,
        period: "daily",
        activated_at: "2026-10-27T12:00:00Z",
      },
    },
  },
  routing: { agents: { max_run_usd: 3, key_bindings: { stable: "worker" } } },
});
const response = (result) => ({ data: { status: true, result } });
const KEYS_URL = "/agentcc/api-keys/";
const orgKey = (id, name = id, status = "active") => ({
  gateway_key_id: id,
  name,
  status,
  organization: "org-a",
});
let configResponse;
let keyRows;
// The panel reads the active config and (when a draft opens) the org's keys
// through the same mocked client; route each by URL.
function readsAs(next) {
  configResponse = next;
}
async function keysSettled() {
  await waitFor(() =>
    expect(screen.getByTestId("admission-keys-status")).toHaveAttribute(
      "data-state",
      expect.stringMatching(/^(ready|error)$/),
    ),
  );
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
async function ready() {
  // Existing entry point imports successfully on the base; missing UI is an assertion failure.
  expect(await screen.findByTestId("admission-cap-policy")).toBeInTheDocument();
  await screen.findByTestId("admission-policy-current");
}
beforeEach(() => {
  queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  get.mockReset();
  post.mockReset();
  legacySave.mockReset();
  context.org = "org-a";
  context.canEdit = true;
  keyRows = [orgKey("stable", "Stable key")];
  get.mockImplementation(async (url) =>
    url === KEYS_URL
      ? { data: { status: true, result: keyRows } }
      : configResponse,
  );
  readsAs(response(active()));
});

describe("Org Config admission activation", () => {
  it("reads current scope, amount, period, revision and honest activation metadata", async () => {
    render(editor());
    await ready();
    expect(screen.getByTestId("admission-policy-current")).toHaveTextContent(
      "Revision 7",
    );
    expect(screen.getByTestId("admission-policy-current")).toHaveTextContent(
      "worker",
    );
    expect(screen.getByTestId("admission-policy-current")).toHaveTextContent(
      "$5",
    );
    expect(screen.getByTestId("admission-policy-current")).toHaveTextContent(
      "2026-10-27T12:00:00Z",
    );
    expect(
      screen.getByText(/Activated by: Not recorded by the server/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Daily run cap: not enforced yet/),
    ).toBeInTheDocument();
  });
  it("edit-and-activate sends current CAS then reads back the server revision", async () => {
    render(editor());
    await ready();
    fireEvent.click(screen.getByTestId("admission-edit"));
    await keysSettled();
    fireEvent.change(screen.getByLabelText("Agent 1 limit (USD)"), {
      target: { value: "6" },
    });
    post.mockResolvedValue(response({ ...active(8), gateway_synced: true }));
    readsAs(response(active(8)));
    fireEvent.click(screen.getByTestId("admission-activate"));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(post.mock.calls[0][1].budgets.policy_revision).toBe(7);
    expect(post.mock.calls[0][1].budgets.agents.worker.limit).toBe(6);
    await waitFor(() =>
      expect(screen.getByTestId("admission-policy-current")).toHaveTextContent(
        "Revision 8",
      ),
    );
    expect(legacySave).not.toHaveBeenCalled();
  });
  it("activates a first policy with the key binding the server and gateway require, without treating the legacy enabled flag as activation", async () => {
    // Nothing here is invented: the read-back and the admission verdict are
    // derived from the request the UI actually sent.
    const backend = createFakeBackend({
      config: null,
      keys: [orgKey("key_a1", "Primary")],
    });
    get.mockImplementation(backend.get);
    post.mockImplementation(backend.post);
    render(editor());
    fireEvent.click(await screen.findByTestId("admission-edit"));
    fireEvent.click(screen.getByRole("button", { name: "Add agent cap" }));
    fireEvent.change(screen.getByLabelText("Agent 1 identity"), {
      target: { value: "worker" },
    });
    fireEvent.change(screen.getByLabelText("Agent 1 limit (USD)"), {
      target: { value: "5" },
    });
    fireEvent.click(await screen.findByRole("button", { name: "Bind key_a1" }));
    fireEvent.change(screen.getByLabelText("Binding 1 agent"), {
      target: { value: "worker" },
    });
    fireEvent.click(screen.getByTestId("admission-activate"));
    await waitFor(() => expect(backend.state.posts).toHaveLength(1));
    const sent = backend.state.posts[0].body;
    expect(sent.budgets).toMatchObject({
      policy_revision: 0,
      enabled: true,
      admission_caps_enabled: true,
    });
    expect(sent.routing.agents.key_bindings).toEqual({ key_a1: "worker" });
    await waitFor(() =>
      expect(screen.getByTestId("admission-policy-current")).toHaveTextContent(
        "Revision 1",
      ),
    );
    expect(screen.getByTestId("admission-bindings-current")).toHaveTextContent(
      "key_a1",
    );
    expect(backend.verdict("key_a1")).toEqual({
      admitted: true,
      agent: "worker",
    });
  });
  it("shows the server's CAS refusal, discards stale draft, and reloads without overwriting", async () => {
    render(editor());
    await ready();
    fireEvent.click(screen.getByTestId("admission-edit"));
    await keysSettled();
    post.mockRejectedValue({
      statusCode: 409,
      code: "cap_policy_revision_conflict",
      policy_revision: 12,
    });
    readsAs(response(active(12)));
    fireEvent.click(screen.getByTestId("admission-activate"));
    await screen.findByText(/cap_policy_revision_conflict/);
    await waitFor(() =>
      expect(screen.getByTestId("admission-policy-current")).toHaveTextContent(
        "Revision 12",
      ),
    );
    expect(post).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId("admission-activate")).toBeNull();
  });
  it("deactivation requires confirmation and carries CAS while preserving legacy budgets", async () => {
    render(editor());
    await ready();
    fireEvent.click(screen.getByTestId("admission-deactivate"));
    expect(post).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(post).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("admission-deactivate"));
    post.mockResolvedValue(response(active(8)));
    const off = active(8);
    off.budgets.admission_caps_enabled = false;
    delete off.routing.agents.max_run_usd;
    readsAs(response(off));
    fireEvent.click(screen.getByRole("button", { name: "Deactivate caps" }));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(post.mock.calls[0][1].budgets).toMatchObject({
      policy_revision: 7,
      enabled: true,
      admission_caps_enabled: false,
    });
    await screen.findByText("Daily caps inactive");
  });
  it.each(["Member", "Viewer"])("keeps %s read-only", async () => {
    context.canEdit = false;
    render(editor());
    await ready();
    expect(screen.queryByTestId("admission-edit")).toBeNull();
    expect(screen.queryByTestId("admission-deactivate")).toBeNull();
    expect(
      screen.getByText(/Only an Owner or Admin can activate/),
    ).toBeInTheDocument();
    expect(post).not.toHaveBeenCalled();
  });
  it("resets policy and draft immediately on org switch and ignores late prior-org reads", async () => {
    let resolveOld;
    get.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveOld = resolve;
        }),
    );
    const view = render(editor());
    expect(await screen.findByTestId("admission-loading")).toBeInTheDocument();
    context.org = "org-b";
    readsAs(
      response({
        ...active(20, "org-b"),
        budgets: { policy_revision: 20 },
        routing: { agents: { max_run_usd: 3, key_bindings: { stable: "builder" } } },
      }),
    );
    view.rerender(editor());
    await waitFor(() =>
      expect(screen.getByTestId("admission-policy-current")).toHaveTextContent(
        "Revision 20",
      ),
    );
    await act(async () => {
      resolveOld(response(active()));
    });
    expect(
      screen.getByTestId("admission-policy-current"),
    ).not.toHaveTextContent("worker");
    expect(screen.getByTestId("admission-policy-current")).toHaveTextContent(
      "Revision 20",
    );
    expect(screen.getByTestId("admission-policy-current")).toHaveTextContent(
      "builder",
    );
  });
  it("does not show a mismatched organization even if the server returns one", async () => {
    readsAs(response(active(7, "org-b")));
    render(editor());
    await screen.findByTestId("admission-error");
    expect(screen.queryByText(/worker/)).toBeNull();
    expect(screen.queryByTestId("admission-edit")).toBeNull();
  });
  it("handles loading, failed read retry, and server denied states without stale controls", async () => {
    get.mockRejectedValueOnce({ statusCode: 503, message: "Unavailable" });
    render(editor());
    await screen.findByTestId("admission-error");
    expect(screen.queryByTestId("admission-edit")).toBeNull();
    get.mockRejectedValueOnce({ statusCode: 403, message: "Denied" });
    fireEvent.click(
      screen.getByRole("button", { name: "Reload admission policy" }),
    );
    await screen.findByTestId("admission-denied");
    expect(screen.queryByTestId("admission-policy-current")).toBeNull();
  });
  it("does not claim live enforcement after a failed gateway push", async () => {
    render(editor());
    await ready();
    fireEvent.click(screen.getByTestId("admission-edit"));
    await keysSettled();
    post.mockResolvedValue(
      response({
        ...active(8),
        gateway_synced: false,
        gateway_warning: "Gateway sync pending",
      }),
    );
    readsAs(response(active(8)));
    fireEvent.click(screen.getByTestId("admission-activate"));
    await screen.findByText(/Gateway sync pending/);
    expect(screen.queryByText("Caps are live")).toBeNull();
  });
  it("locks a draft on role loss before submission", async () => {
    const view = render(editor());
    await ready();
    fireEvent.click(screen.getByTestId("admission-edit"));
    context.canEdit = false;
    view.rerender(editor());
    expect(screen.queryByTestId("admission-activate")).toBeNull();
    expect(post).not.toHaveBeenCalled();
  });
});
