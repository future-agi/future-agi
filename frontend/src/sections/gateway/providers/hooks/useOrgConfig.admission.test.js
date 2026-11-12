import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "./useOrgConfig";
import { admissionDraft } from "../../budgets/admission/admissionPolicy";

const { get, post } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
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

const active = () => ({
  organization: "org-a",
  version: 4,
  created_by: "creator",
  budgets: {
    enabled: true,
    admission_caps_enabled: true,
    policy_revision: 7,
    fail_mode: "closed",
    no_max_tokens: "clamp",
    org_limit: 100,
    agents: {
      worker: {
        limit: 5,
        period: "daily",
        activated_at: "2026-10-27T12:00:00Z",
      },
    },
    key_ids: { stable: { limit: 2, period: "daily" } },
  },
  routing: {
    strategy: "cost",
    agents: { key_bindings: { stable: "worker" }, max_run_usd: 3 },
  },
});
const draft = () => ({
  agents: [{ id: "worker", limit: "6" }],
  key_ids: [{ id: "stable", limit: "2" }],
  runLimit: "4",
});
// These assertion guards deliberately make the base RED without an import failure.
function client(name) {
  expect(api[name], `${name} activation API must exist`).toBeTypeOf("function");
  return api[name];
}
beforeEach(() => {
  get.mockReset();
  post.mockReset();
});

describe("admission policy client", () => {
  it("reads the active policy with an explicit organization header", async () => {
    get.mockResolvedValue({ data: { status: true, result: active() } });
    const signal = new AbortController().signal;
    expect(await client("readAdmissionPolicy")("org-a", signal)).toEqual(
      active(),
    );
    expect(get).toHaveBeenCalledWith("/agentcc/org-configs/active/", {
      headers: { "X-Organization-Id": "org-a" },
      signal,
    });
  });
  it("rejects another organization's response instead of rendering it", async () => {
    get.mockResolvedValue({
      data: { status: true, result: { ...active(), organization: "org-b" } },
    });
    await expect(client("readAdmissionPolicy")("org-a")).rejects.toThrow(
      /organization/i,
    );
  });
  it("accepts only a successful empty read and never sends without org context", async () => {
    get.mockResolvedValue({ data: { status: true, result: null } });
    expect(await client("readAdmissionPolicy")("org-a")).toBeNull();
    await expect(client("readAdmissionPolicy")(null)).rejects.toThrow(
      /organization/i,
    );
    get.mockResolvedValue({ data: { status: false, result: null } });
    await expect(client("readAdmissionPolicy")("org-a")).rejects.toThrow(
      /read/i,
    );
  });
  it("activates a draft using current CAS and preserves sibling sections", async () => {
    const before = active();
    post.mockResolvedValue({
      data: {
        status: true,
        result: { ...active(), policy_revision: 8, gateway_synced: true },
      },
    });
    await client("writeAdmissionPolicy")("org-a", before, draft(), true);
    expect(post).toHaveBeenCalledWith(
      "/agentcc/org-configs/",
      {
        budgets: {
          ...before.budgets,
          agents: {
            worker: { limit: 6, period: "daily", hard: true, action: "block" },
          },
          key_ids: {
            stable: { limit: 2, period: "daily", hard: true, action: "block" },
          },
        },
        routing: {
          strategy: "cost",
          agents: {
            key_bindings: { stable: "worker" },
            max_run_usd: 4,
            unpriced_under_cap: "reject",
          },
        },
        change_description: "Activate admission caps",
      },
      { headers: { "X-Organization-Id": "org-a" } },
    );
    expect(before).toEqual(active());
  });
  it("starts the first policy with revision zero, hard zero-dollar caps and its key binding", async () => {
    post.mockResolvedValue({ data: { status: true, result: active() } });
    await client("writeAdmissionPolicy")(
      "org-a",
      null,
      {
        agents: [{ id: "worker", limit: "0" }],
        key_ids: [],
        bindings: [{ key: "key_a1", agent: "worker" }],
        runLimit: "",
      },
      true,
    );
    const body = post.mock.calls[0][1];
    expect(body.budgets).toMatchObject({
      policy_revision: 0,
      enabled: true,
      admission_caps_enabled: true,
      agents: { worker: { limit: 0 } },
    });
    expect(body.routing.agents).toEqual({
      key_bindings: { key_a1: "worker" },
      unpriced_under_cap: "reject",
    });
  });
  it("passes through stale revision refusal without an automatic write retry", async () => {
    const conflict = {
      statusCode: 409,
      code: "cap_policy_revision_conflict",
      policy_revision: 9,
    };
    post.mockRejectedValue(conflict);
    await expect(
      client("writeAdmissionPolicy")("org-a", active(), draft(), true),
    ).rejects.toEqual(conflict);
    expect(post).toHaveBeenCalledTimes(1);
  });
  it("deactivates daily caps and removes only the configured run ceiling with CAS", async () => {
    post.mockResolvedValue({ data: { status: true, result: active() } });
    await client("writeAdmissionPolicy")("org-a", active(), null, false);
    const payload = post.mock.calls[0][1];
    expect(payload.budgets).toEqual({
      ...active().budgets,
      admission_caps_enabled: false,
    });
    expect(payload.routing).toEqual({
      strategy: "cost",
      agents: { key_bindings: { stable: "worker" } },
    });
    expect(payload.budgets.enabled).toBe(true);
  });
  it("rejects malformed numbers, duplicate identities, and unsafe revisions before POST", async () => {
    const write = client("writeAdmissionPolicy");
    for (const limit of ["", " ", "NaN", "Infinity", "-1", "1000000001"]) {
      await expect(
        write(
          "org-a",
          active(),
          { ...draft(), agents: [{ id: "worker", limit }] },
          true,
        ),
      ).rejects.toThrow();
    }
    await expect(
      write(
        "org-a",
        active(),
        {
          ...draft(),
          agents: [
            { id: "x", limit: "1" },
            { id: "x", limit: "2" },
          ],
        },
        true,
      ),
    ).rejects.toThrow(/duplicate/i);
    await expect(write("org-b", active(), draft(), true)).rejects.toThrow(
      /organization/i,
    );
    const stale = active();
    stale.budgets.policy_revision = -1;
    await expect(write("org-a", stale, draft(), true)).rejects.toThrow(
      /revision/i,
    );
    expect(post).not.toHaveBeenCalled();
  });
  it("does not lose structured denial, validation or sync status", async () => {
    const write = client("writeAdmissionPolicy");
    for (const error of [
      { statusCode: 403, message: "Admin required" },
      { statusCode: 400, message: "run rollout ceiling rejected" },
    ]) {
      post.mockRejectedValueOnce(error);
      await expect(write("org-a", active(), draft(), true)).rejects.toEqual(
        error,
      );
    }
    post.mockResolvedValue({
      data: {
        status: true,
        result: {
          ...active(),
          gateway_synced: false,
          gateway_warning: "Not synced",
        },
      },
    });
    expect(await write("org-a", active(), draft(), true)).toMatchObject({
      gateway_synced: false,
    });
  });
});

