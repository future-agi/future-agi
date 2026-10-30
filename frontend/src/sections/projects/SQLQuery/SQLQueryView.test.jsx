import React from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";
import { ThemeProvider, createTheme, hexToRgb } from "@mui/material/styles";
import userEvent from "@testing-library/user-event";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import { defaultProps } from "src/theme/overrides/default-props";
import { tabs } from "src/theme/overrides/components/tabs";
import SQLQueryView from "./SQLQueryView";
import { sqlApi } from "./api";
import { closeSnackbar, enqueueSnackbar } from "notistack";

const editorState = vi.hoisted(() => ({
  provider: null,
  model: null,
  instance: null,
  action: null,
  providerDispose: vi.fn(),
  actionDispose: vi.fn(),
}));
vi.mock("@monaco-editor/react", () => ({
  // The test double implements Monaco's existing editor/model API.
  // eslint-disable-next-line react/prop-types
  default: function MonacoMock({ value, onChange, onMount, options, height }) {
    editorState.height = height;
    editorState.options = options;
    const current = React.useRef(value);
    current.current = value;
    React.useEffect(() => {
      editorState.model = {
        getValue: () => current.current,
        getOffsetAt: ({ lineNumber, column }) =>
          current.current
            .split("\n")
            .slice(0, lineNumber - 1)
            .reduce((length, line) => length + line.length + 1, 0) +
          column -
          1,
        getPositionAt: (offset) => {
          const lines = current.current.slice(0, offset).split("\n");
          return { lineNumber: lines.length, column: lines.at(-1).length + 1 };
        },
      };
      editorState.instance = {
        getSelection: () => ({}),
        executeEdits: vi.fn(),
        focus: vi.fn(),
        pushUndoStop: vi.fn(),
        getModel: () => editorState.model,
        addAction: (action) => {
          editorState.action = action;
          return { dispose: editorState.actionDispose };
        },
      };
      onMount(editorState.instance, {
        KeyMod: { CtrlCmd: 2048 },
        KeyCode: { Enter: 3 },
        languages: {
          CompletionItemKind: { Class: 1, Field: 2, Keyword: 3, Function: 4 },
          registerCompletionItemProvider: (_, provider) => {
            editorState.provider = provider;
            return { dispose: editorState.providerDispose };
          },
        },
      });
      // Monaco mounts once; current values are read through refs.
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);
    return (
      <textarea
        aria-label="SQL query editor"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    );
  },
}));
vi.mock("src/hooks/useDashboards", () => ({
  useDashboardList: () => ({ data: [] }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ user: { id: "owner" } }),
}));
vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn(),
  closeSnackbar: vi.fn(),
}));
vi.mock("./api", () => ({
  errorMessage: (error) => error.message,
  sqlApi: {
    catalog: vi.fn(),
    saved: vi.fn(),
    save: vi.fn(),
    update: vi.fn(),
    run: vi.fn(),
    estimate: vi.fn(),
    runSaved: vi.fn(),
    result: vi.fn(),
    getSaved: vi.fn(),
    history: vi.fn(),
    cancel: vi.fn(),
    remove: vi.fn(),
    download: vi.fn(),
  },
}));
const schema = {
  version: 1,
  can_save: true,
  identity_semantics:
    "Raw storage IDs; merged identities are not consolidated.",
  datasets: [
    {
      name: "spans",
      description: "Project spans",
      available: true,
      unavailable_reason: "",
      columns: [{ name: "name", type: "String" }],
    },
    {
      name: "derived_metrics",
      description: "Committed metrics",
      available: false,
      unavailable_reason: "Apply the metrics schema.",
      columns: [{ name: "value", type: "Float64" }],
    },
  ],
};
// Import the exact production Tab/Tabs defaults and overrides: disableRipple
// removes MUI's default focus feedback. A bare createTheme() hides that defect.
const libraryTheme = (mode) => {
  const theme = createTheme({ palette: palette(mode), typography });
  const defaults = defaultProps(theme);
  const overrides = tabs(theme);
  theme.components = {
    MuiTab: { ...defaults.MuiTab, ...overrides.MuiTab },
    MuiTabs: { ...defaults.MuiTabs, ...overrides.MuiTabs },
  };
  return theme;
};
function ProjectNavigation() {
  const navigate = useNavigate();
  return (
    <button onClick={() => navigate("/dashboard/observe/other-project/query")}>
      Switch test project
    </button>
  );
}
const renderPage = (theme = createTheme()) =>
  render(
    <ThemeProvider theme={theme}>
      <MemoryRouter initialEntries={["/dashboard/observe/project/query"]}>
        <Routes>
          <Route
            path="/dashboard/observe/:observeId/query"
            element={<SQLQueryView />}
          />
        </Routes>
      </MemoryRouter>
    </ThemeProvider>,
  );
beforeEach(() => {
  vi.clearAllMocks();
  sqlApi.catalog.mockResolvedValue(schema);
  sqlApi.saved.mockResolvedValue({ items: [], has_more: false });
  sqlApi.estimate.mockResolvedValue({
    status: "unavailable",
    reason: "Estimate unavailable. Run is not affected.",
    sources: [],
    max_rows_to_read: 10000000,
    exceeds_scan_bound: null,
    elapsed_ms: 1,
  });
});

