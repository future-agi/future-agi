import { describe, expect, it } from "vitest";

import {
  SimulateApiHarnessEnvironmentsDraftsSaveDraftsResponse,
  SimulateApiHarnessEnvironmentsVersionsAgentDiscardAgentVersionResponse,
  SimulateApiHarnessEnvironmentsVersionsLockPreviewResponse,
  SimulateApiHarnessEnvironmentsVersionsLockVersionBody,
  SimulateApiHarnessEnvironmentsVersionsLockVersionResponse,
  SimulateApiHarnessEnvironmentsVersionsMoveDefaultResponse,
  SimulateApiHarnessEnvironmentsVersionsResponse,
  SimulateApiHarnessEnvironmentsVersionsUnlockVersionBody,
  SimulateApiHarnessEnvironmentsVersionsUnlockVersionResponse,
} from "src/generated/api-contracts/api.zod";
import type { HarnessLockRefusalApi } from "src/generated/api-contracts/api.schemas";

// Bodies shaped as simulate/services/harness_versioning/read.py::list_versions and
// locked_versions.py::lock_preview send them.
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

// A locked version whose locker left the workspace, on versions with no number, not unlocked.
const lockedVersion = {
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
      code: "lock_not_ready",
      message: "Build this environment before you lock a version.",
    },
    train: null,
    test: null,
    test_target: null,
  },
  // Too few test tasks: the counts it would get, and no target.
  blocked: {
    environment_version_id: lockedVersion.environment_version_id,
    suite_version_id: lockedVersion.suite_version_id,
    proved: false,
    can_lock: false,
    refusal: {
      code: "lock_too_few_test",
      message: "Add scenarios to lock: a test split needs at least 4.",
    },
    train: 7,
    test: 3,
    test_target: null,
  },
  ready: {
    environment_version_id: lockedVersion.environment_version_id,
    suite_version_id: lockedVersion.suite_version_id,
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
  ["lock", SimulateApiHarnessEnvironmentsVersionsLockVersionResponse],
  ["unlock", SimulateApiHarnessEnvironmentsVersionsUnlockVersionResponse],
  ["move default", SimulateApiHarnessEnvironmentsVersionsMoveDefaultResponse],
  [
    "discard agent version",
    SimulateApiHarnessEnvironmentsVersionsAgentDiscardAgentVersionResponse,
  ],
])("generated %s response", (_name, schema) => {
  it("accepts its versions and locked versions", () => {
    const body = {
      agent: [version],
      environment: [version],
      suite: [version],
      locked_versions: [lockedVersion],
    };

    expect(schema.parse(body)).toEqual(body);
  });

  it("keeps a locked version's own label required", () => {
    const body = {
      agent: [],
      environment: [],
      suite: [],
      locked_versions: [{ ...lockedVersion, label: null }],
    };

    const parsed = schema.safeParse(body);

    expect(parsed.success).toBe(false);
    expect(parsed.error?.issues.map((issue) => issue.path.join("."))).toEqual([
      "locked_versions.0.label",
    ]);
  });
});

describe("generated lock preview response", () => {
  it.each(Object.entries(previews))(
    "accepts a %s lock preview",
    (_state, preview) => {
      expect(
        SimulateApiHarnessEnvironmentsVersionsLockPreviewResponse.parse(
          preview,
        ),
      ).toEqual(preview);
    },
  );

  it("refuses a refusal without its message", () => {
    const parsed =
      SimulateApiHarnessEnvironmentsVersionsLockPreviewResponse.safeParse({
        ...previews.blocked,
        refusal: { code: "lock_too_few_test" },
      });

    expect(parsed.success).toBe(false);
    expect(parsed.error?.issues.map((issue) => issue.path.join("."))).toEqual([
      "refusal",
    ]);
  });
});

describe("generated drafts save response", () => {
  const versions = {
    agent: [],
    environment: [],
    suite: [],
    locked_versions: [lockedVersion],
  };

  it("accepts the versions it carries, locked versions included", () => {
    const body = { saved: {}, versions };

    expect(
      SimulateApiHarnessEnvironmentsDraftsSaveDraftsResponse.parse(body),
    ).toEqual(body);
  });

  it("keeps a locked version's own label required", () => {
    const body = {
      saved: {},
      versions: {
        ...versions,
        locked_versions: [{ ...lockedVersion, label: null }],
      },
    };

    expect(() =>
      SimulateApiHarnessEnvironmentsDraftsSaveDraftsResponse.parse(body),
    ).toThrow();
  });
});

describe("generated lock refusal", () => {
  it("types a nullable field of the refusal as null, as the server may send it", () => {
    // The type-check holds this: a refusal outside the nullable list types piece
    // as a string only.
    const refusal: HarnessLockRefusalApi = {
      error: "version_conflict",
      message: "The default suite version moved.",
      retryable: false,
      piece: null,
    };

    expect(refusal.piece).toBeNull();
  });
});

// The server refuses an unknown field of these bodies with 400
// (test_harness_locked_versions.py::TestRequestScope); the schemas say so too.
describe.each([
  [
    "lock",
    SimulateApiHarnessEnvironmentsVersionsLockVersionBody,
    {
      environment_version_id: "7d4c1a52-8d43-4c55-9d3e-0c5d0b1f6a10",
      suite_version_id: "0c5d0b1f-6a10-4c55-9d3e-7d4c1a528d43",
    },
  ],
  [
    "unlock",
    SimulateApiHarnessEnvironmentsVersionsUnlockVersionBody,
    { locked_version_id: "7d4c1a52-8d43-4c55-9d3e-0c5d0b1f6a10" },
  ],
])("generated %s request", (_name, schema, body) => {
  it("accepts its own fields", () => {
    expect(schema.parse(body)).toEqual(body);
  });

  it("refuses an unknown field, as the server does", () => {
    expect(schema.safeParse({ ...body, release_id: "x" }).success).toBe(false);
  });
});
