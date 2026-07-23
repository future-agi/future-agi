import { describe, it, expect, vi, beforeEach } from "vitest";

// ---------------------------------------------------------------------------
// Mock axios so the schema's async .refine() uses a controlled mockGet.
// vi.mock is hoisted, so the schema module sees the mock when imported below.
// ---------------------------------------------------------------------------
const mockGet = vi.fn();

vi.mock("src/utils/axios", () => ({
  default: { get: (...args) => mockGet(...args) },
  endpoints: {
    scenarios: {
      list: "/simulate/scenarios/",
    },
  },
}));

// Import the schema AFTER the mock — it will capture mocked axios.
import { CreateScenarioValidationSchema } from "./common";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Build a minimum-valid payload for the "graph" variant that satisfies all
 * cross-field .refine() rules on the discriminated union.
 */
const makeValidData = (overrides = {}) => ({
  kind: "graph",
  name: "Test Scenario",
  sourceType: "agent_definition",
  sourceId: "agent-123",
  sourceLabel: "TestAgent",
  agentDefinitionId: "agent-123",
  agentDefinitionVersionId: "version-456",
  noOfRows: 20,
  addPersonaAutomatically: true,
  columns: [],
  personas: [],
  config: { graph: null, generateGraph: true },
  customInstructionDisabled: true,
  ...overrides,
});

// ---------------------------------------------------------------------------
// Tests — name uniqueness validation
// ---------------------------------------------------------------------------