const resultJob = {
  id: "displayed-execution",
  sql: "SELECT model, count() AS spans, sum(total_tokens) AS tokens\nFROM spans\nGROUP BY model\nORDER BY spans DESC",
  state: "succeeded",
  result: {
    columns: [{ name: "count", type: "UInt64" }],
    rows: [[16], [12], [6], [2]],
    elapsed_ms: 3,
  },
};
const savedQuery = {
  id: "saved-provenance",
  name: "Saved provenance",
  sql: "SELECT count() FROM spans",
  window_hours: 24,
  chart: { type: "table" },
  visibility: "project",
  created_by_id: "owner",
};
const chooseWindow = (label) => {
  fireEvent.mouseDown(screen.getByRole("combobox", { name: /Time window/ }));
  fireEvent.click(screen.getByRole("option", { name: label, exact: true }));
};
const runQuery = () =>
  fireEvent.click(
    screen.getByRole("button", { name: "Run query", exact: true }),
  );
const openSaved = async () => {
  fireEvent.click(screen.getByRole("tab", { name: "Saved", exact: true }));
  fireEvent.click(await screen.findByText(savedQuery.name));
};

describe("SQL refinement regressions", () => {
  it("keeps edited result actions in one non-wrapping group beside compact provenance", async () => {
    sqlApi.run.mockResolvedValue(resultJob);
    renderPage(libraryTheme("light"));
    await screen.findByText(schema.identity_semantics);
    runQuery();
    await screen.findByRole("table");
    fireEvent.change(screen.getByLabelText("SQL query editor"), {
      target: { value: `${resultJob.sql}\nLIMIT 4` },
    });
    // Old markup lets History wrap independently in the shared action row.
    expect(
      getComputedStyle(
        screen.getByRole("button", { name: "History" }).parentElement,
      ).flexWrap,
    ).toBe("nowrap");
    fireEvent.change(screen.getByLabelText("SQL query editor"), {
      target: { value: resultJob.sql },
    });
    const toolbar = screen.getByRole("group", { name: "Results toolbar" });
    const summary = within(toolbar).getByRole("group", {
      name: "Result summary",
    });
    const actions = within(toolbar).getByRole("group", {
      name: "Result actions",
    });
    const freshHeight = getComputedStyle(summary).minHeight;
    expect(freshHeight).toBe("40px");
    expect(getComputedStyle(toolbar).flexWrap).toBe("nowrap");
    expect(getComputedStyle(actions).flexWrap).toBe("nowrap");
    expect(getComputedStyle(actions).flexShrink).toBe("0");
    expect(
      within(actions)
        .getAllByRole("button")
        .map((button) => button.textContent),
    ).toEqual(["CSV", "Parquet", "Add to dashboard", "History"]);
    fireEvent.change(screen.getByLabelText("SQL query editor"), {
      target: { value: `${resultJob.sql}\nLIMIT 4` },
    });
    const notice = within(summary).getByLabelText(
      "Previous query results · last 1 day — Run to update",
    );
    expect(notice).toHaveTextContent("Previous · last 1 day — Run to update");
    expect(notice).toHaveAttribute(
      "title",
      "Previous query results · last 1 day — Run to update",
    );
    expect(getComputedStyle(notice).whiteSpace).toBe("nowrap");
    expect(within(summary).getByRole("status")).toHaveTextContent("succeeded");
    expect(getComputedStyle(summary).minHeight).toBe(freshHeight);
    expect(
      within(actions).getByRole("button", { name: "History" }).parentElement,
    ).toBe(actions);
    expect(screen.getByRole("table")).toHaveTextContent("16");
  });

  it("labels the 24-hour window in the singular without changing any window values", async () => {
    sqlApi.run.mockResolvedValue(resultJob);
    renderPage();
    await screen.findByText(schema.identity_semantics);
    expect(
      screen.getByRole("combobox", { name: /Time window/ }),
    ).toHaveTextContent("Last 1 day");
    fireEvent.mouseDown(screen.getByRole("combobox", { name: /Time window/ }));
    expect(
      screen.getAllByRole("option").map((option) => option.textContent),
    ).toEqual(["Last hour", "Last 1 day", "Last 7 days", "Last 31 days"]);
    fireEvent.click(
      screen.getByRole("option", { name: "Last 1 day", exact: true }),
    );
    for (const [label, hours] of [
      ["Last hour", 1],
      ["Last 1 day", 24],
      ["Last 7 days", 168],
      ["Last 31 days", 744],
    ]) {
      chooseWindow(label);
      runQuery();
      await waitFor(() =>
        expect(
          screen.getByRole("button", { name: "Run query", exact: true }),
        ).not.toBeDisabled(),
      );
      const body = sqlApi.run.mock.calls.at(-1)[0];
      expect(Date.parse(body.end) - Date.parse(body.start)).toBe(
        hours * 3600000,
      );
    }
  });

  it.each([
    ["MacIntel", "⌘", "Meta"],
    ["Linux x86_64", "Ctrl", "Control"],
  ])(
    "merges shortcuts and the advisory estimate into one wrapping footer on %s",
    async (platform, shortcut, modifier) => {
      const platformMock = vi
        .spyOn(navigator, "platform", "get")
        .mockReturnValue(platform);
      try {
        renderPage();
        await screen.findByText(schema.identity_semantics);
        const estimate = await screen.findByText(
          "Estimate unavailable. Run is not affected.",
        );
        // The estimate used to have its own bordered row below the shortcuts.
        expect(getComputedStyle(estimate).borderTopWidth).not.toBe("1px");
        const footer = screen.getByRole("group", {
          name: "Query editor hints",
        });
        expect(estimate.parentElement).toBe(footer);
        const runHint = within(footer).getByText(`${shortcut}+Enter to run`);
        const suggestions = within(footer).getByText(
          "Ctrl+Space for suggestions",
        );
        expect(runHint.parentElement).toBe(suggestions.parentElement);
        expect(runHint.parentElement.parentElement).toBe(footer);
        expect(getComputedStyle(footer).display).toBe("flex");
        expect(getComputedStyle(footer).flexWrap).toBe("wrap");
        expect(getComputedStyle(estimate).marginLeft).toBe("auto");
        expect(getComputedStyle(estimate).textAlign).toBe("right");
        expect(getComputedStyle(estimate).borderTopWidth).not.toBe("1px");
        expect(estimate).toHaveAttribute("aria-live", "polite");
        const run = screen.getByRole("button", {
          name: "Run query",
          exact: true,
        });
        expect(run).not.toBeDisabled();
        expect(run).toHaveAttribute("aria-keyshortcuts", `${modifier}+Enter`);
      } finally {
        platformMock.mockRestore();
      }
    },
  );
});

