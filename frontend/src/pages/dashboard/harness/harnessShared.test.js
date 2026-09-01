import { describe, expect, it } from "vitest";

import { STATUS_TYPES } from "src/utils/statusUtils";
import {
  adjustmentStatus,
  agentTypeIcon,
  jobProgress,
  readable,
  canceledProgress,
  completedStageCount,
  environmentName,
  callSummary,
  errorMessage,
  eventMessage,
  eventTime,
  scenarioOutcome,
  scenarioTally,
  runElapsed,
  shortDuration,
  shortRunId,
  STAGE_STATE,
  stageElapsed,
  tabState,
  TAB_STATE,
  stageState,
  stageStatus,
  stages,
} from "./harnessShared";

describe("stageStatus", () => {
  it("maps the terminal outcomes explicitly", () => {
    expect(stageStatus("completed")).toBe(STATUS_TYPES.PASS);
    expect(stageStatus("failed")).toBe(STATUS_TYPES.ERROR);
    expect(stageStatus("canceled")).toBe(STATUS_TYPES.CANCELED);
  });

  it("treats everything still in flight as running", () => {
    expect(stageStatus("queued")).toBe(STATUS_TYPES.RUNNING);
    expect(stageStatus("understanding_agent")).toBe(STATUS_TYPES.RUNNING);
    expect(stageStatus("cleaning_up")).toBe(STATUS_TYPES.RUNNING);
  });
});

describe("stages", () => {
  // A stage missing here indexes to -1, which strands the checklist and the progress bar.
  it("covers every non-terminal stage the runner reports", () => {
    ["queued", "acquiring_source", "cleaning_up"].forEach((stage) => {
      expect(stages).toContain(stage);
    });
  });

  it("keeps outcomes out of the pipeline", () => {
    expect(stages).not.toContain("failed");
    expect(stages).not.toContain("canceled");
  });
});

describe("agentTypeIcon", () => {
  it("prefers a voice transport over http when the agent serves both", () => {
    const item = { credentials: { detected_connectors: ["http", "livekit"] } };
    expect(agentTypeIcon(item).label).toBe("Voice agent");
  });

  it("falls back to chat for a plain http agent", () => {
    const item = { credentials: { detected_connectors: ["http"] } };
    expect(agentTypeIcon(item).label).toBe("Chat agent");
  });

  it("does not treat a non-transport signal as a type", () => {
    const item = { credentials: { detected_connectors: ["mcp"] } };
    expect(agentTypeIcon(item).label).toBe("Not detected");
  });

  it("survives a job with no credentials block", () => {
    expect(agentTypeIcon(undefined).label).toBe("Not detected");
  });
});

describe("jobProgress", () => {
  it("reports a completed run as full regardless of list position", () => {
    expect(jobProgress({ stage: "completed" })).toBe(100);
  });

  it("keeps a floor so an early run still shows a sliver", () => {
    expect(jobProgress({ stage: "queued" })).toBeGreaterThanOrEqual(2);
  });

  it("advances as the run moves through the pipeline", () => {
    const early = jobProgress({ stage: "understanding_agent" });
    const late = jobProgress({ stage: "grading" });
    expect(late).toBeGreaterThan(early);
  });

  it("does not collapse on an unrecognised stage", () => {
    expect(jobProgress({ stage: "not_a_stage" })).toBeGreaterThanOrEqual(2);
  });
});

describe("readable", () => {
  it("turns a snake_case stage into a sentence", () => {
    expect(readable("uploading_artifacts")).toBe("Uploading artifacts");
  });
});

