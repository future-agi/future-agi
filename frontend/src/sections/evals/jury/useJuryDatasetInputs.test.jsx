import { describe, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";
import {
  QueryClient,
  QueryClientProvider,
  useQuery,
} from "@tanstack/react-query";
import useJuryDatasetInputs from "./useJuryDatasetInputs";
// The hook keeps an idle client of its own for a picker rendered without a
// query provider, so the module's client and context stay real.
vi.mock("@tanstack/react-query", async (importOriginal) => ({
  ...(await importOriginal()),
  useQuery: vi.fn(),
}));
vi.mock("src/launch/launchSwitch", () => ({ isSurfaceShown: () => true }));
vi.mock("src/utils/axios", () => ({ default: { get: vi.fn() } }));

describe("jury input hydration", () => {
  it("retains saved structured mappings while the version loads", () => {
    useQuery.mockReturnValue({ data: undefined });
    const { result } = renderHook(() =>
      useJuryDatasetInputs("parent", true, "version", ["output"], {}, vi.fn(), {
        output: "a",
        steps: "b",
        checks: "c",
      }),
    );
    expect(result.current).toEqual(["output", "steps", "checks"]);
    const definition = { seats: [{ kind: "check" }] };
    expect(useQuery.mock.calls.at(-1)[0].placeholderData(definition)).toBe(
      definition,
    );
  });
  it("drops obsolete inputs only once the chosen definition is available", () => {
    useQuery.mockReturnValue({ data: { seats: [{ kind: "check" }] } });
    const { result } = renderHook(() =>
      useJuryDatasetInputs("parent", true, "version", ["output"], {}, vi.fn(), {
        steps: "b",
        checks: "c",
      }),
    );
    expect(result.current).toEqual(["output", "checks"]);
  });
  it("reads the jury definition from the query provider's client when there is one", async () => {
    const actual = await vi.importActual("@tanstack/react-query");
    useQuery.mockImplementation(actual.useQuery);
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: Infinity } },
    });
    client.setQueryData(["jury-dataset-inputs", "parent", "version"], {
      seats: [{ kind: "eval", level: "step" }, { kind: "check" }],
    });
    const wrapper = (props) => (
      <QueryClientProvider client={client} {...props} />
    );
    try {
      const { result } = renderHook(
        () =>
          useJuryDatasetInputs(
            "parent",
            true,
            "version",
            ["output"],
            {},
            vi.fn(),
            {},
          ),
        { wrapper },
      );
      // Only the provider's cache holds this definition; the hook's own idle
      // client has none, and there is no saved mapping to fall back on.
      expect(result.current).toEqual(["output", "steps", "checks"]);
    } finally {
      useQuery.mockReset();
      client.clear();
    }
  });
});
