import { describe, expect, it } from "vitest";

import {
  SimulateApiAgentFixRunsLaunchOptionsResponse,
  SimulateApiHarnessEnvironmentsDraftsResponse,
  SimulateApiHarnessEnvironmentsDraftsSaveDraftsBody,
  SimulateApiHarnessEnvironmentsNudgesAnswerNudgeResponse,
  SimulateApiHarnessEnvironmentsNudgesResponseItem,
  SimulateApiHarnessEnvironmentsNudgesStopNudgeResponse,
  SimulateApiHarnessEnvironmentsVersionsAgentDiscardAgentVersionResponse,
  SimulateApiHarnessEnvironmentsVersionsMoveDefaultResponse,
  SimulateApiHarnessEnvironmentsVersionsResponse,
  SimulateApiHarnessEnvironmentsVersionsVersionDiffResponse,
  SimulateV3CallExecutionDetailResponse,
  SimulateV3TestExecutionCallsResponse,
} from "src/generated/api-contracts/api.zod";

describe.each([
  ["call detail", SimulateV3CallExecutionDetailResponse.shape.sub_goal_results],
  [
    "call list",
    SimulateV3TestExecutionCallsResponse.shape.results.element.shape
      .sub_goal_results,
  ],
])("generated %s sub-goal contract", (_name, schema) => {
  it.each([true, false, null])("preserves verdict %s", (passed) => {
    const name = "Verify the caller's PIN";
    // Every result says what the results show (`display_name`) and whose check it is (`subject_kind`).
    const results = [
      { name, display_name: name, subject_kind: "task", passed },
    ];

    expect(schema.parse(results)).toEqual(results);
  });

  it.each(["false", 0, undefined])("rejects invalid verdict %s", (passed) => {
    expect(
      schema.safeParse([{ name: "Verify the caller's PIN", passed }]).success,
    ).toBe(false);
  });
});

describe("generated launch options contract", () => {
  const { blocked } = SimulateApiAgentFixRunsLaunchOptionsResponse.shape;

  // Only issue_epoch_measuring names an issue; every other refusal sends null.
  it.each([
    {
      code: "fix_run_active",
      message: "An epoch is running.",
      cluster_id: null,
    },
    {
      code: "issue_epoch_measuring",
      message: "Another issue's epoch is starting.",
      cluster_id: "cluster-1",
    },
  ])("accepts a $code refusal", (refusal) => {
    expect(blocked.parse(refusal)).toEqual(refusal);
  });

  it("accepts a launch nothing refuses", () => {
    expect(blocked.parse(null)).toBeNull();
  });

  // An unnumbered, unfinished run of an unversioned environment off a lock, an
  // uploaded agent with no ref, and a scenario with no verdict that no evidence
  // import added.
  it("accepts the fields the server leaves empty", () => {
    const body = {
      source_run: {
        id: "11111111-1111-4111-8111-111111111111",
        number: null,
        state: "completed",
        finished_at: null,
      },
      environment: {
        id: "22222222-2222-4222-8222-222222222222",
        name: "Support",
        version: null,
      },
      agent: {
        name: "Support agent",
        kind: "upload",
        version_id: "33333333-3333-4333-8333-333333333333",
        version_number: null,
        ref: null,
      },
      scenarios: [
        {
          key: "refund",
          name: "Refund",
          passed: null,
          failing_sub_goals: [],
          evidence_import_id: null,
          split: null,
        },
      ],
      suggested: { target_scenario_keys: [], protected_scenario_keys: [] },
      surfaces: [],
      defaults: {
        surfaces: ["prompt"],
        budget_credits: 100,
        max_minutes: 30,
        allowed_models: [],
        agent_cost_metered: false,
        agent_calls_unmeasured: false,
        reply_latency_margin_pct: 0,
        cost_margin_pct: 0,
      },
      limits: {
        budget_credits_max: 1000,
        max_minutes_max: 120,
        scenarios_min: 1,
        cases_min: 1,
        variations_per_case: 1,
        verified_cases_min: 1,
        scenarios_max: 50,
      },
      blocked: null,
      variations_blocked: null,
      held_back_scenario_keys: null,
      ship: null,
      held_back_from: "sample",
      locked_version_label: null,
    };

    expect(SimulateApiAgentFixRunsLaunchOptionsResponse.parse(body)).toEqual(
      body,
    );
  });
});

