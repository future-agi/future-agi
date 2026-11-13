import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "src/components/snackbar";
import { DevelopDetailContext } from "../Context/DevelopDetailContext";
import { useAddColumnDrawerStore } from "../states";
import AddColumnDrawer from "./AddColumnDrawer";

vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/snackbar", () => ({ enqueueSnackbar: vi.fn() }));

describe("AddColumnDrawer name validation", () => {
  let post;
  let queryClient;

  beforeEach(() => {
    vi.clearAllMocks();
    post = vi.spyOn(axios, "post").mockResolvedValue({ data: {} });
    useAddColumnDrawerStore.setState({ openAddColumnDrawer: true });
    queryClient = new QueryClient({
      defaultOptions: { mutations: { retry: false } },
    });
  });

  afterEach(() => {
    cleanup();
    queryClient.clear();
    useAddColumnDrawerStore.setState({ openAddColumnDrawer: false });
    vi.restoreAllMocks();
  });

  async function openTextColumn(refreshGrid = vi.fn()) {
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/datasets/dataset-1"]}>
          <Routes>
            <Route
              path="/datasets/:dataset"
              element={
                <DevelopDetailContext.Provider value={{ refreshGrid }}>
                  <AddColumnDrawer />
                </DevelopDetailContext.Provider>
              }
            />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await userEvent.click(screen.getByText("Text", { exact: true }));
    return screen.getByRole("textbox", { name: /Column name/ });
  }

  it.each(["User Name", "name!@#", "   "])(
    "shows allowed characters and never submits invalid name %j",
    async (name) => {
      const input = await openTextColumn();
      await userEvent.type(input, name);
      await userEvent.click(screen.getByRole("button", { name: "Add Column" }));

      expect(screen.getByText(/Only lowercase letters/)).toBeInTheDocument();
      expect(input).toHaveAttribute("aria-invalid", "true");
      expect(post).not.toHaveBeenCalled();
      expect(useAddColumnDrawerStore.getState().openAddColumnDrawer).toBe(true);
    },
  );

  it("submits a corrected name through the real mutation and closes after success", async () => {
    const refreshGrid = vi.fn();
    const input = await openTextColumn(refreshGrid);
    await userEvent.type(input, "User Name");
    await userEvent.click(screen.getByRole("button", { name: "Add Column" }));
    expect(post).not.toHaveBeenCalled();

    await userEvent.clear(input);
    await userEvent.type(input, "customer_1-name");
    expect(
      screen.queryByText(/Only lowercase letters/),
    ).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Add Column" }));

    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(post).toHaveBeenCalledWith(
      endpoints.develop.addColumn("dataset-1"),
      {
        new_column_name: "customer_1-name",
        column_type: "text",
      },
    );
    expect(queryClient.getMutationCache().getAll()[0].state.error).toBeNull();
    await waitFor(() => expect(refreshGrid).toHaveBeenCalledTimes(1));
    expect(enqueueSnackbar).toHaveBeenCalledWith("Column added successfully", {
      variant: "success",
    });
    expect(useAddColumnDrawerStore.getState().openAddColumnDrawer).toBe(false);
  });

  it("keeps empty names disabled and limits browser input to fifty characters", async () => {
    const input = await openTextColumn();
    expect(screen.getByRole("button", { name: "Add Column" })).toBeDisabled();
    expect(input).toHaveAttribute("maxlength", "50");
    await userEvent.type(input, "a".repeat(51));
    expect(input).toHaveValue("a".repeat(50));
    expect(post).not.toHaveBeenCalled();
  });
});
