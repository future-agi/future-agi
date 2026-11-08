import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { HelmetProvider } from "react-helmet-async";
import ContextLibraryView, { ContextAuthoringProvider } from "../../contexts/ContextLibraryView";
import ContextDetailView from "../../contexts/ContextDetailView";
import ArchivedSkillsView from "../../library/ArchivedSkillsView";
import CompatibilityInventory from "../../library/CompatibilityInventory";
import ContextLibraryPage from "src/pages/dashboard/skills/contexts/ContextLibraryPage";
import ContextDetailPage from "src/pages/dashboard/skills/contexts/ContextDetailPage";
import ContextEditorPage from "src/pages/dashboard/skills/contexts/ContextEditorPage";
import ArchivedSkillsPage from "src/pages/dashboard/skills/ArchivedSkillsPage";

const scopeKey = "org:workspace:author:context";
const query = { scopeKey, data: { results: [{ id: "context", name: "support-agent", description: "Policy", latestVersion: 6, labels: [{ name: "production", version: 3 }], hasDraft: true }], count: 1, page: 1, pageSize: 25 } };
const detail = { scopeKey, query: { scopeKey, data: { context: query.data.results[0] } }, versionQuery: { scopeKey, data: { version: { id: "version-four", number: 4, digest: "sha256:four" }, descriptor: { description: "Purpose from viewed v4", links: [{ skillId: "child", mount: "skills/refunds/", version: 8, requested: { label: "production" }, digest: "sha256:child" }] }, files: [{ path: "AGENTS.md", origin: { kind: "owned" } }, { path: "skills/refunds/SKILL.md", origin: { kind: "linked", versionId: "child-version" } }, { path: ".futureagi/context.json", origin: { kind: "generated" } }] } }, fileQuery: { scopeKey, data: { path: "AGENTS.md", versionId: "version-four", text: "---\nnot: frontmatter\n---\n# Standing policy" } }, versions: [{ id: "version-four", number: 4 }], onEditViewed: vi.fn(), onSelectPath: vi.fn() };
afterEach(() => { cleanup(); vi.clearAllMocks(); });

describe("scope-safe context library", () => {
  it("shows independent production/latest/draft and leaves unobserved delivery unknown", () => {
    render(<ContextLibraryView scopeKey={scopeKey} query={query} />);
    expect(screen.getByText("v3")).toBeVisible(); expect(screen.getByText("v6")).toBeVisible(); expect(screen.getByText("Your draft")).toBeVisible(); expect(screen.getByText("Not observed")).toBeVisible();
  });
  it("does not expose old-scope rows or counts while the scoped query changes", () => {
    render(<ContextLibraryView scopeKey="other-scope" query={query} />);
    expect(screen.queryByText("support-agent")).not.toBeInTheDocument();
    expect(screen.queryByText(/1–1/)).not.toBeInTheDocument();
  });
  it("resets pagination on a new search without storing private data in the browser", () => {
    const change = vi.fn(); render(<ContextLibraryView scopeKey={scopeKey} query={query} filters={{ page: 4, tag: "policy" }} onFilterChange={change} />);
    fireEvent.change(screen.getByLabelText("Search agent contexts"), { target: { value: "refund" } });
    expect(change).toHaveBeenCalledWith({ page: 1, tag: "policy", search: "refund" });
  });
  it.each(["loading", "error", "empty"])("supports the %s review fixture", (state) => {
    render(<ContextLibraryView scopeKey={scopeKey} query={query} state={state} />);
    expect(screen.queryByText("support-agent")).not.toBeInTheDocument();
    if (state === "error") expect(screen.getByText("Agent contexts unavailable")).toBeVisible();
    if (state === "empty") expect(screen.getByText("No agent contexts")).toBeVisible();
  });
});

