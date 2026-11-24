import PropTypes from "prop-types";
import { describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    default: { put: vi.fn(async () => ({ data: { tags: ["vip"] } })) },
  };
});

const { useSetScenarioTags } = await import("../scenarioTags");

describe("useSetScenarioTags", () => {
  it("stays pending until the scenario list has refetched", async () => {
    const client = new QueryClient();
    const pending = [];
    vi.spyOn(client, "invalidateQueries").mockImplementation(
      () =>
        new Promise((resolve) => {
          pending.push(resolve);
        }),
    );
    const Wrapper = ({ children }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    Wrapper.propTypes = { children: PropTypes.node };
    const { result } = renderHook(() => useSetScenarioTags("e1"), {
      wrapper: Wrapper,
    });

    act(() => result.current.mutate({ scenarioKey: "s1", tags: ["vip"] }));
    await waitFor(() => expect(client.invalidateQueries).toHaveBeenCalled());
    expect(result.current.isPending).toBe(true);

    await act(async () => pending.forEach((resolve) => resolve()));
    await waitFor(() => expect(result.current.isPending).toBe(false));
  });
});