describe.each([
  ["versions", SimulateApiHarnessEnvironmentsVersionsResponse],
  ["move default", SimulateApiHarnessEnvironmentsVersionsMoveDefaultResponse],
  [
    "discard",
    SimulateApiHarnessEnvironmentsVersionsAgentDiscardAgentVersionResponse,
  ],
])("generated %s response", (_name, schema) => {
  it.each(["agent", "environment", "suite"])(
    "accepts a null source and prompt on %s items",
    (piece) => {
      const item = schema.shape[piece].element.shape;

      expect(item.source.parse(null)).toBeNull();
      expect(item.prompt.parse(null)).toBeNull();
    },
  );
});

describe.each([
  ["nudges", SimulateApiHarnessEnvironmentsNudgesResponseItem],
  ["answer", SimulateApiHarnessEnvironmentsNudgesAnswerNudgeResponse],
  ["stop", SimulateApiHarnessEnvironmentsNudgesStopNudgeResponse],
])("generated %s response", (_name, schema) => {
  it("accepts a failed rebuild's problems", () => {
    const error = {
      code: "checks_failed",
      problems: [
        {
          item: "refund_order",
          kind: "contract",
          message: "bad",
          in_your_unsaved_edits: true,
        },
      ],
    };

    expect(schema.shape.error.parse(error)).toEqual(error);
    expect(schema.shape.error.parse(null)).toBeNull();
  });

  it("accepts a nudge with nothing answered, applied or known yet", () => {
    const item = {
      id: "7d4c1a52-8d43-4c55-9d3e-0c5d0b1f6a10",
      target: "environment",
      state: "pending",
      facts: {
        source: { kind: "github", changed: true, from: null, to: "abc1234" },
        changed_files: [
          {
            path: "prompt.md",
            status: "modified",
            previous_path: null,
            additions: null,
            deletions: null,
            has_patch: false,
            patch_omitted: null,
          },
        ],
        files_total: null,
        files_unavailable: null,
        variables: { added: [], removed: [], changed: ["KEY"], unknown: [] },
        environment_diff: null,
        from_environment_version_id: null,
      },
      trigger_agent_version: {
        id: "7d4c1a52-8d43-4c55-9d3e-0c5d0b1f6a11",
        label: null,
      },
      base_agent_version: null,
      base_environment_version: null,
      base_suite_version: null,
      result_environment_version: null,
      result_suite_version: null,
      summary: null,
      error: null,
      closed_at: null,
      queued_behind_input: false,
      can_stop: false,
      replaced_by: null,
      card_message_id: null,
      answered_by: null,
      answered_at: null,
      created_at: "2026-10-17T00:00:00+00:00",
    };

    expect(schema.parse(item)).toEqual(item);
  });
});

describe("generated version diff contract", () => {
  it("accepts a version without unsaved edits", () => {
    const body = {
      summary: "1 tool added",
      diff: null,
      includes_draft_edits: false,
      snapshot_diff: null,
    };

    expect(
      SimulateApiHarnessEnvironmentsVersionsVersionDiffResponse.parse(body),
    ).toEqual(body);
  });
});

describe("generated drafts contract", () => {
  const draft = {
    id: "7d4c1a52-8d43-4c55-9d3e-0c5d0b1f6a10",
    label: null,
    status: "draft",
    is_default: false,
    created_at: "2026-10-17T00:00:00+00:00",
    created_by: null,
    origin: "chat",
    summary: "1 scenario added",
    hash: "tree_v1:abc",
    parent_id: "0b5d0b1f-8d43-4c55-9d3e-7d4c1a526a10",
    derived_from_agent_version_id: null,
    scenario_count: 3,
    source: null,
    prompt: null,
    preflight_status: null,
    preflight_report: null,
    preflighted_at: null,
    check_started_at: null,
    credential_names: null,
    required_variable_names: null,
    activation_outcome: null,
    requested_here: null,
    pickable: null,
    discardable: null,
    stale: false,
    revision: 1,
    last_edited_by: null,
    updated_at: "2026-10-17T00:00:00+00:00",
    parent_label: "v1",
    default_label: "v1",
    changes: { added: 1, removed: 0, changed: 0 },
    name: null,
    candidate_state: null,
  };

  it("accepts a piece without a draft and an unnumbered draft", () => {
    const body = {
      environment: null,
      suite: draft,
      set_aside: [],
      validation: null,
      candidates: null,
    };

    expect(SimulateApiHarnessEnvironmentsDraftsResponse.parse(body)).toEqual(
      body,
    );
  });

  it("accepts a save that saw no parent", () => {
    const body = {
      drafts: [{ piece: "suite", expected_parent_id: null }],
      idempotency_key: "save-1",
    };

    expect(
      SimulateApiHarnessEnvironmentsDraftsSaveDraftsBody.parse(body),
    ).toEqual(body);
  });
});
