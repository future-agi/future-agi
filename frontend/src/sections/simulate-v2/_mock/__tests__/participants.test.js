import { describe, expect, it } from "vitest";
import {
  callStatusOf,
  clock,
  didLineOf,
  didOf,
  durationLabel,
  fullAnswerOf,
  handoffMessageOf,
  kvOf,
  participantsOf,
  rawEventsOf,
  replyOf,
  resultSentenceOf,
  spanLabelOf,
  summaryOf,
  toolCallsOf,
  toolDurationOf,
  tokensOf,
  workerJsonOf,
} from "../participants";

const squad = (extra = {}) => ({
  sourceKind: "platform",
  definition: {
    kind: "platform",
    members: [
      { id: "front-desk", name: "Front desk", tools: ["lookup_order"] },
      {
        id: "refunds",
        name: "Refunds specialist",
        role: "Refunds",
        tools: ["issue_refund"],
      },
      {
        id: "escalations",
        name: "Escalations",
        role: "Approvals",
        tools: ["escalate_to_human"],
      },
    ],
  },
  ...extra,
});
const squad4 = () => {
  const base = squad();
  return {
    ...base,
    definition: {
      ...base.definition,
      members: [
        {
          id: "front-desk",
          name: "Front desk",
          tools: ["lookup_order", "shipment_status"],
        },
        {
          id: "refunds",
          name: "Refunds specialist",
          role: "Refunds",
          tools: ["check_return_eligibility", "issue_refund", "lookup_order"],
        },
        {
          id: "escalations",
          name: "Escalations",
          role: "Approvals",
          tools: ["escalate_to_human", "lookup_order"],
        },
        {
          id: "shipping",
          name: "Shipping specialist",
          role: "Delivery",
          tools: [
            "shipment_status",
            "update_shipping_address",
            "cancel_order",
            "lookup_order",
          ],
        },
      ],
    },
  };
};
const task = (i, status = "passed") => ({
  id: `t${i}`,
  scenarioId: `s${i}`,
  status,
  steps: [{ role: "caller", text: "I want a refund" }],
  callLog: {
    calls: [{ id: `c${i}`, name: "issue_refund", args: { id: "A-1043" } }],
  },
});
const all = (agent, status) =>
  Array.from({ length: 200 }, (_, i) =>
    participantsOf({ agent, task: task(i, status) }),
  ).filter(Boolean);

describe("wording", () => {
  it("turns a tool name into what was done", () => {
    expect(didOf("check_return_eligibility")).toBe(
      "Checked return eligibility",
    );
    expect(didOf("lookup_order")).toBe("Looked up the order");
    expect(didOf("get_order_status")).toBe("Got the order status");
    expect(didOf("issue_refund")).toBe("Issued the refund");
    expect(didOf("send_return_label")).toBe("Sent return label");
    expect(didOf("escalate_to_human")).toBe("Transferred the call to a person");
    expect(didOf("transfer_call")).toBe("Transferred the call to a person");
    expect(didOf("issue_refund", { attempt: true })).toBe(
      "Tried to issue the refund",
    );
    expect(didOf("issue_refund", { blocked: true })).toBe(
      "Wasn't allowed to issue the refund",
    );
  });
  it("joins a worker's tools into one phrase", () => {
    expect(
      didLineOf({
        calls: [
          { name: "check_return_eligibility", output: {} },
          { name: "lookup_order", output: {} },
        ],
      }),
    ).toBe("Checked return eligibility, looked up the order");
  });
  it("lists an input or output as label: value pairs", () => {
    expect(
      kvOf({ order_id: 9891, amount: 89, status: "email_on_file" }),
    ).toEqual([
      ["Order ID", "9891"],
      ["Amount", "$89"],
      ["Status", "email on file"],
    ]);
    expect(kvOf({ ok: true, rows: null })).toEqual([
      ["Ok", "Yes"],
      ["Rows", "none"],
    ]);
    expect(kvOf(null)).toEqual([]);
  });
  it("formats times and spots hand-offs", () => {
    expect(clock(42.4)).toBe("0:42");
    expect(clock(null)).toBe("—");
    expect(
      toolCallsOf([
        { name: "issue_refund" },
        { name: "transfer_call" },
        { name: "handoff_to_x" },
      ]),
    ).toHaveLength(1);
  });
});

