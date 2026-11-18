import { describe, expect, it } from "vitest";
import { workerChecksOf } from "../participants";
import { alkCommandsOf } from "../alkLocal";
import { isBrowserAgent } from "../rsiModel";

const w = (over) => ({
  id: "pay",
  name: "Payment worker",
  state: "returned",
  calls: [],
  ...over,
});

describe("workerChecksOf", () => {
  it("is Unmeasured for a worker with no events", () => {
    const [r] = workerChecksOf({
      observed: true,
      workers: [w({ state: "unobserved" })],
    });
    expect(r.retries).toBe("Unmeasured");
    expect(r.duplicate.text).toBe("Unmeasured");
    expect(r.told.text).toBe("Unmeasured");
  });
  it("names a write that ran twice and counts the retry", () => {
    const calls = [{ name: "issue_refund" }, { name: "issue_refund" }];
    const [r] = workerChecksOf({ observed: true, workers: [w({ calls })] });
    expect(r.retries).toBe("1");
    expect(r.duplicate).toEqual({ text: "issue_refund ran twice", bad: true });
    expect(r.told.bad).toBe(false);
  });
  it("reads the coordinator's retry, duplicate and false success for a timed-out worker", () => {
    const p = {
      observed: true,
      after: { retried: true, duplicateWrite: true, toldSuccess: true },
      workers: [w({ state: "timeout", calls: [{ name: "issue_refund" }] })],
    };
    const [r] = workerChecksOf(p);
    expect(r.retries).toBe("1");
    expect(r.duplicate.bad).toBe(true);
    expect(r.told).toEqual({ text: "Told it worked — it didn't", bad: true });
  });
  it("leaves reads alone", () => {
    const [r] = workerChecksOf({
      observed: true,
      workers: [
        w({ calls: [{ name: "lookup_order" }, { name: "lookup_order" }] }),
      ],
    });
    expect(r.duplicate.bad).toBe(false);
    expect(r.retries).toBe("1");
  });
});

describe("ALK and browser agents", () => {
  it("gives ALK's own install, doctor and harness commands", () => {
    expect(alkCommandsOf({ path: "./repo", count: 5 }).split("\n")).toEqual([
      "pip install agent-learning-kit",
      "agent-learn doctor",
      "agent-learn harness auto --path ./repo --count 5",
    ]);
  });
  it("knows browser and computer-use agents", () => {
    expect(isBrowserAgent({ typeId: "browser_agent" })).toBe(true);
    expect(isBrowserAgent({ surface: "computer" })).toBe(true);
    expect(isBrowserAgent({ typeId: "voice_platform" })).toBe(false);
  });
});

describe("Fix on a browser agent", () => {
  it("is not held back for being a browser agent", async () => {
    const { fixGateOf } = await import("../../errorFeed/issueFixModel");
    const agent = {
      id: "agt_b",
      name: "Refund portal operator",
      typeId: "browser_agent",
      values: { provider: "browserbase", agentId: "sess_tmpl_refund-portal" },
    };
    const gate = fixGateOf(agent, { id: "env-browser", surface: "browser" });
    expect(gate?.key).not.toBe("coming-soon");
  });
});
