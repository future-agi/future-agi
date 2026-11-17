import React from "react";
import { expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { render } from "src/utils/test-utils";
const state = vi.hoisted(() => ({ data: undefined, refetch: vi.fn() }));
vi.mock("../getEvalsList", async (original) => ({
  ...(await original()),
  useEvalsList: () => ({
    data: state.data,
    isLoading: false,
    isError: true,
    refetch: state.refetch,
  }),
}));
vi.mock("../EvaluationsSelectionGrid", () => ({ default: () => null }));
vi.mock("../EvaluationMappingForm", () => ({ default: () => null }));
vi.mock("../CustomEvalsForm", () => ({ default: () => null }));
vi.mock("../CreateEvaluationGroupDrawer", () => ({ default: () => null }));
vi.mock("../DeleteEval", () => ({ default: () => null }));
vi.mock("../RunEvals", () => ({ default: () => null }));
vi.mock("src/sections/common/EvalPicker", () => ({
  EvalPickerDrawer: () => null,
}));
import EvaluationDrawer from "../EvaluationDrawer";

it.each([undefined, { evals: [] }])(
  "does not present a failed read/refresh as a verified empty list (%s)",
  (data) => {
    state.data = data;
    state.refetch.mockClear();
    render(
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <EvaluationDrawer
          open
          module="dataset"
          id="dataset-a"
          allColumns={[]}
          onClose={() => {}}
          refreshGrid={() => {}}
        />
      </QueryClientProvider>,
      {
        theme: createTheme({
          palette: palette("light"),
          customShadows: { drawer: "none" },
        }),
      },
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      data ? "Could not refresh evaluations" : "Could not load evaluations",
    );
    expect(screen.queryByText("No evaluations added")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(state.refetch).toHaveBeenCalledOnce();
  },
);
