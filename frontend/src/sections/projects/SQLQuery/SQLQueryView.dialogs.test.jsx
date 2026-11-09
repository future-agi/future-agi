import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import SQLQueryView from "./SQLQueryView";
import { sqlApi } from "./api";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";

vi.mock("@monaco-editor/react", () => ({ default: ({ value, onChange }) => <textarea aria-label="SQL query editor" value={value} onChange={(e) => onChange(e.target.value)} /> }));
vi.mock("src/auth/hooks", () => ({ useAuthContext: () => ({ user: { id: "owner" } }) }));
vi.mock("src/hooks/useDashboards", () => ({ useDashboardList: () => ({ data: [{ id: "board", name: "Fixture dashboard" }] }) }));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn(), closeSnackbar: vi.fn() }));
vi.mock("./api", () => ({
  errorMessage: (error) => error.message,
  sqlApi: Object.fromEntries(["catalog", "saved", "save", "update", "estimate", "getSaved", "history", "remove", "addWidget"].map((name) => [name, vi.fn()])),
}));
vi.mock("./useSQLExecution", () => ({ default: () => ({ job: null, busy: false, error: "", run: vi.fn(), cancel: vi.fn() }) }));
const saved = { id: "saved", name: "Fixture saved query", sql: "SELECT count() FROM spans", window_hours: 24, visibility: "project", created_by_id: "owner", chart: { type: "table" } };
const deferred = () => { let resolve; const promise = new Promise((r) => { resolve = r; }); return { promise, resolve }; };
function mount(mode = "light") {
  return render(
    <ThemeProvider theme={createTheme({ palette: palette(mode), typography })}>
      <MemoryRouter initialEntries={["/query/project"]}>
        <Routes><Route path="/query/:observeId" element={<SQLQueryView />} /></Routes>
      </MemoryRouter>
    </ThemeProvider>,
  );
}
async function openSave() {
  const trigger = await screen.findByRole("button", { name: "Save query", exact: true });
  await waitFor(() => expect(trigger).not.toBeDisabled());
  trigger.focus(); fireEvent.click(trigger);
  return trigger;
}
async function openSaved() {
  fireEvent.click(screen.getByRole("tab", { name: "Saved", exact: true }));
  fireEvent.click(await screen.findByText(saved.name));
}
beforeEach(() => {
  vi.clearAllMocks();
  sqlApi.catalog.mockResolvedValue({ version: 1, datasets: [], can_save: true });
  sqlApi.saved.mockResolvedValue({ items: [saved], has_more: false });
  sqlApi.estimate.mockResolvedValue({ status: "unavailable", reason: "Fixture estimate unavailable", sources: [] });
  sqlApi.history.mockResolvedValue({ items: [{ id: "history-1", sql: "SELECT 42", state: "succeeded", created_at: "2026-01-01T00:00:00Z" }] });
});

