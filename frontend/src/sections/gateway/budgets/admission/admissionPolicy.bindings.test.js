import { describe, expect, it } from "vitest";
import * as policy from "./admissionPolicy";

function fn(name) {
  expect(policy[name], `${name} must be exported`).toBeTypeOf("function");
  return policy[name];
}

const row = (id, limit = "5") => ({ id, limit });
const draftOf = (over = {}) => ({
  agents: [],
  key_ids: [],
  bindings: [],
  runLimit: "",
  ack: "",
  ...over,
});
const orgKeys = (list, status = "ready") => ({
  status,
  keys: list.map(([id, state = "active", name = id]) => ({
    id,
    name,
    status: state,
  })),
});
const config = (agents = {}, extra = {}) => ({
  organization: "org-a",
  budgets: {
    enabled: true,
    admission_caps_enabled: true,
    policy_revision: 7,
    fail_mode: "closed",
    no_max_tokens: "clamp",
  },
  routing: { agents, ...extra },
});

describe("key-to-agent binding validation before saving", () => {
  const payload = (stored, draft) =>
    fn("admissionPayload")(stored, draft, true);
  it("refuses agent caps with no bindings, naming the 402 consequence", () => {
    expect(() =>
      payload(null, draftOf({ agents: [row("worker")], bindings: [] })),
    ).toThrow(/binding/i);
    expect(() =>
      payload(null, draftOf({ agents: [row("worker")], bindings: [] })),
    ).toThrow(/call_cap_unenforceable|refuses every/i);
  });
  it("refuses to remove the last binding while agent caps remain", () => {
    const stored = config({ key_bindings: { stable: "worker" } });
    stored.budgets.agents = { worker: { limit: 5, period: "daily" } };
    expect(() =>
      payload(stored, { ...fn("admissionDraft")(stored), bindings: [] }),
    ).toThrow(/binding/i);
  });
  it("refuses an agent cap that no binding reaches (unmatched agent identity)", () => {
    const draft = draftOf({
      agents: [row("worker")],
      bindings: [{ key: "key_a1", agent: "workr" }],
    });
    expect(() => payload(null, draft)).toThrow(/worker/);
    expect(() => payload(null, draft)).toThrow(/binding/i);
  });
  it("accepts every agent cap once a binding reaches it", () => {
    const result = payload(
      null,
      draftOf({
        agents: [row("worker"), row("batch", "2")],
        bindings: [
          { key: "key_a1", agent: "worker" },
          { key: "key_a2", agent: "batch" },
          { key: "key_a3", agent: "worker" },
        ],
      }),
    );
    expect(result.routing.agents.key_bindings).toEqual({
      key_a1: "worker",
      key_a2: "batch",
      key_a3: "worker",
    });
    expect(result.routing.agents.unpriced_under_cap).toBe("reject");
    expect(result.budgets.policy_revision).toBe(0);
  });
  it("does not over-block: key caps and a run ceiling need no bindings", () => {
    const keyOnly = payload(
      null,
      draftOf({ key_ids: [row("key_a1", "2")], bindings: [] }),
    );
    expect(keyOnly.routing.agents).not.toHaveProperty("key_bindings");
    expect(keyOnly.budgets.key_ids.key_a1.limit).toBe(2);
    const runOnly = payload(null, draftOf({ runLimit: "3" }));
    expect(runOnly.routing.agents.max_run_usd).toBe(3);
    expect(runOnly.routing.agents).not.toHaveProperty("key_bindings");
  });
  it("keeps bindings when the agent caps are removed from the draft", () => {
    const result = payload(
      null,
      draftOf({
        key_ids: [row("key_a1", "2")],
        bindings: [{ key: "key_a1", agent: "worker" }],
      }),
    );
    expect(result.routing.agents.key_bindings).toEqual({ key_a1: "worker" });
  });
  it("rejects blank, invalid and duplicate binding rows", () => {
    const base = { agents: [row("worker")] };
    for (const bindings of [
      [{ key: "", agent: "worker" }],
      [{ key: "key_a1", agent: "" }],
      [{ key: "bad key", agent: "worker" }],
      [{ key: "key_a1", agent: "wor ker" }],
      [{ key: "é", agent: "worker" }],
    ])
      expect(() => payload(null, draftOf({ ...base, bindings }))).toThrow(
        /binding/i,
      );
    expect(() =>
      payload(
        null,
        draftOf({
          ...base,
          bindings: [
            { key: "key_a1", agent: "worker" },
            { key: "key_a1", agent: "worker" },
          ],
        }),
      ),
    ).toThrow(/duplicate binding/i);
  });
  it("preserves the stored bindings when a draft does not carry binding rows", () => {
    const stored = config({
      keyBindings: { stable: "worker", other: "internal" },
    });
    stored.budgets.agents = { worker: { limit: 5, period: "daily" } };
    const draft = { agents: [row("worker")], key_ids: [], runLimit: "" };
    expect(payload(stored, draft).routing.agents.key_bindings).toEqual({
      stable: "worker",
      other: "internal",
    });
  });
  it("round-trips unrelated stored bindings through the draft", () => {
    const stored = config({
      key_bindings: { stable: "worker", other: "internal" },
    });
    stored.budgets.agents = { worker: { limit: 5, period: "daily" } };
    const result = payload(stored, fn("admissionDraft")(stored));
    expect(result.routing.agents.key_bindings).toEqual({
      stable: "worker",
      other: "internal",
    });
  });
  it("does not require bindings to deactivate", () => {
    const stored = config({});
    stored.budgets.agents = { worker: { limit: 5, period: "daily" } };
    const result = fn("admissionPayload")(stored, null, false);
    expect(result.budgets.admission_caps_enabled).toBe(false);
    expect(result.routing.agents).toEqual({});
  });
  it("exposes the stored rows from either spelling", () => {
    const rows = fn("bindingRows");
    expect(rows({ keyBindings: { k: "a" } })).toEqual([
      { key: "k", agent: "a" },
    ]);
    expect(rows({ key_bindings: { k: "a" } })).toEqual([
      { key: "k", agent: "a" },
    ]);
    expect(rows(undefined)).toEqual([]);
  });
});