describe("admission policy client: key-to-agent bindings", () => {
  const write = (...args) => client("writeAdmissionPolicy")(...args);
  const withBindings = (bindings, over = {}) => ({
    ...draft(),
    bindings,
    ...over,
  });
  beforeEach(() => {
    post.mockResolvedValue({ data: { status: true, result: active() } });
  });
  it("sends the draft's bindings in the CAS-protected routing payload", async () => {
    await write(
      "org-a",
      active(),
      withBindings([
        { key: "stable", agent: "worker" },
        { key: "key_b2", agent: "worker" },
      ]),
      true,
    );
    const body = post.mock.calls[0][1];
    expect(body.budgets.policy_revision).toBe(7);
    expect(body.routing.agents.key_bindings).toEqual({
      stable: "worker",
      key_b2: "worker",
    });
    expect(body.routing.strategy).toBe("cost");
  });
  it("refuses agent caps with no bindings before any POST", async () => {
    await expect(
      write(
        "org-a",
        null,
        {
          agents: [{ id: "worker", limit: "5" }],
          key_ids: [],
          bindings: [],
          runLimit: "",
        },
        true,
      ),
    ).rejects.toThrow(/call_cap_unenforceable/);
    await expect(
      write("org-a", active(), withBindings([]), true),
    ).rejects.toThrow(/call_cap_unenforceable/);
    expect(post).not.toHaveBeenCalled();
  });
  it("refuses an agent cap that no binding reaches", async () => {
    await expect(
      write(
        "org-a",
        active(),
        withBindings([{ key: "stable", agent: "other-agent" }]),
        true,
      ),
    ).rejects.toThrow(/worker/);
    expect(post).not.toHaveBeenCalled();
  });
  it("does not over-block a key-cap-only activation that has no bindings", async () => {
    await write(
      "org-a",
      null,
      {
        agents: [],
        key_ids: [{ id: "key_a1", limit: "2" }],
        bindings: [],
        runLimit: "",
      },
      true,
    );
    const body = post.mock.calls[0][1];
    expect(body.budgets.key_ids).toEqual({
      key_a1: { limit: 2, period: "daily", hard: true, action: "block" },
    });
    expect(body.routing.agents).not.toHaveProperty("key_bindings");
  });
  it("rejects malformed or duplicate binding rows before POST", async () => {
    for (const bindings of [
      [{ key: "", agent: "worker" }],
      [{ key: "key a1", agent: "worker" }],
      [
        { key: "stable", agent: "worker" },
        { key: "stable", agent: "worker" },
      ],
    ])
      await expect(
        write("org-a", active(), withBindings(bindings), true),
      ).rejects.toThrow(/binding/i);
    expect(post).not.toHaveBeenCalled();
  });
});

