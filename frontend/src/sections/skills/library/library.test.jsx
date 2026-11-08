import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithRouter } from "src/utils/test-utils";
import SkillsLibraryView from "./SkillsLibraryView";
import NewSkillDialog from "./NewSkillDialog";
import ImportSkillsDialog from "./ImportSkillsDialog";

const api = vi.hoisted(() => ({
  list: vi.fn(), templates: vi.fn(), create: vi.fn(), archive: vi.fn(),
  assistEnabled: true, canMove: true, preview: vi.fn(), importSkills: vi.fn(), download: vi.fn(), refetch: vi.fn(),
}));
vi.mock("src/api/skills", () => ({
  useSkillsList: api.list,
  useSkill: () => ({ data: { ...skill, labels: skill.labels.map((label) => ({ ...label, canMove: api.canMove })), protectedLabelNames: ["production"], permissions: { canArchive: true, canManageLabelProtection: api.canMove }, releaseRevision: 3 } }),
  useSkillTemplates: api.templates,
  useCreateSkill: () => ({ mutateAsync: api.create, isPending: false }),
  useArchiveSkill: () => ({ mutateAsync: api.archive, isPending: false }),
  useImportPreview: () => ({ mutateAsync: api.preview, isPending: false }),
  useImportSkills: () => ({ mutateAsync: api.importSkills, isPending: false }),
  downloadSkillsZip: api.download,
  getSkillErrorCode: (error) => error.code,
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

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
    expect(screen.getByText("82")).toBeInTheDocument();
    expect(screen.getByText("42")).toBeInTheDocument();
    expect(screen.getByText("719")).toBeInTheDocument();
    expect(screen.getByText("17")).toBeInTheDocument();
    expect(screen.getByText("Your drafts")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("link", { name: "support-policy" }));
    expect(window.location.pathname).toBe("/dashboard/skills/skill-1");
  });

  it("shows all three first-run paths and a compact template gallery", () => {
    api.list.mockReturnValue(listResult({ results: [], count: 0, stats: { total: 0, inProduction: 0, fetches7d: 0, myDrafts: 0 } }));
    renderLibrary();
    expect(screen.getByRole("button", { name: "Start from a template" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Import a folder or ZIP" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Write from scratch" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Support policy/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "New skill" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Start from a template" }));
    expect(screen.getByLabelText("Skill templates")).toHaveFocus();
    fireEvent.click(screen.getByRole("button", { name: /Support policy/ }));
    expect(screen.getByRole("combobox", { name: "Start from" })).toHaveTextContent("Support policy");
  });

  it("keeps workspace metrics when search returns no results and clears only filters", () => {
    api.list.mockReturnValue(listResult({ results: [], count: 0 }));
    renderLibrary("?search=missing&tag=support&label=production&page=3&sort=name&order=asc&keep=yes");
    expect(screen.getByText("No skills found")).toBeInTheDocument();
    expect(screen.getByText("719")).toBeInTheDocument();
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
    expect(screen.getByText("Couldn't load skills")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(api.refetch).toHaveBeenCalledOnce();
  });

  it.each(["loading", "error", "empty"])("allows the %s state through the URL", (state) => {
    renderLibrary(`?state=${state}`);
    const text = { loading: "Loading skills", error: "Couldn't load skills", empty: "Give your agents a shared library" };
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
    expect(screen.getByText("No skills on this page")).toBeInTheDocument();
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

it('keeps the new-skill paid entry disabled when assistance is unavailable', async () => {
  api.assistEnabled = false;
  renderCreate();
  fillCreate();
  fireEvent.click(screen.getByRole('button', { name: 'Create skill' }));
  expect(await screen.findByRole('button', { name: 'Draft with AI' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Open editor' })).toBeEnabled();
});