describe("SQL shared dialog interactions", () => {
  it.each(["light", "dark"])("autofocuses, traps focus, closes with Escape and returns focus in %s", async (mode) => {
    mount(mode);
    const trigger = await openSave();
    const dialog = screen.getByRole("dialog", { name: /^Save query/ });
    await waitFor(() => expect(screen.getByLabelText("Query name")).toHaveFocus());
    trigger.focus();
    // MUI redirects forced outside focus to its container before Tab enters the paper.
    expect(dialog.closest(".MuiDialog-container")).toContainElement(document.activeElement);
    await userEvent.tab();
    await waitFor(() => expect(dialog).toContainElement(document.activeElement));
    fireEvent.keyDown(document.activeElement, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(trigger).toHaveFocus();
  });
  it.each([
    { kind: "save", title: /^Save query/ },
    { kind: "dashboard", title: /^Add to dashboard/ },
    { kind: "delete", title: /^Delete saved query\?/ },
  ])("names the $kind close control without submitting", async ({ kind, title }) => {
    const user = userEvent.setup();
    mount();
    let trigger;
    if (kind === "save") {
      trigger = await openSave();
    } else {
      await openSaved();
      trigger = screen.getByRole("button", {
        name: kind === "dashboard" ? "Add to dashboard" : "Delete saved query",
        exact: true,
      });
      trigger.focus();
      await user.click(trigger);
    }
    const dialog = await screen.findByRole("dialog", { name: title });
    const closeButton = within(dialog).getByRole("button", { name: "Close", exact: true });
    expect(closeButton).toHaveAccessibleName("Close");
    await user.click(closeButton);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(trigger).toHaveFocus();
    expect(sqlApi.save).not.toHaveBeenCalled();
    expect(sqlApi.addWidget).not.toHaveBeenCalled();
    expect(sqlApi.remove).not.toHaveBeenCalled();
  });
  it("dismisses save through the actual backdrop without saving", async () => {
    mount(); await openSave();
    await userEvent.click(document.querySelector(".MuiDialog-container"));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(sqlApi.save).not.toHaveBeenCalled();
  });
  it("preserves disabled-empty and pending-save dismissal guards", async () => {
    const pending = deferred(); sqlApi.save.mockReturnValue(pending.promise);
    mount(); await openSave();
    const save = screen.getByRole("button", { name: "Save", exact: true });
    expect(save).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Query name"), { target: { value: "Fixture new query" } });
    fireEvent.click(save);
    await waitFor(() => expect(sqlApi.save).toHaveBeenCalledTimes(1));
    expect(save).toBeDisabled();
    fireEvent.keyDown(screen.getByLabelText("Query name"), { key: "Escape" });
    await userEvent.click(document.querySelector(".MuiDialog-container"));
    expect(screen.getByRole("dialog", { name: /^Save query/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel", exact: true })).toBeDisabled();
    const closeButton = screen.getByRole("button", { name: "Close", exact: true });
    expect(closeButton).toBeDisabled();
    // A disabled MUI control rejects user-event pointer targeting; native click is a no-op.
    closeButton.click();
    await act(async () => pending.resolve({ ...saved, name: "Fixture new query" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(sqlApi.save).toHaveBeenCalledWith(expect.objectContaining({ name: "Fixture new query", visibility: "private", project_id: "project" }));
  });
  it("selects a dashboard and submits once while preventing pending dismissal", async () => {
    const pending = deferred(); sqlApi.addWidget.mockReturnValue(pending.promise);
    mount(); await openSaved();
    fireEvent.click(screen.getByRole("button", { name: "Add to dashboard" }));
    const dialog = screen.getByRole("dialog", { name: /^Add to dashboard/ });
    expect(within(dialog).getByRole("button", { name: "Add query" })).toBeDisabled();
    fireEvent.mouseDown(screen.getByRole("combobox", { name: "Dashboard" }));
    fireEvent.click(await screen.findByRole("option", { name: "Fixture dashboard" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Add query" }));
    await waitFor(() => expect(sqlApi.addWidget).toHaveBeenCalledWith("project", "saved", "board"));
    fireEvent.keyDown(dialog, { key: "Escape" });
    expect(within(dialog).getByRole("button", { name: "Cancel", exact: true })).toBeDisabled();
    const closeButton = within(dialog).getByRole("button", { name: "Close", exact: true });
    expect(closeButton).toBeDisabled();
    // A disabled MUI control rejects user-event pointer targeting; native click is a no-op.
    closeButton.click();
    expect(dialog).toBeInTheDocument();
    await act(async () => pending.resolve({}));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });
  it("uses confirmation cancel without deleting and keeps deletion guarded while pending", async () => {
    const pending = deferred(); sqlApi.remove.mockReturnValue(pending.promise);
    mount(); await openSaved();
    const trigger = screen.getByRole("button", { name: "Delete saved query" });
    fireEvent.click(trigger);
    fireEvent.click(screen.getByRole("button", { name: "Keep query", exact: true }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(sqlApi.remove).not.toHaveBeenCalled();
    fireEvent.click(trigger);
    const dialog = screen.getByRole("dialog", { name: /^Delete saved query\?/ });
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete query" }));
    await waitFor(() => expect(sqlApi.remove).toHaveBeenCalledWith("project", "saved"));
    expect(within(dialog).getByRole("button", { name: "Keep query", exact: true })).toBeDisabled();
    const closeButton = within(dialog).getByRole("button", { name: "Close", exact: true });
    expect(closeButton).toBeDisabled();
    // A disabled MUI control rejects user-event pointer targeting; native click is a no-op.
    closeButton.click();
    fireEvent.keyDown(dialog, { key: "Escape" });
    await userEvent.click(document.querySelector(".MuiDialog-container"));
    expect(dialog).toBeInTheDocument();
    await act(async () => pending.resolve({}));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });
  it("closes history with its labeled cross and still applies history SQL", async () => {
    mount();
    const trigger = screen.getByRole("button", { name: "History" });
    trigger.focus(); fireEvent.click(trigger);
    let dialog = await screen.findByRole("dialog", { name: "Recent queries", exact: true });
    fireEvent.click(within(dialog).getByRole("button", { name: "Close", exact: true }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(trigger).toHaveFocus();
    fireEvent.click(trigger);
    dialog = await screen.findByRole("dialog", { name: "Recent queries", exact: true });
    fireEvent.click(within(dialog).getByText("SELECT 42"));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.getByLabelText("SQL query editor")).toHaveValue("SELECT 42");
  });
});
