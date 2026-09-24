import { describe, it, expect, vi, beforeEach } from "vitest";
import PropTypes from "prop-types";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("src/api/simulate-environments/scenarios", async () => {
  const actual = await vi.importActual("src/api/simulate-environments/scenarios");
  return {
    ...actual,
    listScenarios: vi.fn(),
    amendScenarios: vi.fn(),
    scenarioCoverage: vi.fn(),
  };
});

const { listScenarios, amendScenarios, scenarioCoverage } = await import(
  "src/api/simulate-environments/scenarios"
);
const { queryScenarioFixture } = await import(
  "src/api/simulate-environments/_fixtures/scenariosFixtures"
);
const {
  useHarnessScenarios,
  harnessScenariosKey,
  useAmendScenarios,
  useScenarioCoverage,
  harnessScenarioCoverageKey,
} = await import("../scenariosHooks");

const wrapper = ({ children }) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
};
wrapper.propTypes = { children: PropTypes.node };

beforeEach(() => {
  listScenarios.mockReset();
  listScenarios.mockImplementation((jobId, params) => queryScenarioFixture(params));
  amendScenarios.mockReset();
  amendScenarios.mockResolvedValue({ receipts: [] });
  scenarioCoverage.mockReset();
  scenarioCoverage.mockResolvedValue({ per_axis: [], rows: [], columns: [], cells: [], axes: [] });
});

describe("useHarnessScenarios", () => {
  it("translates the 0-indexed UI page to a 1-indexed wire page", async () => {
    const { result } = renderHook(
      () => useHarnessScenarios({ jobId: "job-1", page: 1, pageSize: 5 }),
      { wrapper },
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(listScenarios).toHaveBeenCalledWith(
      "job-1",
      expect.objectContaining({ page: 2, limit: 5 }),
    );
  });

  it("maps the raw response through toPage into rows/total/pageCount/fields", async () => {
    const { result } = renderHook(
      () => useHarnessScenarios({ jobId: "job-1", pageSize: 25, groupBy: "accent" }),
      { wrapper },
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const data = result.current.data;
    expect(data.total).toBe(20);
    expect(data.pageCount).toBe(1);
    expect(data.groupBy).toBe("accent");
    expect(data.rows).toHaveLength(20);
    // Rows are the mapped shape, not the raw server rows.
    expect(data.rows[0]).toHaveProperty("useCase");
    expect(data.rows[0]).toHaveProperty("_raw");
    expect(data.fields.length).toBeGreaterThan(0);
    expect(data.groupings.length).toBeGreaterThan(0);
    expect(data.scenarioEditing).toBeTruthy();
  });

  it("forwards search, group_by, ordering and object-style filters to the api", async () => {
    const { result } = renderHook(
      () =>
        useHarnessScenarios({
          jobId: "job-1",
          search: "payment",
          groupBy: "",
          ordering: "-name",
          filters: { "persona.accent": ["Canadian"] },
        }),
      { wrapper },
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(listScenarios).toHaveBeenCalledWith(
      "job-1",
      expect.objectContaining({
        search: "payment",
        group_by: "",
        ordering: "-name",
        "persona.accent": ["Canadian"],
      }),
    );
  });

  it("stays disabled without a jobId", () => {
    const { result } = renderHook(() => useHarnessScenarios({ jobId: undefined }), {
      wrapper,
    });
    expect(result.current.fetchStatus).toBe("idle");
    expect(listScenarios).not.toHaveBeenCalled();
  });

  it("keys the query off the job id, page, filters and grouping", () => {
    const key = harnessScenariosKey("job-1", {
      page: 2,
      pageSize: 25,
      search: "x",
      groupBy: "accent",
      filters: { status: ["passed"] },
    });
    expect(key[0]).toBe("harness-scenarios");
    expect(key[1]).toBe("job-1");
    expect(key[2]).toMatchObject({ page: 2, groupBy: "accent" });
  });
});

describe("useAmendScenarios", () => {
  it("posts the amend body and invalidates the scenarios prefix (list + coverage)", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const spy = vi.spyOn(client, "invalidateQueries");
    const clientWrapper = ({ children }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    clientWrapper.propTypes = { children: PropTypes.node };

    const body = { rework: true, changes: [{ op: "drop", scenarios: ["a", "b"] }] };
    const { result } = renderHook(() => useAmendScenarios("job-1"), { wrapper: clientWrapper });
    result.current.mutate(body);

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(amendScenarios).toHaveBeenCalledWith("job-1", body);
    // Invalidating the list prefix hits both the list pages and the coverage key
    // (coverage is nested under it).
    expect(spy).toHaveBeenCalledWith({ queryKey: ["harness-scenarios", "job-1"] });
  });
});

describe("useScenarioCoverage", () => {
  it("nests the coverage key under the list prefix so an amend invalidate reaches it", () => {
    const key = harnessScenarioCoverageKey("job-1", { search: "x", rowAxis: "task" });
    expect(key.slice(0, 2)).toEqual(["harness-scenarios", "job-1"]);
    expect(key[2]).toBe("coverage");
  });

  it("sends row_axis / col_axis only when set, so the server default stands", async () => {
    const { result } = renderHook(() => useScenarioCoverage("job-1", { search: "" }), {
      wrapper,
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const params = scenarioCoverage.mock.calls[0][1];
    expect(params).not.toHaveProperty("row_axis");
    expect(params).not.toHaveProperty("col_axis");
  });

  it("forwards a chosen axis and the filters", async () => {
    const { result } = renderHook(
      () =>
        useScenarioCoverage("job-1", {
          rowAxis: "counterparty",
          colAxis: "interface",
          filters: { "persona.accent": ["Canadian"] },
        }),
      { wrapper },
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(scenarioCoverage).toHaveBeenCalledWith(
      "job-1",
      expect.objectContaining({
        row_axis: "counterparty",
        col_axis: "interface",
        "persona.accent": ["Canadian"],
      }),
    );
  });

  it("stays disabled without a jobId", () => {
    const { result } = renderHook(() => useScenarioCoverage(undefined), { wrapper });
    expect(result.current.fetchStatus).toBe("idle");
    expect(scenarioCoverage).not.toHaveBeenCalled();
  });
});
