// W6 C1: suite-wide checks are kept with the suite's settings as the change
// set's `set_environment` op (the next version records them); the mock world
// reads the same list (taskSuite.suiteChecksOfDataset).
import { describe, expect, it } from "vitest";
import { buildCheck, draftFromCheck } from "../checkTypes";
import {
  APPLIES_TO,
  removeSuiteCheck,
  suiteChangeText,
  suiteChecksOfEnvironment,
  suiteChecksOp,
  takenKeys,
  upsertSuiteCheck,
} from "../suiteChecksModel";
import { applyOp, toOperations } from "../../DataTab/Versions/changeSetStore";

const neverEmail = buildCheck({
  ...draftFromCheck(null, { defaults: { type: "tool_call" } }),
  key: "never_emails",
  name: "Never emails the customer",
  tool: "send_email",
  toolMode: "not_called",
});
const noDate = buildCheck({
  ...draftFromCheck(null, { defaults: { type: "assertion" } }),
  key: "no_refund_date",
  sentence: "The reply never promises a refund date.",
});

describe("W6 C1 suite checks model", () => {
  it("offers This task and Every task in the suite", () => {
    expect(APPLIES_TO.map((o) => o.label)).toEqual([
      "This task",
      "Every task in the suite",
    ]);
  });

  it("builds the shapes the mock world judges", () => {
    expect(neverEmail).toMatchObject({
      kind: "tool_call",
      assert: { tool: "send_email", mode: "not_called" },
    });
    expect(noDate).toMatchObject({
      kind: "assertion",
      assert: { sentence: "The reply never promises a refund date." },
    });
  });

  it("upserts by key, keeps order, removes by key", () => {
    const one = upsertSuiteCheck([], neverEmail);
    const two = upsertSuiteCheck(one, noDate);
    expect(two.map((c) => c.key)).toEqual(["never_emails", "no_refund_date"]);
    const renamed = { ...neverEmail, name: "No email" };
    expect(upsertSuiteCheck(two, renamed)[0].name).toBe("No email");
    expect(upsertSuiteCheck(two, renamed)).toHaveLength(2);
    expect(removeSuiteCheck(two, "never_emails").map((c) => c.key)).toEqual([
      "no_refund_date",
    ]);
    expect(upsertSuiteCheck(two, null)).toBe(two);
  });

  it("reads the list from an environment and ignores junk", () => {
    expect(suiteChecksOfEnvironment(null)).toEqual([]);
    expect(suiteChecksOfEnvironment({ suite_checks: "x" })).toEqual([]);
    expect(
      suiteChecksOfEnvironment({ suite_checks: [neverEmail, null, {}] }),
    ).toEqual([neverEmail]);
  });

  it("the op keeps a pending environment change and is the one environment op", () => {
    const saved = { image: "acme-support-sandbox:1.4" };
    const pendingOp = {
      id: "op-1",
      at: 1,
      op: "set_environment",
      image: "acme-support-sandbox:1.5",
      before: saved,
    };
    const op = suiteChecksOp({ saved, pendingOp, next: [neverEmail] });
    expect(op).toEqual({
      op: "set_environment",
      image: "acme-support-sandbox:1.5",
      suite_checks: [neverEmail],
      before: saved,
    });
    const ops = applyOp(applyOp([], pendingOp), { ...op, id: "op-2" });
    const env = ops.filter((o) => o.op === "set_environment");
    expect(env).toHaveLength(1);
    expect(env[0]).toMatchObject({
      image: "acme-support-sandbox:1.5",
      suite_checks: [neverEmail],
    });
    // The version body (N2 operations) carries the list to the server.
    expect(toOperations(env)).toEqual([
      {
        op: "set_environment",
        image: "acme-support-sandbox:1.5",
        suite_checks: [neverEmail],
      },
    ]);
  });

  it("a new check may not reuse a task or suite key, except the one edited", () => {
    const suite = [neverEmail, noDate];
    expect(takenKeys({ taskKeys: ["order_found"], suite })).toEqual([
      "order_found",
      "never_emails",
      "no_refund_date",
    ]);
    expect(takenKeys({ taskKeys: [], suite, editing: "never_emails" })).toEqual(
      ["no_refund_date"],
    );
  });

  it("says what the save did", () => {
    expect(suiteChangeText("never_emails", { count: 2 })).toBe(
      "never_emails applies to every task in the suite (2 suite-wide) · unsaved",
    );
    expect(suiteChangeText("never_emails", { removed: true })).toBe(
      "never_emails no longer applies to every task · unsaved",
    );
  });
});
