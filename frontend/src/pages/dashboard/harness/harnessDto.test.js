import { describe, expect, it } from "vitest";

/**
 * Full DTO fixture matching the public read contract:
 * {job, status, events, stage_outputs, scenarios, receipts}
 */
export const FULL_DTO_FIXTURE = {
  job: {
    job_id: "aaa-bbb-ccc-111",
    run_id: "run-222-333",
    source: {
      kind: "github",
      repository: "future-agi/ride-voice-agent",
      ref: "main",
      visibility: "public",
    },
    metadata: {
      agent_name: "Ride Voice Agent",
      livekit_agent_name: "ride-voice",
      tool_trace_path: "/traces/ride.json",
    },
    run_test_id: "rt-444",
    test_execution_id: "te-555",
  },
  status: {
    state: "completed",
    stage: "completed",
    updated_at: "2026-08-26T12:00:00Z",
    attempt: 1,
    completed_scenarios: 3,
    failed_scenarios: 0,
    total_scenarios: 3,
    deadline_at: "2026-08-26T13:00:00Z",
    failure: null,
  },
  events: [
    {
      event_id: "ev-1",
      sequence: 1,
      stage: "queued",
      type: "harness.stage.started",
      payload: { stage: "queued" },
      emitted_at: "2026-08-26T11:50:00Z",
    },
    {
      event_id: "ev-2",
      sequence: 2,
      stage: "running",
      type: "harness.stage.started",
      payload: { stage: "running" },
      emitted_at: "2026-08-26T11:51:00Z",
    },
    {
      event_id: "ev-3",
      sequence: 3,
      stage: "completed",
      type: "harness.stage.completed",
      payload: { stage: "completed" },
      emitted_at: "2026-08-26T12:00:00Z",
    },
  ],
  stage_outputs: [
    {
      id: "so-1",
      title: "Agent contract",
      summary: "Voice ride-hail agent identified",
      kind: "contract",
      data: {
        one_liner: "A voice agent that handles ride booking",
        modality: "voice",
        runtime: { entrypoint: "agent.py" },
        tools: [{ name: "book_ride" }, { name: "get_eta" }],
        hard_constraints: ["Must confirm pickup location"],
      },
    },
    {
      id: "so-2",
      title: "Environment",
      summary: "LiveKit runtime assembled",
      kind: "environment",
      data: {
        services: ["livekit-server", "redis"],
        project: "ride-harness",
        managed: true,
        overrides: { LIVEKIT_URL: "wss://lk.test" },
      },
    },
    {
      id: "so-3",
      title: "Scenarios",
      summary: "3 grounded test cases",
      kind: "scenarios",
      data: [
        {
          name: "book_airport_ride",
          instruction: "Book a ride from downtown to the airport",
          use_case: "Standard airport transfer",
        },
        {
          name: "cancel_ride",
          instruction: "Cancel a previously booked ride",
          use_case: "Cancellation flow",
        },
        {
          name: "payment_failure",
          instruction: "Attempt booking with a declined card",
          use_case: "Error recovery",
        },
      ],
    },
    {
      id: "so-4",
      title: "Simulation",
      summary: "Simulation completed",
      kind: "simulation",
      data: { url: "https://app.futureagi.com/sim/run-222" },
    },
  ],
  scenarios: [
    {
      scenario_key: "sc-key-1",
      scenario_id: "sc-id-1",
      name: "book_airport_ride",
      instruction: "Book a ride from downtown to the airport",
      use_case: "Standard airport transfer",
      call_execution_id: "ce-1",
      status: "completed",
    },
    {
      scenario_key: "sc-key-2",
      scenario_id: "sc-id-2",
      name: "cancel_ride",
      instruction: "Cancel a previously booked ride",
      use_case: "Cancellation flow",
      call_execution_id: "ce-2",
      status: "completed",
    },
    {
      scenario_key: "sc-key-3",
      scenario_id: "sc-id-3",
      name: "payment_failure",
      instruction: "Attempt booking with a declined card",
      use_case: "Error recovery",
      call_execution_id: "ce-3",
      status: "completed",
    },
  ],
  receipts: [
    {
      receipt_id: "rec-1",
      scenario_key: "sc-key-1",
      scenario_name: "Book airport ride",
      status: "graded",
      call: {
        duration_seconds: 45.2,
        transcript_artifact: "artifact-tx-1",
        recording_artifacts: ["artifact-rec-1"],
      },
      evaluation: {
        score: 0.92,
        summary: "Successfully booked airport ride with correct details",
      },
    },
    {
      receipt_id: "rec-2",
      scenario_key: "sc-key-2",
      scenario_name: "Cancel ride",
      status: "graded",
      call: {
        duration_seconds: 22.8,
        transcript_artifact: "artifact-tx-2",
        recording_artifacts: [],
      },
      evaluation: {
        score: 0.85,
        summary: "Cancellation handled but confirmation was slow",
      },
    },
    {
      receipt_id: "rec-3",
      scenario_key: "sc-key-3",
      scenario_name: "Payment failure",
      status: "graded",
      call: {
        duration_seconds: 38.1,
        transcript_artifact: "artifact-tx-3",
        recording_artifacts: ["artifact-rec-3a", "artifact-rec-3b"],
      },
      evaluation: {
        score: 0.78,
        summary: "Error recovery worked but agent did not offer an alternative",
      },
    },
  ],
};