describe("stageState", () => {
  const at = (stage) => stages.indexOf(stage);

  it("marks the stage a run is in as active, not done", () => {
    const status = { stage: "generating_environment" };
    expect(stageState(status, at("understanding_agent"))).toBe(
      STAGE_STATE.DONE,
    );
    expect(stageState(status, at("generating_environment"))).toBe(
      STAGE_STATE.ACTIVE,
    );
    expect(stageState(status, at("running"))).toBe(STAGE_STATE.PENDING);
  });

  it("splits a failed run around failure.stage", () => {
    const status = {
      stage: "failed",
      failure: { stage: "validating_environment" },
    };
    expect(stageState(status, at("building_environment"))).toBe(
      STAGE_STATE.DONE,
    );
    expect(stageState(status, at("validating_environment"))).toBe(
      STAGE_STATE.FAILED,
    );
    expect(stageState(status, at("generating_data"))).toBe(STAGE_STATE.PENDING);
  });

  it("completes every stage once the run completes", () => {
    const status = { stage: "completed" };
    stages.forEach((_, index) => {
      expect(stageState(status, index)).toBe(STAGE_STATE.DONE);
    });
  });

  it("claims no progress when there is nothing to anchor on", () => {
    // A cancel names no stage, and a failure stage outside the vocabulary cannot be placed.
    const canceled = { stage: "canceled" };
    const unknown = { stage: "failed", failure: { stage: "not_a_stage" } };
    const missing = { stage: "failed", failure: null };
    stages.forEach((_, index) => {
      expect(stageState(canceled, index)).toBe(STAGE_STATE.PENDING);
      expect(stageState(unknown, index)).toBe(STAGE_STATE.PENDING);
      expect(stageState(missing, index)).toBe(STAGE_STATE.PENDING);
    });
  });

  it("survives a missing status", () => {
    expect(stageState(undefined, 0)).toBe(STAGE_STATE.PENDING);
  });
});

describe("canceledProgress", () => {
  const started = (stage) => ({
    type: "harness.stage.started",
    payload: { stage },
  });
  const completed = (stage) => ({
    type: "harness.stage.completed",
    payload: { stage },
  });

  it("credits nothing when there are no events", () => {
    const { doneStages, stoppedAt } = canceledProgress([]);
    expect(doneStages.size).toBe(0);
    expect(stoppedAt).toBe(-1);
  });

  it("stops in the group that started and never completed", () => {
    const { doneStages, stoppedAt } = canceledProgress([started("understand")]);
    expect(stages[stoppedAt]).toBe("understanding_agent");
    expect(doneStages.has("queued")).toBe(true);
    expect(doneStages.has("acquiring_source")).toBe(true);
    expect(doneStages.has("understanding_agent")).toBe(false);
  });

  it("credits every stage of a group that completed", () => {
    const { doneStages } = canceledProgress([
      started("environment"),
      completed("environment"),
    ]);
    [
      "generating_environment",
      "building_environment",
      "validating_environment",
      "generating_data",
    ].forEach((stage) => expect(doneStages.has(stage)).toBe(true));
  });

  it("never credits a group that only started", () => {
    const { doneStages } = canceledProgress([started("environment")]);
    [
      "building_environment",
      "validating_environment",
      "generating_data",
    ].forEach((stage) => expect(doneStages.has(stage)).toBe(false));
  });

  // The runner emits cleaning_up BEFORE uploading_artifacts, the reverse of the declared
  // stage order. Deriving progress from list position credited artifact upload that had
  // not happened, and dropped the later event entirely.
  it("handles the runner emitting stages out of list order", () => {
    const { doneStages, stoppedAt } = canceledProgress([
      started("cleaning_up"),
      completed("cleaning_up"),
      started("uploading_artifacts"),
    ]);
    expect(doneStages.has("cleaning_up")).toBe(true);
    expect(doneStages.has("uploading_artifacts")).toBe(false);
    expect(stages[stoppedAt]).toBe("uploading_artifacts");
  });
});

describe("stageState for a canceled run", () => {
  const status = { stage: "canceled" };
  const events = [
    { type: "harness.stage.started", payload: { stage: "understand" } },
  ];
  const at = (stage) => stages.indexOf(stage);

  it("shows how far the run got instead of blanking", () => {
    expect(stageState(status, at("queued"), events)).toBe(STAGE_STATE.DONE);
    expect(stageState(status, at("acquiring_source"), events)).toBe(
      STAGE_STATE.DONE,
    );
    expect(stageState(status, at("understanding_agent"), events)).toBe(
      STAGE_STATE.STOPPED,
    );
    expect(stageState(status, at("running"), events)).toBe(STAGE_STATE.PENDING);
  });

  it("falls back to pending with no events to anchor on", () => {
    stages.forEach((_, index) => {
      expect(stageState(status, index, [])).toBe(STAGE_STATE.PENDING);
    });
  });
});

