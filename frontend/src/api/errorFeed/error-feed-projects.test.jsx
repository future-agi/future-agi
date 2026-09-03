import React from "react";
import PropTypes from "prop-types";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useObserveProjectList } from "./error-feed";
import { fetchAllObserveProjects } from "src/api/project/observe-project-list";

vi.mock("src/api/project/observe-project-list", () => ({
  fetchAllObserveProjects: vi.fn(),
}));

const createWrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const Wrapper = ({ children }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  Wrapper.propTypes = { children: PropTypes.node };
  return Wrapper;
};

describe("Error Feed observe project picker", () => {
  beforeEach(() => vi.clearAllMocks());

  it("maps the complete bounded project catalog into dropdown options", async () => {
    fetchAllObserveProjects.mockResolvedValue([
      { id: "project-1", name: "Whatfix" },
      { id: "project-2", name: "Colektia" },
    ]);

    const { result } = renderHook(() => useObserveProjectList(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toEqual([
      { value: "project-1", label: "Whatfix" },
      { value: "project-2", label: "Colektia" },
    ]);
    expect(fetchAllObserveProjects).toHaveBeenCalledOnce();
    expect(fetchAllObserveProjects).toHaveBeenCalledWith({
      signal: expect.any(AbortSignal),
    });
  });
});