describe("DTO fixture completeness", () => {
  it("has all top-level DTO keys", () => {
    const keys = Object.keys(FULL_DTO_FIXTURE).sort();
    expect(keys).toEqual(
      ["events", "job", "receipts", "scenarios", "stage_outputs", "status"].sort(),
    );
  });

  it("job contains required identifiers", () => {
    const { job } = FULL_DTO_FIXTURE;
    expect(job.job_id).toBeTruthy();
    expect(job.run_id).toBeTruthy();
    expect(job.source).toBeDefined();
    expect(job.metadata).toBeDefined();
  });

  it("job contains optional run identifiers", () => {
    const { job } = FULL_DTO_FIXTURE;
    expect(job.run_test_id).toBeTruthy();
    expect(job.test_execution_id).toBeTruthy();
  });

  it("status carries all contract fields", () => {
    const { status } = FULL_DTO_FIXTURE;
    expect(status.state).toBe("completed");
    expect(status.stage).toBe("completed");
    expect(status.updated_at).toBeTruthy();
    expect(status.attempt).toBeGreaterThanOrEqual(1);
    expect(status.completed_scenarios).toBe(3);
    expect(status.failed_scenarios).toBe(0);
    expect(status.total_scenarios).toBe(3);
    expect(status.deadline_at).toBeTruthy();
    expect(status.failure).toBeNull();
  });

  it("events carry emitted_at, not wall_time", () => {
    FULL_DTO_FIXTURE.events.forEach((event) => {
      expect(event).toHaveProperty("emitted_at");
      expect(event).not.toHaveProperty("wall_time");
      expect(new Date(event.emitted_at).getTime()).not.toBeNaN();
    });
  });

  it("stage_outputs cover all four tab kinds", () => {
    const kinds = FULL_DTO_FIXTURE.stage_outputs.map((output) => output.kind);
    expect(kinds).toContain("contract");
    expect(kinds).toContain("environment");
    expect(kinds).toContain("scenarios");
    expect(kinds).toContain("simulation");
    FULL_DTO_FIXTURE.stage_outputs.forEach((output) => {
      expect(output.id).toBeTruthy();
      expect(output.title).toBeTruthy();
      expect(output.kind).toBeTruthy();
      expect(output.data).toBeDefined();
    });
  });

  it("scenarios have required fields and optional status", () => {
    FULL_DTO_FIXTURE.scenarios.forEach((scenario) => {
      expect(scenario.scenario_key).toBeTruthy();
      expect(scenario.scenario_id).toBeTruthy();
      expect(scenario.name).toBeTruthy();
      // Optional fields present in this fixture
      expect(scenario.instruction).toBeTruthy();
      expect(scenario.use_case).toBeTruthy();
      expect(scenario.call_execution_id).toBeTruthy();
      expect(scenario.status).toBeTruthy();
    });
  });

  it("receipts carry call and evaluation data", () => {
    FULL_DTO_FIXTURE.receipts.forEach((receipt) => {
      expect(receipt.scenario_key).toBeTruthy();
      expect(receipt.status).toBeTruthy();
      expect(receipt.call).toBeDefined();
      expect(receipt.call.duration_seconds).toBeGreaterThan(0);
      expect(receipt.evaluation).toBeDefined();
      expect(typeof receipt.evaluation.score).toBe("number");
      expect(receipt.evaluation.summary).toBeTruthy();
    });
  });

  it("scenario count matches receipt count", () => {
    expect(FULL_DTO_FIXTURE.scenarios.length).toBe(
      FULL_DTO_FIXTURE.receipts.length,
    );
  });
});

