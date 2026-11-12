import { describe, expect, it } from "vitest";
import {
  parseBudgetLink,
  resolveBudgetTarget,
  budgetRaisePayload,
} from "./budgetCapTargets";

const config = {
  budgets: {
    enabled: true,
    admission_caps_enabled: true,
    policy_revision: 7,
    org_limit: { limit: 20, period: "weekly", on_exceed: "block" },
    keys: { worker: { limit: 3, period: "monthly", hard: true } },
    key_ids: { worker: { limit: 4, period: "daily", activated_at: "kept" } },
    agents: { worker: { limit: 5, period: "daily" } },
  },
  routing: { agents: { max_run_usd: 2, key_bindings: { worker: "worker" } } },
};
const target = (level, id, data = config) =>
  resolveBudgetTarget(
    parseBudgetLink(new URLSearchParams({ level, id })),
    data,
  );

describe("budget refusal deep links", () => {
  it("does nothing without an editor request", () => {
    expect(parseBudgetLink(new URLSearchParams("tab=budgets"))).toBeNull();
  });
  it.each([
    "level=org",
    "level=unknown&id=current",
    "level=org&level=key&id=current",
    "level=key&id=name%3A__proto__",
    "level=agent_key&id=not-a-workspace",
    "level=org&id=current&id=other",
    "level=key&id=name%3A%00",
  ])("rejects ambiguous or invalid parameters: %s", (search) => {
    expect(parseBudgetLink(new URLSearchParams(search)).error).toBeTruthy();
  });
  it("opens the effective org entry rather than a shadowed one", () => {
    const data = structuredClone(config);
    data.budgets.hard_limit = {
      limit: 12,
      saved_at: "2026-10-01T00:00:00Z",
      on_exceed: "block",
    };
    const selected = target("org", "current", data);
    expect(selected.limit).toBe(12);
    expect(budgetRaisePayload(selected, "15")).toEqual({
      budgets: { policy_revision: 7, hard_limit: { limit: 15 } },
    });
  });
  it("distinguishes key names from stable key IDs", () => {
    expect(budgetRaisePayload(target("key", "name:worker"), "9")).toEqual({
      budgets: { policy_revision: 7, keys: { worker: { limit: 9 } } },
    });
    expect(budgetRaisePayload(target("key", "id:worker"), "9")).toEqual({
      budgets: { policy_revision: 7, key_ids: { worker: { limit: 9 } } },
    });
  });
  it("edits the strict agent daily cap, without activation or mode changes", () => {
    expect(budgetRaisePayload(target("org", "agent:worker"), "10")).toEqual({
      budgets: { policy_revision: 7, agents: { worker: { limit: 10 } } },
    });
    expect(config.budgets.agents.worker.limit).toBe(5);
  });
  it("preserves the period and sibling fields by patching only the limit", () => {
    expect(budgetRaisePayload(target("org", "current"), "21")).toEqual({
      budgets: { policy_revision: 7, org_limit: { limit: 21 } },
    });
    expect(config.budgets.org_limit.on_exceed).toBe("block");
  });
  it("patches a flat org limit as a number, not as an entry with lost action", () => {
    const data = { budgets: { org_limit: 4, hard_limit: true } };
    expect(budgetRaisePayload(target("org", "current", data), "8")).toEqual({
      budgets: { policy_revision: 0, org_limit: 8 },
    });
  });
  it("does not create an arbitrary budget from a URL", () => {
    expect(target("key", "id:missing").error).toBeTruthy();
  });
  it("labels the administrative run ceiling as new-run-only", () => {
    const selected = target("agent_run", "policy");
    expect(selected.notice).toMatch(/existing run/i);
    expect(budgetRaisePayload(selected, "4")).toEqual({
      budgets: { policy_revision: 7 },
      routing: { agents: { max_run_usd: 4 } },
    });
  });
  it("cannot pretend to raise an existing lower-only run ledger", () => {
    const selected = target("agent_run", "run:scope-1");
    expect(selected.readOnly).toBe(true);
    expect(() => budgetRaisePayload(selected, "4")).toThrow();
  });
  it.each(["", "NaN", "Infinity", "-1", "0", "20", "1e20"])(
    "rejects non-increases and non-finite limits: %s",
    (value) => {
      expect(() =>
        budgetRaisePayload(target("org", "current"), value),
      ).toThrow();
    },
  );
  it("edits the workspace scoped-key policy, not an org-config key budget", () => {
    const id = "11111111-1111-4111-8111-111111111111";
    const selected = target("agent_key", id, {
      workspace: id,
      budget_usd: "50.00",
      budget_period: "monthly",
    });
    expect(selected.notice).toMatch(/every agent key/i);
    expect(budgetRaisePayload(selected, "60.25")).toEqual({
      budget_usd: "60.25",
      budget_period: "monthly",
    });
    expect(() => budgetRaisePayload(selected, "60.251")).toThrow();
    expect(() => budgetRaisePayload(selected, "1000001")).toThrow();
  });
  it("refuses a workspace response for another workspace", () => {
    expect(
      target("agent_key", "11111111-1111-4111-8111-111111111111", {
        workspace: "22222222-2222-4222-8222-222222222222",
        budget_usd: "50.00",
        budget_period: "monthly",
      }).error,
    ).toBeTruthy();
  });
});
