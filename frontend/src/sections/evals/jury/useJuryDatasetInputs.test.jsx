import { describe, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";
import { useQuery } from "@tanstack/react-query";
import useJuryDatasetInputs from "./useJuryDatasetInputs";
vi.mock("@tanstack/react-query", () => ({ useQuery: vi.fn() }));
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
});
