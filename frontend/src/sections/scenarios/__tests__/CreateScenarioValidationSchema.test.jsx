import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("src/utils/axios", () => ({
  default: { get: mocks.get },
  endpoints: { scenarios: { list: "/simulate/scenarios/" } },
}));

import { CreateScenarioType, CreateScenarioValidationSchema } from "../common";

const DUPLICATE_MESSAGE =
  "A scenario with this name already exists. Please choose another name.";

const buildValues = (name) => ({
  agentType: "text",
  sourceType: "agent_definition",
  sourceId: "agent-1",
  sourceLabel: "Agent",
  name,
  description: "",
  agentDefinitionId: "agent-1",
  agentDefinitionVersionId: "version-1",
  promptTemplateId: "",
  promptVersionId: "",
  customInstructionDisabled: true,
  customInstruction: "",
  kind: CreateScenarioType.GRAPH,
  noOfRows: 20,
  addPersonaAutomatically: true,
  columns: [],
  personas: [],
  config: { graph: null, generateGraph: true },
});

const listResponse = (names) => ({
  data: { results: names.map((name, index) => ({ id: `s-${index}`, name })) },
});

const nameIssues = (result) =>
  (result.error?.issues ?? []).filter((issue) => issue.path[0] === "name");

describe("CreateScenarioValidationSchema name uniqueness", () => {
  beforeEach(() => {
    mocks.get.mockReset();
  });

  it("rejects a name that exactly matches an existing scenario", async () => {
    mocks.get.mockResolvedValue(
      listResponse(["Support flow v2", "Support flow"]),
    );

    const result = await CreateScenarioValidationSchema.safeParseAsync(
      buildValues("Support flow"),
    );

    expect(result.success).toBe(false);
    expect(nameIssues(result).map((issue) => issue.message)).toEqual([
      DUPLICATE_MESSAGE,
    ]);
  });

  it("looks up existing scenarios by the trimmed name", async () => {
    mocks.get.mockResolvedValue(listResponse([]));

    await CreateScenarioValidationSchema.safeParseAsync(
      buildValues("  Support flow  "),
    );

    expect(mocks.get).toHaveBeenCalledTimes(1);
    const [url, config] = mocks.get.mock.calls[0];
    expect(url).toBe("/simulate/scenarios/");
    expect(config.params.search).toBe("Support flow");
  });

  it("treats names that differ only in case as different", async () => {
    mocks.get.mockResolvedValue(listResponse(["test"]));

    const result = await CreateScenarioValidationSchema.safeParseAsync(
      buildValues("Test"),
    );

    expect(result.success).toBe(true);
    expect(nameIssues(result)).toEqual([]);
  });

  it("does not flag partial matches returned by the search", async () => {
    mocks.get.mockResolvedValue(listResponse(["Support flow v2"]));

    const result = await CreateScenarioValidationSchema.safeParseAsync(
      buildValues("Support flow"),
    );

    expect(result.success).toBe(true);
  });

  it("keeps the required message for a blank name and skips the lookup", async () => {
    const result = await CreateScenarioValidationSchema.safeParseAsync(
      buildValues("   "),
    );

    expect(nameIssues(result).map((issue) => issue.message)).toEqual([
      "Name is required",
    ]);
    expect(mocks.get).not.toHaveBeenCalled();
  });

  it("lets the form submit when the duplicate check request fails", async () => {
    mocks.get.mockRejectedValue(new Error("Network Error"));

    const result = await CreateScenarioValidationSchema.safeParseAsync(
      buildValues("Support flow"),
    );

    expect(result.success).toBe(true);
    expect(nameIssues(result)).toEqual([]);
  });
});
