import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";

const mocks = vi.hoisted(() => ({ previewConfig: vi.fn() }));

vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/sections/projects/runPermission", () => ({
  VIEWER_CANT_RUN: "Viewer cannot run",
  useCanStartRuns: () => true,
}));
vi.mock("../insightsApi", () => ({
  actionCreditsBlocked: () => false,
  draftConfig: vi.fn(),
  previewConfig: (...args) => mocks.previewConfig(...args),
  rememberPopulation: vi.fn(),
  startRun: vi.fn(),
  useActionCredits: () => ({ data: { limited: false } }),
  useExampleRows: () => ({}),
  useHasSessions: () => ({ data: true }),
  usePopulationCount: () => ({
    data: { total: 3, sampleIds: ["retained-user", "u2", "u3"] },
    isLoading: false,
    isPlaceholderData: false,
  }),
  isAbort: () => false,
  insightsCreditRefusal: () => null,
  insightsErrorMessage: (error) => error.message,
}));
vi.mock("../ActionCreditNote", () => ({ default: () => null }));
vi.mock("../PreviewExamples", () => ({ default: () => null }));
vi.mock("src/sections/projects/ScopeFilterBar", async () => {
  const { default: React } = await import("react");
  const { default: PropTypes } = await import("prop-types");
  function ScopeFilterBar({ scope, onChange, disabled }) {
    return React.createElement(
      "button",
      {
        type: "button",
        disabled,
        onClick: () =>
          onChange({
            ...scope,
            // Keep the label unchanged: only the actual window changes.
            window: ["2026-11-08T00:00:00.000Z", "2026-11-09T00:00:00.000Z"],
          }),
      },
      "Change preview date",
    );
  }
  ScopeFilterBar.propTypes = {
    scope: PropTypes.object.isRequired,
    onChange: PropTypes.func.isRequired,
    disabled: PropTypes.bool,
  };
  return { default: ScopeFilterBar };
});
vi.mock("../NewReportTune", async () => {
  const { default: React } = await import("react");
  const { default: PropTypes } = await import("prop-types");
  function NewReportTune({ draft, onChange }) {
    return React.createElement("input", {
      "aria-label": "Report name",
      value: draft.name,
      onChange: (event) => onChange({ name: event.target.value }),
    });
  }
  NewReportTune.propTypes = {
    draft: PropTypes.shape({ name: PropTypes.string.isRequired }).isRequired,
    onChange: PropTypes.func.isRequired,
  };
  return { default: NewReportTune };
});

import NewReport from "../NewReport";

const WINDOW = ["2026-11-01T00:00:00.000Z", "2026-11-02T00:00:00.000Z"];
const SEED = { unit: "users", dateOption: "Custom", window: WINDOW };
const clients = [];

function renderReport(seed = SEED) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  clients.push(client);
  const theme = createTheme({ palette: palette("light"), typography });
  render(
    React.createElement(
      QueryClientProvider,
      { client },
      React.createElement(
        ThemeProvider,
        { theme },
        React.createElement(NewReport, {
          projectId: "project",
          seed,
          configs: [],
          onCancel: vi.fn(),
          onStarted: vi.fn(),
        }),
      ),
    ),
  );
  // A fixed name prevents the auto-name from accidentally testing freshness
  // via a changed date label rather than the selected window itself.
  fireEvent.change(screen.getByRole("textbox", { name: "Report name" }), {
    target: { value: "Historical activity" },
  });
}

beforeEach(() => {
  mocks.previewConfig.mockReset();
  mocks.previewConfig.mockResolvedValue({ units: [] });
});

afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
});

describe("NewReport preview scope", () => {
  it("sends the selected custom window with the preview", async () => {
    renderReport();
    fireEvent.click(
      screen.getByRole("button", { name: "Preview on 3 examples" }),
    );
    await screen.findByRole("button", { name: "Try 3 others" });
    expect(mocks.previewConfig).toHaveBeenCalledTimes(1);
    const [body] = mocks.previewConfig.mock.calls[0];
    expect(body.unit_ids).toEqual(["retained-user", "u2", "u3"]);
    expect(body.scope).toMatchObject({ preset: "Custom", window: WINDOW });
    expect(body.scope.filters).toContainEqual({
      column_id: "created_at",
      filter_config: {
        filter_type: "datetime",
        filter_op: "between",
        filter_value: WINDOW,
      },
    });
  });

  it("marks a preview stale when only the date window changes", async () => {
    renderReport();
    fireEvent.click(
      screen.getByRole("button", { name: "Preview on 3 examples" }),
    );
    await screen.findByRole("button", { name: "Try 3 others" });
    fireEvent.click(
      screen.getByRole("button", { name: "Change preview date" }),
    );
    expect(
      screen.getByText(/The settings changed since this preview/),
    ).toBeInTheDocument();
    const preview = screen.getByRole("button", {
      name: "Preview on 3 examples",
    });
    // Don't pair old sampled IDs with a newly selected scope during debounce.
    expect(preview).toBeDisabled();
    await waitFor(() => expect(preview).toBeEnabled());
    fireEvent.click(preview);
    await screen.findByRole("button", { name: "Try 3 others" });
    expect(mocks.previewConfig).toHaveBeenCalledTimes(2);
    const [first] = mocks.previewConfig.mock.calls[0];
    const [second] = mocks.previewConfig.mock.calls[1];
    expect(second.config).toEqual(first.config);
    expect(second.scope.window).toEqual([
      "2026-11-08T00:00:00.000Z",
      "2026-11-09T00:00:00.000Z",
    ]);
    expect(
      screen.queryByText(/The settings changed since this preview/),
    ).toBeNull();
  });

  it("marks handed-over populations as fixed rather than applying the seeded date", async () => {
    renderReport({ ...SEED, population: [{ id: "retained-user" }] });
    fireEvent.click(
      screen.getByRole("button", { name: "Preview on 3 examples" }),
    );
    await screen.findByRole("button", { name: "Try 3 others" });
    const [body] = mocks.previewConfig.mock.calls[0];
    expect(body.unit_ids).toEqual(["retained-user"]);
    expect(body.scope.preset).toBe("fixed");
    expect(body.scope).not.toHaveProperty("window");
    expect(body.scope).not.toHaveProperty("filters");
  });
});
