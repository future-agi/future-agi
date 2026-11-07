import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import PublishDialog from "./PublishDialog";

const api = vi.hoisted(() => ({ publish: vi.fn(), checks: vi.fn(), compare: vi.fn(), navigate: vi.fn(), snackbar: vi.fn() }));
vi.mock("src/api/skills", () => ({
  usePublishSkillDraft: () => ({ mutateAsync: api.publish, isPending: false }),
  useRunSkillChecks: () => ({ mutateAsync: api.checks }),
  useSkillCompare: (...args) => api.compare(...args),
  getSkillErrorCode: (error) => error.code,
}));
vi.mock("react-router-dom", () => ({ useNavigate: () => api.navigate }));
vi.mock("notistack", () => ({ enqueueSnackbar: api.snackbar }));
vi.mock("src/sections/skills/components/TextDiff", () => ({ default: ({ fromText, toText }) => <div data-testid="diff">{fromText} → {toText}</div> }));

const skill = { id: "skill-id", name: "sample-skill" };
const draft = { baseVersion: null, files: [{ path: "SKILL.md", content: "Instructions" }], note: "", revision: 1 };
const checks = (errors = 0, warnings = 0) => ({
  summary: { errors, warnings, infos: 0 },
  items: [...(errors ? [{ code: "name_missing", severity: "error", message: "Name is required.", path: "SKILL.md" }] : []), ...(warnings ? [{ code: "description_short", severity: "warning", message: "Description is short.", path: "SKILL.md" }] : [])],
});
const renderDialog = (overrides = {}) => render(<PublishDialog open onClose={vi.fn()} skill={skill} draft={draft} {...overrides} />);

beforeEach(() => {
  vi.clearAllMocks();
  api.checks.mockResolvedValue(checks());
  api.publish.mockResolvedValue({ version: { number: 3 }, unchanged: false });
  api.compare.mockReturnValue({ data: null, isPending: false, isError: false });
});
afterEach(() => cleanup());

describe("publish review", () => {
  it("keeps Publish disabled while checks are pending and when errors exist", async () => {
    let finish;
    api.checks.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    renderDialog();
    expect(screen.getByRole("button", { name: "Publish", exact: true })).toBeDisabled();
    await waitFor(() => expect(api.checks).toHaveBeenCalled());
    await act(async () => finish(checks(1)));
    expect(screen.getByText("Fix the errors in the editor before publishing.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Publish", exact: true })).toBeDisabled();
    expect(api.publish).not.toHaveBeenCalled();
  });

  it("requires acknowledgment of warnings and sends the note and selected labels", async () => {
    api.checks.mockResolvedValue(checks(0, 1));
    const close = vi.fn();
    renderDialog({ onClose: close });
    await screen.findByText("Description is short.");
    const button = screen.getByRole("button", { name: "Publish", exact: true });
    expect(button).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Version note"), { target: { value: "Reviewed policy" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "production", exact: true }));
    fireEvent.click(screen.getByRole("checkbox", { name: /I reviewed the warnings/ }));
    expect(button).toBeEnabled();
    fireEvent.click(button);
    await waitFor(() => expect(api.publish).toHaveBeenCalledWith({ note: "Reviewed policy", labels: ["production"], acknowledgeWarnings: true, force: false }));
    expect(api.navigate).toHaveBeenCalledWith("/dashboard/skills/skill-id");
    expect(api.snackbar).toHaveBeenCalledWith("Published v3.", { variant: "success" });
    expect(close).toHaveBeenCalledOnce();
  });

  it("explains stale_base and only forces after Publish anyway is clicked", async () => {
    api.publish.mockRejectedValueOnce({ code: "stale_base", detail: { base_version: 1, latest_version: 2 } });
    renderDialog();
    const button = screen.getByRole("button", { name: "Publish", exact: true });
    await waitFor(() => expect(button).toBeEnabled());
    fireEvent.click(button);
    const force = await screen.findByRole("button", { name: "Publish anyway" });
    expect(screen.getByText(/Publishing anyway creates a version from your draft/)).toBeInTheDocument();
    expect(api.publish).toHaveBeenCalledExactlyOnceWith({ note: "", labels: [], acknowledgeWarnings: false, force: false });
    fireEvent.click(force);
    await waitFor(() => expect(api.publish).toHaveBeenNthCalledWith(2, { note: "", labels: [], acknowledgeWarnings: false, force: true }));
  });

  it("renders changed files first, opens their diff, and blocks when comparison fails", async () => {
    api.compare.mockReturnValue({ data: { files: [
      { path: "SKILL.md", status: "unchanged" },
      { path: "references/policy.md", status: "modified", fromText: "Old policy", toText: "New policy" },
    ] }, isError: false });
    const { rerender } = renderDialog({ draft: { ...draft, baseVersion: 1 } });
    expect(api.compare).toHaveBeenCalledWith("skill-id", 1, "draft");
    expect(screen.getByTestId("diff")).toHaveTextContent("Old policy → New policy");
    fireEvent.click(screen.getByText("SKILL.md"));
    expect(screen.getByText("This file has no changes.")).toBeInTheDocument();
    api.compare.mockReturnValue({ data: null, isError: true, refetch: vi.fn() });
    rerender(<PublishDialog open onClose={vi.fn()} skill={skill} draft={{ ...draft, baseVersion: 1 }} />);
    expect(screen.getByRole("button", { name: "Publish", exact: true })).toBeDisabled();
    expect(screen.getByText("Couldn't load file changes")).toBeInTheDocument();
  });

  it("uses authoritative server errors if publishing rechecks reject the draft", async () => {
    api.publish.mockRejectedValueOnce({ code: "checks_failed", detail: checks(1) });
    renderDialog();
    const button = screen.getByRole("button", { name: "Publish", exact: true });
    await waitFor(() => expect(button).toBeEnabled());
    fireEvent.click(button);
    await screen.findByText("Checks changed. Review the results before publishing.");
    expect(button).toBeDisabled();
    expect(api.navigate).not.toHaveBeenCalled();
  });

  it("fails closed when checks cannot run, with a retry", async () => {
    api.checks.mockRejectedValueOnce(new Error("offline"));
    renderDialog();
    await screen.findByText("Couldn't run checks");
    const button = screen.getByRole("button", { name: "Publish", exact: true });
    expect(button).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(button).toBeEnabled());
  });

  it("does not run checks when closed", () => {
    renderDialog({ open: false });
    expect(api.checks).not.toHaveBeenCalled();
  });
});
