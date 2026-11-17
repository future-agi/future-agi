import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithRouter } from "src/utils/test-utils";
import SkillsLibraryView from "./SkillsLibraryView";
import NewSkillDialog from "./NewSkillDialog";
import ImportSkillsDialog from "./ImportSkillsDialog";
import ArchivedSkillsView from "./ArchivedSkillsView";
import { enqueueSnackbar } from "notistack";
import { setSkillTracesOverride } from "../runtime/skillTracesFlag";

const api = vi.hoisted(() => ({
  isViewer: false, canAdmin: true, restoreReady: false, canArchive: true, list: vi.fn(), templates: vi.fn(), create: vi.fn(), archive: vi.fn(),
  assistEnabled: true, canMove: true, preview: vi.fn(), importSkills: vi.fn(), download: vi.fn(), refetch: vi.fn(),
}));
vi.mock("src/api/skills/runtime", () => ({ useLibraryRuntime: () => ({ data: { rows: [] }, isPending: false, isError: false, fetchStatus: 'idle' }) }));
vi.mock("src/api/skills", () => ({
  // The generated contract decides whether the archive confirm promises a way back.
  archivedSkillsReady: () => api.restoreReady,
  useSkillsList: api.list,
  useSkill: () => ({ data: { ...skill, labels: skill.labels.map((label) => ({ ...label, canMove: api.canMove })), protectedLabelNames: ["production"], permissions: { canArchive: api.canArchive, canManageLabelProtection: api.canMove }, releaseRevision: 3 } }),
  useSkillTemplates: api.templates,
  useCreateSkill: () => ({ mutateAsync: api.create, isPending: false }),
  useArchiveSkill: () => ({ mutateAsync: api.archive, isPending: false }),
  useImportPreview: () => ({ mutateAsync: api.preview, isPending: false }),
  useImportSkills: () => ({ mutateAsync: api.importSkills, isPending: false }),
  downloadSkillsZip: api.download,
  getSkillErrorCode: (error) => error.code,
}));
vi.mock("../utils/access", () => ({ useSkillsAccess: () => ({ ready: true, isViewer: api.isViewer, canWrite: !api.isViewer, canAdmin: api.canAdmin }) }));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
// CTX5 rewrites the restore dialog's own copy in parallel, so this lane tests the view against a stand-in.
// The stand-in runs the real operation hook, so the retained key and retry behave as they do in the dialog.
vi.mock("../contexts/ContextSafetyDialog", async () => {
  const { useAuthoringOperation } = await vi.importActual("../editor/HotfixHeadReview");
  return { default: function Dialog({ scopeKey, resource, review, canAct, onApply, onApplied, onClose }) {
    const operation = useAuthoringOperation(scopeKey);
    const input = { expectedReleaseRevision: review.releaseRevision, note: "", expectedReviewFingerprint: review.reviewFingerprint };
    const done = (result) => { if (result) onApplied(result); };
    return <div role="dialog" aria-label={`Restore ${resource.name}`}>
      {review.labels.map((label) => <p key={label.name}>{label.name} v{label.version}</p>)}
      {review.blockers.map((blocker) => <p key={blocker}>{blocker}</p>)}
      <p data-testid="can-act">{String(canAct)}</p>
      <button disabled={review.blocked} onClick={async () => done(await operation.run(onApply, input))}>Confirm restore</button>
      <button onClick={async () => done(await operation.retry())}>Retry restore</button>
      <button onClick={onClose}>Close</button>
    </div>;
  } };
});

const skill = {
  id: "skill-1", name: "support-policy", description: "Use when answering customer refund questions.",
  tags: ["support"], latestVersion: 3, labels: [{ name: "production", version: 2 }],
  hasDraft: true, fetches7d: 12, updatedAt: "2026-11-06T12:00:00Z", updatedBy: { id: "user-1", name: "Sam" },
};
const stats = { total: 82, inProduction: 42, fetches7d: 719, myDrafts: 17 };
const templates = [{ id: "blank", title: "Blank", description: "Write your own." }, {
  id: "support-policy", title: "Support policy", description: "Use when answering refund questions.",
}];
const listResult = (overrides = {}) => ({
  data: { results: [skill], count: 1, page: 1, pageSize: 25, totalPages: 1, stats, ...overrides },
  isPending: false, isError: false, isFetching: false, refetch: api.refetch,
});
const bundle = (key, status = "new", warnings = 0, errors = 0) => ({
  key, root: key, name: key, status, fileCount: 1, totalBytes: 128,
  checks: { summary: { errors, warnings, infos: 0 }, items: warnings ? [{
    code: "description_short", severity: "warning", message: "Describe when to use this skill.", path: "SKILL.md",
  }] : [] },
});

beforeEach(() => {
  vi.clearAllMocks();
  api.isViewer = false; api.canAdmin = true; api.restoreReady = false; api.canArchive = true;
  api.canMove = true; api.assistEnabled = true;
  api.list.mockReturnValue(listResult());
  api.templates.mockReturnValue({ data: templates, isPending: false, isError: false, refetch: vi.fn() });
  api.create.mockResolvedValue({ id: "created-1", permissions: { canRunTests: true } });
  api.archive.mockResolvedValue(undefined);
  api.preview.mockResolvedValue({ bundles: [bundle("valid-skill")], rejected: [], skipped: [] });
  api.importSkills.mockResolvedValue({ created: [{ name: "valid-skill" }], updated: [], unchanged: [], failed: [] });
});

function renderLibrary(search = "") {
  return renderWithRouter(<SkillsLibraryView />, { route: `/dashboard/skills${search}` });
}

function renderCreate() {
  return renderWithRouter(<NewSkillDialog open onClose={vi.fn()} />, { route: "/dashboard/skills" });
}

function fillCreate(name = "my-skill") {
  fireEvent.change(screen.getByRole("textbox", { name: "Name" }), { target: { value: name } });
  fireEvent.change(screen.getByRole("textbox", { name: "Description" }), {
    target: { value: "Use when reviewing a support request for a refund." },
  });
}

function chooseFolder() {
  const file = new File(["---\nname: valid-skill\n---\nInstructions"], "SKILL.md", { type: "text/markdown" });
  Object.defineProperty(file, "webkitRelativePath", { value: "valid-skill/SKILL.md" });
  fireEvent.change(screen.getByLabelText("Choose skill folder"), { target: { files: [file] } });
}