describe("SQL review regressions", () => {
  it("labels the executed window, restores freshness and keeps downloads on that execution", async () => {
    sqlApi.run.mockResolvedValue(resultJob);
    sqlApi.download.mockRejectedValue(new Error("Download test boundary"));
    renderPage();
    await screen.findByText(schema.identity_semantics);
    const editor = screen.getByLabelText("SQL query editor");
    const original = editor.value;
    runQuery();
    await screen.findByRole("table", { name: "SQL query results" });
    expect(screen.queryByLabelText(/Previous query results/)).toBeNull();
    chooseWindow("Last hour");
    expect(screen.getByLabelText(/Previous query results/)).toHaveTextContent(
      /last 1 day.*Run to update/,
    );
    expect(screen.getByRole("table")).toHaveTextContent("16");
    expect(
      screen.getByRole("button", { name: "Run query", exact: true }),
    ).not.toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "CSV", exact: true }));
    await waitFor(() =>
      expect(sqlApi.download).toHaveBeenCalledWith(
        "project",
        resultJob.id,
        "csv",
      ),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Parquet", exact: true }),
    );
    await waitFor(() =>
      expect(sqlApi.download).toHaveBeenCalledWith(
        "project",
        resultJob.id,
        "parquet",
      ),
    );
    chooseWindow("Last 1 day");
    expect(screen.queryByLabelText(/Previous query results/)).toBeNull();
    fireEvent.change(editor, { target: { value: `${original}\nLIMIT 4` } });
    expect(screen.getByLabelText(/Previous query results/)).toBeVisible();
    fireEvent.change(editor, { target: { value: original } });
    expect(screen.queryByLabelText(/Previous query results/)).toBeNull();
    expect(sqlApi.run).toHaveBeenCalledTimes(1);
    chooseWindow("Last hour");
    sqlApi.run.mockResolvedValue({
      ...resultJob,
      id: "hour-execution",
      result: { ...resultJob.result, rows: [] },
    });
    runQuery();
    await screen.findByText(/No rows in this window/);
    expect(screen.queryByLabelText(/Previous query results/)).toBeNull();
    expect(sqlApi.run).toHaveBeenCalledTimes(2);
  });

  it("captures controls at submission, not when a queued execution finishes", async () => {
    let accept;
    sqlApi.run.mockReturnValue(
      new Promise((resolve) => {
        accept = resolve;
      }),
    );
    sqlApi.result.mockResolvedValue(resultJob);
    renderPage();
    await screen.findByText(schema.identity_semantics);
    runQuery();
    chooseWindow("Last hour");
    await act(async () =>
      accept({ ...resultJob, state: "queued", result: undefined }),
    );
    await screen.findByRole("table", { name: "SQL query results" });
    expect(screen.getByLabelText(/Previous query results/)).toHaveTextContent(
      /last 1 day.*Run to update/,
    );
    expect(sqlApi.run).toHaveBeenCalledTimes(1);
    chooseWindow("Last 1 day");
    expect(screen.queryByLabelText(/Previous query results/)).toBeNull();
  });

  it("keeps exact saved-versus-edited routing when the selected window changes", async () => {
    sqlApi.saved.mockResolvedValue({ items: [savedQuery], has_more: false });
    sqlApi.runSaved.mockResolvedValue({ ...resultJob, sql: savedQuery.sql });
    sqlApi.run.mockResolvedValue({
      ...resultJob,
      id: "edited-execution",
      sql: `${savedQuery.sql} LIMIT 1`,
    });
    renderPage();
    await screen.findByText(schema.identity_semantics);
    await openSaved();
    runQuery();
    await screen.findByRole("table");
    chooseWindow("Last hour");
    expect(screen.getByLabelText(/Previous query results/)).toHaveTextContent(
      /last 1 day/,
    );
    runQuery();
    await waitFor(() => expect(sqlApi.runSaved).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Run query", exact: true }),
      ).not.toBeDisabled(),
    );
    expect(screen.queryByLabelText(/Previous query results/)).toBeNull();
    const body = sqlApi.runSaved.mock.calls.at(-1)[0];
    expect(body.saved_query_id).toBe(savedQuery.id);
    expect(body.sql).toBeUndefined();
    expect(Date.parse(body.end) - Date.parse(body.start)).toBe(3600000);
    fireEvent.change(screen.getByLabelText("SQL query editor"), {
      target: { value: `${savedQuery.sql} LIMIT 1` },
    });
    expect(screen.getByLabelText(/Previous query results/)).toHaveTextContent(
      /last 1 hour/,
    );
    runQuery();
    await waitFor(() => expect(sqlApi.run).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Run query", exact: true }),
      ).not.toBeDisabled(),
    );
    expect(sqlApi.run.mock.calls[0][0]).toMatchObject({
      sql: `${savedQuery.sql} LIMIT 1`,
      project_id: "project",
    });
    expect(sqlApi.run.mock.calls[0][0].saved_query_id).toBeUndefined();
    fireEvent.change(screen.getByLabelText("SQL query editor"), {
      target: { value: savedQuery.sql },
    });
    runQuery();
    await waitFor(() => expect(sqlApi.runSaved).toHaveBeenCalledTimes(3));
  });

  it.each(["queued", "running"])(
    "shows a %s result placeholder rather than idle guidance",
    async (state) => {
      sqlApi.run.mockResolvedValue({ id: "pending-job", state });
      sqlApi.result.mockResolvedValue({ id: "pending-job", state });
      sqlApi.cancel.mockResolvedValue({
        id: "pending-job",
        state: "cancelled",
      });
      renderPage();
      await screen.findByText(schema.identity_semantics);
      expect(screen.getByText(/Explore fields in Catalog/)).toBeVisible();
      runQuery();
      await screen.findByText(`Query ${state}…`);
      expect(screen.getByText(`Query ${state}`, { exact: true })).toBeVisible();
      expect(screen.queryByText(/Run a query to inspect/)).toBeNull();
      expect(screen.queryByText(/Explore fields in Catalog/)).toBeNull();
      const cancel = screen.getByRole("button", { name: "Cancel query" });
      expect(cancel).not.toBeDisabled();
      fireEvent.click(cancel);
      await waitFor(() =>
        expect(sqlApi.cancel).toHaveBeenCalledWith("project", "pending-job"),
      );
      await screen.findByText("cancelled", { exact: true });
      expect(
        screen.getByText("Query cancelled", { exact: true }),
      ).toBeVisible();
      expect(screen.getByText("Run the query again when ready.")).toBeVisible();
      expect(
        screen.queryByText(/Waiting for query results|Results will appear/),
      ).toBeNull();
      expect(
        screen.getByRole("button", { name: "Run query", exact: true }),
      ).not.toBeDisabled();
      expect(screen.queryByText(/Explore fields in Catalog/)).toBeNull();
    },
  );

  it("offers a non-mutating History Close outside the scrolling list", async () => {
    sqlApi.saved.mockResolvedValue({ items: [savedQuery], has_more: false });
    sqlApi.history.mockResolvedValue({
      items: [
        {
          id: "old",
          sql: "SELECT 2",
          state: "succeeded",
          created_at: "2026-10-24T00:00:00Z",
        },
      ],
    });
    sqlApi.runSaved.mockResolvedValue({ ...resultJob, sql: savedQuery.sql });
    renderPage();
    await screen.findByText(schema.identity_semantics);
    await openSaved();
    fireEvent.click(
      screen.getByRole("button", { name: "History", exact: true }),
    );
    const dialog = await screen.findByRole("dialog");
    const close = within(dialog).getByRole("button", {
      name: "Close",
      exact: true,
    });
    expect(close).toBeVisible();
    expect(close.closest(".MuiDialogContent-root")).toBeNull();
    fireEvent.click(close);
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.getByLabelText("SQL query editor")).toHaveValue(
      savedQuery.sql,
    );
    runQuery();
    await waitFor(() =>
      expect(sqlApi.runSaved).toHaveBeenCalledWith(
        expect.objectContaining({ saved_query_id: savedQuery.id }),
        expect.any(Object),
      ),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "History", exact: true }),
    );
    const reopened = await screen.findByRole("dialog");
    fireEvent.click(within(reopened).getByText("SELECT 2"));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.getByLabelText("SQL query editor")).toHaveValue("SELECT 2");
  });

  it.each(["Escape", "backdrop"])(
    "keeps History %s dismissal non-mutating",
    async (dismissal) => {
      sqlApi.saved.mockResolvedValue({ items: [savedQuery], has_more: false });
      sqlApi.history.mockResolvedValue({ items: [] });
      sqlApi.runSaved.mockResolvedValue({ ...resultJob, sql: savedQuery.sql });
      renderPage();
      await screen.findByText(schema.identity_semantics);
      await openSaved();
      fireEvent.click(
        screen.getByRole("button", { name: "History", exact: true }),
      );
      const dialog = await screen.findByRole("dialog");
      if (dismissal === "Escape") {
        fireEvent.keyDown(dialog, { key: "Escape", code: "Escape" });
      } else {
        const backdropTarget = dialog.parentElement;
        fireEvent.mouseDown(backdropTarget);
        fireEvent.click(backdropTarget);
      }
      await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
      expect(screen.getByLabelText("SQL query editor")).toHaveValue(
        savedQuery.sql,
      );
      runQuery();
      await waitFor(() =>
        expect(sqlApi.runSaved).toHaveBeenCalledWith(
          expect.objectContaining({ saved_query_id: savedQuery.id }),
          expect.any(Object),
        ),
      );
    },
  );

  it("keeps the original project-reset and late-response protections", async () => {
    let accept;
    sqlApi.run.mockReturnValueOnce(
      new Promise((resolve) => {
        accept = resolve;
      }),
    );
    render(
      <ThemeProvider theme={createTheme()}>
        <MemoryRouter initialEntries={["/dashboard/observe/project/query"]}>
          <Routes>
            <Route
              path="/dashboard/observe/:observeId/query"
              element={<SQLQueryView />}
            />
          </Routes>
          <ProjectNavigation />
        </MemoryRouter>
      </ThemeProvider>,
    );
    await screen.findByText(schema.identity_semantics);
    runQuery();
    expect(screen.getByText("Query submitting", { exact: true })).toBeVisible();
    expect(screen.queryByText(/Explore fields in Catalog/)).toBeNull();
    fireEvent.click(
      screen.getByRole("button", { name: "Switch test project" }),
    );
    await waitFor(() =>
      expect(sqlApi.catalog).toHaveBeenCalledWith("other-project"),
    );
    await act(async () => accept(resultJob));
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.queryByLabelText(/Previous query results/)).toBeNull();
    expect(
      screen.getByRole("button", { name: "CSV", exact: true }),
    ).toBeDisabled();
    expect(screen.getByText(/Explore fields in Catalog/)).toBeVisible();
    sqlApi.run.mockResolvedValue({
      ...resultJob,
      id: "other-project-execution",
    });
    sqlApi.download.mockRejectedValue(new Error("Download test boundary"));
    runQuery();
    await screen.findByRole("table");
    expect(sqlApi.run.mock.calls.at(-1)[0].project_id).toBe("other-project");
    expect(screen.queryByLabelText(/Previous query results/)).toBeNull();
    chooseWindow("Last hour");
    expect(screen.getByLabelText(/Previous query results/)).toHaveTextContent(
      /last 1 day/,
    );
    fireEvent.click(screen.getByRole("button", { name: "CSV", exact: true }));
    await waitFor(() =>
      expect(sqlApi.download).toHaveBeenCalledWith(
        "other-project",
        "other-project-execution",
        "csv",
      ),
    );
  });

  it("sizes short SQL compactly and grows monotonically without remounting Monaco", async () => {
    renderPage();
    await screen.findByText(schema.identity_semantics);
    expect(Number.parseFloat(editorState.height)).toBe(116);
    const instance = editorState.instance;
    const editor = screen.getByLabelText("SQL query editor");
    const sizes = [];
    for (const lines of [1, 4, 8, 14, 50, 100]) {
      fireEvent.change(editor, {
        target: { value: Array(lines).fill("SELECT 1").join("\n") },
      });
      sizes.push(Number.parseFloat(editorState.height));
      expect(editorState.instance).toBe(instance);
    }
    expect(sizes).toEqual([116, 116, 208, 346, 346, 346]);
    fireEvent.change(editor, {
      target: { value: Array(100).fill("SELECT 12345").join("\n") },
    });
    expect(Number.parseFloat(editorState.height)).toBe(sizes.at(-1));
    expect(editorState.providerDispose).not.toHaveBeenCalled();
    expect(editorState.actionDispose).not.toHaveBeenCalled();
    expect(editorState.options.automaticLayout).toBe(true);
    expect(editorState.options.scrollBeyondLastLine).toBe(false);
    expect(editorState.options.fontSize).toBe(14);
    expect(editorState.action.keybindings).toEqual([2051]);
    expect(screen.getByText("Ctrl+Space for suggestions")).toBeVisible();
  });
});