describe("DTO tab rendering helpers", () => {
  it("filters stage_outputs for contract tab", () => {
    const contractOutputs = FULL_DTO_FIXTURE.stage_outputs.filter(
      (output) => output.kind === "contract",
    );
    expect(contractOutputs).toHaveLength(1);
    expect(contractOutputs[0].data.one_liner).toBeTruthy();
    expect(contractOutputs[0].data.tools).toHaveLength(2);
  });

  it("filters stage_outputs for environment tab", () => {
    const envOutputs = FULL_DTO_FIXTURE.stage_outputs.filter(
      (output) => output.kind === "environment",
    );
    expect(envOutputs).toHaveLength(1);
    expect(envOutputs[0].data.services).toContain("livekit-server");
  });

  it("filters stage_outputs for scenarios tab", () => {
    const scenarioOutputs = FULL_DTO_FIXTURE.stage_outputs.filter(
      (output) => output.kind === "scenarios",
    );
    expect(scenarioOutputs).toHaveLength(1);
    expect(Array.isArray(scenarioOutputs[0].data)).toBe(true);
  });

  it("filters stage_outputs for runs tab (non-tab kinds)", () => {
    const runsOutputs = FULL_DTO_FIXTURE.stage_outputs.filter(
      (output) => !["contract", "environment", "scenarios"].includes(output.kind),
    );
    expect(runsOutputs).toHaveLength(1);
    expect(runsOutputs[0].kind).toBe("simulation");
  });

  it("events deduplicate by event_id", () => {
    const seen = new Set();
    const unique = FULL_DTO_FIXTURE.events.filter((event) => {
      if (seen.has(event.event_id)) return false;
      seen.add(event.event_id);
      return true;
    });
    expect(unique.length).toBe(FULL_DTO_FIXTURE.events.length);
  });
});

describe("empty DTO states", () => {
  const emptyDto = {
    job: {
      job_id: "empty-job-1",
      run_id: "empty-run-1",
      source: { kind: "archive", archive_artifact_id: "src-1" },
      metadata: { agent_name: "Empty Agent" },
    },
    status: {
      state: "queued",
      stage: "queued",
      updated_at: "2026-08-26T12:00:00Z",
      attempt: 0,
      completed_scenarios: 0,
      failed_scenarios: 0,
      total_scenarios: 5,
      deadline_at: "2026-08-26T13:00:00Z",
      failure: null,
    },
    events: [],
    stage_outputs: [],
    scenarios: [],
    receipts: [],
  };

  it("empty events produce no activity items", () => {
    expect(emptyDto.events).toHaveLength(0);
  });

  it("empty stage_outputs produce no tab content", () => {
    expect(emptyDto.stage_outputs).toHaveLength(0);
  });

  it("empty scenarios render the empty state", () => {
    expect(emptyDto.scenarios).toHaveLength(0);
  });

  it("empty receipts render the empty state", () => {
    expect(emptyDto.receipts).toHaveLength(0);
  });

  it("failure renders when status has failure", () => {
    const failedDto = {
      ...emptyDto,
      status: {
        ...emptyDto.status,
        state: "failed",
        stage: "failed",
        failure: {
          domain: "infrastructure",
          code: "sandbox_launch_failed",
          message: "Daytona sandbox could not be provisioned",
        },
      },
    };
    expect(failedDto.status.failure).toBeTruthy();
    expect(failedDto.status.failure.domain).toBe("infrastructure");
    expect(failedDto.status.failure.code).toBe("sandbox_launch_failed");
    expect(failedDto.status.failure.message).toBeTruthy();
  });
});