describe("results and the reply", () => {
  const eligible = {
    name: "check_return_eligibility",
    input: { order_id: 9891 },
    output: { status: "eligible", days_left: 13 },
  };
  const lookup = {
    name: "lookup_order",
    input: { order_id: 9891 },
    output: { status: "found", items: 1 },
  };
  const transfer = {
    name: "escalate_to_human",
    input: { order_id: 9891 },
    output: { status: "transferred" },
  };
  const w = (state, calls, name = "Worker") => ({ state, calls, name });
  it("gives one sentence from the most informative output", () => {
    expect(
      resultSentenceOf(w("returned", [lookup, eligible, { ...lookup }])),
    ).toBe("Order 9891 can be returned — 13 days left in the window.");
    expect(
      resultSentenceOf(
        w("returned", [
          {
            name: "issue_refund",
            input: { order_id: 7 },
            output: { amount: 89 },
          },
        ]),
      ),
    ).toBe("Refunded $89 on order 7 to the original payment method.");
    expect(resultSentenceOf(w("timeout", [lookup]))).toBe(
      "No answer came back.",
    );
    expect(resultSentenceOf(w("unobserved", []))).toBe(
      "No events from this worker.",
    );
  });
  it("replies the way the front desk would", () => {
    expect(
      replyOf([w("returned", [lookup, eligible]), w("returned", [transfer])]),
    ).toBe(
      "Your order can be returned — you have 13 days left. I've also put you through to a supervisor.",
    );
    expect(
      replyOf([
        w("returned", [
          { name: "issue_refund", input: {}, output: { amount: 5 } },
        ]),
        w("returned", [
          { name: "send_return_label", input: {}, output: { status: "sent" } },
        ]),
      ]),
    ).toBe(
      "I've refunded $5 to your original payment method. I've also emailed you a return label.",
    );
    expect(replyOf([w("timeout", [], "Refunds")])).toBe(
      "I wasn't able to help with that.",
    );
    expect(replyOf([])).toMatch(/myself/);
  });
});

describe("summaryOf", () => {
  const w = (state) => ({ state });
  const c = "Front desk";
  const sum = (workers, observed = true) =>
    summaryOf({ coordinator: c, workers, observed });
  it("says how the workers ended", () => {
    expect(sum([w("returned"), w("returned")])).toBe(
      "Front desk passed this call to 2 workers. Both finished.",
    );
    expect(sum([w("returned")])).toBe(
      "Front desk passed this call to 1 worker. It finished.",
    );
    expect(sum([w("returned"), w("timeout")])).toBe(
      "Front desk passed this call to 2 workers. 1 finished, 1 didn't answer.",
    );
    expect(sum([w("timeout")])).toBe(
      "Front desk passed this call to 1 worker. It didn't answer.",
    );
    expect(sum([w("unobserved")], false)).toBe(
      "Workers not observed — shown as the app declares them",
    );
    expect(sum([])).toContain("no worker");
  });
});