describe("immutable detail and recovery", () => {
  it("reads purpose and AGENTS bytes from the selected version without YAML stripping", () => {
    render(<ContextDetailView {...detail} />);
    expect(screen.getByText("Purpose from viewed v4")).toBeVisible();
    expect(screen.getByLabelText("AGENTS.md read-only source").textContent).toBe(detail.fileQuery.data.text);
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Edit viewed v4" }));
    expect(detail.onEditViewed).toHaveBeenCalledWith(detail.versionQuery.data.version);
  });
  it("preserves an existing draft rather than silently editing a different origin", () => {
    const preserve = vi.fn(); render(<ContextDetailView {...detail} query={{ ...detail.query, data: { ...detail.query.data, ownDraft: { revision: 12, baseVersion: 6 } } }} onPreserveDraft={preserve} onResumeDraft={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Edit viewed v4" }));
    expect(preserve).toHaveBeenCalledWith(detail.versionQuery.data.version);
    expect(detail.onEditViewed).not.toHaveBeenCalled();
  });
  it("refuses stale file bytes from a different immutable version", () => {
    render(<ContextDetailView {...detail} fileQuery={{ scopeKey, data: { ...detail.fileQuery.data, versionId: "version-six", text: "Wrong-version private body" } }} />);
    expect(screen.queryByText("Wrong-version private body")).not.toBeInTheDocument();
  });
  it("shows linked source identity and generated files read-only", () => {
    const view = render(<ContextDetailView {...detail} selectedPath="skills/refunds/SKILL.md" fileQuery={{ scopeKey, data: { path: "skills/refunds/SKILL.md", versionId: "version-four", text: "Child instructions" } }} />);
    expect(screen.getByText(/Read-only linked content/)).toHaveTextContent("child-version");
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    view.rerender(<ContextDetailView {...detail} selectedPath=".futureagi/context.json" />);
    expect(screen.getByText(/Generated descriptor · read-only/)).toBeVisible();
  });
  it("never labels absent observations as delivered", () => {
    render(<ContextDetailView {...detail} />);
    expect(screen.getByText(/Installed: Not observed · Delivered: Not observed/)).toBeVisible();
  });
  it("keeps a blocked compatibility member in the inventory", () => {
    render(<CompatibilityInventory items={[{ key: "binary", root: "assets/", kind: "skill", blocked: true, blockers: ["Binary dependency"] }]} closure={{ fileCount: 201, totalBytes: 500, linkCount: 1, blocked: true }} />);
    expect(screen.getByText("Binary dependency")).toBeVisible();
    expect(screen.getByText(/201\/200 files/)).toBeVisible();
    expect(screen.getByRole("checkbox")).toBeDisabled();
  });
  it("does not present recovery on an unauthorized archive list error", () => {
    render(<ArchivedSkillsView scopeKey={scopeKey} query={{ scopeKey, error: { status: 403 } }} />);
    expect(screen.getByText("Archived skills unavailable")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Review recovery" })).not.toBeInTheDocument();
  });
});

describe("page wrappers require scoped integration", () => {
  it.each([ContextLibraryPage, ContextDetailPage, ContextEditorPage, ArchivedSkillsPage])("never falls back to skill-only operations without bindings", (Page) => {
    render(<HelmetProvider><MemoryRouter><Page /></MemoryRouter></HelmetProvider>);
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Publish" })).not.toBeInTheDocument();
  });
  it("does not render an old-scope private editor node after a scope switch", () => {
    render(<HelmetProvider><MemoryRouter><ContextAuthoringProvider scopeKey="new-principal" bindings={{ editor: { scopeKey, element: <div>Private previous draft</div> } }}><ContextEditorPage /></ContextAuthoringProvider></MemoryRouter></HelmetProvider>);
    expect(screen.queryByText("Private previous draft")).not.toBeInTheDocument();
    expect(screen.getByText("Context editor unavailable")).toBeVisible();
  });
  it("composes a scoped query and remounts private state when the principal changes", () => {
    const view = render(<HelmetProvider><MemoryRouter><ContextAuthoringProvider scopeKey={scopeKey} bindings={{ library: { query } }}><ContextLibraryPage /></ContextAuthoringProvider></MemoryRouter></HelmetProvider>);
    expect(screen.getByText("support-agent")).toBeVisible();
    view.rerender(<HelmetProvider><MemoryRouter><ContextAuthoringProvider scopeKey="new-principal" bindings={{ library: { query } }}><ContextLibraryPage /></ContextAuthoringProvider></MemoryRouter></HelmetProvider>);
    expect(screen.queryByText("support-agent")).not.toBeInTheDocument();
  });
});
