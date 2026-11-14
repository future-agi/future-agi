import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, userEvent, cleanup } from "src/utils/test-utils";
import TaskCreatePage from "../TaskCreatePage";
import { Box } from "@mui/material";
import TaskDetailPage from "../TaskDetailPage";
import { defaultSamplingPolicy } from "../components/samplingPolicyForm";
import { productionTheme } from "../components/__tests__/samplingContrastTestUtils";

const harness = vi.hoisted(() => ({
  draft: null,
  save: vi.fn(),
  task: null,
  get: vi.fn(),
  post: vi.fn(),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Admin" }),
}));
vi.mock("../hooks/useTaskDraft", () => ({
  useTaskDraft: () => ({
    initialValues: harness.draft,
    save: harness.save,
    clear: vi.fn(),
  }),
}));
vi.mock("src/utils/axios", async (original) => ({
  ...(await original()),
  default: { get: harness.get, post: harness.post },
  readQuery: harness.get,
}));
vi.mock("src/api/project/project-detail", () => ({
  useGetProjectDetails: () => ({ data: { source: "api" }, isSuccess: true }),
}));
vi.mock("src/sections/common/EvalsTasks/common", async (original) => ({
  ...(await original()),
  useGetTaskData: () => ({
    data: harness.task,
    isLoading: false,
    isError: false,
  }),
}));
vi.mock(
  "src/sections/projects/LLMTracing/TraceFilterPanel",
  async (original) => ({
    ...(await original()),
    useTraceFilterProperties: () => ({ data: [] }),
  }),
);
vi.mock("src/hooks/useDashboards", async (original) => ({
  ...(await original()),
  useDashboardFilterValues: () => ({ data: [] }),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/snackbar", () => ({ enqueueSnackbar: vi.fn() }));

const clients = [];
function show(mode, edit = false) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  clients.push(client);
  return render(
    <QueryClientProvider client={client}>
      <Box sx={{ bgcolor: "background.paper" }}>
        {edit ? <TaskDetailPage /> : <TaskCreatePage />}
      </Box>
    </QueryClientProvider>,
    { theme: productionTheme(mode) },
  );
}

beforeEach(() => {
  harness.draft = null;
  harness.task = null;
  harness.get.mockResolvedValue({
    data: {
      result: {
        projects: [{ id: "p1", name: "Alpha" }],
        config: [],
        table: [],
        metadata: { total_rows: 0, has_more: false, next_cursor: null },
      },
    },
  });
});
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
});
describe.each(["light", "dark"])(
  "real task pages with production %s theme",
  (mode) => {
    it.each([false, true])(
      "shows the saved selection beside the task heading with local dates and exact UTC (%s)",
      async (advanced) => {
        const window = {
          start: "2026-10-01T12:34:56.123456Z",
          end: "2026-10-02T12:34:56.654321Z",
        };
        const policy = {
          ...defaultSamplingPolicy(),
          sampling_unit: "attribute",
          unit_attribute: { source: "metadata", key: "account.id" },
          missing_id: "row",
          window,
        };
        harness.task = {
          id: "t1",
          name: "Saved selection task",
          project_id: "p1",
          project_name: "Alpha",
          run_type: "historical",
          row_type: "spans",
          status: "pending",
          sampling_policy: advanced ? policy : null,
          spans_limit: 275,
          sampling_rate: 50,
          evals_applied: [],
          filters_applied: { date_range: [window.start, window.end] },
        };
        show(mode, true);
        const summary = screen.getByLabelText("Saved data selection");
        const heading = screen.getByRole("heading", {
          name: "Saved selection task",
        });
        const header = heading.closest(".MuiBreadcrumbs-root").parentElement
          .parentElement;
        expect(header).toContainElement(summary);
        expect(summary).toBeVisible();
        expect(summary).toHaveTextContent("Historical data");
        expect(summary).toHaveTextContent(
          new Intl.DateTimeFormat(undefined, {
            year: "numeric",
            month: "short",
            day: "numeric",
            hour: "numeric",
            minute: "2-digit",
            timeZoneName: "short",
          }).format(new Date(window.start)),
        );
        const utc = summary.querySelector("details");
        expect(utc).not.toHaveAttribute("open");
        await userEvent.click(utc.querySelector("summary"));
        expect(utc).toHaveTextContent(window.start);
        expect(utc).toHaveTextContent(window.end);
        if (advanced) {
          expect(summary).toHaveTextContent(
            "Custom identity: Metadata: account.id",
          );
          expect(summary).toHaveTextContent(
            "Rows without an ID are sampled individually",
          );
        } else {
          expect(summary).toHaveTextContent(
            "About 50% of filter-matching spans",
          );
          expect(summary).toHaveTextContent("275 matching rows");
          await userEvent.click(
            screen.getByRole("button", { name: "10%", exact: true }),
          );
          expect(summary).toHaveTextContent("About 50%");
          expect(
            screen.getByLabelText("Data selection summary"),
          ).toHaveTextContent("About 10%");
        }
      },
    );

    it("keeps required create errors and keyboard association without a form-wide style override", async () => {
      show(mode);
      await userEvent.click(
        screen.getByRole("button", { name: "Create task" }),
      );
      expect(await screen.findByText("Name is required")).toBeVisible();
      expect(screen.getByText("Project is required")).toBeVisible();
      expect(
        screen.getByText("At least one evaluation is required"),
      ).toBeVisible();
      const project = screen.getByRole("combobox", { name: /Project/ });
      expect(project).toHaveAttribute("aria-invalid", "true");
      for (
        let step = 0;
        step < 40 && document.activeElement !== project;
        step++
      )
        await userEvent.tab();
      expect(project).toHaveFocus();
      await userEvent.keyboard("{Enter}");
      await screen.findByRole("option", { name: "Alpha" });
      await userEvent.keyboard("{ArrowDown}{Enter}");
      expect(project).toHaveValue("Alpha");
    });

    it("keeps custom processing-limit and broken evaluation validation", async () => {
      harness.draft = {
        name: "Scheduling task",
        project: "p1",
        evalsDetails: [
          { name: "Incomplete evaluation", mapping: { input: "locale" } },
        ],
      };
      show(mode);
      await userEvent.click(
        screen.getByRole("button", { name: "Custom limit", exact: true }),
      );
      const limit = screen.getByRole("spinbutton", {
        name: "Matching rows to process",
      });
      await userEvent.clear(limit);
      await userEvent.click(
        screen.getByRole("button", { name: "Create task" }),
      );
      expect(
        await screen.findByText("Max Spans is required for historical runs"),
      ).toBeVisible();
      expect(
        screen.getByText(
          "Remove the highlighted evaluation(s) and re-add them before continuing.",
        ),
      ).toBeVisible();
      for (let step = 0; step < 60 && document.activeElement !== limit; step++)
        await userEvent.tab();
      expect(limit).toHaveFocus();
    });

    it("blocks an invalid simple count and lets keyboard entry recover", async () => {
      harness.draft = {
        name: "Policy task",
        project: "p1",
        samplingPolicy: {
          ...defaultSamplingPolicy(),
          default: { target_n: 100 },
        },
      };
      show(mode);
      const input = screen.getByRole("spinbutton", {
        name: "How many conversations?",
      });
      await userEvent.clear(input);
      await userEvent.click(
        screen.getByRole("button", { name: "Create task" }),
      );
      expect(
        await screen.findByText("Enter the number of target units."),
      ).toBeVisible();
      expect(
        screen.getByRole("button", { name: "Preview sample" }),
      ).toBeDisabled();
      await userEvent.type(input, "-1");
      expect(
        screen.getByText("Use a whole number from 0 to 1,000,000."),
      ).toBeVisible();
      await userEvent.clear(input);
      await userEvent.type(input, "3");
      expect(input).toHaveAttribute("aria-invalid", "false");
      expect(
        screen.getByRole("button", { name: "Preview sample" }),
      ).toBeEnabled();
    });
  },
);
