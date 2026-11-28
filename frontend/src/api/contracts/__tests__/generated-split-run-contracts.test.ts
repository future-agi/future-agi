import { describe, expect, it } from "vitest";

import {
  SimulateApiHarnessEnvironmentsCompareResponse,
  SimulateApiHarnessEnvironmentsRunPlanResponse,
  SimulateRunTestsExecutionsListResponse,
  SimulateV3TestExecutionCallsResponse,
} from "src/generated/api-contracts/api.zod";

// A run of the scenarios selected, not of a split: the server sends its split and
// locked version as null (simulate/services/harness_versioning/run_warnings.py::_versions
// and read.py::run_version_labels).
const LOCKED = {
  id: "0b5d0b1f-8d43-4c55-9d3e-7d4c1a526a10",
  label: "2026.11.1",
  locked: true,
};

const plan = (versions: Record<string, unknown>) => ({
  versions: { agent: null, environment: null, suite: null, ...versions },
  warnings: [],
  bundle_cached: null,
  blocked_tools: [],
  runnable: true,
  blocked_code: null,
  held_back: 0,
});

describe("generated run-plan response", () => {
  it("accepts a run without a split", () => {
    const body = plan({ split: null, locked_version: null });

    expect(SimulateApiHarnessEnvironmentsRunPlanResponse.parse(body)).toEqual(
      body,
    );
  });

  it("accepts a split run and the scenarios it would run", () => {
    const body = {
      ...plan({ split: "test", locked_version: LOCKED }),
      scenario_keys: ["cancel-active-ride"],
    };

    expect(SimulateApiHarnessEnvironmentsRunPlanResponse.parse(body)).toEqual(
      body,
    );
  });
});

describe.each([
  [
    "compare, current run",
    SimulateApiHarnessEnvironmentsCompareResponse.shape.versions.shape.current
      .shape,
  ],
  [
    "compare, against run",
    SimulateApiHarnessEnvironmentsCompareResponse.shape.versions.shape.against.unwrap()
      .shape,
  ],
  [
    "executions list",
    SimulateRunTestsExecutionsListResponse.shape.results.unwrap().element.shape,
  ],
  [
    "execution calls",
    SimulateV3TestExecutionCallsResponse.shape.execution.shape,
  ],
])("generated %s fields", (_name, shape) => {
  it("accept a null split and locked version", () => {
    expect(shape.split.safeParse(null).success).toBe(true);
    expect(shape.locked_version.safeParse(null).success).toBe(true);
  });

  it("accept a split and its locked version", () => {
    expect(shape.split.parse("train")).toBe("train");
    expect(shape.locked_version.parse(LOCKED)).toEqual(LOCKED);
  });
});