describe("unbound execution keys and acknowledgment", () => {
  const worker = [row("worker")];
  it("needs no acknowledgment when no agent cap exists (key caps only)", () => {
    const report = fn("bindingReport")(
      draftOf({ key_ids: [row("key_a1", "2")] }),
      null,
      orgKeys([["key_a1"], ["key_a2"]]),
    );
    expect(report).toMatchObject({
      required: false,
      unbound: [],
      needsAck: false,
      listUnavailable: false,
    });
  });
  it("lists every active key without a binding and needs acknowledgment", () => {
    const keys = orgKeys([["key_a1", "active", "Primary"], ["key_a2"]]);
    const report = fn("bindingReport")(
      draftOf({
        agents: worker,
        bindings: [{ key: "key_a1", agent: "worker" }],
      }),
      null,
      keys,
    );
    expect(report.required).toBe(true);
    expect(report.unbound).toEqual([{ id: "key_a2", name: "key_a2" }]);
    expect(report.needsAck).toBe(true);
    expect(report.signature).toContain("key_a2");
    const all = fn("bindingReport")(
      draftOf({
        agents: worker,
        bindings: [
          { key: "key_a1", agent: "worker" },
          { key: "key_a2", agent: "worker" },
        ],
      }),
      null,
      keys,
    );
    expect(all).toMatchObject({ unbound: [], needsAck: false });
  });
  it("ignores keys that are not active", () => {
    const report = fn("bindingReport")(
      draftOf({
        agents: worker,
        bindings: [{ key: "key_a1", agent: "worker" }],
      }),
      null,
      orgKeys([
        ["key_a1"],
        ["r1", "revoked"],
        ["e1", "expired"],
        ["p1", "pending"],
        ["f1", "failed"],
      ]),
    );
    expect(report.unbound).toEqual([]);
    expect(report.needsAck).toBe(false);
  });
  it("needs an acknowledgment when the key list cannot be read", () => {
    const report = fn("bindingReport")(
      draftOf({
        agents: worker,
        bindings: [{ key: "key_a1", agent: "worker" }],
      }),
      null,
      { status: "error", keys: [] },
    );
    expect(report).toMatchObject({ listUnavailable: true, needsAck: true });
    const ready = fn("bindingReport")(
      draftOf({
        agents: worker,
        bindings: [{ key: "key_a1", agent: "worker" }],
      }),
      null,
      orgKeys([["key_a1"], ["key_a2"]]),
    );
    expect(report.signature).not.toBe(ready.signature);
  });
  it("treats an idle or loading key list as loading, not as acknowledged", () => {
    for (const status of ["idle", "loading"]) {
      const report = fn("bindingReport")(
        draftOf({
          agents: worker,
          bindings: [{ key: "key_a1", agent: "worker" }],
        }),
        null,
        { status, keys: [] },
      );
      expect(report.loading).toBe(true);
    }
  });
  it("counts a key cap whose key has no binding as unbound even without a key list", () => {
    const report = fn("bindingReport")(
      draftOf({
        agents: worker,
        key_ids: [row("key_c9", "1")],
        bindings: [{ key: "key_a1", agent: "worker" }],
      }),
      null,
      { status: "error", keys: [] },
    );
    expect(report.unbound.map((key) => key.id)).toEqual(["key_c9"]);
  });
  it("gives an order-insensitive signature that changes with the unbound set", () => {
    const report = (list) =>
      fn("bindingReport")(
        draftOf({
          agents: worker,
          bindings: [{ key: "key_a1", agent: "worker" }],
        }),
        null,
        orgKeys([["key_a1"], ...list.map((id) => [id])]),
      );
    expect(report(["b", "c"]).signature).toBe(report(["c", "b"]).signature);
    expect(report(["b"]).signature).not.toBe(report(["b", "c"]).signature);
  });
  it("ignores blank rows still being typed", () => {
    const report = fn("bindingReport")(
      draftOf({
        agents: [row("worker"), row("")],
        bindings: [
          { key: "key_a1", agent: "worker" },
          { key: "", agent: "" },
        ],
      }),
      null,
      orgKeys([["key_a1"]]),
    );
    expect(report).toMatchObject({ unbound: [], needsAck: false });
  });
});

