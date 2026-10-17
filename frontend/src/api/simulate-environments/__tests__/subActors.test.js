import { describe, it, expect } from "vitest";

import { stageOutputsToWorld } from "../environment";
import { scenarioFromApi } from "../scenarios";
import { mapCallDetail } from "../runDetail";
import { PREBUILT_ENVIRONMENTS_FIXTURE } from "../_fixtures/prebuiltEnvironments";

describe("sub-actors", () => {
  it("reads the cast from its stage output and leaves a world without one unchanged", () => {
    const world = stageOutputsToWorld([
      {
        kind: "cast",
        data: {
          sub_actors: [
            { id: "subactor_passenger", name: "Passenger", role: "in the back seat", behaviour: { prompt: "asks for a stop" }, origin: "derived" },
          ],
        },
      },
    ]);
    expect(world.cast.subActors).toEqual([
      { id: "subactor_passenger", name: "Passenger", role: "in the back seat", channel: "", prompt: "asks for a stop", origin: "derived" },
    ]);
    expect(stageOutputsToWorld([{ kind: "contract", data: {} }])).toBeNull();
  });

  it("lists the sub-actors a scenario binds", () => {
    expect(
      scenarioFromApi({ sub_actors: [{ id: "subactor_bystander", trigger: {} }] }).subActors,
    ).toEqual(["subactor_bystander"]);
    expect(scenarioFromApi({}).subActors).toEqual([]);
  });

  it("keeps a sub-actor's name on its turn", () => {
    const detail = mapCallDetail({
      transcript: [{ speaker_role: "sub_actor", speaker_name: "Bystander", content: "Add a stop." }],
    });
    expect(detail.turns[0]).toMatchObject({ role: "sub_actor", speakerName: "Bystander" });
    const chat = mapCallDetail({
      simulation_call_type: "text",
      transcript: [{ role: "sub_actor", content: [{ role: "sub_actor", content: "Add one.", name: "Friend" }] }],
    });
    expect(chat.turns[0]).toMatchObject({ role: "sub_actor", speakerName: "Friend" });
  });
});

describe("sub-goal results by actor", () => {
  it("keeps each sub-goal's actor and narrows the sub-goal columns to the actors chosen", async () => {
    const { buildTraceColumns, subGoalColumnsFor } = await import("../runCalls");
    const columns = buildTraceColumns([
      { id: "a", name: "ignored_bystander", kind: "sub_goal", actor: "subactor_bystander" },
      { id: "b", name: "identity_verified", kind: "sub_goal", actor: "primary" },
      { id: "c", name: "Tone", kind: "evaluation" },
    ]);
    const keys = (filters) => subGoalColumnsFor(columns, filters).map((c) => c.key);
    expect(keys({})).toEqual(["a", "b"]);
    expect(keys({ actor: ["primary"] })).toEqual(["b"]);
    expect(keys({ actor: ["subactor_bystander"] })).toEqual(["a"]);
    expect(keys({ actor_not: ["primary"] })).toEqual(["a"]);
  });
});

describe("template casts", () => {
  it("gives a surface the harness does not reach no cast", () => {
    expect(PREBUILT_ENVIRONMENTS_FIXTURE.find((one) => one.id === "env-coding").cast).toBeUndefined();
  });
});