async function renderImport() {
  renderWithRouter(<ImportSkillsDialog open onClose={vi.fn()} />);
  chooseFolder();
  fireEvent.click(screen.getByRole("button", { name: "Preview import" }));
  await screen.findByRole("button", { name: "Import", exact: true });
}

describe("Skills library", () => {
  it("renders rows, production and unpublished versions, and workspace-wide metrics", () => {
    api.list.mockReturnValue(listResult({ results: [skill, { ...skill, id: "skill-2", name: "new-skill", latestVersion: null, labels: [] }] }));
    renderLibrary();
    expect(screen.getByRole("link", { name: "support-policy" })).toHaveAttribute("href", "/dashboard/skills/skill-1");
    expect(screen.getByText("Not published")).toBeInTheDocument();
    expect(screen.getByText("v2")).toBeInTheDocument();
    expect(screen.getByText("v3")).toBeInTheDocument();
    expect(screen.getByText(/82 skills/)).toBeInTheDocument();
    expect(screen.getByText(/42 in production/)).toBeInTheDocument();
    expect(screen.getByText(/719 fetches in 7 days/)).toBeInTheDocument();
    expect(screen.getByText(/17 drafts/)).toBeInTheDocument();
    expect(screen.getByText(/drafts/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("link", { name: "support-policy" }));
    expect(window.location.pathname).toBe("/dashboard/skills/skill-1");
  });

  it("opens template selection inside the first-run create dialog", () => {
    api.list.mockReturnValue(listResult({ results: [], count: 0, stats: { total: 0, inProduction: 0, fetches7d: 0, myDrafts: 0 } }));
    renderLibrary();
    expect(screen.getByRole("button", { name: "Create your first skill" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Import a folder or ZIP" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "New skill" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Create your first skill" }));
    expect(screen.getByRole("combobox", { name: "Start from" })).toHaveTextContent("Blank");
    fireEvent.mouseDown(screen.getByRole("combobox", { name: "Start from" }));
    fireEvent.click(screen.getByRole("option", { name: "Support policy" }));
    expect(screen.getByRole("combobox", { name: "Start from" })).toHaveTextContent("Support policy");
  });

  it("keeps workspace metrics when search returns no results and clears only filters", () => {
    api.list.mockReturnValue(listResult({ results: [], count: 0 }));
    renderLibrary("?search=missing&tag=support&label=production&page=3&sort=name&order=asc&keep=yes");
    expect(screen.getByText("No skills match these filters.")).toBeInTheDocument();
    expect(screen.getByText(/719 fetches in 7 days/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Write from scratch" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Clear filters/i }));
    expect(new URLSearchParams(window.location.search).get("search")).toBeNull();
    expect(new URLSearchParams(window.location.search).get("page")).toBeNull();
    expect(new URLSearchParams(window.location.search).get("keep")).toBe("yes");
    expect(new URLSearchParams(window.location.search).get("sort")).toBe("name");
  });

  it("renders a recoverable error without a misleading empty state", () => {
    api.list.mockReturnValue({ ...listResult(), isError: true });
    renderLibrary();
    expect(screen.getByText("Couldn't load skills.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(api.refetch).toHaveBeenCalledOnce();
  });

  it.each(["loading", "error", "empty"])("allows the %s state through the URL", (state) => {
    renderLibrary(`?state=${state}`);
    const text = { loading: "Loading skills", error: "Couldn't load skills.", empty: "Give your agents a shared library" };
    if (state === "loading") expect(screen.getByLabelText(text[state])).toBeInTheDocument();
    else expect(screen.getByText(text[state])).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "support-policy" })).not.toBeInTheDocument();
  });

  it("parses URL filters and resets the page when search changes", () => {
    renderLibrary("?tag=support&label=staging&sort=name&order=asc&page=4&page_size=50&keep=yes");
    expect(api.list).toHaveBeenLastCalledWith({ search: "", tag: "support", label: "staging", sort: "name", order: "asc", page: 4, pageSize: 50 });
    fireEvent.change(screen.getByRole("textbox", { name: "Search skills" }), { target: { value: "refund" } });
    expect(api.list).toHaveBeenLastCalledWith({ search: "refund", tag: "support", label: "staging", sort: "name", order: "asc", page: 1, pageSize: 50 });
    expect(new URLSearchParams(window.location.search).get("keep")).toBe("yes");
  });

  it("filters by a tag found only beyond the first 25 skills", () => {
    const workspaceSkills = Array.from({ length: 26 }, (_, index) => ({
      ...skill, id: `skill-${index}`, name: `skill-${index}`, tags: [index === 25 ? "finance" : "support"],
    }));
    api.list.mockImplementation(({ tag, page, pageSize }) => {
      const matches = workspaceSkills.filter((item) => !tag || item.tags.includes(tag));
      return listResult({ results: matches.slice((page - 1) * pageSize, page * pageSize), count: matches.length, totalPages: Math.ceil(matches.length / pageSize) });
    });
    renderLibrary();
    expect(screen.queryByRole("link", { name: "skill-25" })).not.toBeInTheDocument();
    const filter = screen.getByRole("combobox", { name: "Tag" });
    fireEvent.keyDown(filter, { key: "ArrowDown" });
    expect(screen.queryByRole("option", { name: "finance" })).not.toBeInTheDocument();
    fireEvent.change(filter, { target: { value: "finance" } });
    fireEvent.keyDown(filter, { key: "Enter" });
    expect(api.list).toHaveBeenLastCalledWith(expect.objectContaining({ tag: "finance", page: 1 }));
    expect(screen.getByRole("link", { name: "skill-25" })).toBeInTheDocument();
    expect(filter).toHaveValue("finance");
  });

  it("switches between disjoint tag results and resets pagination without clearing other filters", () => {
    api.list.mockImplementation(({ tag }) => listResult({ results: [{ ...skill, tags: [tag] }], count: 26, totalPages: 3 }));
    renderLibrary("?tag=support&search=policy&label=production&page=2&page_size=10&keep=yes");
    const filter = screen.getByRole("combobox", { name: "Tag" });
    fireEvent.change(filter, { target: { value: "security" } });
    fireEvent.keyDown(filter, { key: "Enter" });
    expect(api.list).toHaveBeenLastCalledWith(expect.objectContaining({ tag: "security", search: "policy", label: "production", page: 1, pageSize: 10 }));
    expect(new URLSearchParams(window.location.search).get("page")).toBeNull();
    expect(new URLSearchParams(window.location.search).get("keep")).toBe("yes");
    fireEvent.change(filter, { target: { value: "support" } });
    fireEvent.keyDown(filter, { key: "Enter" });
    expect(api.list).toHaveBeenLastCalledWith(expect.objectContaining({ tag: "support", page: 1 }));
    expect(filter).toHaveValue("support");
  });

  it("keeps an off-page tag selected with no results and allows any tag on blur", async () => {
    const user = userEvent.setup();
    api.list.mockReturnValue(listResult({ results: [], count: 0 }));
    renderLibrary("?search=missing&tag=finance&page=3");
    const filter = screen.getByRole("combobox", { name: "Tag" });
    expect(filter).toHaveValue("finance");
    await user.click(filter);
    await user.clear(filter);
    await user.type(filter, "security");
    await user.tab();
    expect(api.list).toHaveBeenLastCalledWith(expect.objectContaining({ tag: "security", search: "missing", page: 1 }));
    await user.click(filter);
    // JSDOM misses MUI's higher-specificity parent-focus visibility rule.
    // Browser coverage separately checks this control is visible and clickable.
    await user.click(screen.getByLabelText("Clear"));
    expect(api.list).toHaveBeenLastCalledWith(expect.objectContaining({ tag: "", search: "missing", page: 1 }));
    expect(filter).toHaveValue("");
  });

  it("still lets users choose a suggested tag", () => {
    renderLibrary("?page=2");
    fireEvent.keyDown(screen.getByRole("combobox", { name: "Tag" }), { key: "ArrowDown" });
    fireEvent.click(screen.getByRole("option", { name: "support" }));
    expect(api.list).toHaveBeenLastCalledWith(expect.objectContaining({ tag: "support", page: 1 }));
  });

  it("normalizes malformed pagination and moves between server pages", () => {
    api.list.mockReturnValue(listResult({ count: 82, totalPages: 4 }));
    renderLibrary("?page=-3&page_size=1000");
    expect(api.list).toHaveBeenLastCalledWith(expect.objectContaining({ page: 1, pageSize: 25 }));
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(api.list).toHaveBeenLastCalledWith(expect.objectContaining({ page: 2 }));
    fireEvent.change(screen.getByRole("combobox", { name: "Rows per page" }), { target: { value: "100" } });
    expect(api.list).toHaveBeenLastCalledWith(expect.objectContaining({ page: 1, pageSize: 100 }));
  });

  it("recovers from an empty last page after archiving", () => {
    api.list.mockReturnValue(listResult({ results: [], count: 25, totalPages: 1 }));
    renderLibrary("?page=2");
    expect(screen.getByText("This page is no longer available")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Go to first page" }));
    expect(api.list).toHaveBeenLastCalledWith(expect.objectContaining({ page: 1 }));
  });

  it("requires archive confirmation and keeps portal clicks from opening the row", async () => {
    renderLibrary();
    fireEvent.click(screen.getByRole("button", { name: "Actions for support-policy" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Archive" }));
    expect(api.archive).not.toHaveBeenCalled();
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(window.location.pathname).toBe("/dashboard/skills");
    fireEvent.click(screen.getByRole("button", { name: "Actions for support-policy" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Archive" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Archive" }));
    await waitFor(() => expect(api.archive).toHaveBeenCalledWith({ id: "skill-1", expectedReleaseRevision: 3 }));
    expect(window.location.pathname).toBe("/dashboard/skills");
  });

  it("says what archiving does here: no restore path, and which labels stop resolving", () => {
    renderLibrary();
    fireEvent.click(screen.getByRole("button", { name: "Actions for support-policy" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Archive" }));
    const dialog = within(screen.getByRole("dialog"));
    expect(dialog.getByText("Archive support-policy?")).toBeInTheDocument();
    expect(dialog.getByText("Agents can no longer fetch it, including through labels.")).toBeInTheDocument();
    expect(dialog.getByText("production points at v2. Agents following production will stop receiving this skill.")).toBeInTheDocument();
    expect(dialog.getByText("Restoring an archived skill isn't available in this workspace; contact support to restore it.")).toBeInTheDocument();
    expect(dialog.queryByText(/More → Archived skills/)).not.toBeInTheDocument();
    expect(dialog.getByRole("button", { name: "Archive" })).toHaveClass("MuiButton-contained");
  });

  it("points an admin to Archived skills when the workspace can restore, and omits the label line without labels", () => {
    api.restoreReady = true;
    api.list.mockReturnValue(listResult({ results: [{ ...skill, labels: [] }] }));
    renderLibrary();
    fireEvent.click(screen.getByRole("button", { name: "Actions for support-policy" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Archive" }));
    const dialog = within(screen.getByRole("dialog"));
    expect(dialog.getByText("You can restore it from More → Archived skills.")).toBeInTheDocument();
    expect(dialog.queryByText(/isn't available in this workspace/)).not.toBeInTheDocument();
    expect(dialog.queryByText(/points at/)).not.toBeInTheDocument();
  });

  it("sends someone who can archive but is not an admin to a workspace admin", () => {
    api.restoreReady = true;
    api.canAdmin = false;
    renderLibrary();
    fireEvent.click(screen.getByRole("button", { name: "Actions for support-policy" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Archive" }));
    const dialog = within(screen.getByRole("dialog"));
    expect(dialog.getByText("A workspace admin can restore it from More → Archived skills.")).toBeInTheDocument();
    expect(dialog.queryByText(/contact support/)).not.toBeInTheDocument();
  });

  it("names the fetch column by what it counts: a server fetch, never a use (UX-18)", () => {
    renderLibrary();
    expect(screen.getAllByText("Fetched (7 days)").length).toBeGreaterThan(0);
    expect(screen.queryByText("Used (7 days)")).not.toBeInTheDocument();
    expect(screen.queryByText("Fetches (7 days)")).not.toBeInTheDocument();
    // The runtime column is a separate, flagged change; nothing here claims a load.
    expect(screen.queryByText("Loaded (7 days)")).not.toBeInTheDocument();
    expect(screen.queryByText(/\buses? in 7 days/)).not.toBeInTheDocument();
  });

  it("keeps inapplicable row actions visible but disabled", () => {
    api.list.mockReturnValue(listResult({ results: [{ ...skill, latestVersion: null, labels: [] }] }));
    renderLibrary();
    fireEvent.click(screen.getByRole("button", { name: "Actions for support-policy" }));
    expect(screen.getByRole("menuitem", { name: "Copy install command" })).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByRole("menuitem", { name: "Download" })).toHaveAttribute("aria-disabled", "true");
  });
});

describe("New skill", () => {
  it.each(["Uppercase", "-leading", "trailing-", "double--dash", "has space", "under_score", "a".repeat(65)])("rejects invalid name %s", (name) => {
    renderCreate();
    fillCreate(name);
    expect(screen.getByRole("button", { name: "Create skill" })).toBeDisabled();
    expect(screen.getByRole("textbox", { name: "Name" })).toHaveAttribute("aria-invalid", "true");
    expect(api.create).not.toHaveBeenCalled();
  });

  it("requires a description and submits a valid skill into the editor", async () => {
    renderCreate();
    expect(screen.getByRole("button", { name: "Create skill" })).toBeDisabled();
    fillCreate("a".repeat(64));
    fireEvent.click(screen.getByRole("button", { name: "Create skill" }));
    fireEvent.click(await screen.findByRole("button", { name: "Open editor" }));
    await waitFor(() => expect(window.location.pathname).toBe("/dashboard/skills/created-1/edit"));
    expect(api.create).toHaveBeenCalledWith({ name: "a".repeat(64), description: "Use when reviewing a support request for a refund.", templateId: "blank" });
  });

  it("shows server name_taken and enables retry after editing the name", async () => {
    api.create.mockRejectedValueOnce({ code: "name_taken" });
    renderCreate();
    fillCreate();
    fireEvent.click(screen.getByRole("button", { name: "Create skill" }));
    expect(await screen.findByText(/This name is already taken/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create skill" })).toBeDisabled();
    fireEvent.change(screen.getByRole("textbox", { name: "Name" }), { target: { value: "another-skill" } });
    expect(screen.getByRole("button", { name: "Create skill" })).toBeEnabled();
    expect(screen.getByRole("textbox", { name: "Description" })).toHaveValue("Use when reviewing a support request for a refund.");
  });

  it("preserves form input after a server failure", async () => {
    api.create.mockRejectedValueOnce({ code: "unavailable" });
    renderCreate();
    fillCreate();
    fireEvent.click(screen.getByRole("button", { name: "Create skill" }));
    expect(await screen.findByText(/Couldn't create this skill/)).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue("my-skill");
  });
});

describe("Import skills", () => {
  it.each(["{Enter}", " "])("opens the folder input exactly once with %s", async (key) => {
    const user = userEvent.setup();
    renderWithRouter(<ImportSkillsDialog open onClose={vi.fn()} />);
    const input = screen.getByLabelText("Choose skill folder");
    const click = vi.spyOn(input, "click").mockImplementation(() => {});
    screen.getByRole("button", { name: "Choose folder", exact: true }).focus();
    await user.keyboard(key);
    expect(click).toHaveBeenCalledOnce();
    click.mockRestore();
  });

  it("preserves folder paths and uses files multipart parts for preview and import", async () => {
    await renderImport();
    const preview = api.preview.mock.calls[0][0];
    expect(preview.getAll("files")).toHaveLength(1);
    expect(preview.get("files").name).toBe("valid-skill/SKILL.md");
    expect(preview.has("files[]")).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Import", exact: true }));
    await screen.findByText(/1 created · 0 updated/);
    const submission = api.importSkills.mock.calls[0][0];
    expect(submission.get("files").name).toBe("valid-skill/SKILL.md");
    expect(JSON.parse(submission.get("selected"))).toEqual(["valid-skill"]);
    expect(JSON.parse(submission.get("labels"))).toEqual([]);
    expect(submission.get("acknowledge_warnings")).toBe("false");
    expect(screen.getByRole("button", { name: "Done" })).toBeInTheDocument();
  });

  it("does not select invalid or error-containing bundles", async () => {
    api.preview.mockResolvedValueOnce({ bundles: [bundle("invalid-skill", "invalid"), bundle("error-skill", "new", 0, 1), bundle("valid-skill")], rejected: [], skipped: [] });
    await renderImport();
    expect(screen.getByRole("checkbox", { name: "Select invalid-skill" })).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: "Select invalid-skill" })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Select error-skill" })).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: "Select valid-skill" })).toBeChecked();
    fireEvent.click(screen.getByRole("button", { name: "Import", exact: true }));
    await waitFor(() => expect(api.importSkills).toHaveBeenCalledOnce());
    expect(JSON.parse(api.importSkills.mock.calls[0][0].get("selected"))).toEqual(["valid-skill"]);
  });

  it("blocks all-invalid imports", async () => {
    api.preview.mockResolvedValueOnce({ bundles: [bundle("invalid-skill", "invalid", 0, 1)], rejected: [], skipped: [] });
    await renderImport();
    expect(screen.getByRole("button", { name: "Import", exact: true })).toBeDisabled();
  });

  it("requires warning acknowledgment only for selected bundles and resets it on selection changes", async () => {
    api.preview.mockResolvedValueOnce({ bundles: [bundle("warning-skill", "changed", 1), bundle("valid-skill")], rejected: [], skipped: [] });
    await renderImport();
    expect(screen.getByRole("button", { name: "Import", exact: true })).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox", { name: /I reviewed the warnings/ }));
    expect(screen.getByRole("button", { name: "Import", exact: true })).toBeEnabled();
    fireEvent.click(screen.getByRole("checkbox", { name: "Select valid-skill" }));
    expect(screen.getByRole("checkbox", { name: /I reviewed the warnings/ })).not.toBeChecked();
    expect(screen.getByRole("button", { name: "Import", exact: true })).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox", { name: "Select warning-skill" }));
    expect(screen.queryByRole("checkbox", { name: /I reviewed the warnings/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("checkbox", { name: "Select valid-skill" }));
    expect(screen.getByRole("button", { name: "Import", exact: true })).toBeEnabled();
  });

  it("sends the chosen label and warnings acknowledgment with the reviewed selection", async () => {
    api.preview.mockResolvedValueOnce({ bundles: [bundle("warning-skill", "changed", 1)], rejected: [], skipped: [] });
    await renderImport();
    fireEvent.mouseDown(screen.getByRole("combobox", { name: "Also label as" }));
    fireEvent.click(screen.getByRole("option", { name: "Production" }));
    expect(screen.getByText(/This moves production to each imported version/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("checkbox", { name: /I reviewed the warnings/ }));
    fireEvent.click(screen.getByRole("button", { name: "Import", exact: true }));
    await waitFor(() => expect(api.importSkills).toHaveBeenCalledOnce());
    const form = api.importSkills.mock.calls[0][0];
    expect(form.get("labels")).toBe('["production"]');
    expect(form.get("acknowledge_warnings")).toBe("true");
  });

  it("invalidates the preview when a different folder is selected", async () => {
    await renderImport();
    chooseFolder();
    expect(screen.queryByRole("checkbox", { name: "Select valid-skill" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Preview import" })).toBeEnabled();
    expect(api.importSkills).not.toHaveBeenCalled();
  });

  it("reports preview errors and permits retry", async () => {
    api.preview.mockRejectedValueOnce({ code: "import_invalid" });
    renderWithRouter(<ImportSkillsDialog open onClose={vi.fn()} />);
    chooseFolder();
    fireEvent.click(screen.getByRole("button", { name: "Preview import" }));
    expect(await screen.findByText(/Couldn't preview these files/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Preview import" })).toBeEnabled();
  });

  it("shows rejected and skipped entries, and reports partial import failures", async () => {
    api.preview.mockResolvedValueOnce({ bundles: [bundle("valid-skill")], rejected: [{ path: "nested.zip", reason: "Nested ZIPs are not supported." }], skipped: [".DS_Store"] });
    api.importSkills.mockResolvedValueOnce({ created: [], updated: [], unchanged: [], failed: [{ key: "valid-skill", name: "valid-skill", code: "name_taken", message: "The skill changed. Preview it again." }] });
    await renderImport();
    expect(screen.getByText(/nested.zip: Nested ZIPs/)).toBeInTheDocument();
    expect(screen.getByText("Skipped: .DS_Store")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Import", exact: true }));
    expect(await screen.findByText(/0 created · 0 updated · 0 up to date · 1 failed/)).toBeInTheDocument();
    expect(screen.getByText(/Successful imports are saved/)).toBeInTheDocument();
  });
});

vi.mock("src/sections/skills/sources/GitHubImportPanel", () => ({ default: () => <div>GitHub draft import</div> }));

it('exposes Draft with AI only after creation succeeds', async () => {
  renderCreate();
  expect(screen.queryByRole('button', { name: 'Draft with AI' })).not.toBeInTheDocument();
  fillCreate();
  fireEvent.click(screen.getByRole('button', { name: 'Create skill' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Draft with AI' }));
  expect(window.location.pathname + window.location.search).toBe('/dashboard/skills/created-1/edit?assist=draft');
});

it('gives the GitHub panel its own draft action without the upload submit', () => {
  renderWithRouter(<ImportSkillsDialog open onClose={vi.fn()} />);
  fireEvent.click(screen.getByRole('tab', { name: 'GitHub' }));
  expect(screen.getByText('GitHub draft import')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Preview import' })).not.toBeInTheDocument();
  expect(api.importSkills).not.toHaveBeenCalled();
});

vi.mock("src/api/skills/migration", () => ({
  useMigrationPreview: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useMigrationDecision: () => ({ mutateAsync: vi.fn() }),
  useMigrationPublish: () => ({ mutateAsync: vi.fn() }),
  useMigrationPlanReload: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));
vi.mock("src/api/skills/testing", () => ({ useSkillV11Scope: () => ({ identity: "org:ws:user" }), useSkillTestingOptions: () => ({ data: { assistEnabled: api.assistEnabled } }) }));

it('sends reviewed aggregate release revisions for existing upload destinations', async () => {
  api.preview.mockResolvedValueOnce({ bundles: [{ ...bundle('existing', 'changed'), skillId: 'skill-1' }], rejected: [], skipped: [] });
  await renderImport();
  fireEvent.mouseDown(screen.getByRole('combobox', { name: 'Also label as' }));
  fireEvent.click(screen.getByRole('option', { name: 'Production' }));
  await screen.findByText(/release r3/);
  fireEvent.click(screen.getByRole('button', { name: 'Import', exact: true }));
  await waitFor(() => expect(api.importSkills).toHaveBeenCalled());
  expect(JSON.parse(api.importSkills.mock.calls[0][0].get('expected_release_revisions'))).toEqual({ existing: 3 });
});

it('does not block ordinary uploads when the selected production move is forbidden', async () => {
  api.canMove = false;
  api.preview.mockResolvedValueOnce({ bundles: [{ ...bundle('existing', 'changed'), skillId: 'skill-1' }], rejected: [], skipped: [] });
  await renderImport();
  fireEvent.mouseDown(screen.getByRole('combobox', { name: 'Also label as' }));
  fireEvent.click(screen.getByRole('option', { name: 'Production' }));
  await screen.findByText(/An admin must move protected production/);
  expect(screen.getByRole('button', { name: 'Import', exact: true })).toBeDisabled();
  fireEvent.mouseDown(screen.getByRole('combobox', { name: 'Also label as' }));
  fireEvent.click(screen.getByRole('option', { name: 'None' }));
  expect(screen.getByRole('button', { name: 'Import', exact: true })).toBeEnabled();
});

it('hides the new-skill AI entry, and its explanation, when AI drafting is off for the workspace', async () => {
  api.assistEnabled = false;
  renderCreate();
  fillCreate();
  fireEvent.click(screen.getByRole('button', { name: 'Create skill' }));
  expect(await screen.findByRole('button', { name: 'Open editor' })).toBeEnabled();
  expect(screen.queryByRole('button', { name: 'Draft with AI' })).not.toBeInTheDocument();
  expect(screen.queryByText('AI drafting is off for this workspace')).not.toBeInTheDocument();
});

it('keeps the new-skill AI entry disabled, with its reason, when only permission is missing', async () => {
  api.create.mockResolvedValue({ id: 'created-1', permissions: { canRunTests: false } });
  renderCreate();
  fillCreate();
  fireEvent.click(screen.getByRole('button', { name: 'Create skill' }));
  expect(await screen.findByRole('button', { name: 'Draft with AI' })).toBeDisabled();
  expect(screen.getByText('You need test permissions to use AI assistance.')).toBeInTheDocument();
});

it('supports the filtered-empty state preview without presenting a first-run library', () => {
  api.list.mockReturnValue(listResult());
  renderLibrary('?state=filtered-empty');
  expect(screen.getByText('No skills match these filters.')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'New skill', exact: true })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Create your first skill' })).not.toBeInTheDocument();
});


describe('library access presentation', () => {
  it('omits create and import destinations for viewers', () => {
    api.isViewer = true;
    renderLibrary();
    expect(screen.queryByRole('button', { name: 'New skill' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Import', exact: true })).not.toBeInTheDocument();
  });
  it('gives a viewer an actionable first-run explanation without creation controls', () => {
    api.isViewer = true;
    api.list.mockReturnValue(listResult({ results: [], count: 0, stats: { total: 0 } }));
    renderLibrary();
    expect(screen.getByText('No skills yet.')).toBeInTheDocument();
    expect(screen.getByText('Ask a workspace member to create one.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Create your first skill' })).not.toBeInTheDocument();
  });
  it('keeps member creation and disables archive using server permission', async () => {
    api.canArchive = false;
    renderLibrary();
    expect(screen.getByRole('button', { name: 'New skill' })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: 'Actions for support-policy' }));
    expect(screen.getByRole('menuitem', { name: 'Archive' })).toHaveAttribute('aria-disabled', 'true');
    expect(screen.getByText("You can't archive this skill. Ask a workspace admin.")).toBeInTheDocument();
  });
  it('distinguishes an invalid page response from a network error', () => {
    api.list.mockReturnValue({ isError: true, error: { response: { status: 404 } }, refetch: api.refetch });
    renderLibrary('?page=99');
    expect(screen.getByText("This page is no longer available")).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Go to first page' }));
    expect(new URLSearchParams(window.location.search).get('page')).toBe('1');
  });
});

it.each([
  { statusCode: 404, detail: 'Invalid page.' },
  { response: { status: 400, data: { page: ['Invalid page.'] } } },
  { status: 422, detail: { page: ['Out of range.'] } },
  { statusCode: 400, errors: { page: ['Invalid page.'] } },
  { statusCode: 422, details: { page: ['Invalid page.'] } },
])('recovers from page errors without losing filters: %j', (error) => {
  api.list.mockReturnValue({ isError: true, error });
  renderLibrary('?page=9&search=policy&tag=support&label=production&sort=name&order=asc');
  expect(screen.getByText('This page is no longer available')).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Go to first page' }));
  expect(api.list).toHaveBeenLastCalledWith(expect.objectContaining({ page: 1, search: 'policy', tag: 'support', label: 'production', sort: 'name', order: 'asc' }));
});
it.each([{ statusCode: 500 }, { status: 422, detail: { label: ['Invalid label'] } }, new Error('Network error')])('keeps Retry for non-page failures: %j', (error) => {
  api.list.mockReturnValue({ isError: true, error, refetch: api.refetch });
  renderLibrary('?page=9');
  expect(screen.queryByText('This page is no longer available')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: /Retry/i })).toBeVisible();
});


describe("library recipe (UX iteration 3, LIB3)", () => {
  it("keeps the header primary contained and offers an outlined Go to first page that says filters are kept", () => {
    api.list.mockReturnValue(listResult({ results: [], count: 25, totalPages: 1 }));
    renderLibrary("?page=9&search=policy");
    expect(screen.getByText("Your filters are kept.")).toBeVisible();
    expect(screen.getByRole("button", { name: "Go to first page" })).toHaveClass("MuiButton-outlined");
    expect(screen.getByRole("button", { name: "New skill" })).toHaveClass("MuiButton-contained");
  });
  it("reads pagination as from-to of total, like Agent contexts", () => {
    api.list.mockReturnValue(listResult({ count: 82, totalPages: 4 }));
    renderLibrary();
    expect(screen.getByText("1–25 of 82")).toBeVisible();
    expect(screen.queryByText(/^Page \d+ of/)).not.toBeInTheDocument();
  });
  it("renders stacked cards below sm with the filters full width", () => {
    vi.stubGlobal("matchMedia", (query) => ({ matches: /max-width/.test(query), media: query, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} }));
    try {
      renderLibrary();
      const list = screen.getByRole("list", { name: "Skills" });
      const card = within(list).getByRole("listitem");
      expect(within(card).getByRole("link", { name: "support-policy" })).toHaveAttribute("href", "/dashboard/skills/skill-1");
      expect(card).toHaveTextContent(/Production v2 · Latest v3 · /);
      expect(within(card).getByRole("button", { name: "Actions for support-policy" })).toBeVisible();
      expect(screen.queryByRole("grid")).not.toBeInTheDocument();
      fireEvent.click(within(card).getByText("Use when answering customer refund questions."));
      expect(window.location.pathname).toBe("/dashboard/skills/skill-1");
    } finally { vi.unstubAllGlobals(); }
  });
});

describe("one import footer (UX iteration 3, LIB2)", () => {
  const footerButtons = () => screen.getAllByRole("button").filter((button) => ["Cancel", "Preview import", "Import", "Done"].includes(button.textContent));
  it("keeps the reason on the left, then Cancel, then the frozen primary on the right, on the Upload tab", () => {
    renderWithRouter(<ImportSkillsDialog open onClose={vi.fn()} />);
    const reason = screen.getByText("Choose a file or folder first");
    const [cancel, primary] = footerButtons();
    expect(cancel).toHaveTextContent("Cancel");
    expect(primary).toHaveTextContent("Preview import");
    expect(primary).toBeDisabled();
    expect(primary).toHaveAccessibleDescription("Choose a file or folder first");
    expect(reason.compareDocumentPosition(cancel) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    chooseFolder();
    expect(screen.queryByText("Choose a file or folder first")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Preview import" })).toBeEnabled();
  });
  it("shows why Import is unavailable after the preview, in the same footer", async () => {
    api.preview.mockResolvedValueOnce({ bundles: [bundle("warning-skill", "changed", 1)], rejected: [], skipped: [] });
    await renderImport();
    expect(screen.getByRole("button", { name: "Import", exact: true })).toBeDisabled();
    expect(screen.getByText("Review the warnings to continue")).toBeVisible();
    fireEvent.click(screen.getByRole("checkbox", { name: /I reviewed the warnings/ }));
    expect(screen.queryByText("Review the warnings to continue")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Import", exact: true })).toBeEnabled();
  });
  it("offers one Preview import on From another tool, in the footer, never in the body", () => {
    renderWithRouter(<ImportSkillsDialog open onClose={vi.fn()} initialTab="migration" />);
    const previews = screen.getAllByRole("button", { name: "Preview import", exact: true });
    expect(previews).toHaveLength(1);
    expect(previews[0]).toBeDisabled();
    expect(screen.getByText("Choose a file or folder first")).toBeVisible();
    expect(screen.getAllByRole("button", { name: "Cancel" })).toHaveLength(1);
  });
  it("keeps GitHub to Cancel only, since that panel owns its draft action", () => {
    renderWithRouter(<ImportSkillsDialog open onClose={vi.fn()} />);
    fireEvent.click(screen.getByRole("tab", { name: "GitHub" }));
    expect(screen.getAllByRole("button", { name: "Cancel" })).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "Preview import" })).not.toBeInTheDocument();
  });
});

describe("Archived skills (UX iteration 5, LIB1)", () => {
  const LABELS_CHANGED = "The labels changed since you opened this. Review again.";
  const archived = (overrides = {}) => ({
    id: "archived-1", name: "refund-policy", description: "Use when reviewing refund requests.", latestVersion: 2, releaseRevision: 5,
    archivedAt: "2026-11-10T09:30:00Z", labels: [{ name: "production", version: 1 }], ...overrides,
  });
  const review = (overrides = {}) => ({ subjectId: "archived-1", releaseRevision: 5, reviewFingerprint: `sha256:${"a".repeat(64)}`, labels: [{ name: "production", version: 1, isProtected: true }], blocked: false, blockers: [], ...overrides });
  const scopeKey = "org:workspace:admin";
  const page = (results = [archived()]) => ({ scopeKey, data: { results, count: results.length, page: 1, pageSize: 25 }, isLoading: false, refetch: vi.fn() });
  const handlers = (overrides = {}) => ({ onReview: vi.fn().mockResolvedValue(review()), onRestore: vi.fn().mockResolvedValue({ id: "archived-1" }), onOpenSkill: vi.fn(), onBack: vi.fn(), canRestore: true, ...overrides });
  const show = (props = {}) => renderWithRouter(<ArchivedSkillsView scopeKey={scopeKey} query={page()} {...handlers()} {...props} />, { route: "/dashboard/skills/archived" });

  it("lists archived skills with the labels that come back, the latest version and when it was archived", () => {
    show();
    expect(screen.getByRole("heading", { name: "Archived skills" })).toBeVisible();
    expect(screen.getByText("Restore a skill to bring back its versions, labels and history.")).toBeVisible();
    const table = within(screen.getByRole("table", { name: "Archived skills" }));
    for (const name of ["Skill", "Labels", "Latest", "Archived"]) expect(table.getByRole("columnheader", { name })).toBeInTheDocument();
    expect(table.getByText("refund-policy")).toBeVisible();
    expect(table.getByText("Use when reviewing refund requests.")).toBeVisible();
    expect(table.getByText("Production v1")).toBeVisible();
    expect(table.getByText("v2")).toBeVisible();
    expect(table.getByRole("time")).toHaveAttribute("datetime", "2026-11-10T09:30:00.000Z");
    expect(screen.queryByText("archived-1")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Inspect history/ })).not.toBeInTheDocument();
  });

  it("shows a dash when no time or labels are reported", () => {
    show({ query: page([archived({ archivedAt: null, labels: [] })]) });
    expect(screen.getAllByText("—")).toHaveLength(2);
  });

  it("restores after a fresh review, says so, and offers Open skill", async () => {
    const props = handlers();
    show(props);
    fireEvent.click(screen.getByRole("button", { name: "Restore refund-policy" }));
    const dialog = within(await screen.findByRole("dialog", { name: "Restore refund-policy" }));
    expect(props.onReview).toHaveBeenCalledWith(expect.objectContaining({ id: "archived-1" }));
    expect(dialog.getByText("production v1")).toBeVisible();
    expect(dialog.getByTestId("can-act")).toHaveTextContent("true");
    fireEvent.click(dialog.getByRole("button", { name: "Confirm restore" }));
    await waitFor(() => expect(props.onRestore).toHaveBeenCalledTimes(1));
    const [skill, input, options] = props.onRestore.mock.calls[0];
    expect(skill.id).toBe("archived-1");
    expect(input).toEqual({ expectedReleaseRevision: 5, note: "", expectedReviewFingerprint: `sha256:${"a".repeat(64)}` });
    expect(options.idempotencyKey).toMatch(/^[0-9a-f-]{36}$/);
    await waitFor(() => expect(enqueueSnackbar).toHaveBeenCalledWith("refund-policy restored", expect.objectContaining({ variant: "success" })));
    const action = enqueueSnackbar.mock.calls.at(-1)[1].action;
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    renderWithRouter(action("key"));
    fireEvent.click(screen.getByRole("button", { name: "Open skill" }));
    expect(props.onOpenSkill).toHaveBeenCalledWith(expect.objectContaining({ id: "archived-1" }));
  });

  it("blocks Restore while a live skill uses the name, and says what to do", async () => {
    show(handlers({ onReview: vi.fn().mockResolvedValue(review({ blocked: true, blockers: ["A live skill already uses this name. Rename or archive that skill first."] })) }));
    fireEvent.click(screen.getByRole("button", { name: "Restore refund-policy" }));
    const dialog = within(await screen.findByRole("dialog"));
    expect(dialog.getByText("A live skill already uses this name. Rename or archive that skill first.")).toBeVisible();
    expect(dialog.getByRole("button", { name: "Confirm restore" })).toBeDisabled();
  });

  it("closes the review and asks for a new one when the labels changed (impact_changed)", async () => {
    const onReview = vi.fn().mockResolvedValue(review());
    const onRestore = vi.fn().mockRejectedValue({ code: "impact_changed", statusCode: 409 });
    show(handlers({ onReview, onRestore }));
    fireEvent.click(screen.getByRole("button", { name: "Restore refund-policy" }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Confirm restore" }));
    expect(await screen.findByText(LABELS_CHANGED)).toBeVisible();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(enqueueSnackbar).not.toHaveBeenCalled();
    // Review again loads the labels from the server once more and starts from an empty acknowledgment.
    fireEvent.click(screen.getByRole("button", { name: "Restore refund-policy" }));
    await screen.findByRole("dialog");
    expect(onReview).toHaveBeenCalledTimes(2);
    expect(screen.queryByText(LABELS_CHANGED)).not.toBeInTheDocument();
  });

  it("repeats one restore with the same key after an unknown outcome", async () => {
    const onRestore = vi.fn().mockRejectedValueOnce(new Error("Network Error")).mockResolvedValueOnce({ id: "archived-1" });
    show(handlers({ onRestore }));
    fireEvent.click(screen.getByRole("button", { name: "Restore refund-policy" }));
    const dialog = within(await screen.findByRole("dialog"));
    fireEvent.click(dialog.getByRole("button", { name: "Confirm restore" }));
    await waitFor(() => expect(onRestore).toHaveBeenCalledTimes(1));
    fireEvent.click(dialog.getByRole("button", { name: "Retry restore" }));
    await waitFor(() => expect(onRestore).toHaveBeenCalledTimes(2));
    expect(onRestore.mock.calls[1][2].idempotencyKey).toBe(onRestore.mock.calls[0][2].idempotencyKey);
    expect(onRestore.mock.calls[1][1]).toEqual(onRestore.mock.calls[0][1]);
  });

  it("keeps Restore off for anyone who can't act", () => {
    show(handlers({ canRestore: false }));
    expect(screen.getByRole("button", { name: "Restore refund-policy" })).toBeDisabled();
  });

  it("has a skeleton, an error with Retry, an empty state and a workspace-admin message", () => {
    const view = show({ query: { scopeKey, isLoading: true } });
    expect(screen.getByLabelText("Loading archived skills")).toBeInTheDocument();
    view.unmount();
    const refetch = vi.fn();
    const failed = show({ query: { scopeKey, error: { statusCode: 500 }, refetch } });
    expect(screen.getByText("Couldn't load archived skills.")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(refetch).toHaveBeenCalledTimes(1);
    failed.unmount();
    const empty = show({ query: page([]) });
    expect(screen.getByText("No archived skills")).toBeVisible();
    expect(screen.getByText("Skills you archive appear here. Restoring brings back their versions, labels and history.")).toBeVisible();
    empty.unmount();
    show({ query: { scopeKey, error: { response: { status: 403 } } } });
    expect(screen.getByText("Only workspace admins can see archived skills.")).toBeVisible();
    expect(screen.queryByRole("button", { name: /^Restore/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Back to skills" }));
  });

  it("pages 25 at a time", () => {
    const onPageChange = vi.fn();
    show({ query: { scopeKey, data: { results: [archived()], count: 60, page: 1, pageSize: 25 } }, onPageChange });
    expect(screen.getByText("1–25 of 60")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: /next/i }));
    expect(onPageChange).toHaveBeenCalledWith(2);
  });

  it("does not show another workspace's rows after a scope change", () => {
    show({ scopeKey: "org:other", query: page() });
    expect(screen.queryByText("refund-policy")).not.toBeInTheDocument();
  });
});

// SKILL-TRACES §7.3 "Library column", UX-01, UX-16, §15 D12 (UXF5 commit 2): with `skillTraces` on,
// "Loaded (7 days)" sits beside "Fetched (7 days)" and reads the list serializer's `runtime` field.
describe("Loaded (7 days) with skillTraces on", () => {
  const runtimeSkill = (runtime) => ({ ...skill, runtime });
  const loadedCells = () => [...document.querySelectorAll('.MuiDataGrid-cell[data-field="loaded7d"]')].map((cell) => cell.textContent);
  afterEach(() => setSkillTracesOverride(null));

  it("adds the column beside Fetched, with the count and the short move state", () => {
    setSkillTracesOverride(true);
    api.list.mockReturnValue(listResult({ results: [runtimeSkill({ loaded_7d: 1204, move: { verdict: "watching", to_version: 4 } })] }));
    renderLibrary();
    expect(screen.getAllByText("Fetched (7 days)").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Loaded (7 days)").length).toBeGreaterThan(0);
    expect(loadedCells()).toEqual(["1,204v4 · watching"]);
    expect(screen.getByText("v4 · watching")).toBeInTheDocument();
  });

  it("says it can't record loads yet for null, and shows 0 when consumers can record but none arrived", () => {
    setSkillTracesOverride(true);
    api.list.mockReturnValue(listResult({ results: [
      runtimeSkill({ loaded_7d: null }),
      { ...runtimeSkill({ loaded_7d: 0 }), id: "skill-2", name: "tone" },
    ] }));
    renderLibrary();
    expect(screen.getByLabelText("Can't record loads yet")).toHaveTextContent("—");
    expect(loadedCells()).toEqual(["—", "0"]);
  });

  it("shows a dash, never a count, when the list carries no runtime field", () => {
    setSkillTracesOverride(true);
    renderLibrary();
    expect(screen.getAllByText("Loaded (7 days)").length).toBeGreaterThan(0);
    expect(loadedCells()).toEqual(["—"]);
    expect(screen.queryByText(/loaded by|in use/i)).not.toBeInTheDocument();
  });

  it("moves the value into the stacked card below sm", () => {
    setSkillTracesOverride(true);
    api.list.mockReturnValue(listResult({ results: [runtimeSkill({ loaded_7d: 31 })] }));
    vi.stubGlobal("matchMedia", (query) => ({ matches: /max-width/.test(query), media: query, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} }));
    try {
      renderLibrary();
      const card = within(screen.getByRole("list", { name: "Skills" })).getByRole("listitem");
      expect(card).toHaveTextContent("Loaded (7 days)31");
    } finally { vi.unstubAllGlobals(); }
  });

  it("renders nothing from the runtime column with the flag off", () => {
    setSkillTracesOverride(false);
    api.list.mockReturnValue(listResult({ results: [runtimeSkill({ loaded_7d: 1204 })] }));
    renderLibrary();
    expect(screen.queryByText("Loaded (7 days)")).not.toBeInTheDocument();
    expect(loadedCells()).toEqual([]);
    expect(screen.queryByText("1,204")).not.toBeInTheDocument();
  });
});
