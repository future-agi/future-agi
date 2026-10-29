import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SnackbarProvider } from "notistack";
import {
  cleanup,
  render,
  screen,
  userEvent,
  waitFor,
} from "src/utils/test-utils";
import ThemeProvider from "src/theme";
import axios, { endpoints } from "src/utils/axios";
import DerivedMetricsView from "../DerivedMetricsView";
import { initialForm } from "../metricForm";

const context = vi.hoisted(() => ({ mode: "light" }));
vi.mock("src/components/settings", () => ({
  useSettingsContext: () => ({
    themeMode: context.mode,
    themeDirection: "ltr",
    themeColorPresets: "default",
    themeContrast: "default",
  }),
}));
vi.mock("src/hooks/use-system-theme-mode", () => ({
  useSystemThemeMode: () => context.mode,
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({
    currentOrganizationId: "11111111-1111-4111-8111-111111111111",
  }),
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({
    currentWorkspaceId: "22222222-2222-4222-8222-222222222222",
    isReady: true,
  }),
}));
vi.mock("src/sections/dashboards/hooks/useCanEditDashboard", () => ({
  default: () => ({ isReadOnly: false }),
}));
vi.mock("src/utils/Mixpanel", () => ({ resetUser: vi.fn() }));
vi.mock("src/api/project/observe-project-list", () => ({
  readObserveProjectPage: async () => ({
    rows: [
      { id: "33333333-3333-4333-8333-333333333333", name: "Synthetic project" },
    ],
    totalPages: 1,
  }),
}));
const names = [
  "Metrics window 082042Metrics window 082031 with a long saved definition name",
  "UnbrokenMetricName".repeat(8),
];
const metrics = names.map((name, index) => ({
  id: `${index + 4}4444444-4444-4444-8444-444444444444`,
  project_id: "33333333-3333-4333-8333-333333333333",
  name,
  description: "Saved definition",
  current_version: index + 1,
  definition: {
    ...initialForm(),
    version: index + 1,
    definition_hash: "a".repeat(64),
    created_at: "2026-10-20T00:00:00Z",
  },
}));
let client;
let oldAdapter;
let finishValidation;
let adapter;
beforeEach(() => {
  localStorage.clear();
  window.history.replaceState({ idx: 0 }, "", "/dashboard/evaluations/metrics");
  oldAdapter = axios.defaults.adapter;
  finishValidation = null;
  adapter = vi.fn(async (config) => {
    let data;
    if (config.url === endpoints.derivedMetrics.list)
      data = { results: metrics, next_cursor: null };
    else if (
      metrics.some(
        (metric) => config.url === endpoints.derivedMetrics.runs(metric.id),
      )
    )
      data = { results: [], next_cursor: null };
    else if (config.url === endpoints.derivedMetrics.validate) {
      await new Promise((resolve) => {
        finishValidation = resolve;
      });
      data = {
        valid: true,
        kind: "ratio",
        required_inputs: [],
        semantics: "Valid formula",
      };
    } else
      throw new Error(
        `Unexpected controlled request: ${config.method} ${config.url}`,
      );
    return { data, status: 200, statusText: "OK", headers: {}, config };
  });
  axios.defaults.adapter = adapter;
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
});
afterEach(() => {
  finishValidation?.();
  cleanup();
  client.clear();
  axios.defaults.adapter = oldAdapter;
  vi.restoreAllMocks();
  localStorage.clear();
});
function mount(mode) {
  context.mode = mode;
  render(
    <ThemeProvider>
      <QueryClientProvider client={client}>
        <SnackbarProvider>
          <DerivedMetricsView />
        </SnackbarProvider>
      </QueryClientProvider>
    </ThemeProvider>,
  );
}
describe("metric table and detail with the real production theme", () => {
  it.each(["light", "dark"])(
    "keeps full names accessible in compact rows, supports native keyboard activation and blocks busy editor actions (%s; jsdom CSS contract)",
    async (mode) => {
      mount(mode);
      const user = userEvent.setup();
      for (let index = 0; index < names.length; index += 1) {
        const name = await screen.findByRole("button", { name: names[index] });
        expect(name).toHaveStyle({
          height: "auto",
          minWidth: "0",
          whiteSpace: "nowrap",
          overflow: "hidden",
        });
        expect(name).toHaveAttribute("title", names[index]);
        expect(name.firstElementChild).toHaveStyle({
          textOverflow: "ellipsis",
        });
        expect(
          screen.getByRole("cell", { name: `v${index + 1}` }),
        ).toBeInTheDocument();
      }
      const first = screen.getByRole("button", { name: names[0] });
      first.focus();
      await user.keyboard("{Enter}");
      expect(
        await screen.findByRole("heading", { name: names[0] }),
      ).toBeInTheDocument();
      expect(window.location.pathname).toBe(
        `/dashboard/evaluations/metrics/${metrics[0].id}`,
      );
      await user.click(screen.getByRole("button", { name: "Metrics" }));
      const second = await screen.findByRole("button", { name: names[1] });
      second.focus();
      await user.keyboard(" ");
      expect(
        await screen.findByRole("heading", { name: names[1] }),
      ).toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: "Edit definition" }));
      await user.click(screen.getByRole("button", { name: "Continue" }));
      await user.click(screen.getByRole("button", { name: "Continue" }));
      await user.click(
        screen.getByRole("button", { name: "Validate formula" }),
      );
      await waitFor(() => expect(finishValidation).toBeTypeOf("function"));
      const save = screen.getByRole("button", { name: "Save new version" });
      expect(save).toBeDisabled();
      save.focus();
      await user.keyboard("{Enter}");
      expect(
        adapter.mock.calls.filter(([config]) => config.method !== "get"),
      ).toHaveLength(1);
      finishValidation();
      await waitFor(() => expect(save).toBeEnabled());
    },
  );
});