describe("eventTime", () => {
  it("shows time only for something that happened today", () => {
    const today = new Date();
    today.setHours(16, 43, 56, 0);
    expect(eventTime(today.toISOString())).not.toMatch(/[A-Z][a-z]{2}/);
  });

  it("adds the date once the event is not from today", () => {
    // Without this, a run crossing midnight reads as though it went backwards.
    const earlier = new Date();
    earlier.setDate(earlier.getDate() - 1);
    expect(eventTime(earlier.toISOString())).toMatch(/\d{1,2} [A-Z][a-z]{2},/);
  });

  it("renders nothing for a missing or unparseable value", () => {
    expect(eventTime(undefined)).toBe("");
    expect(eventTime("")).toBe("");
    expect(eventTime("not-a-date")).toBe("");
  });
});

describe("errorMessage", () => {
  // The axios interceptor spreads response.data flat and adds statusCode; it never
  // produces a nested .response, so reading through one silently loses every message.
  it("reads a bare detail, which is what the harness views return", () => {
    expect(errorMessage({ detail: "job not found", statusCode: 404 })).toBe(
      "job not found",
    );
  });

  it("prefers detail on the platform envelope", () => {
    expect(
      errorMessage({
        status: false,
        detail: "Authentication credentials were not provided.",
        message: "Authentication credentials were not provided.",
        statusCode: 401,
      }),
    ).toBe("Authentication credentials were not provided.");
  });

  it("unwraps DRF field errors", () => {
    expect(
      errorMessage({ detail: { source_id: ["This field is required."] } }),
    ).toBe("This field is required.");
  });

  it("falls back to message when there was no response at all", () => {
    expect(errorMessage({ message: "Network Error" })).toBe("Network Error");
    expect(
      errorMessage({ response: { data: { detail: "Create request rejected" } } }),
    ).toBe("Create request rejected");
  });

  it("surfaces string and platform error-envelope messages", () => {
    expect(errorMessage("Submission failed")).toBe("Submission failed");
    expect(errorMessage({ error: "Credential reference is unsupported" })).toBe(
      "Credential reference is unsupported",
    );
  });

  it("never returns an empty string", () => {
    expect(errorMessage({})).toBe("Something went wrong");
    expect(errorMessage(undefined)).toBe("Something went wrong");
  });
});

describe("completedStageCount", () => {
  it("does not count the stage the run is still in", () => {
    expect(completedStageCount({ stage: "queued" })).toBe(0);
    expect(completedStageCount({ stage: "running" })).toBe(
      stages.indexOf("running"),
    );
  });

  it("counts everything once the run completes", () => {
    expect(completedStageCount({ stage: "completed" })).toBe(stages.length);
  });

  // "failed"/"canceled" are not in the stage list, so indexing on them would report nothing
  // finished while the stepper shows otherwise. These must agree.
  it("counts what a failed run got through, from failure.stage", () => {
    const status = {
      stage: "failed",
      failure: { stage: "validating_environment" },
    };
    expect(completedStageCount(status)).toBe(
      stages.indexOf("validating_environment"),
    );
  });

  it("counts what a canceled run got through, from its events", () => {
    const events = [
      { type: "harness.stage.started", payload: { stage: "understand" } },
    ];
    expect(completedStageCount({ stage: "canceled" }, events)).toBe(
      stages.indexOf("understanding_agent"),
    );
  });

  it("claims nothing when there is no anchor", () => {
    expect(completedStageCount({ stage: "canceled" }, [])).toBe(0);
    expect(completedStageCount({ stage: "failed", failure: null })).toBe(0);
    expect(completedStageCount(undefined)).toBe(0);
  });
});

