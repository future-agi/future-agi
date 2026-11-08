import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import SkillEditorView from "./SkillEditorView";

const api = vi.hoisted(() => ({ skill: {}, draft: {}, version: {}, file: vi.fn(), save: vi.fn(), discard: vi.fn(), refetch: vi.fn(), tests: null, assist: null, testSnapshot: null, assistEnabled: true, scope: "org:ws:user" }));
vi.mock("src/api/skills", () => ({
  useSkill: () => api.skill,
  useSkillDraft: () => ({ ...api.draft, refetch: api.refetch }),
  useSkillVersion: () => api.version,
  useSkillFile: (...args) => api.file(...args),
  useSaveSkillDraft: () => ({ mutateAsync: api.save }),
  useDiscardSkillDraft: () => ({ mutateAsync: api.discard }),
  getSkillErrorCode: (error) => error.code,
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("./SkillFileEditor", () => ({ default: ({ file, onChange, readOnly, target }) => <textarea aria-label="File content" data-line={target?.line} value={file.content} readOnly={readOnly} onChange={(event) => onChange(event.target.value)} /> }));
vi.mock("./DraftChecksPanel", () => ({ default: ({ onSelect }) => <button onClick={() => onSelect({ path: "SKILL.md", line: 3 })}>Open check</button> }));
vi.mock("./PublishDialog", () => ({ default: ({ draft, onClose, onDraftChange }) => <div role="dialog" aria-label="Publish review"><pre>{JSON.stringify(draft)}</pre><button onClick={onClose}>Close review</button><button onClick={() => { onDraftChange({ ...draft, revision: 10, files: [{ path: "SKILL.md", content: "Reloaded during publication" }] }); onClose(); }}>Resolve publish conflict</button></div> }));
vi.mock("src/sections/skills/components/SkillMarkdownPreview", () => ({ default: ({ content }) => <div>{content}</div> }));
vi.mock("src/sections/skills/components/FileTree", () => ({ default: ({ files, onSelect, onCreate, onRename, onDelete }) => (
  <nav aria-label="Files">
    {files.map(({ path }) => <button key={path} onClick={() => onSelect(path)}>{path}</button>)}
    <button onClick={() => onCreate("references/new.md")}>New file</button>
    <button onClick={() => onDelete("SKILL.md")}>Delete root</button>
    <button onClick={() => onRename("SKILL.md", "renamed.md")}>Rename root</button>
  </nav>
) }));

const initial = { baseVersion: 1, files: [{ path: "SKILL.md", content: "Saved instructions" }], note: "", revision: 4 };
const mount = (entry = "/dashboard/skills/skill-id/edit") => {
  const tree = () => <MemoryRouter initialEntries={[entry]}>
    <Routes>
      <Route path="/dashboard/skills/:skillId/edit" element={<SkillEditorView />} />
      <Route path="/dashboard/skills/:skillId" element={<div>Skill detail</div>} />
      <Route path="/dashboard/skills/:skillId/compare" element={<div>Compare draft</div>} />
      <Route path="/dashboard/skills" element={<div>Skills library</div>} />
    </Routes>
  </MemoryRouter>;
  const view = render(tree());
  return { ...view, refresh: () => view.rerender(tree()) };
};

beforeEach(() => {
  vi.clearAllMocks();
  api.assistEnabled = true; api.tests = null; api.assist = null; api.testSnapshot = null; api.scope = "org:ws:user";
  api.skill = { data: { id: "skill-id", name: "sample-skill", latestVersion: 1, permissions: { canRunTests: true } } };
  api.draft = { data: initial };
  api.version = { data: { files: [{ path: "SKILL.md" }, { path: "references/policy.md" }] } };
  api.file.mockImplementation((_id, _number, path) => ({ data: { text: path === "SKILL.md" ? "Published instructions" : "Published policy" } }));
  api.save.mockImplementation(async (payload) => ({ ...payload, revision: payload.revision + 1 }));
  api.discard.mockResolvedValue(undefined);
  api.refetch.mockResolvedValue({ data: { ...initial, revision: 9, files: [{ path: "SKILL.md", content: "Other tab" }] } });
});
afterEach(() => { cleanup(); vi.useRealTimers(); });

describe("skill editor", () => {
  it("edits description and instructions through the real route's canonical session", async () => {
    const content = '---\nname: sample-skill\ndescription: Use when reviewing a support request.\nlicense: MIT\n---\nOriginal policy.\n';
    api.draft = { data: { ...initial, files: [{ path: 'SKILL.md', content }] } };
    mount();
    fireEvent.change(await screen.findByLabelText('When should the agent use this skill?'), { target: { value: 'Use when reviewing refunds.' } });
    fireEvent.change(screen.getByLabelText('Instructions'), { target: { value: 'Reviewed policy.\n' } });
    fireEvent.click(screen.getByRole('tab', { name: 'Source' }));
    const expected = '---\nname: sample-skill\ndescription: "Use when reviewing refunds."\nlicense: MIT\n---\nReviewed policy.\n';
    expect(screen.getByLabelText('File content')).toHaveValue(expected);
    fireEvent.click(screen.getByRole('button', { name: 'Publish', exact: true }));
    await screen.findByRole('dialog', { name: 'Publish review' });
    expect(api.save).toHaveBeenCalledExactlyOnceWith({ ...initial, files: [{ path: 'SKILL.md', content: expected }] });
    expect(api.tests.draft.files[0].content).toBe(expected);
  });

  it("waits for stale cached draft revalidation before latching the editing snapshot", async () => {
    api.draft = { data: initial, isFetching: true };
    const view = mount();
    expect(screen.queryByLabelText("File content")).not.toBeInTheDocument();
    expect(screen.queryByText("Saved")).not.toBeInTheDocument();
    api.draft = { data: { ...initial, revision: 5, files: [{ path: "SKILL.md", content: "Newer saved instructions" }] }, isFetching: false };
    view.refresh();
    expect(await screen.findByLabelText("File content")).toHaveValue("Newer saved instructions");
    fireEvent.click(screen.getByRole("button", { name: "Publish", exact: true }));
    await screen.findByRole("dialog", { name: "Publish review" });
    expect(api.save).toHaveBeenCalledExactlyOnceWith(api.draft.data);
  });

  it("does not initialize from stale cache when revalidation is paused or fails", async () => {
    api.draft = { data: initial, isFetching: false, isPaused: true };
    const view = mount();
    expect(screen.queryByLabelText("File content")).not.toBeInTheDocument();
    api.draft = { data: initial, isFetching: false, isError: true };
    view.refresh();
    expect(screen.getByText("Couldn't load your draft")).toBeInTheDocument();
    expect(screen.queryByLabelText("File content")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(api.refetch).toHaveBeenCalledOnce();
  });

  it("resolves a stale cached absence before loading a published version", async () => {
    api.draft = { data: null, isFetching: true };
    const view = mount();
    expect(api.file).not.toHaveBeenCalled();
    expect(screen.queryByLabelText("File content")).not.toBeInTheDocument();
    api.draft = { data: initial, isFetching: false };
    view.refresh();
    expect(await screen.findByLabelText("File content")).toHaveValue("Saved instructions");
    expect(api.file).not.toHaveBeenCalled();
  });

  it("saves local edits before opening the publish review", async () => {
    mount();
    fireEvent.change(await screen.findByLabelText("File content"), { target: { value: "My edits" } });
    fireEvent.click(screen.getByRole("button", { name: "Publish", exact: true }));
    await screen.findByRole("dialog", { name: "Publish review" });
    expect(api.save).toHaveBeenCalledExactlyOnceWith({ ...initial, files: [{ path: "SKILL.md", content: "My edits" }] });
    expect(screen.getByRole("dialog", { name: "Publish review" })).toHaveTextContent('"revision":5');
    expect(screen.getByLabelText("File content")).toHaveAttribute("readonly");
  });

  it("adopts conflict recovery from the publish dialog before saving or reviewing again", async () => {
    mount();
    await screen.findByLabelText("File content");
    fireEvent.click(screen.getByRole("button", { name: "Publish", exact: true }));
    fireEvent.click(await screen.findByRole("button", { name: "Resolve publish conflict" }));
    expect(screen.getByLabelText("File content")).toHaveValue("Reloaded during publication");
    expect(screen.getByRole("status")).toHaveTextContent("Saved");
    fireEvent.click(screen.getByRole("button", { name: "Publish", exact: true }));
    await screen.findByRole("dialog", { name: "Publish review" });
    expect(api.save).toHaveBeenLastCalledWith(expect.objectContaining({ revision: 10, files: [{ path: "SKILL.md", content: "Reloaded during publication" }] }));
    expect(screen.getByRole("dialog", { name: "Publish review" })).toHaveTextContent('"revision":11');
  });

  it("checks the server revision before review even when local files are unchanged", async () => {
    api.save.mockRejectedValueOnce({ code: "draft_conflict", detail: { revision: 8 } });
    mount();
    await screen.findByLabelText("File content");
    fireEvent.click(screen.getByRole("button", { name: "Publish", exact: true }));
    await screen.findByRole("button", { name: "Reload theirs" });
    expect(api.save).toHaveBeenCalledExactlyOnceWith(initial);
    expect(screen.queryByRole("dialog", { name: "Publish review" })).not.toBeInTheDocument();
  });

  it("can retry a failed pre-publish save even without local file changes", async () => {
    api.save.mockRejectedValueOnce(new Error("offline"));
    mount();
    await screen.findByLabelText("File content");
    fireEvent.click(screen.getByRole("button", { name: "Publish", exact: true }));
    fireEvent.click(await screen.findByRole("button", { name: "Retry", exact: true }));
    await waitFor(() => expect(api.save).toHaveBeenCalledTimes(2));
    expect(screen.getByRole("status")).toHaveTextContent("Saved");
  });

  it("opens the conflict dialog and reloads their latest content", async () => {
    api.save.mockRejectedValueOnce({ code: "draft_conflict", detail: { revision: 8 } });
    mount();
    fireEvent.change(await screen.findByLabelText("File content"), { target: { value: "My edits" } });
    fireEvent.click(screen.getByRole("button", { name: "Publish", exact: true }));
    fireEvent.click(await screen.findByRole("button", { name: "Reload theirs" }));
    await waitFor(() => expect(screen.getByLabelText("File content")).toHaveValue("Other tab"));
    expect(api.refetch).toHaveBeenCalledOnce();
    expect(screen.queryByRole("dialog", { name: "Publish review" })).not.toBeInTheDocument();
  });

  it("overwrites with local files and the freshly fetched server revision", async () => {
    api.save.mockRejectedValueOnce({ code: "draft_conflict", detail: { revision: 8 } });
    mount();
    fireEvent.change(await screen.findByLabelText("File content"), { target: { value: "My edits" } });
    fireEvent.click(screen.getByRole("button", { name: "Publish", exact: true }));
    fireEvent.click(await screen.findByRole("button", { name: "Overwrite with mine" }));
    await waitFor(() => expect(api.save).toHaveBeenNthCalledWith(2, expect.objectContaining({ revision: 9, files: [{ path: "SKILL.md", content: "My edits" }] })));
    expect(screen.getByLabelText("File content")).toHaveValue("My edits");
  });

  it("initializes an absent draft from all latest-version files with revision zero", async () => {
    api.draft = { data: null };
    mount();
    expect(await screen.findByLabelText("File content")).toHaveValue("Published instructions");
    fireEvent.click(screen.getByRole("button", { name: "Publish", exact: true }));
    await waitFor(() => expect(api.save).toHaveBeenCalledWith({ baseVersion: 1, revision: 0, note: "", files: [
      { path: "SKILL.md", content: "Published instructions" },
      { path: "references/policy.md", content: "Published policy" },
    ] }));
    expect(screen.getByRole("dialog", { name: "Publish review" })).toBeInTheDocument();
  });

  it("does not allow editing a partially loaded version", async () => {
    api.draft = { data: null };
    api.file.mockImplementation((_id, _number, path) => path === "SKILL.md" ? { data: { text: "Published instructions" } } : { isError: true, refetch: vi.fn(), tests: null, assist: null, testSnapshot: null, assistEnabled: true, scope: "org:ws:user" });
    mount();
    await screen.findByText("Couldn't load references/policy.md");
    expect(screen.queryByLabelText("File content")).not.toBeInTheDocument();
    expect(api.save).not.toHaveBeenCalled();
  });

  it("starts an unpublished skill with an empty root and prevents root removal or rename", async () => {
    api.draft = { data: null };
    api.skill.data.latestVersion = null;
    mount();
    expect(await screen.findByLabelText("File content")).toHaveValue("");
    fireEvent.click(screen.getByRole("button", { name: "Delete root" }));
    fireEvent.click(screen.getByRole("button", { name: "Rename root" }));
    expect(screen.getByRole("button", { name: "SKILL.md", exact: true })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("File content"), { target: { value: "First draft" } });
    fireEvent.click(screen.getByRole("button", { name: "Publish", exact: true }));
    await waitFor(() => expect(api.save).toHaveBeenCalledWith(expect.objectContaining({ revision: 0, baseVersion: null })));
  });

  it("opens a check's file and line from Markdown preview", async () => {
    mount();
    await screen.findByLabelText("File content");
    fireEvent.click(screen.getByRole("tab", { name: "Preview" }));
    expect(screen.getByLabelText("File content")).not.toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Open check" }));
    expect(screen.getByLabelText("File content")).toHaveAttribute("data-line", "3");
    expect(screen.getByRole("tab", { name: "Source" })).toHaveAttribute("aria-selected", "true");
  });

  it("blocks navigation and document unload for unsaved edits", async () => {
    mount();
    fireEvent.change(await screen.findByLabelText("File content"), { target: { value: "Unsaved" } });
    const event = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Back to skill" }));
    await screen.findByText("Leave with unsaved changes?");
    expect(screen.queryByText("Skill detail")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Leave without saving" }));
    await screen.findByText("Skill detail");
  });

  it("discards only after confirmation and cancels a pending autosave", async () => {
    mount();
    const editor = await screen.findByLabelText("File content");
    vi.useFakeTimers();
    fireEvent.change(editor, { target: { value: "Unsaved" } });
    fireEvent.click(screen.getByRole("button", { name: "Discard draft", exact: true }));
    await act(async () => vi.advanceTimersByTimeAsync(1500));
    expect(api.save).not.toHaveBeenCalled();
    expect(api.discard).not.toHaveBeenCalled();
    const buttons = screen.getAllByRole("button", { name: "Discard draft", exact: true });
    await act(async () => fireEvent.click(buttons[buttons.length - 1]));
    expect(api.discard).toHaveBeenCalledOnce();
    expect(screen.getByText("Skill detail")).toBeInTheDocument();
  });

  it("saves an unsaved stale draft before navigating to compare", async () => {
    api.skill.data.latestVersion = 2;
    mount();
    fireEvent.change(await screen.findByLabelText("File content"), { target: { value: "Compare these edits" } });
    fireEvent.click(screen.getByRole("button", { name: "Compare", exact: true }));
    await screen.findByText("Compare draft");
    expect(api.save).toHaveBeenCalledWith(expect.objectContaining({ files: [{ path: "SKILL.md", content: "Compare these edits" }] }));
  });

  it("retains unsaved edits across failed background skill, draft, and version refetches", async () => {
    api.draft = { data: null };
    const view = mount();
    fireEvent.change(await screen.findByLabelText("File content"), { target: { value: "Do not lose this" } });
    api.skill = { ...api.skill, isError: true };
    api.draft = { data: null, isError: true };
    api.version = { ...api.version, isError: true };
    view.refresh();
    expect(screen.getByLabelText("File content")).toHaveValue("Do not lose this");
    expect(screen.queryByText("Couldn't load this skill")).not.toBeInTheDocument();
  });

  it("does not adopt a refetched draft while the editor has local changes", async () => {
    const view = mount();
    fireEvent.change(await screen.findByLabelText("File content"), { target: { value: "Keep mine" } });
    api.draft = { data: { ...initial, files: [{ path: "SKILL.md", content: "Someone else" }], revision: 8 } };
    view.refresh();
    expect(screen.getByLabelText("File content")).toHaveValue("Keep mine");
    fireEvent.click(screen.getByRole("button", { name: "Publish", exact: true }));
    await waitFor(() => expect(api.save).toHaveBeenCalledWith(expect.objectContaining({ revision: 4, files: [{ path: "SKILL.md", content: "Keep mine" }] })));
  });

  it.each(["loading", "error", "empty"])("exposes the %s preview state without editing", async (state) => {
    mount(`/dashboard/skills/skill-id/edit?state=${state}`);
    expect(screen.queryByLabelText("File content")).not.toBeInTheDocument();
    if (state === "error") expect(screen.getByText("Couldn't load this skill")).toBeInTheDocument();
    if (state === "empty") expect(screen.getByText("No skill to edit")).toBeInTheDocument();
    expect(api.save).not.toHaveBeenCalled();
  });
});

vi.mock("src/sections/skills/tests/EditorTestsPanel", () => ({ default: (props) => {
  api.tests = props;
  return <div>Editor tests<button onClick={async () => { api.testSnapshot = await props.onSave(); }}>Prepare test snapshot</button><button onClick={() => props.onImproveSelection({ sourceRunId: 'failed-run', caseId: 'case-id' })}>Improve selection</button></div>;
} }));
vi.mock("src/sections/skills/assist/AssistPanel", () => ({ default: (props) => {
  api.assist = props;
  return <div>Draft assistance: {props.kind}<button onClick={() => props.onApplied({ ...props.draft, revision: 12, files: [{ path: 'SKILL.md', content: 'Reviewed proposal' }] })}>Apply reviewed proposal</button></div>;
} }));
vi.mock("src/api/skills/testing", () => ({ useSkillV11Scope: () => ({ identity: api.scope }), useSkillTestingOptions: () => ({ data: { assistEnabled: api.assistEnabled } }) }));

 it('composes live dirty state and flushes an older save before Tests consumes a snapshot', async () => {
   let finish;
   api.save.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
   mount('/dashboard/skills/skill-id/edit?tab=tests');
   await screen.findByLabelText('File content');
   vi.useFakeTimers();
   fireEvent.change(screen.getByLabelText('File content'), { target: { value: 'Older edit' } });
   await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
   fireEvent.change(screen.getByLabelText('File content'), { target: { value: 'Latest edit' } });
   expect(api.tests.isDirty).toBe(true);
   fireEvent.click(screen.getByRole('button', { name: 'Prepare test snapshot' }));
   await act(async () => { finish({ ...initial, revision: 5, files: [{ path: 'SKILL.md', content: 'Older edit' }] }); });
   expect(api.save).toHaveBeenLastCalledWith(expect.objectContaining({ revision: 5, files: [{ path: 'SKILL.md', content: 'Latest edit' }] }));
   expect(api.testSnapshot).toMatchObject({ revision: 6, files: [{ path: 'SKILL.md', content: 'Latest edit' }] });
   expect(api.tests.isDirty).toBe(false);
 });

 it('opens description assistance from an immutable run link without starting work', async () => {
   mount('/dashboard/skills/skill-id/edit?assist=description&run=failed-run&case=case-id');
   await screen.findByText('Draft assistance: description');
   expect(api.assist.sourceRunId).toBe('failed-run');
   expect(api.assist.draft.revision).toBe(4);
   expect(api.save).not.toHaveBeenCalled();
   fireEvent.click(screen.getByRole('button', { name: 'Apply reviewed proposal' }));
   expect(screen.getByLabelText('File content')).toHaveValue('Reviewed proposal');
   expect(api.tests.draft.revision).toBe(12);
   expect(api.tests.isDirty).toBe(false);
 });

 it('forwards the selected failing run from editor Tests into assistance', async () => {
   mount('/dashboard/skills/skill-id/edit?tab=tests');
   await screen.findByRole('button', { name: 'Improve selection' });
   fireEvent.click(screen.getByRole('button', { name: 'Improve selection' }));
   expect(api.assist.kind).toBe('description');
   expect(api.assist.sourceRunId).toBe('failed-run');
   expect(api.save).not.toHaveBeenCalled();
 });

 it('reinitializes the session rather than retaining private local files across a scope switch', async () => {
   const view = mount();
   await screen.findByLabelText('File content');
   fireEvent.change(screen.getByLabelText('File content'), { target: { value: 'Private local edit' } });
   api.scope = 'org:other-ws:other-user';
   api.draft = { data: { ...initial, files: [{ path: 'SKILL.md', content: 'New workspace draft' }] } };
   view.refresh();
   await waitFor(() => expect(screen.getByLabelText('File content')).toHaveValue('New workspace draft'));
   expect(api.tests.isDirty).toBe(false);
 });
