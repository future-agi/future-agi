import { describe, expect, it } from "vitest";

import {
  SimulateApiHarnessEnvironmentsVersionsLockReleaseResponse,
  SimulateApiHarnessEnvironmentsVersionsResponse,
  SimulateApiHarnessEnvironmentsVersionsUnlockReleaseResponse,
} from "src/generated/api-contracts/api.zod";

// Bodies shaped as simulate/services/harness_versioning/read.py::list_versions sends them.
const AT = "2026-11-25T10:00:00+00:00";

// An unnumbered draft: its label, origin, hash and parent are null on the wire.
const version = {
  id: "7d4c1a52-8d43-4c55-9d3e-0c5d0b1f6a10",
  label: null,
  status: "active",
  is_default: true,
  created_at: AT,
  created_by: null,
  origin: null,
  summary: "",
  hash: null,
  parent_id: null,
  derived_from_agent_version_id: null,
  scenario_count: null,
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
};

// A release whose locker left the workspace, on versions with no number, not unlocked.
const release = {
  id: "0b5d0b1f-8d43-4c55-9d3e-7d4c1a526a10",
  label: "2026.11.1",
  environment_version_id: "1b5d0b1f-8d43-4c55-9d3e-7d4c1a526a10",
  environment_label: null,
  suite_version_id: "2b5d0b1f-8d43-4c55-9d3e-7d4c1a526a10",
  suite_label: null,
  locked_at: AT,
  locked_by: null,
  unlocked_at: null,
  unlocked_by: null,
  train: 14,
  test: 6,
};

const previews = {
  // No build yet: no default pair, so no ids and no counts.
  unbuilt: {
    environment_version_id: null,
    suite_version_id: null,
    proved: false,
    can_lock: false,
    refusal: {
      code: "release_not_ready",
      message: "Build this environment before you lock a release.",
    },
    train: null,
    test: null,
    test_target: null,
  },
  // Too few test tasks: the counts it would get, and no target.
  blocked: {
    environment_version_id: release.environment_version_id,
    suite_version_id: release.suite_version_id,
    proved: false,
    can_lock: false,
    refusal: {
      code: "release_too_few_test",
      message: "Add scenarios to lock: a test split needs at least 4.",
    },
    train: 7,
    test: 3,
    test_target: null,
  },
  ready: {
    environment_version_id: release.environment_version_id,
    suite_version_id: release.suite_version_id,
    proved: true,
    can_lock: true,
    refusal: null,
    train: 14,
    test: 6,
    test_target: 6,
  },
};

describe.each([
  ["versions", SimulateApiHarnessEnvironmentsVersionsResponse],
  ["lock", SimulateApiHarnessEnvironmentsVersionsLockReleaseResponse],
  ["unlock", SimulateApiHarnessEnvironmentsVersionsUnlockReleaseResponse],
])("generated %s response", (_name, schema) => {
  it.each(Object.entries(previews))(
    "accepts a %s lock preview with its releases and versions",
    (_state, lock) => {
      const body = {
        agent: [version],
        environment: [version],
        suite: [version],
        releases: [release],
        lock,
      };

      expect(schema.parse(body)).toEqual(body);
    },
  );

  it("keeps a release's own label required", () => {
    const body = {
      agent: [],
      environment: [],
      suite: [],
      releases: [{ ...release, label: null }],
      lock: previews.ready,
    };

    const parsed = schema.safeParse(body);

    expect(parsed.success).toBe(false);
    expect(parsed.error?.issues.map((issue) => issue.path.join("."))).toEqual([
      "releases.0.label",
    ]);
  });
});
