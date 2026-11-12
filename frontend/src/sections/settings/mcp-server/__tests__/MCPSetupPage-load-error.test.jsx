import React from "react";
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, within, fireEvent } from "src/utils/test-utils";
import MCPSetupPage from "../MCPSetupPage";

// W10-SETTINGS-TASKS-U03: a failed read of the MCP server configuration is
// shown on the page, once (no toast repeats it), with a Retry.

const h = vi.hoisted(() => ({
  options: [],
  state: {},
}));

vi.mock("src/api/mcp", () => ({
  useMCPConfig: (options) => {
    h.options.push(options);
    return h.state;
  },
  useUpdateMCPToolGroups: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: vi.fn(),
}));

beforeEach(() => {
  h.options = [];
  h.state = {
    data: undefined,
    isLoading: false,
    isError: true,
    isRefetching: false,
    refetch: vi.fn(),
  };
});

describe("Settings > MCP Server load failure", () => {
  it("offers Retry and marks the error as shown on the page", () => {
    render(<MCPSetupPage />);

    const failure = within(screen.getByRole("alert"));
    expect(
      failure.getByText("Failed to load MCP server configuration"),
    ).toBeVisible();
    expect(failure.getByText("Check your connection and retry.")).toBeVisible();
    expect(screen.queryByText("Please try again later.")).toBeNull();

    const retry = failure.getByRole("button", { name: "Retry" });
    expect(retry).toBeEnabled();
    fireEvent.click(retry);
    expect(h.state.refetch).toHaveBeenCalledTimes(1);

    expect(h.options.at(-1)?.meta?.errorHandled).toBe(true);
  });

  it("disables Retry while the configuration is read again", () => {
    h.state.isRefetching = true;
    render(<MCPSetupPage />);

    expect(
      within(screen.getByRole("alert")).getByRole("button", {
        name: "Retrying…",
      }),
    ).toBeDisabled();
  });
});
