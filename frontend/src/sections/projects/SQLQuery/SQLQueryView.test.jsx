import React from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import SQLQueryView from "./SQLQueryView";
import { sqlApi } from "./api";

const editorState = vi.hoisted(() => ({ provider: null }));
vi.mock("@monaco-editor/react", () => ({
  default: ({ value, onChange, onMount }) => {
    React.useEffect(() => {
      onMount(
        { getSelection: () => ({}), executeEdits: vi.fn(), focus: vi.fn() },
        {
          languages: {
            CompletionItemKind: { Class: 1, Field: 2 },
            registerCompletionItemProvider: (_, provider) => {
              editorState.provider = provider;
              return { dispose: vi.fn() };
            },
          },
        },
      );
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
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("./api", () => ({
  errorMessage: (error) => error.message,
  sqlApi: {
    catalog: vi.fn(),
    saved: vi.fn(),
    save: vi.fn(),
    update: vi.fn(),
    run: vi.fn(),
    runSaved: vi.fn(),
    result: vi.fn(),
    getSaved: vi.fn(),
    history: vi.fn(),
    cancel: vi.fn(),
    remove: vi.fn(),
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
const renderPage = () =>
  render(
    <ThemeProvider theme={createTheme()}>
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
});

describe("SQL workbench page", () => {
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
    const result = editorState.provider.provideCompletionItems(
      { getWordUntilPosition: () => ({ startColumn: 1, endColumn: 1 }) },
      { lineNumber: 1 },
    );
    expect(result.suggestions.map((value) => value.label)).toEqual([
      "spans",
      "name",
    ]);
  });
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
});
