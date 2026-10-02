import { describe, it, expect } from "vitest";
import PropTypes from "prop-types";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  usePrebuiltEnvironments,
  prebuiltEnvironmentsQueryKey,
} from "../prebuilt";
import { PREBUILT_ENVIRONMENTS_FIXTURE } from "../_fixtures/prebuiltEnvironments";

const makeWrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const Wrapper = ({ children }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  Wrapper.propTypes = { children: PropTypes.node };
  return { queryClient, Wrapper };
};

describe("usePrebuiltEnvironments", () => {
  it("resolves the fixture as a cloned array, none twin-backed", async () => {
    const { Wrapper } = makeWrapper();
    const { result } = renderHook(() => usePrebuiltEnvironments(), {
      wrapper: Wrapper,
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toHaveLength(15);
    expect(result.current.data[0].id).toBe("env-voice-support");
    expect(
      result.current.data.every((t) => t.agentType !== "twin_backed"),
    ).toBe(true);
    expect(result.current.data).not.toBe(PREBUILT_ENVIRONMENTS_FIXTURE);
  });

  it("keys the query under the shared simulate-environments namespace", () => {
    expect(prebuiltEnvironmentsQueryKey()).toEqual([
      "simulate-environments",
      "prebuilt",
    ]);
  });
});
