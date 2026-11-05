import { describe, expect, it } from "vitest";
import { getEnvironment } from "../environments";
import {
  actorActionsOf,
  actorViewOf,
  castBySide,
  castOf,
  createActorPatch,
  savedCastOf,
  worldActionsOf,
  NOT_A_WORLD_ACTION,
} from "../actors";
import { actorEventsOf } from "../actorEvents";
import {
  discardEnvChangesPatch,
  pendingEnvChanges,
  saveEnvChangesPatch,
  worldConfigOf,
} from "../versions";
import { stampEdit } from "../editReview";

const env = getEnvironment("env-chat-banking");

/* An environment a run has pinned, so edits wait in the draft. */
const pinned = () => ({
  runs: [{ id: "run-1", envVersion: "v1" }],
  envVersions: [
    {
      id: "env-chat-banking-v1",
      label: "v1",
      createdAt: new Date().toISOString(),
      note: "First build.",
      config: {},
    },
  ],
  activeEnvVersion: "v1",
});

const manager = {
  id: "manager",
  side: "world",
  kind: "approver",
  name: "Manager",
  role: "approver",
  blurb: "Approves a card freeze",
  entry: "joins",
  pressure: "authority",
  modalities: ["chat"],
  traits: [],
  knowledge: ["The card policy"],
  actions: ["approve_freeze_card", "teleport"],
  canApprove: true,
  approvalScope: "Card freezes",
};

describe("a Manager added to the draft", () => {
  const es0 = pinned();
  const patch = stampEdit(es0, createActorPatch(env, es0, manager));
  const es1 = { ...es0, ...patch };

  it("is read back with its permissions and view", () => {
    const m = castOf(env, es1).find((a) => a.id === "manager");
    expect(m).toBeTruthy();
    expect(m.canApprove).toBe(true);
    expect(m.actions).toContain("approve_freeze_card");
    const view = actorViewOf(m, { env, envState: es1, cast: castOf(env, es1) });
    expect(view.sees.find((r) => r.label === "Can do").text).toBe(
      "approve_freeze_card",
    );
    expect(view.sees.find((r) => r.label === "Can approve").text).toBe(
      "Card freezes",
    );
    expect(view.never.join(" ")).toMatch(
      /expected answers, hidden checks and scoring/,
    );
  });

  it("marks an action the world does not have", () => {
    const m = castOf(env, es1).find((a) => a.id === "manager");
    expect(actorActionsOf(m, env, es1)).toEqual([
      { name: "approve_freeze_card", known: true },
      { name: "teleport", known: false },
    ]);
    expect(NOT_A_WORLD_ACTION).toBe("not an action of this world");
  });

  it("offers only the world's own actions, and the approval of each write", () => {
    const names = worldActionsOf(env, es1).map((a) => a.name);
    expect(names).toContain("freeze_card");
    expect(names).toContain("approve_freeze_card");
    expect(names).not.toContain("teleport");
  });

  it("waits in the draft as a proposed change; the saved version is unchanged", () => {
    expect(pendingEnvChanges(es1)).toHaveLength(1);
    expect(pendingEnvChanges(es1)[0].kind).toBe("actors");
    expect(es1.envVersions).toBe(es0.envVersions);
    expect(es1.envVersions[0].config).toEqual({});
    expect(savedCastOf(env, es1).some((a) => a.id === "manager")).toBe(false);
    expect(castOf(env, es1).some((a) => a.id === "manager")).toBe(true);
  });

  it("joins a new version when the draft is saved", () => {
    const saved = { ...es1, ...saveEnvChangesPatch(env, es1) };
    expect(saved.envVersions).toHaveLength(2);
    expect(saved.envVersions[1].config.customActors.map((a) => a.id)).toContain(
      "manager",
    );
    expect(pendingEnvChanges(saved)).toHaveLength(0);
    expect(savedCastOf(env, saved).some((a) => a.id === "manager")).toBe(true);
  });

  it("goes when the draft is discarded", () => {
    const back = { ...es1, ...discardEnvChangesPatch(env, es1) };
    expect(castOf(env, back).some((a) => a.id === "manager")).toBe(false);
    expect(worldConfigOf(back)).toEqual(worldConfigOf(es0));
  });
});

describe("the default cast", () => {
  it("has both sides on a chat environment", () => {
    const by = castBySide(castOf(env, pinned()));
    expect(by.caller.length).toBeGreaterThan(0);
    expect(by.world.length).toBeGreaterThan(0);
  });
});

describe("actor events in a transcript", () => {
  const task = {
    id: "task-1",
    status: "passed",
    steps: Array.from({ length: 8 }, (_, i) => ({
      role: i % 2 ? "agent" : "customer",
      text: `turn ${i}`,
    })),
    callLog: { calls: [{ name: "freeze_card", wrote: true }] },
  };
  it("is the same every time and follows a turn", () => {
    const es = pinned();
    const a = actorEventsOf({ env, envState: es, task });
    const b = actorEventsOf({ env, envState: es, task });
    expect(a).toEqual(b);
    a.forEach((e) => {
      expect(["Committed", "Refused", "Failed"]).toContain(e.outcome);
      expect(e.after).toBeGreaterThanOrEqual(0);
      expect(e.after).toBeLessThan(task.steps.length);
      expect(e.role).toBeTruthy();
    });
  });
  it("shows a world actor's own action", () => {
    const es0 = pinned();
    const es = { ...es0, ...createActorPatch(env, es0, manager) };
    const ev = actorEventsOf({ env, envState: es, task }).filter(
      (e) => e.actorId === "manager",
    );
    expect(ev).toHaveLength(1);
    expect(ev[0].name).toBe("Manager");
    expect(ev[0].action).toBe("approve_freeze_card");
  });
  it("has none for a conversation that is too short", () => {
    expect(
      actorEventsOf({
        env,
        envState: pinned(),
        task: { ...task, steps: [{}] },
      }),
    ).toEqual([]);
  });
});