describe("admission policy client: stored aliases and amount precision", () => {
  const write = (...args) => client("writeAdmissionPolicy")(...args);
  const SPELLINGS = ["max_run_usd", "maxRunUsd", "maxRunUSD"];
  const aliased = (agents) => ({
    ...active(),
    routing: { strategy: "cost", agents },
  });
  const FIXTURES = {
    maxRunUSD: { keyBindings: { stable: "worker" }, maxRunUSD: 0.1 },
    maxRunUsd: { key_bindings: { stable: "worker" }, maxRunUsd: 0.1 },
    "canonical null beside an alias": {
      key_bindings: { stable: "worker" },
      max_run_usd: null,
      maxRunUSD: 0.1,
    },
  };
  beforeEach(() => {
    post.mockResolvedValue({ data: { status: true, result: active() } });
  });
  it.each(Object.entries(FIXTURES))(
    "blank-run activation removes stored %s and carries the current CAS",
    async (_name, agents) => {
      const stored = aliased(agents);
      await write(
        "org-a",
        stored,
        { ...admissionDraft(stored), runLimit: "" },
        true,
      );
      const body = post.mock.calls[0][1];
      for (const spelling of SPELLINGS)
        expect(body.routing.agents).not.toHaveProperty(spelling);
      expect(body.routing.agents.key_bindings).toEqual({ stable: "worker" });
      expect(body.budgets.policy_revision).toBe(7);
    },
  );
  it.each(Object.entries(FIXTURES))(
    "confirmed deactivation removes stored %s with the current CAS",
    async (_name, agents) => {
      await write("org-a", aliased(agents), null, false);
      const body = post.mock.calls[0][1];
      expect(body.routing).toEqual({
        strategy: "cost",
        agents: { key_bindings: { stable: "worker" } },
      });
      expect(body.budgets).toMatchObject({
        policy_revision: 7,
        admission_caps_enabled: false,
      });
    },
  );
  it("rejects more than six decimals for every amount before POST", async () => {
    for (const bad of ["0.0000009", "1.0000001", "1e-7"]) {
      await expect(
        write(
          "org-a",
          active(),
          { ...draft(), agents: [{ id: "worker", limit: bad }] },
          true,
        ),
      ).rejects.toThrow();
      await expect(
        write(
          "org-a",
          active(),
          { ...draft(), key_ids: [{ id: "stable", limit: bad }] },
          true,
        ),
      ).rejects.toThrow();
      await expect(
        write("org-a", active(), { ...draft(), runLimit: bad }, true),
      ).rejects.toThrow(/six decimal|plain USD/i);
    }
    await expect(
      write("org-a", active(), { ...draft(), runLimit: "0.0000009" }, true),
    ).rejects.toThrow(/six decimal places/i);
    expect(post).not.toHaveBeenCalled();
  });
  it("sends whole micro-dollar amounts as exact numbers", async () => {
    await write(
      "org-a",
      active(),
      {
        ...draft(),
        agents: [{ id: "worker", limit: "0.000001" }],
        runLimit: "0.25",
      },
      true,
    );
    const body = post.mock.calls[0][1];
    expect(body.budgets.agents.worker.limit).toBe(0.000001);
    expect(body.routing.agents.max_run_usd).toBe(0.25);
  });
});

describe("organization key list for binding selection", () => {
  const read = (...args) => client("readAdmissionKeys")(...args);
  const rows = (over = {}) => ({
    data: {
      status: true,
      result: [
        {
          gateway_key_id: "key_a1",
          name: "Primary",
          status: "active",
          organization: "org-a",
          key_prefix: "pfx_demo...",
          key_hash: "never-shown",
          metadata: { org_id: "org-a" },
        },
        {
          gatewayKeyId: "key_a2",
          name: "Batch",
          status: "revoked",
          organization: "org-a",
        },
        { name: "no gateway id", status: "active" },
      ],
      ...over,
    },
  });
  it("reads key identities with an explicit organization header and drops secrets", async () => {
    get.mockResolvedValue(rows());
    const signal = new AbortController().signal;
    expect(await read("org-a", signal)).toEqual([
      { id: "key_a1", name: "Primary", status: "active" },
      { id: "key_a2", name: "Batch", status: "revoked" },
    ]);
    expect(get).toHaveBeenCalledWith("/agentcc/api-keys/", {
      headers: { "X-Organization-Id": "org-a" },
      signal,
    });
  });
  it("rejects another organization's rows instead of listing them", async () => {
    get.mockResolvedValue({
      data: {
        status: true,
        result: [
          { gateway_key_id: "key_b1", status: "active", organization: "org-b" },
        ],
      },
    });
    await expect(read("org-a")).rejects.toThrow(/organization/i);
  });
  it("rejects failed, non-list or organization-less reads", async () => {
    get.mockResolvedValue({ data: { status: false, result: [] } });
    await expect(read("org-a")).rejects.toThrow(/keys/i);
    get.mockResolvedValue({ data: { status: true, result: { a: 1 } } });
    await expect(read("org-a")).rejects.toThrow(/keys/i);
    get.mockClear();
    await expect(read(null)).rejects.toThrow(/organization/i);
    expect(get).not.toHaveBeenCalled();
  });
});
