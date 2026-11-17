import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { MemoryRouter } from "react-router-dom";
import { palette } from "src/theme/palette";
import BaseColumnsDrawer from "../BaseColumnsDrawer";

const h = vi.hoisted(() => ({
  columns: [
    "question",
    "question translated for the customer support comparison",
    "answer",
  ],
  get: vi.fn(),
}));
vi.mock("src/utils/axios", () => ({
  default: { get: h.get },
  endpoints: { dataset: { baseColumndata: "/test/base-columns" } },
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));

let client;
beforeEach(() => {
  const noNetwork = () => {
    throw new Error("Network is forbidden in this presentation test");
  };
  vi.stubGlobal("fetch", vi.fn(noNetwork));
  vi.spyOn(XMLHttpRequest.prototype, "open").mockImplementation(noNetwork);
  h.get.mockResolvedValue({ data: { result: { base_columns: h.columns } } });
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
});
afterEach(() => {
  cleanup();
  client.clear();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  h.get.mockReset();
});

function renderDrawer() {
  const setSelectedValue = vi.fn();
  render(
    <QueryClientProvider client={client}>
      <ThemeProvider theme={createTheme({ palette: palette("light") })}>
        <MemoryRouter>
          <BaseColumnsDrawer
            baseColumnDrawerVisible
            selectedDatasets={["one", "two"]}
            selectedDatasetsValues={["First dataset", "Second dataset"]}
            selectedValue="question"
            setSelectedValue={setSelectedValue}
            onBaseColumnDrawerClose={vi.fn()}
            onCompareDatasetDrawerClose={vi.fn()}
          />
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
  return setSelectedValue;
}

describe("D05 comparison column names", () => {
  it("names the radio group using its existing visible heading", async () => {
    renderDrawer();
    await screen.findAllByRole("radio");
    expect(
      screen.getByRole("radiogroup", { name: "Select Base Columns" }),
    ).toBeInTheDocument();
  });

  it("associates each radio with its visible column name, including similar long names", async () => {
    const select = renderDrawer();
    const radios = await screen.findAllByRole("radio");
    expect(radios).toHaveLength(h.columns.length);
    const group = screen.getByRole("radiogroup");
    for (const column of h.columns) {
      const radio = within(group).getByRole("radio", {
        name: column,
        exact: true,
      });
      expect(radio).toHaveAccessibleName(column);
      expect(within(group).getByText(column, { exact: true })).toBeVisible();
    }
    expect(
      within(group).getByRole("radio", { name: "question", exact: true }),
    ).toBeChecked();
    const answer = within(group).getByRole("radio", {
      name: "answer",
      exact: true,
    });
    answer.focus();
    expect(answer).toHaveFocus();
    fireEvent.click(answer);
    expect(select).toHaveBeenCalledWith("answer");
  });
});