describe("CreateScenarioValidationSchema — name uniqueness", () => {
  beforeEach(() => {
    mockGet.mockReset();
  });

  // -----------------------------------------------------------------------
  // Basic required-field behaviour (unchanged by our .refine())
  // -----------------------------------------------------------------------

  it("rejects an empty name via min(1)", async () => {
    const result = await CreateScenarioValidationSchema.safeParseAsync(
      makeValidData({ name: "" }),
    );
    expect(result.success).toBe(false);
    const messages = result.error.issues.map((i) => i.message);
    expect(messages).toContain("Name is required");
  });

  it("does not call the API when the name is empty", async () => {
    await CreateScenarioValidationSchema.safeParseAsync(
      makeValidData({ name: "" }),
    );
    expect(mockGet).not.toHaveBeenCalled();
  });

  it("rejects whitespace-only names and does not call the API", async () => {
    const result = await CreateScenarioValidationSchema.safeParseAsync(
      makeValidData({ name: "   " }),
    );
    expect(result.success).toBe(false);
    const messages = result.error.issues.map((i) => i.message);
    expect(messages).toContain("Name is required");
    // API must not be called for whitespace-only (sync refine catches it first)
    expect(mockGet).not.toHaveBeenCalled();
  });

  // -----------------------------------------------------------------------
  // Uniqueness checks
  // -----------------------------------------------------------------------

  it("passes when no scenario with the same name exists", async () => {
    mockGet.mockResolvedValueOnce({
      data: { results: [] },
    });

    const result = await CreateScenarioValidationSchema.safeParseAsync(
      makeValidData({ name: "Unique Name" }),
    );
    expect(result.success).toBe(true);
    expect(mockGet).toHaveBeenCalledTimes(1);
  });

  it("fails when a scenario with the exact same name exists", async () => {
    mockGet.mockResolvedValueOnce({
      data: { results: [{ name: "Duplicate Name", id: "scenario-1" }] },
    });

    const result = await CreateScenarioValidationSchema.safeParseAsync(
      makeValidData({ name: "Duplicate Name" }),
    );
    expect(result.success).toBe(false);
    const messages = result.error.issues.map((i) => i.message);
    expect(messages).toContain(
      "A scenario with this name already exists. Please choose another name.",
    );
  });

  it("passes when API returns results but none match exactly (partial match)", async () => {
    mockGet.mockResolvedValueOnce({
      data: {
        results: [
          { name: "My Test Scenario Extended", id: "s-1" },
          { name: "Another Test Scenario", id: "s-2" },
        ],
      },
    });

    const result = await CreateScenarioValidationSchema.safeParseAsync(
      makeValidData({ name: "My Test Scenario" }),
    );
    expect(result.success).toBe(true);
  });

  it("treats different-case names as distinct (case-sensitive)", async () => {
    // Backend search is case-insensitive and returns "test" when searching
    // for "Test", but client-side === enforces exact case match.
    mockGet.mockResolvedValueOnce({
      data: { results: [{ name: "test" }] },
    });

    const result = await CreateScenarioValidationSchema.safeParseAsync(
      makeValidData({ name: "Test" }),
    );
    // "Test" !== "test" → no duplicate → validation passes
    expect(result.success).toBe(true);
  });

  it("finds the exact match among multiple results", async () => {
    mockGet.mockResolvedValueOnce({
      data: {
        results: [
          { name: "Testing A", id: "s-1" },
          { name: "Testing B", id: "s-2" },
          { name: "Test", id: "s-3" }, // <-- exact match
          { name: "Testing C", id: "s-4" },
        ],
      },
    });

    const result = await CreateScenarioValidationSchema.safeParseAsync(
      makeValidData({ name: "Test" }),
    );
    expect(result.success).toBe(false);
  });

  // -----------------------------------------------------------------------
  // Graceful degradation (API errors)
  // -----------------------------------------------------------------------

  it("allows submission when the API call rejects (network error)", async () => {
    mockGet.mockRejectedValueOnce(new Error("Network Error"));

    const result = await CreateScenarioValidationSchema.safeParseAsync(
      makeValidData({ name: "Anything" }),
    );
    expect(result.success).toBe(true);
  });

  it("allows submission when the API returns null data", async () => {
    mockGet.mockResolvedValueOnce({ data: null });

    const result = await CreateScenarioValidationSchema.safeParseAsync(
      makeValidData({ name: "Anything" }),
    );
    expect(result.success).toBe(true);
  });

  it("allows submission when the API response has no results field", async () => {
    mockGet.mockResolvedValueOnce({ data: {} });

    const result = await CreateScenarioValidationSchema.safeParseAsync(
      makeValidData({ name: "Anything" }),
    );
    expect(result.success).toBe(true);
  });

  it("allows submission when the API response has undefined results", async () => {
    mockGet.mockResolvedValueOnce({
      data: { results: undefined },
    });

    const result = await CreateScenarioValidationSchema.safeParseAsync(
      makeValidData({ name: "Anything" }),
    );
    expect(result.success).toBe(true);
  });

  // -----------------------------------------------------------------------
  // Special characters and unicode
  // -----------------------------------------------------------------------

  it("handles names with special regex characters", async () => {
    mockGet.mockResolvedValueOnce({
      data: { results: [] },
    });

    const result = await CreateScenarioValidationSchema.safeParseAsync(
      makeValidData({ name: "Test (v1) [draft] *final*" }),
    );
    expect(result.success).toBe(true);
    // Verify the correct search param was sent (escaped on the backend side)
    expect(mockGet).toHaveBeenCalledWith(
      "/simulate/scenarios/",
      expect.objectContaining({
        params: expect.objectContaining({
          search: "Test (v1) [draft] *final*",
        }),
      }),
    );
  });

  it("handles unicode names", async () => {
    mockGet.mockResolvedValueOnce({
      data: { results: [{ name: "测试场景" }] },
    });

    const result = await CreateScenarioValidationSchema.safeParseAsync(
      makeValidData({ name: "测试场景" }),
    );
    expect(result.success).toBe(false);
  });

  it("distinguishes unicode names with different code points", async () => {
    mockGet.mockResolvedValueOnce({
      data: { results: [{ name: "测试场景" }] },
    });

    // Different unicode string — should not match
    const result = await CreateScenarioValidationSchema.safeParseAsync(
      makeValidData({ name: "测试场景2" }),
    );
    expect(result.success).toBe(true);
  });

  // -----------------------------------------------------------------------
  // Edge cases with empty results from API
  // -----------------------------------------------------------------------

  it("uses a limit of 50 to maximise chance of finding an exact match", async () => {
    mockGet.mockResolvedValueOnce({
      data: { results: [] },
    });

    await CreateScenarioValidationSchema.safeParseAsync(
      makeValidData({ name: "Test" }),
    );

    expect(mockGet).toHaveBeenCalledWith(
      "/simulate/scenarios/",
      expect.objectContaining({
        params: expect.objectContaining({ limit: 50 }),
      }),
    );
  });

  it("trims the name before sending to the API", async () => {
    mockGet.mockResolvedValueOnce({
      data: { results: [] },
    });

    await CreateScenarioValidationSchema.safeParseAsync(
      makeValidData({ name: "  Padded Name  " }),
    );

    expect(mockGet).toHaveBeenCalledWith(
      "/simulate/scenarios/",
      expect.objectContaining({
        params: expect.objectContaining({ search: "Padded Name" }),
      }),
    );
  });

  it("allows submission when results is not an array", async () => {
    // .some() is only available on arrays; nullish coalescing handles this
    mockGet.mockResolvedValueOnce({
      data: { results: "not-an-array" },
    });

    // String doesn't have .some(), so this would throw.
    // But our ?? [] ensures results is always an array.
    const result = await CreateScenarioValidationSchema.safeParseAsync(
      makeValidData({ name: "Test" }),
    );
    expect(result.success).toBe(true);
  });
});