describe("runElapsed", () => {
  const at = (iso) => ({ wall_time: iso });

  it("measures from the first event to now while the run is live", () => {
    const events = [at("2026-08-25T11:13:56Z"), at("2026-08-25T11:15:56Z")];
    const now = new Date("2026-08-25T11:23:56Z").getTime();
    expect(runElapsed(events, now, false)).toBe(10 * 60 * 1000);
  });

  it("measures between first and last once terminal", () => {
    const events = [at("2026-08-25T11:13:56Z"), at("2026-08-25T11:29:39Z")];
    expect(runElapsed(events, Date.now(), true)).toBe(15 * 60 * 1000 + 43000);
  });

  // A queued job has emitted nothing, so there is no start to measure from.
  it("is unmeasurable with no usable events", () => {
    expect(runElapsed([], Date.now(), false)).toBeNull();
    expect(
      runElapsed([{ wall_time: "nonsense" }], Date.now(), false),
    ).toBeNull();
  });
});

describe("stageElapsed", () => {
  const started = (stage, iso) => ({
    type: "harness.stage.started",
    payload: { stage },
    wall_time: iso,
  });

  it("times a stage the runner actually reports", () => {
    const now = new Date("2026-08-25T11:59:36Z").getTime();
    const events = [started("calls", "2026-08-25T11:55:45Z")];
    expect(stageElapsed({ stage: "connecting_agent" }, events, now)).toBe(
      3 * 60 * 1000 + 51000,
    );
  });

  // Nine of the fifteen stages never emit an event. Returning null lets the caller omit the
  // line; a zero would read as "instant" when it means "not reported".
  it("returns null for a stage the runner never emits", () => {
    const events = [started("calls", "2026-08-25T11:55:45Z")];
    expect(stageElapsed({ stage: "grading" }, events, Date.now())).toBeNull();
    expect(
      stageElapsed({ stage: "validating_scenarios" }, events, Date.now()),
    ).toBeNull();
  });

  it("returns null when the stage has not started yet", () => {
    expect(
      stageElapsed({ stage: "understanding_agent" }, [], Date.now()),
    ).toBeNull();
  });
});

describe("shortDuration", () => {
  it("formats compactly and pads seconds so the width does not jump", () => {
    expect(shortDuration(9000)).toBe("9s");
    expect(shortDuration(63000)).toBe("1m 03s");
    expect(shortDuration(15 * 60 * 1000 + 43000)).toBe("15m 43s");
    expect(shortDuration(3 * 60 * 60 * 1000 + 4 * 60 * 1000)).toBe("3h 4m");
  });

  it("renders nothing when there is nothing to measure", () => {
    expect(shortDuration(null)).toBeNull();
    expect(shortDuration(undefined)).toBeNull();
  });
});

describe("shortRunId", () => {
  it("keeps the prefix and the first uuid group", () => {
    expect(shortRunId("harness-2d60ba74-6ba2-4744-b0b7-d011ab52017b")).toBe(
      "harness-2d60ba74",
    );
  });

  it("leaves anything that is not shaped like one alone", () => {
    expect(shortRunId("harness")).toBe("harness");
    expect(shortRunId("")).toBe("");
    expect(shortRunId(undefined)).toBe("");
  });
});

describe("adjustmentStatus", () => {
  const pending = { status: "pending", target_stage: "environment" };

  it("names the stage a change landed at", () => {
    expect(
      adjustmentStatus(
        {
          status: "applied",
          target_stage: "understand",
          applied_stage: "environment",
        },
        "completed",
      ),
    ).toBe("Applied at Environment");
  });

  it("still promises delivery while the run is alive", () => {
    expect(adjustmentStatus(pending, "generating_environment")).toBe(
      "Pending · will land at Environment",
    );
  });

  // ALK leaves an unreached change at "pending" forever, so the job's own outcome is the
  // only thing that says it will never land.
  it.each([
    ["canceled", "Not applied — the run was canceled first"],
    ["failed", "Not applied — the run failed first"],
    ["completed", "Not applied — the run finished first"],
  ])("reports a change stranded by a %s run", (stage, expected) => {
    expect(adjustmentStatus(pending, stage)).toBe(expected);
  });
});