describe("SQL workbench page", () => {
  it.each(["light", "dark"])(
    "keeps keyboard tab focus distinct from selection with production %s overrides",
    async (mode) => {
      const theme = libraryTheme(mode);
      expect(theme.components.MuiTab.defaultProps.disableRipple).toBe(true);
      const user = userEvent.setup();
      renderPage(theme);
      await screen.findByText(schema.identity_semantics);
      const catalog = screen.getByRole("tab", { name: "Catalog" });
      const examples = screen.getByRole("tab", { name: "Examples" });
      const saved = screen.getByRole("tab", { name: "Saved" });
      const observations = [];
      const observeFocus = (tab) => {
        expect(tab).toHaveFocus();
        expect(tab).toHaveClass("Mui-focusVisible");
        expect(tab.querySelector(".MuiTouchRipple-root")).toBeNull();
        const style = getComputedStyle(tab);
        observations.push({
          tab: tab.textContent,
          selected: tab.getAttribute("aria-selected"),
          outline: style.outline,
          outlineColor: style.outlineColor,
          outlineOffset: style.outlineOffset,
        });
      };
      await user.tab();
      observeFocus(catalog);
      await user.keyboard("{ArrowRight}");
      observeFocus(examples);
      expect(catalog).toHaveAttribute("aria-selected", "true");
      expect(examples).toHaveAttribute("aria-selected", "false");
      expect(catalog).not.toHaveClass("Mui-focusVisible");
      const selectedWithoutFocus = getComputedStyle(catalog).outline;
      await user.keyboard("{ArrowRight}");
      observeFocus(saved);
      expect(saved).toHaveAttribute("aria-selected", "false");
      await user.keyboard("{ArrowLeft}");
      observeFocus(examples);
      await user.keyboard("{End}");
      observeFocus(saved);
      await user.keyboard("{Home}");
      observeFocus(catalog);
      await user.keyboard("{ArrowRight}{Enter}");
      observeFocus(examples);
      expect(examples).toHaveAttribute("aria-selected", "true");
      expect(screen.getByRole("tabpanel", { name: "Examples" })).toBeVisible();
      await user.tab();
      expect(examples).not.toHaveFocus();
      expect(examples).not.toHaveClass("Mui-focusVisible");
      // Computed CSS plus real MUI key handling in jsdom; not a pixel/contrast
      // or real-browser accessibility assertion.
      for (const state of observations) {
        expect(state.outline).toBe("2px solid");
        expect(state.outlineColor).toBe(hexToRgb(theme.palette.text.primary));
        expect(state.outlineOffset).toBe("-3px");
        expect(state.outline).not.toBe(selectedWithoutFocus);
      }
    },
  );
  it("navigates Catalog / Examples / Saved with an associated searchable panel", async () => {
    sqlApi.saved.mockResolvedValue({
      items: [
        {
          id: "q",
          name: "Latency by model",
          sql: "SELECT count() FROM spans",
          visibility: "private",
        },
      ],
      has_more: false,
    });
    renderPage();
    await screen.findByText(schema.identity_semantics);
    fireEvent.click(
      screen.getByRole("button", {
        name: "Browse datasets, examples & saved queries",
      }),
    );
    expect(screen.getByRole("tabpanel", { name: "Catalog" })).toBeVisible();
    fireEvent.change(screen.getByLabelText("Search catalog"), {
      target: { value: "Float64" },
    });
    expect(
      screen.getByRole("button", { name: "Insert derived_metrics.value" }),
    ).toHaveAttribute("aria-disabled", "true");
    fireEvent.change(screen.getByLabelText("Search catalog"), {
      target: { value: "nothing" },
    });
    expect(screen.getByText("No matching datasets or columns")).toBeVisible();
    fireEvent.click(screen.getByRole("tab", { name: "Examples" }));
    expect(screen.getByRole("tabpanel", { name: "Examples" })).toBeVisible();
    expect(screen.getByLabelText("Search examples")).toHaveValue("");
    fireEvent.click(screen.getByRole("tab", { name: "Saved" }));
    expect(screen.getByRole("tabpanel", { name: "Saved" })).toBeVisible();
    fireEvent.change(screen.getByLabelText("Search saved queries"), {
      target: { value: "not found" },
    });
    expect(
      screen.getByText("No matching saved queries on this page."),
    ).toBeVisible();
    fireEvent.change(screen.getByLabelText("Search saved queries"), {
      target: { value: "LATENCY" },
    });
    expect(screen.getByText("Latency by model")).toBeVisible();
    fireEvent.click(screen.getByRole("tab", { name: "Catalog" }));
    fireEvent.click(screen.getByRole("button", { name: "Insert spans.name" }));
    expect(editorState.instance.executeEdits).toHaveBeenCalledWith("catalog", [
      { range: {}, text: "`name`" },
    ]);
    expect(editorState.instance.pushUndoStop).toHaveBeenCalledTimes(2);
    expect(editorState.instance.focus).toHaveBeenCalled();
  });
  it("registers a platform run action with current SQL and disposes providers/actions", async () => {
    sqlApi.run.mockResolvedValue({
      id: "hotkey",
      state: "succeeded",
      sql: "SELECT name FROM spans",
    });
    const { unmount } = renderPage();
    await screen.findByText(schema.identity_semantics);
    expect(editorState.action.keybindings).toEqual([2051]);
    expect(editorState.options.wordBasedSuggestions).toBe(false);
    expect(screen.getByText("Ctrl+Space for suggestions")).toBeVisible();
    fireEvent.change(screen.getByLabelText("SQL query editor"), {
      target: { value: "SELECT name FROM spans" },
    });
    await act(async () => editorState.action.run());
    await waitFor(() =>
      expect(sqlApi.run).toHaveBeenCalledWith(
        expect.objectContaining({ sql: "SELECT name FROM spans" }),
        expect.any(Object),
      ),
    );
    fireEvent.change(screen.getByLabelText("SQL query editor"), {
      target: { value: " " },
    });
    await act(async () => editorState.action.run());
    expect(sqlApi.run).toHaveBeenCalledTimes(1);
    expect(editorState.provider.provideCompletionItems({}, {})).toEqual({
      suggestions: [],
    });
    unmount();
    expect(editorState.providerDispose).toHaveBeenCalled();
    expect(editorState.actionDispose).toHaveBeenCalled();
  });
  it("shows explicit identity and missing-dataset boundaries", async () => {
    renderPage();
    await screen.findByText(schema.identity_semantics);
    expect(
      screen.getByText("derived_metrics (unavailable)"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", {
        name: "Insert derived_metrics.value",
        hidden: true,
      }),
    ).toHaveAttribute("aria-disabled", "true");
  });
  it("completion reads the catalog after editor mount, not its initial null closure", async () => {
    renderPage();
    await screen.findByText(schema.identity_semantics);
    fireEvent.change(screen.getByLabelText("SQL query editor"), {
      target: { value: "SELECT *\nFROM " },
    });
    const result = editorState.provider.provideCompletionItems(
      editorState.model,
      { lineNumber: 2, column: 6 },
    );
    expect(result.suggestions.map((value) => value.label)).toEqual(["spans"]);
    expect(result.suggestions[0]).toMatchObject({
      insertText: "`spans`",
      range: {
        startLineNumber: 2,
        endLineNumber: 2,
        startColumn: 6,
        endColumn: 6,
      },
    });
    fireEvent.change(screen.getByLabelText("SQL query editor"), {
      target: { value: "SELECT * FROM spans WHERE " },
    });
    const fields = editorState.provider.provideCompletionItems(
      editorState.model,
      { lineNumber: 1, column: 27 },
    );
    expect(
      fields.suggestions
        .filter((item) => item.kind === 2)
        .map((item) => item.label),
    ).toEqual(["name"]);
  });
  it.each([
    [
      "SELECT s.na|me\nFROM spans s",
      "name",
      "name",
      "SELECT s.`name`\nFROM spans s",
    ],
    [
      "SELECT s.`na|me`\nFROM spans s",
      "name",
      "`name`",
      "SELECT s.`name`\nFROM spans s",
    ],
    [
      'SELECT s."na|me"\nFROM spans s',
      "name",
      '"name"',
      "SELECT s.`name`\nFROM spans s",
    ],
    ["SELECT *\nFROM sp|ans", "spans", "spans", "SELECT *\nFROM `spans`"],
    ["SELECT *\nFROM `sp|`", "spans", "`spans`", "SELECT *\nFROM `spans`"],
    ['SELECT *\nFROM "sp|"', "spans", '"spans"', "SELECT *\nFROM `spans`"],
  ])(
    "forwards filterText and full-token ranges to Monaco for %s",
    async (marked, label, filterText, expected) => {
      renderPage();
      await screen.findByText(schema.identity_semantics);
      const sql = marked.replace("|", "");
      fireEvent.change(screen.getByLabelText("SQL query editor"), {
        target: { value: sql },
      });
      const position = editorState.model.getPositionAt(marked.indexOf("|"));
      const result = editorState.provider.provideCompletionItems(
        editorState.model,
        position,
      );
      expect(result.suggestions).toHaveLength(1);
      const item = result.suggestions[0];
      expect(item).toMatchObject({ label, filterText });
      const from = editorState.model.getOffsetAt({
        lineNumber: item.range.startLineNumber,
        column: item.range.startColumn,
      });
      const to = editorState.model.getOffsetAt({
        lineNumber: item.range.endLineNumber,
        column: item.range.endColumn,
      });
      expect(from).toBeLessThanOrEqual(marked.indexOf("|"));
      expect(to).toBeGreaterThanOrEqual(marked.indexOf("|"));
      expect(sql.slice(0, from) + item.insertText + sql.slice(to)).toBe(
        expected,
      );
      // This double checks provider wiring, not Monaco filtering or interaction.
      // The repair contract suite separately executes the actual 0.43 filter.
    },
  );
  it("keeps viewer execution available while disabling saved-query writes", async () => {
    sqlApi.catalog.mockResolvedValue({ ...schema, can_save: false });
    renderPage();
    await screen.findByText(schema.identity_semantics);
    expect(
      screen.getByRole("button", { name: "Run query", exact: true }),
    ).not.toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Save query", exact: true }),
    ).toBeDisabled();
  });
  it("saves a new private definition through its project-scoped API", async () => {
    sqlApi.save.mockResolvedValue({
      id: "saved",
      project_id: "project",
      created_by_id: "owner",
      name: "My query",
      visibility: "private",
      sql: "SELECT name FROM spans",
      chart: {},
      window_hours: 24,
    });
    renderPage();
    await screen.findByText(schema.identity_semantics);
    fireEvent.change(
      screen.getByRole("textbox", { name: "SQL query editor" }),
      { target: { value: "SELECT name FROM spans" } },
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Save query", exact: true }),
    );
    fireEvent.change(screen.getByLabelText("Query name"), {
      target: { value: "My query" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save", exact: true }));
    await waitFor(() =>
      expect(sqlApi.save).toHaveBeenCalledWith(
        expect.objectContaining({
          project_id: "project",
          sql: "SELECT name FROM spans",
          name: "My query",
          visibility: "private",
          chart: { type: "table" },
        }),
      ),
    );
    expect(sqlApi.update).not.toHaveBeenCalled();
  });
  it("shows an over-limit estimate as a warning but never disables Run", async () => {
    sqlApi.estimate.mockResolvedValue({
      status: "ok",
      reason: "",
      sources: [
        {
          dataset: "spans",
          estimated: true,
          parts: 4,
          marks: 1300,
          max_rows: 10649600,
          reason: "",
        },
      ],
      max_rows_to_read: 10000000,
      exceeds_scan_bound: true,
      elapsed_ms: 3,
    });
    sqlApi.run.mockResolvedValue({ id: "job", state: "queued", sql: "" });
    sqlApi.result.mockResolvedValue({ id: "job", state: "queued", sql: "" });
    renderPage();
    await screen.findByText(schema.identity_semantics);
    const line = await screen.findByTestId("sql-estimate", undefined, {
      timeout: 3000,
    });
    await waitFor(() =>
      expect(line).toHaveTextContent(
        "likely over the 10,000,000-row scan limit",
      ),
    );
    expect(line).toHaveAttribute("data-tone", "warning");
    expect(sqlApi.estimate).toHaveBeenCalledWith(
      expect.objectContaining({
        project_id: "project",
        sql: expect.any(String),
      }),
      expect.any(AbortSignal),
    );
    const runButton = screen.getByRole("button", {
      name: "Run query",
      exact: true,
    });
    expect(runButton).not.toBeDisabled();
    fireEvent.click(runButton);
    await waitFor(() => expect(sqlApi.run).toHaveBeenCalledTimes(1));
    // The estimate opens no dialog (the E2E flow asserts none is open).
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
  it.each([false, true])(
    "runs an opened saved query fresh (edited=%s)",
    async (edited) => {
      const saved = {
        id: "saved-1",
        name: "Saved count",
        sql: "SELECT count() FROM spans",
        window_hours: 24,
        chart: { type: "table" },
        visibility: "private",
        created_by_id: "owner",
      };
      sqlApi.saved.mockResolvedValue({ items: [saved], has_more: false });
      sqlApi.runSaved.mockResolvedValue({
        id: "fresh-saved",
        state: "succeeded",
        sql: saved.sql,
      });
      sqlApi.run.mockResolvedValue({
        id: "fresh-edit",
        state: "succeeded",
        sql: "SELECT count() FROM traces",
      });
      renderPage();
      await screen.findByText(schema.identity_semantics);
      fireEvent.click(screen.getByRole("tab", { name: "Saved", exact: true }));
      fireEvent.click(await screen.findByText("Saved count"));
      if (edited) {
        fireEvent.change(screen.getByLabelText("SQL query editor"), {
          target: { value: "SELECT count() FROM traces" },
        });
      }
      for (let count = 1; count <= 2; count += 1) {
        fireEvent.click(
          screen.getByRole("button", { name: "Run query", exact: true }),
        );
        const route = edited ? sqlApi.run : sqlApi.runSaved;
        await waitFor(() => expect(route).toHaveBeenCalledTimes(count));
        await waitFor(() =>
          expect(
            screen.getByRole("button", { name: "Run query", exact: true }),
          ).not.toBeDisabled(),
        );
      }
      if (edited) {
        expect(sqlApi.runSaved).not.toHaveBeenCalled();
        expect(sqlApi.run).toHaveBeenLastCalledWith(
          expect.objectContaining({
            project_id: "project",
            sql: "SELECT count() FROM traces",
          }),
          expect.any(Object),
        );
      } else {
        expect(sqlApi.run).not.toHaveBeenCalled();
        expect(sqlApi.runSaved).toHaveBeenLastCalledWith(
          expect.objectContaining({
            project_id: "project",
            saved_query_id: "saved-1",
            start: expect.any(String),
            end: expect.any(String),
          }),
          expect.any(Object),
        );
      }
    },
  );
  it("inserts a vetted example in one click and offers Undo", async () => {
    sqlApi.catalog.mockResolvedValue({
      ...schema,
      examples: [
        {
          id: "cost-tokens-by-model",
          title: "Cost and tokens by model",
          description: "Spans, total tokens and total cost per model.",
          sql: "SELECT model, count() AS span_count\nFROM spans\nGROUP BY model",
          datasets: ["spans"],
        },
      ],
    });
    renderPage();
    const editor = await screen.findByLabelText("SQL query editor");
    const typed = editor.value;
    await screen.findByText(schema.identity_semantics);
    fireEvent.click(screen.getByRole("tab", { name: "Examples", exact: true }));
    fireEvent.click(
      await screen.findByRole("button", {
        name: "Use example: Cost and tokens by model",
      }),
    );
    expect(editor.value).toBe(
      "SELECT model, count() AS span_count\nFROM spans\nGROUP BY model",
    );
    expect(enqueueSnackbar).toHaveBeenCalledWith(
      "Inserted example: Cost and tokens by model",
      expect.objectContaining({ action: expect.any(Function) }),
    );
    // Run stays the same single control the E2E flow clicks.
    expect(
      screen.getAllByRole("button", { name: "Run query", exact: true }),
    ).toHaveLength(1);
    const { action } = enqueueSnackbar.mock.calls.at(-1)[1];
    render(action("snack-1"));
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect(editor.value).toBe(typed);
    expect(closeSnackbar).toHaveBeenCalledWith("snack-1");
  });
});
