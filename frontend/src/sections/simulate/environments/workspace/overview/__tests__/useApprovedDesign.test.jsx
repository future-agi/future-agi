import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, screen, within } from "@testing-library/react";

import { render } from "src/utils/test-utils";
import { MOCK_WORLD } from "src/api/simulate-environments/_fixtures/world";
import { useDesignReview } from "src/api/simulate-environments/designReview";
import { BUILD_STATUS } from "../../../myEnvironments.constants";
import useApprovedDesign, { withApprovedDesign } from "../useApprovedDesign";
import OverviewPanel from "../OverviewPanel";
import { MAP_COPY } from "../sourceToSandbox.constants";

vi.mock(
  "src/api/simulate-environments/designReview",
  async (importOriginal) => ({
    ...(await importOriginal()),
    useDesignReview: vi.fn(() => ({ data: undefined })),
  }),
);

vi.mock("../../agents/AgentVersionsPanel", () => ({
  default: () => null,
}));

const REVIEW = {
  rules: [
    { id: "r1", text: "Never refund more than 100 dollars" },
    { id: "r2", text: "Dropped rule", removed: true },
    { id: "r3", text: "Verify the rider before changing a booking" },
  ],
  use_cases: [
    { id: "u1", text: "Cancel a booked ride" },
    { id: "u2", text: "Dropped use case", removed: true },
    { id: "u3", text: "Change the pickup address" },
  ],
  tools: [
    {
      id: "t1",
      name: "book_ride",
      status: "simulated",
      tables_changed: ["rides"],
    },
    { id: "t2", name: "get_fare", tables_changed: [] },
    { id: "t3", name: "dropped_tool", status: "simulated", removed: true },
  ],
};

const DESIGN_RULES = [
  "Never refund more than 100 dollars",
  "Verify the rider before changing a booking",
];
const DESIGN_USE_CASES = ["Cancel a booked ride", "Change the pickup address"];
const DESIGN_TOOLS = [
  { tool: "book_ride", status: "simulated", writes: true },
  { tool: "get_fare", status: "unknown", writes: false },
];

const waitingEnv = (overrides = {}) => ({
  id: "env-approved",
  designApproved: true,
  buildStatus: BUILD_STATUS.BUILDING,
  rules: [],
  useCases: [],
  toolStatus: [],
  ...overrides,
});

beforeEach(() => {
  useDesignReview.mockReset();
  useDesignReview.mockReturnValue({ data: REVIEW });
});

describe("withApprovedDesign", () => {
  it("returns the env untouched until the review has loaded", () => {
    const env = waitingEnv();
    expect(withApprovedDesign(env, undefined)).toBe(env);
  });

  it("fills rules, use cases and tools from the kept design items", () => {
    const shown = withApprovedDesign(waitingEnv(), REVIEW);
    expect(shown.rules).toEqual(DESIGN_RULES);
    expect(shown.useCases).toEqual(DESIGN_USE_CASES);
    expect(shown.toolStatus).toEqual(DESIGN_TOOLS);
    expect(shown.id).toBe("env-approved");
  });

  it("keeps whatever the env already carries", () => {
    const toolStatus = [{ tool: "own_tool", status: "in_process" }];
    const shown = withApprovedDesign(
      waitingEnv({
        rules: ["Own rule"],
        useCases: ["Own use case"],
        toolStatus,
      }),
      REVIEW,
    );
    expect(shown.rules).toEqual(["Own rule"]);
    expect(shown.useCases).toEqual(["Own use case"]);
    expect(shown.toolStatus).toBe(toolStatus);
  });
});

describe("useApprovedDesign", () => {
  const run = (env) => renderHook(() => useApprovedDesign(env)).result.current;

  it("shows the approved design while an approved env builds without tool status", () => {
    const { env, fromDesign } = run(waitingEnv());
    expect(useDesignReview).toHaveBeenCalledWith("env-approved", {
      enabled: true,
    });
    expect(fromDesign).toBe(true);
    expect(env.rules).toEqual(DESIGN_RULES);
    expect(env.useCases).toEqual(DESIGN_USE_CASES);
    expect(env.toolStatus).toEqual(DESIGN_TOOLS);
  });

  it("treats a missing tool status like an empty one", () => {
    const { fromDesign } = run(waitingEnv({ toolStatus: undefined }));
    expect(fromDesign).toBe(true);
  });

  it("leaves the env alone while the review is still loading", () => {
    useDesignReview.mockReturnValue({ data: undefined });
    const input = waitingEnv();
    const { env, fromDesign } = run(input);
    expect(env).toBe(input);
    expect(fromDesign).toBe(false);
  });

  it("uses the env's own data once the build reports tool status", () => {
    const input = waitingEnv({
      toolStatus: [{ tool: "built_tool", status: "simulated" }],
    });
    const { env, fromDesign } = run(input);
    expect(useDesignReview).toHaveBeenCalledWith("env-approved", {
      enabled: false,
    });
    expect(env).toBe(input);
    expect(fromDesign).toBe(false);
  });

  it.each([
    ["the design is not approved", { designApproved: false }],
    ["the build is not running", { buildStatus: BUILD_STATUS.READY }],
  ])("leaves the env unchanged when %s", (_label, overrides) => {
    const input = waitingEnv(overrides);
    const { env, fromDesign } = run(input);
    expect(useDesignReview).toHaveBeenCalledWith("env-approved", {
      enabled: false,
    });
    expect(env).toBe(input);
    expect(fromDesign).toBe(false);
  });
});

describe("OverviewPanel while an approved design builds", () => {
  const BACKED_WORLD = { stores: [], amendments: [], dependencies: [] };

  const renderPanel = (env) =>
    render(
      <OverviewPanel
        env={{ ...MOCK_WORLD, tools: [], ...env }}
        envState={{ scenarios: [] }}
        onGo={vi.fn()}
        agentConnected
        counts={{ scenarios: 0, evaluations: 0, runs: 0, hardRules: 0 }}
        backedWorld={BACKED_WORLD}
      />,
    );

  const mapGroup = (label) =>
    within(screen.getByText(label).closest('[tabindex="0"]'));

  it("shows the design's kept rules, use cases and tools", () => {
    renderPanel(waitingEnv());

    expect(
      screen.getByRole("button", { name: `${DESIGN_RULES.length} rules` }),
    ).toBeInTheDocument();
    DESIGN_USE_CASES.forEach((text) =>
      expect(screen.getByText(text)).toBeInTheDocument(),
    );
    expect(screen.queryByText("Dropped use case")).toBeNull();
    expect(
      mapGroup(MAP_COPY.groups.tools.label).getByText(
        MAP_COPY.toolsRight(DESIGN_TOOLS.length),
      ),
    ).toBeInTheDocument();
    expect(
      mapGroup(MAP_COPY.groups.rules.label).getByText(
        MAP_COPY.rulesRight(DESIGN_RULES.length),
      ),
    ).toBeInTheDocument();
  });

  it("shows the env's own counts once the build reports tool status", () => {
    renderPanel(
      waitingEnv({
        rules: ["Built rule"],
        useCases: ["Built use case"],
        toolStatus: [{ tool: "built_tool", status: "simulated" }],
      }),
    );

    expect(screen.getByRole("button", { name: "0 rules" })).toBeInTheDocument();
    expect(screen.getByText("Built use case")).toBeInTheDocument();
    expect(screen.queryByText(DESIGN_USE_CASES[0])).toBeNull();
    expect(
      mapGroup(MAP_COPY.groups.tools.label).getByText(MAP_COPY.toolsRight(1)),
    ).toBeInTheDocument();
  });
});