describe("tabState", () => {
  const at = (stage) => ({ stage });

  it("spins the tab the runner is inside", () => {
    expect(tabState("contract", at("understanding_agent"))).toBe(
      TAB_STATE.WORKING,
    );
  });

  // Environment covers four checklist stages, three of which never emit an event.
  it("spins Environment on a stage that is never reported", () => {
    expect(tabState("environment", at("building_environment"))).toBe(
      TAB_STATE.WORKING,
    );
  });

  it("ticks a tab the run has moved past", () => {
    expect(tabState("contract", at("generating_scenarios"))).toBe(
      TAB_STATE.DONE,
    );
  });

  it("leaves a tab bare until its turn", () => {
    expect(tabState("scenarios", at("understanding_agent"))).toBe(
      TAB_STATE.PENDING,
    );
  });

  // An artifact is proof on its own: Runs is the catch-all and holds outputs from stages the
  // checklist never reaches in order.
  it("ticks a tab holding an artifact regardless of stage", () => {
    expect(tabState("runs", at("understanding_agent"), [], true)).toBe(
      TAB_STATE.DONE,
    );
  });

  it("stops spinning once the run is terminal", () => {
    expect(tabState("environment", at("canceled"), [])).not.toBe(
      TAB_STATE.WORKING,
    );
  });
});

describe("environmentName", () => {
  it("prefers agent_name, which only older jobs carry", () => {
    expect(
      environmentName({ metadata: { agent_name: "ride-voice-agent", name: "repo" } }),
    ).toBe("ride-voice-agent");
  });

  it("falls back to the name authoring writes", () => {
    expect(environmentName({ metadata: { name: "harden-voice-harness-flows" } })).toBe(
      "harden-voice-harness-flows",
    );
  });

  // The job id is the value the list used to show, and the one search cannot match.
  it("never shows the job id", () => {
    const job = { job_id: "ee0f9fa4-e5a8-4c0e-a6d3-05522801abf5", metadata: {} };
    expect(environmentName(job)).toBe("\u2014");
  });

  it("says nothing rather than a uuid when there is no name", () => {
    expect(environmentName({ metadata: {} })).toBe("\u2014");
    expect(environmentName({})).toBe("\u2014");
    expect(environmentName(undefined)).toBe("\u2014");
  });

  it("treats a blank name as absent", () => {
    expect(environmentName({ metadata: { agent_name: "", name: "" } })).toBe("\u2014");
  });

  it("lets a caller supply its own fallback for slots that cannot be blank", () => {
    expect(environmentName({ metadata: {} }, "RL Environment")).toBe("RL Environment");
  });
});

describe("eventMessage — hosted vocabulary", () => {
  const hosted = (type, payload, stage) => ({ type, stage, payload });

  it("arrows a stage change, and says 'started' when there is no predecessor", () => {
    expect(
      eventMessage(hosted("stage_changed", { from: null, to: "validating_environment" })),
    ).toBe("Started validating environment");
    expect(
      eventMessage(
        hosted("stage_changed", { from: "validating_environment", to: "running" }),
      ),
    ).toBe("Validating environment → Running scenarios");
  });

  it("names the frozen baseline", () => {
    expect(
      eventMessage(hosted("baseline_frozen", { baseline_ref: "alk_baseline_world_db" })),
    ).toBe("Baseline frozen · alk_baseline_world_db");
    expect(eventMessage(hosted("baseline_frozen", {}))).toBe("Baseline frozen");
  });

  // Ten of these in a row are indistinguishable without the key.
  it("names the scenario, and only mentions a repeat attempt", () => {
    expect(
      eventMessage(
        hosted("scenario_started", { scenario_key: "book-with-card-and-otp", scenario_attempt: 1 }),
      ),
    ).toBe("Scenario book-with-card-and-otp started");
    expect(
      eventMessage(
        hosted("scenario_retried", { scenario_key: "cancel-booked-ride", scenario_attempt: 2 }),
      ),
    ).toBe("Scenario cancel-booked-ride retried · attempt 2");
  });

  it("reports how the run actually went", () => {
    expect(
      eventMessage(
        hosted("terminal", {
          stage: "completed",
          reason: null,
          scenario_counts: { passed: 1, failed: 9, errored: 0, skipped: 0 },
        }),
      ),
    ).toBe("Completed · 1 passed, 9 failed");
  });

  it("gives the reason a run was cut short", () => {
    expect(
      eventMessage(
        hosted("terminal", { stage: "canceled", reason: "ttl_exceeded", scenario_counts: {} }),
      ),
    ).toBe("Canceled · Ttl exceeded");
  });

  it("spells out degraded parallelism", () => {
    expect(
      eventMessage(
        hosted("parallelism_degraded", { requested: 4, effective: 1, reason: "fixed_port" }),
      ),
    ).toBe("Parallelism reduced 4 → 1 · Fixed port");
  });

  it("covers the remaining contract types", () => {
    expect(eventMessage(hosted("world_unhealthy", {}))).toBe("World unhealthy");
    expect(eventMessage(hosted("baseline_inputs_changed", {}))).toBe(
      "Baseline inputs changed",
    );
  });

  it("shows a log line as written", () => {
    expect(
      eventMessage(hosted("log", { level: "warning", message: "retrying upload" })),
    ).toBe("retrying upload");
  });

  // An event type ALK adds later still reads as its name rather than breaking.
  it("falls back to the type for an unknown event", () => {
    expect(eventMessage(hosted("something_new", {}))).toBe("Something new");
  });
});

