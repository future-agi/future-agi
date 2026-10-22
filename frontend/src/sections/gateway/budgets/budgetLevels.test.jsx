import { describe, it, expect } from "vitest";
import {
  budgetRows,
  budgetsEnabled,
  effectiveOrgLimit,
  orgEntryInForce,
} from "./budgetLevels";

// The console reads budgets the way Django pushes them
// (agentcc/services/config_push.py _transform_budgets; the same cases as
// agentcc/tests/test_budget_push.py).

const T1 = "2026-10-21T08:00:00.000Z";
const T2 = "2026-10-21T09:00:00.000Z";

describe("budgetLevels", () => {
  it("the later stamped org entry is in force", () => {
    const later = {
      org_limit: { limit: 100, on_exceed: "block", saved_at: T1 },
      hard_limit: { limit: 1e-6, on_exceed: "block", saved_at: T2 },
    };
    expect(orgEntryInForce(later)).toBe("hard_limit");
    expect(effectiveOrgLimit(later)).toBe(1e-6);
    const earlier = {
      hard_limit: { limit: 1e-6, on_exceed: "block", saved_at: T1 },
      org_limit: { limit: 100, on_exceed: "block", saved_at: T2 },
    };
    expect(orgEntryInForce(earlier)).toBe("org_limit");
    expect(effectiveOrgLimit(earlier)).toBe(100);
  });

  it("a stamped save beats an unstamped one; an unreadable stamp is none", () => {
    expect(
      orgEntryInForce({
        org_limit: { limit: 100, on_exceed: "warn" },
        hard_limit: { limit: 5, on_exceed: "block", saved_at: T1 },
      }),
    ).toBe("hard_limit");
    expect(
      orgEntryInForce({
        org_limit: { limit: 100, saved_at: "yesterday" },
        hard_limit: { limit: 5, saved_at: T1 },
      }),
    ).toBe("hard_limit");
  });

  it("unstamped entries: Organization is in force over Hard Limit", () => {
    const b = {
      org_limit: { limit: 100, on_exceed: "warn" },
      hard_limit: { limit: 5, on_exceed: "block" },
    };
    expect(orgEntryInForce(b)).toBe("org_limit");
    const rows = budgetRows(b);
    expect(rows.find((r) => r.level === "hard_limit").inForce).toBe(false);
    expect(rows.find((r) => r.level === "hard_limit").laterSave).toBe(false);
  });

  it("enabled: an explicit choice stays; else on exactly when a level has a limit", () => {
    expect(budgetsEnabled({ org_limit: { limit: 1e-6 } })).toBe(true);
    expect(budgetsEnabled({ enabled: false, org_limit: { limit: 1e-6 } })).toBe(
      false,
    );
    expect(budgetsEnabled({ enabled: true })).toBe(true);
    expect(budgetsEnabled({ org_limit: 0, teams: { ops: { limit: 0 } } })).toBe(
      false,
    );
    expect(budgetsEnabled({ teams: { eng: { limit: 50 } } })).toBe(true);
    expect(budgetsEnabled({})).toBe(false);
    expect(budgetsEnabled(undefined)).toBe(false);
  });

  it("a flat hard_limit flag is not a level", () => {
    const rows = budgetRows({ enabled: true, org_limit: 40, hard_limit: true });
    expect(rows.map((r) => r.level)).toEqual(["org_limit"]);
    expect(rows[0].action).toBe("block");
  });
});
