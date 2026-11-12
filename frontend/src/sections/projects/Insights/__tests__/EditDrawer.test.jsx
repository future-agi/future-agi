import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import EditDrawer from "../EditDrawer";

vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/sections/projects/ScopeFilterBar", () => ({
  default: () => null,
}));
vi.mock("../NewReportTune", () => ({ AttributesEditor: () => null }));

const theme = createTheme({ palette: palette("light"), typography });

// Saved from New report's "Analyse a sample of 137 instead", which fits the
// sample to the credits left.
const config = {
  id: "config-1",
  name: "Weekly failures",
  question: "Where does the agent fall short?",
  lens: "failures",
  unit: "sessions",
  attributes: [],
  sample_size: 137,
  scope: { preset: "fixed", label: "137 sessions from Instant eval" },
  has_taxonomy: false,
};

function renderDrawer() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ThemeProvider theme={theme}>
        <EditDrawer
          open
          projectId="project-1"
          config={config}
          scopeLabel={config.scope.label}
          onClose={vi.fn()}
          onRunNow={vi.fn()}
        />
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.clearAllMocks();
});

describe("EditDrawer sample size (W10-INSIGHTS-U08)", () => {
  it("shows a credit-fitted sample size the report runs on", () => {
    renderDrawer();
    const sample = screen.getByRole("combobox", { name: "Sample size" });
    expect(sample).toHaveTextContent("137");

    fireEvent.mouseDown(sample);
    const listbox = screen.getByRole("listbox");
    const options = within(listbox).getAllByRole("option");
    expect(options.map((option) => option.textContent)).toEqual([
      "50",
      "100",
      "137",
      "300",
      "500",
      "1,000",
    ]);
    const picked = within(listbox).getByRole("option", { name: "137" });
    expect(picked).toHaveAttribute("aria-selected", "true");
  });
});