describe("eventMessage — sandbox vocabulary is unchanged", () => {
  it("still reads stage events off the payload", () => {
    expect(
      eventMessage({ type: "harness.stage.started", payload: { stage: "environment" } }),
    ).toBe("Environment started");
    expect(
      eventMessage({ type: "harness.stage.completed", payload: { stage: "calls" } }),
    ).toBe("Calls completed");
    expect(
      eventMessage({ type: "harness.stage.failed", payload: { stage: "scenarios" } }),
    ).toBe("Scenarios failed");
  });

  it("names the run rather than repeating its terminal stage", () => {
    expect(
      eventMessage({ type: "harness.run.completed", payload: { stage: "completed" } }),
    ).toBe("Run completed");
  });

  it("still prefers an explicit detail or message", () => {
    expect(
      eventMessage({ type: "stage_changed", payload: { detail: "hand written", to: "running" } }),
    ).toBe("hand written");
  });
});

describe("scenarioTally", () => {
  it("names the failures a hosted run reports", () => {
    expect(
      scenarioTally({ completed_scenarios: 1, failed_scenarios: 9, total_scenarios: 10 }),
    ).toBe("1 passed · 9 failed / 10 scenarios");
  });

  // The sandbox sends no failure count; inventing one would be worse than the ratio.
  it("keeps the plain ratio when nothing failed or nothing is counted", () => {
    expect(scenarioTally({ completed_scenarios: 1, total_scenarios: 1 })).toBe(
      "1 / 1 scenarios",
    );
    expect(
      scenarioTally({ completed_scenarios: 3, failed_scenarios: 0, total_scenarios: 3 }),
    ).toBe("3 / 3 scenarios");
  });

  it("falls back to the requested count before the run reports one", () => {
    expect(scenarioTally({}, { scenario_count: 10 })).toBe("0 / 10 scenarios");
    expect(scenarioTally(undefined, undefined)).toBe("0 / 0 scenarios");
  });
});

