import React from "react";
import PropTypes from "prop-types";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  fetchAllObserveProjects: vi.fn(),
}));

vi.mock("src/api/project/observe-project-list", () => ({
  fetchAllObserveProjects: mocks.fetchAllObserveProjects,
}));

import useProjectFilterField from "../useProjectFilterField";

const createWrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const QueryWrapper = ({ children }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  QueryWrapper.propTypes = { children: PropTypes.node };
  return QueryWrapper;
};

describe("useProjectFilterField", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("builds the complete Project picker from the bounded shared loader", async () => {
    mocks.fetchAllObserveProjects.mockResolvedValue([
      { id: "project-1", name: "Project One" },
      { id: "project-2", name: "Project Two" },
    ]);

    const { result } = renderHook(() => useProjectFilterField(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current?.choices).toHaveLength(2));
    expect(result.current).toEqual({
      id: "project_id",
      name: "Project",
      category: "system",
      type: "string",
      choices: [
        { value: "project-1", label: "Project One" },
        { value: "project-2", label: "Project Two" },
      ],
    });
    expect(mocks.fetchAllObserveProjects).toHaveBeenCalledOnce();
    expect(
      mocks.fetchAllObserveProjects.mock.calls[0][0]?.signal,
    ).toBeInstanceOf(AbortSignal);
  });

  it("does not load or publish the Project field when disabled", () => {
    const { result } = renderHook(
      () => useProjectFilterField({ enabled: false }),
      { wrapper: createWrapper() },
    );

    expect(result.current).toBeNull();
    expect(mocks.fetchAllObserveProjects).not.toHaveBeenCalled();
  });
});