describe("binding warnings that do not block", () => {
  it("flags a binding to an agent that has no cap", () => {
    const report = fn("bindingReport")(
      draftOf({
        agents: [row("worker")],
        bindings: [
          { key: "key_a1", agent: "worker" },
          { key: "key_a2", agent: "internal" },
        ],
      }),
      null,
      orgKeys([["key_a1"], ["key_a2"]]),
    );
    expect(report.uncapped).toEqual([{ key: "key_a2", agent: "internal" }]);
    expect(report.unbound).toEqual([]);
  });
  it("flags binding and key-cap identities that are not active organization keys", () => {
    const draft = draftOf({
      agents: [row("worker")],
      key_ids: [row("ghost2", "1")],
      bindings: [
        { key: "key_a1", agent: "worker" },
        { key: "ghost", agent: "worker" },
      ],
    });
    const ready = fn("bindingReport")(draft, null, orgKeys([["key_a1"]]));
    expect(ready.unknownKeys.sort()).toEqual(["ghost", "ghost2"]);
    const unavailable = fn("bindingReport")(draft, null, {
      status: "error",
      keys: [],
    });
    expect(unavailable.unknownKeys).toEqual([]);
  });
  it("detects bindings a configured rollout candidate does not repeat", () => {
    const stored = config(
      { keyBindings: { key_a1: "worker" } },
      {
        rollout: {
          routing: { agents: { keyBindings: { key_a1: "worker" } } },
        },
      },
    );
    const base = {
      agents: [row("worker")],
      bindings: [{ key: "key_a1", agent: "worker" }],
    };
    const keys = orgKeys([["key_a1"], ["key_a2"]]);
    const report = fn("bindingReport");
    expect(report(draftOf(base), stored, keys).rolloutConflicts).toEqual([]);
    expect(
      report(
        draftOf({
          ...base,
          bindings: [...base.bindings, { key: "key_a2", agent: "worker" }],
        }),
        stored,
        keys,
      ).rolloutConflicts,
    ).toEqual(["key_a2"]);
    expect(
      report(
        draftOf({
          ...base,
          bindings: [{ key: "key_a1", agent: "batch" }],
        }),
        stored,
        keys,
      ).rolloutConflicts,
    ).toEqual(["key_a1"]);
    for (const rollout of [undefined, {}]) {
      const plain = config({ key_bindings: { key_a1: "worker" } });
      if (rollout) plain.routing.rollout = rollout;
      expect(report(draftOf(base), plain, keys).rolloutConflicts).toEqual([]);
    }
  });
});