describe("scenarioOutcome", () => {
  const run = {
    scenarios: [{ scenario_key: "book-with-card-and-otp", status: "failed" }],
    receipts: [
      {
        scenario_key: "book-with-card-and-otp",
        scenario_attempt: 1,
        status: "failed",
        call: { turns: 13, duration_ms: 97560 },
        sub_goals: [
          { name: "ride_booked", held: false, judged: false, reason: "book_ride never succeeded" },
        ],
      },
    ],
  };

  it("joins a scenario to its call and sub-goals on the key", () => {
    const outcome = scenarioOutcome("book-with-card-and-otp", 1, run);
    expect(outcome.status).toBe("failed");
    expect(outcome.turns).toBe(13);
    expect(outcome.subGoals).toHaveLength(1);
    expect(callSummary(outcome)).toBe("13 turns · 1m 37s");
  });

  // Scenarios are registered before they run, so `scenarios[]` answers for one that has not
  // started. "registered" is not a verdict and must not reach the row as a chip.
  it("says nothing for a scenario that has only been registered", () => {
    expect(
      scenarioOutcome("pending-one", 1, {
        scenarios: [{ scenario_key: "pending-one", status: "registered" }],
        receipts: [],
      }),
    ).toBeNull();
  });

  // A retry emits a second row under the same key. Attempt 1 must not claim attempt 2's verdict.
  it("keeps each attempt to its own receipt", () => {
    const retried = {
      scenarios: [{ scenario_key: "flaky", status: "passed" }],
      receipts: [
        { scenario_key: "flaky", scenario_attempt: 1, status: "failed", call: { turns: 4 }, sub_goals: [] },
        { scenario_key: "flaky", scenario_attempt: 2, status: "passed", call: { turns: 9 }, sub_goals: [] },
      ],
    };
    expect(scenarioOutcome("flaky", 1, retried).status).toBe("failed");
    expect(scenarioOutcome("flaky", 1, retried).turns).toBe(4);
    expect(scenarioOutcome("flaky", 2, retried).status).toBe("passed");
    expect(scenarioOutcome("flaky", 2, retried).turns).toBe(9);
  });

  // The registration's verdict belongs to the attempt that ran last, so a row whose own
  // attempt has not reported shows nothing rather than borrowing it.
  it("does not lend the registration verdict to an unreported attempt", () => {
    expect(
      scenarioOutcome("flaky", 1, {
        scenarios: [{ scenario_key: "flaky", status: "passed" }],
        receipts: [
          { scenario_key: "flaky", scenario_attempt: 2, status: "passed", call: {}, sub_goals: [] },
        ],
      }),
    ).toBeNull();
  });

  it("treats a missing attempt as the first", () => {
    const outcome = scenarioOutcome("book-with-card-and-otp", undefined, run);
    expect(outcome.status).toBe("failed");
  });

  it("returns nothing when the scenario has not reported", () => {
    expect(scenarioOutcome("not-run-yet", 1, run)).toBeNull();
    expect(scenarioOutcome("book-with-card-and-otp", 1, { scenarios: [], receipts: [] })).toBeNull();
    expect(scenarioOutcome(undefined, 1, run)).toBeNull();
    expect(scenarioOutcome("book-with-card-and-otp", 1, undefined)).toBeNull();
  });

  it("reports a scenario whose call was never measured", () => {
    const outcome = scenarioOutcome("k", 1, {
      scenarios: [{ scenario_key: "k", status: "passed" }],
      receipts: [],
    });
    expect(outcome.status).toBe("passed");
    expect(callSummary(outcome)).toBe("");
    expect(outcome.subGoals).toEqual([]);
  });
});

// Shapes taken from a real hosted run: the scenario errored before it ran, so every check
// is unjudged and the receipt's failure is the only account of what happened.
describe("scenarioOutcome — an errored scenario", () => {
  const run = {
    scenarios: [{ scenario_key: "noor-books-uberx", status: "errored" }],
    receipts: [
      {
        scenario_key: "noor-books-uberx",
        scenario_attempt: 1,
        status: "errored",
        call: null,
        failure: {
          domain: "environment",
          code: "world_unavailable",
          stage: "running",
          message: "target agent never joined the room: Target agent did not become ready",
        },
        sub_goals: [
          { name: "address_confirmed", held: null, judged: false, reason: null },
          { name: "otp_verified", held: null, judged: false, reason: null },
        ],
      },
    ],
  };

  it("carries the failure that explains the run", () => {
    const outcome = scenarioOutcome("noor-books-uberx", 1, run);
    expect(outcome.status).toBe("errored");
    expect(outcome.failure.code).toBe("world_unavailable");
    expect(outcome.failure.message).toMatch(/never joined the room/);
  });

  it("survives a null call", () => {
    const outcome = scenarioOutcome("noor-books-uberx", 1, run);
    expect(outcome.turns).toBeNull();
    expect(callSummary(outcome)).toBe("");
  });
});