describe("participantsOf", () => {
  it("carries the request, reply and duration the rows need", () => {
    all(squad())
      .filter((r) => r.workers.length)
      .forEach((r) => {
        expect(r.request).toBe("I want a refund");
        expect(r.reply).toBeTruthy();
        expect(r.duration).toBeGreaterThan(0);
      });
    all(squad({ values: { delegationEvents: false } }), "failed")
      .filter((r) => r.workers.length)
      .forEach((r) => {
        expect(r.reply).toBeNull();
        expect(r.duration).toBeNull();
      });
  });
  it("is null for a single agent", () => {
    expect(participantsOf({ agent: {}, task: task(1) })).toBeNull();
  });
  it("names the worker on every tool call and keeps its ID stable", () => {
    const rows = all(squad());
    const withWorkers = rows.filter((r) => r.workers.length);
    expect(withWorkers.length).toBeGreaterThan(0);
    withWorkers.forEach((r) => {
      r.workers.forEach((w) => {
        expect(w.wid).toBe(`run/${w.id}`);
        w.calls.forEach((c) => expect(c.workerName).toBe(w.name));
      });
      expect(r.workers.every((w) => w.id !== "front-desk")).toBe(true);
    });
    expect(withWorkers.some((r) => r.workers.length === 2)).toBe(true);
  });
  it("credits a call only to a worker whose own tools list it", () => {
    [squad(), squad4()].forEach((agent) => {
      const owns = Object.fromEntries(
        agent.definition.members.map((m) => [m.id, m.tools]),
      );
      ["passed", "failed", "flaky"].forEach((status) =>
        all(agent, status).forEach((r) =>
          r.workers.forEach((w) =>
            w.calls
              .filter((c) => !c.blocked)
              .forEach((c) => expect(owns[w.id]).toContain(c.name)),
          ),
        ),
      );
    });
  });
  it("sends some calls through three workers in a squad of four", () => {
    const rows = all(squad4(), "failed").filter((r) => r.workers.length);
    const three = rows.filter((r) => r.workers.length === 3);
    expect(three.length).toBeGreaterThan(0);
    expect(three.length / rows.length).toBeGreaterThan(0.1);
    expect(three.length / rows.length).toBeLessThan(0.4);
    expect(rows.some((r) => r.workers.length === 1)).toBe(true);
    expect(rows.some((r) => r.workers.length === 2)).toBe(true);
    three.forEach((r) => {
      expect(new Set(r.workers.map((w) => w.id)).size).toBe(3);
      expect(r.workers.map((w) => w.state)).toEqual([
        "returned",
        "returned",
        "timeout",
      ]);
      expect(r.summary).toMatch(
        /3 workers over 2 turns\. 2 finished, 1 didn't answer/,
      );
    });
    all(squad4(), "passed")
      .filter((r) => r.workers.length === 3)
      .forEach((r) =>
        r.workers.forEach((w) => expect(w.state).toBe("returned")),
      );
    /* A squad of three never goes past two. */
    expect(
      Math.max(...all(squad(), "failed").map((r) => r.workers.length)),
    ).toBe(2);
  });
  it("words the shipping tools", () => {
    expect(didOf("shipment_status")).toBe("Checked the delivery status");
    expect(didOf("update_shipping_address")).toBe(
      "Updated the delivery address",
    );
    expect(didOf("cancel_order")).toBe("Cancelled the order");
    expect(didOf("shipment_status", { attempt: true })).toBe(
      "Tried to check the delivery status",
    );
    const call = (name, output) => ({
      name,
      input: { order_id: 9891 },
      output,
    });
    const w = (c) => ({ state: "returned", name: "Shipping", calls: [c] });
    const ship = call("shipment_status", {
      status: "out_for_delivery",
      eta_days: 1,
    });
    expect(resultSentenceOf(w(ship))).toBe(
      "Order 9891 is out for delivery — it arrives tomorrow.",
    );
    expect(replyOf([w(ship)])).toBe(
      "Order 9891 is out for delivery — it arrives tomorrow.",
    );
    expect(
      resultSentenceOf(
        w(call("shipment_status", { status: "in_transit", eta_days: 3 })),
      ),
    ).toBe("Order 9891 is on its way — it arrives in 3 days.");
    expect(
      resultSentenceOf(
        w(call("update_shipping_address", { status: "updated" })),
      ),
    ).toBe("Changed the delivery address on order 9891.");
    expect(replyOf([w(call("cancel_order", { status: "cancelled" }))])).toBe(
      "I've cancelled your order.",
    );
  });
  it("never guesses an unobserved worker", () => {
    const agent = squad({ values: { delegationEvents: false } });
    const rows = all(agent, "failed").filter((r) => r.workers.length);
    expect(rows.length).toBeGreaterThan(0);
    rows.forEach((r) => {
      expect(r.observed).toBe(false);
      r.workers.forEach((w) => {
        expect(w.state).toBe("unobserved");
        expect(w.calls).toEqual([]);
        expect(resultSentenceOf(w)).toBe("No events from this worker.");
      });
      expect(
        r.steps.some((s) => s.kind === "return" || s.kind === "tool"),
      ).toBe(false);
      expect(r.steps.at(-1).verb).toMatch(/not in the recorded events/);
      r.checks.forEach((c) => expect(c.value).toBe("Unmeasured"));
    });
  });
  it("shows a timed-out worker with no return", () => {
    const rows = all(squad(), "failed").filter((r) => r.outcome === "timeout");
    expect(rows.length).toBeGreaterThan(0);
    rows.forEach((r) => {
      const gone = r.workers.find((w) => w.state === "timeout");
      expect(resultSentenceOf(gone)).toBe("No answer came back.");
      expect(r.steps.find((s) => s.kind === "timeout").verb).toMatch(
        /didn't answer before the call ended/,
      );
      expect(r.summary).toMatch(/didn't answer/);
      expect(r.duration).toBeGreaterThan(gone.endedAt);
    });
  });
});

describe("engineer detail", () => {
  it("carries model, order and seed on every worker", () => {
    const a = squad();
    a.definition.members[1].model = "gpt-4o";
    a.definition.members[2].model = { name: "gpt-4.1" };
    const ws = all(a, "passed").flatMap((p) => p.workers);
    expect(ws.find((w) => w.id === "refunds")?.model).toBe("gpt-4o");
    expect(ws.find((w) => w.id === "escalations")?.model).toBe("gpt-4.1");
    expect(ws.every((w) => w.order && w.seed)).toBe(true);
  });
  it("words the hand-off message from the worker's tools", () => {
    const w = {
      state: "returned",
      order: 9891,
      calls: [
        { name: "check_return_eligibility" },
        { name: "lookup_order" },
        { name: "lookup_order" },
      ],
    };
    expect(handoffMessageOf(w)).toBe(
      "Check return eligibility and look up the order for order 9891. Reply with eligibility and days left and the order status and item count.",
    );
    expect(handoffMessageOf({ state: "unobserved", calls: [] })).toBeNull();
    expect(handoffMessageOf({ state: "returned", calls: [] })).toMatch(
      /^Handle the caller's request/,
    );
  });
  it("gives the full answer, or none after a timeout", () => {
    const w = {
      state: "returned",
      calls: [
        {
          name: "check_return_eligibility",
          input: { order_id: 9891 },
          output: { status: "eligible", days_left: 13 },
        },
        { name: "issue_refund", blocked: true, output: { status: "blocked" } },
      ],
    };
    const text = fullAnswerOf(w);
    expect(text.split("\n")[0]).toBe(resultSentenceOf(w));
    expect(text).toContain("Checked return eligibility — Status: eligible");
    expect(text).toContain("Wasn't allowed to issue the refund — outside");
    expect(fullAnswerOf({ state: "timeout", calls: [] })).toBe(
      "No answer came back before the call ended.",
    );
    expect(fullAnswerOf({ state: "unobserved" })).toBeNull();
  });
  it("times and states each call", () => {
    const done = { id: "a", output: { status: "ok" } };
    expect(toolDurationOf(done)).toBe(toolDurationOf({ ...done }));
    expect(toolDurationOf(done)).toBeGreaterThanOrEqual(90);
    expect(toolDurationOf({ id: "b", output: null })).toBeNull();
    expect(toolDurationOf({ id: "c", blocked: true, output: {} })).toBeLessThan(
      90,
    );
    expect(durationLabel(180)).toBe("180 ms");
    expect(durationLabel(1250)).toBe("1.3 s");
    expect(durationLabel(null)).toBe("—");
    expect(callStatusOf(done)).toEqual({ kind: "ok", text: "ok" });
    expect(callStatusOf({ blocked: true }).kind).toBe("error");
    expect(callStatusOf({ output: null })).toEqual({
      kind: "none",
      text: "no response",
    });
  });
  it("counts tokens deterministically", () => {
    const w = { seed: "s1:refunds", state: "returned", calls: [{}, {}] };
    expect(tokensOf(w)).toBe(tokensOf({ ...w }));
    expect(tokensOf(w)).toBeGreaterThan(800);
    expect(tokensOf({ state: "unobserved" })).toBeNull();
  });
  it("labels a worker's span", () => {
    expect(spanLabelOf({ delegatedAt: 2.4, endedAt: 6.4 })).toBe(
      "0:02 → 0:06 · 4.0 s",
    );
    expect(spanLabelOf({ delegatedAt: null, endedAt: null })).toBeNull();
  });
  it("builds a worker's JSON record", () => {
    const p = all(squad(), "passed").find((x) => x.workers.length);
    const w = p.workers[0];
    const j = workerJsonOf(w);
    expect(Object.keys(j)).toEqual([
      "id",
      "name",
      "model",
      "task",
      "handoff_message",
      "calls",
      "answer",
    ]);
    expect(j.calls).toHaveLength(w.calls.length);
    expect(j.calls[0]).toHaveProperty("duration_ms");
    expect(JSON.parse(JSON.stringify(j))).toEqual(j);
  });
  it("lists every event turn by turn, workers under their hand-off", () => {
    const ps = all(squad4(), "failed");
    ps.filter((p) => p.observed && p.workers.length).forEach((p) => {
      const ev = rawEventsOf(p);
      expect(ev[0].type).toBe("Message");
      /* in order within each turn */
      p.turns.forEach((t) => {
        const at = ev.filter((e) => e.turn === t.n).map((e) => e.at);
        expect(at).toEqual([...at].sort((a, b) => a - b));
      });
      const handoffs = ev.filter((e) => e.type === "Hand-off");
      expect(handoffs).toHaveLength(p.workers.length + p.undeclared.length);
      /* a sub-worker's hand-off comes from its parent, one level down */
      p.workers
        .filter((w) => w.parentId)
        .forEach((w) => {
          const h = handoffs.find((e) => e.to === w.name);
          expect(h.actor).toBe(w.parentName);
          expect(h.depth).toBe(1);
        });
      const calls = ev.filter((e) => e.type === "Tool call");
      expect(calls).toHaveLength(
        [...p.workers, ...p.undeclared].reduce(
          (n, w) => n + w.calls.length,
          0,
        ) + p.untied.length,
      );
      const gone = ev.filter((e) => e.type === "No answer");
      expect(gone).toHaveLength(
        p.workers.filter((w) => w.state === "timeout").length,
      );
      gone.forEach((e) =>
        expect(e.text).toBe("No answer came back before the call ended."),
      );
    });
  });
  it("splits calls into turns, with nesting and parallel work", () => {
    const ps = all(squad4(), "failed").filter((p) => p.workers.length);
    const two = ps.filter((p) => p.turns.length === 2);
    expect(two.length).toBeGreaterThan(0);
    ps.filter((p) => p.workers.length === 3).forEach((p) => {
      const [a, b, c] = p.workers;
      expect(a.parallelWith).toBe(b.name);
      expect(a.delegatedAt).toBe(b.delegatedAt);
      expect(c.parentId).toBe(b.id);
      expect(c.depth).toBe(1);
      /* the call ended with a worker still out: no reply on the last turn */
      expect(p.turns.at(-1).reply).toBeNull();
    });
    /* a turn can open without a caller message */
    const quiet = all(squad4(), "passed").filter(
      (p) => p.workers.length === 2 && p.turns.length === 2,
    );
    expect(quiet.some((p) => p.turns[1].request === null)).toBe(true);
  });
  it("shows the states real calls have", () => {
    const ps = all(squad4(), "passed").filter((p) => p.workers.length);
    const failed = ps.flatMap((p) => p.workers).flatMap((w) => w.calls);
    expect(failed.some((c) => c.failed)).toBe(true);
    failed
      .filter((c) => c.failed)
      .forEach((c) => expect(callStatusOf(c).kind).toBe("error"));
    expect(ps.some((p) => p.undeclared.length)).toBe(true);
    ps.filter((p) => p.undeclared.length).forEach((p) =>
      expect(p.undeclared[0].undeclared).toBe(true),
    );
    expect(ps.some((p) => p.untied.length)).toBe(true);
    ps.filter((p) => p.untied.length).forEach((p) =>
      expect(
        p.boundaries.some((b) => b.worker === "Not tied to a worker"),
      ).toBe(true),
    );
    /* declared members the call never reached */
    ps.forEach((p) =>
      p.notRun.forEach((n) =>
        expect(p.workers.some((w) => w.name === n)).toBe(false),
      ),
    );
    expect(ps.some((p) => p.notRun.length)).toBe(true);
  });
  it("needs the app's traces for Open in Traces", () => {
    const on = all(squad4()).find((p) => p.workers.length);
    expect(on.traces).toBe(true);
    const off = all({ ...squad4(), values: { sendsTraces: false } }).find(
      (p) => p.workers.length,
    );
    expect(off.traces).toBe(false);
  });
  it("gives no raw events for an unobserved squad", () => {
    expect(rawEventsOf(null)).toEqual([]);
    expect(rawEventsOf({ observed: false })).toEqual([]);
  });
});
