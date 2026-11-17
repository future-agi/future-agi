import { createHash, webcrypto } from "node:crypto";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import PublishDialog from "./PublishDialog";

const api = vi.hoisted(() => ({ publish: vi.fn(), checks: vi.fn(), compare: vi.fn(), refreshCompare: vi.fn(), navigate: vi.fn(), snackbar: vi.fn(), refetch: vi.fn(), save: vi.fn(), currentSkill: null, assistEnabled: true }));
vi.mock("src/api/skills", () => ({
  useSkill: () => ({ data: api.currentSkill ?? skill, refetch: async () => ({ data: skill }) }),
  usePublishSkillDraft: () => ({ mutateAsync: api.publish, isPending: false }),
  useRunSkillChecks: () => ({ mutateAsync: api.checks }),
  useSkillCompare: (...args) => ({ ...api.compare(...args), refetch: api.refreshCompare }),
  useSkillDraft: () => ({ refetch: api.refetch }),
  useSaveSkillDraft: () => ({ mutateAsync: api.save }),
  getSkillErrorCode: (error) => error.code,
}));
vi.mock("src/api/skills/testing", () => ({ useSkillTestingOptions: () => ({ data: { assistEnabled: api.assistEnabled } }) }));
vi.mock("react-router-dom", () => ({ useNavigate: () => api.navigate }));
vi.mock("notistack", () => ({ enqueueSnackbar: api.snackbar }));
// eslint-disable-next-line react/prop-types
vi.mock("src/sections/skills/governance/LabelProtectionDialog", () => ({ default: ({ open }) => (open ? <div role="dialog" aria-label="Manage label protection">Label rules</div> : null) }));
vi.mock("src/sections/skills/components/TextDiff", () => ({ default: ({ fromText, toText }) => <div data-testid="diff">{fromText} → {toText}</div> }));

const skill = { id: "skill-id", name: "sample-skill", labels: [], protectedLabelNames: [], releaseRevision: 3, permissions: { canRunTests: true } };
const draft = { baseVersion: null, files: [{ path: "SKILL.md", content: "Instructions" }], note: "", revision: 1 };
const checks = (errors = 0, warnings = 0) => ({
  summary: { errors, warnings, infos: 0 },
  items: [...(errors ? [{ code: "name_missing", severity: "error", message: "Name is required.", path: "SKILL.md" }] : []), ...(warnings ? [{ code: "description_short", severity: "warning", message: "Description is short.", path: "SKILL.md" }] : [])],
});
const renderDialog = (overrides = {}) => render(<PublishDialog open onClose={vi.fn()} skill={skill} draft={draft} {...overrides} />);

beforeEach(() => {
  vi.clearAllMocks();
  api.currentSkill = null; api.assistEnabled = true;
  vi.stubGlobal("crypto", webcrypto);
  api.checks.mockResolvedValue(checks());
  api.publish.mockResolvedValue({ version: { number: 3 }, unchanged: false });
  api.compare.mockReturnValue({ data: null, isPending: false, isError: false });
  api.refreshCompare.mockImplementation(async () => api.compare());
  api.refetch.mockResolvedValue({ data: { ...draft, revision: 9, files: [{ path: "SKILL.md", content: "Other tab" }] } });
  api.save.mockImplementation(async (payload) => ({ ...payload, revision: payload.revision + 1 }));
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
    await waitFor(() => expect(button).toBeEnabled());
    fireEvent.click(button);
    await waitFor(() => expect(api.publish).toHaveBeenCalledWith({ note: "Reviewed policy", labels: ["production"], acknowledgeWarnings: true, force: false, revision: 1, expectedDigest: expect.stringMatching(/^sha256:/), expectedReleaseRevision: 3, evidenceRunId: null }));
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
    expect(api.publish).toHaveBeenCalledExactlyOnceWith({ note: "", labels: [], acknowledgeWarnings: false, force: false, revision: 1, expectedDigest: expect.stringMatching(/^sha256:/), expectedReleaseRevision: 3, evidenceRunId: null });
    fireEvent.click(force);
    await waitFor(() => expect(api.publish).toHaveBeenNthCalledWith(2, { note: "", labels: [], acknowledgeWarnings: false, force: true, revision: 1, expectedDigest: expect.stringMatching(/^sha256:/), expectedReleaseRevision: 3, evidenceRunId: null }));
  });

  it("collapses unchanged files and states the production move once without summary assistance", async () => {
    api.compare.mockReturnValue({ data: { files: [{ path: 'SKILL.md', status: 'unchanged', toSha256: createHash('sha256').update(draft.files[0].content).digest('hex') }] }, isError: false });
    const released = { ...skill, latestVersion: 2, labels: [{ name: 'production', version: 1, canMove: true }] };
    api.currentSkill = released;
    renderDialog({ skill: released, draft: { ...draft, baseVersion: 2 } });
    await screen.findByText('No file changes since v2');
    expect(screen.queryByRole('list', { name: 'File changes' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Summarize changes' })).not.toBeInTheDocument();
    expect(screen.getByText('Review what changes, then publish.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('checkbox', { name: 'production', exact: true }));
    expect(screen.getAllByText('Reuses v2 and moves production to v2.')).toHaveLength(1);
    expect(screen.getByRole('button', { name: 'Publish and move production' })).toBeEnabled();
  });

  it("renders changed files first and opens their freshly loaded diff", async () => {
    const files = [...draft.files, { path: 'references/policy.md', content: 'New policy' }];
    api.compare.mockReturnValue({ data: { files: [
      { path: "SKILL.md", status: "unchanged", toSha256: createHash('sha256').update(draft.files[0].content).digest('hex') },
      { path: "references/policy.md", status: "modified", fromText: "Old policy", toText: "New policy", toSha256: createHash('sha256').update('New policy').digest('hex') },
    ] }, isError: false });
    renderDialog({ draft: { ...draft, files, baseVersion: 1 } });
    expect(api.compare).toHaveBeenCalledWith("skill-id", 1, "draft");
    expect(await screen.findByTestId("diff")).toHaveTextContent("Old policy → New policy");
    fireEvent.click(screen.getByText("SKILL.md"));
    expect(screen.getByText(/has no changes\./)).toBeInTheDocument();
  });

  it("does not review or publish a cached comparison while the saved revision's refresh is pending", async () => {
    const previous = { files: [{ path: "SKILL.md", status: "unchanged", toSha256: createHash("sha256").update(draft.files[0].content).digest("hex") }] };
    api.compare.mockReturnValue({ data: previous, isFetching: false });
    const onClose = vi.fn();
    const first = { ...draft, baseVersion: 1 };
    const { rerender } = renderDialog({ draft: first, onClose });
    await screen.findByText("No file changes since v1");
    await waitFor(() => expect(screen.getByRole("button", { name: "Publish", exact: true })).toBeEnabled());
    rerender(<PublishDialog open={false} onClose={onClose} skill={skill} draft={first} />);

    const saved = { ...first, revision: 2, files: [{ path: "SKILL.md", content: "New reviewed instructions" }] };
    let finish;
    api.refreshCompare.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
    api.compare.mockReturnValue({ data: previous, isFetching: true, isError: false });
    rerender(<PublishDialog open onClose={onClose} skill={skill} draft={saved} />);
    await screen.findByText("No issues found.");
    expect(api.checks).toHaveBeenCalledTimes(2);
    const publish = screen.getByRole("button", { name: "Publish", exact: true });
    expect(publish).toBeDisabled();
    expect(screen.queryByText(/has no changes\./)).not.toBeInTheDocument();
    expect(screen.queryByTestId("diff")).not.toBeInTheDocument();
    fireEvent.click(publish);
    expect(api.publish).not.toHaveBeenCalled();
    const refreshed = { data: { files: [{ path: "SKILL.md", status: "modified", fromText: "Instructions", toText: saved.files[0].content, toSha256: createHash("sha256").update(saved.files[0].content).digest("hex") }] }, isError: false, isFetching: false };
    api.compare.mockReturnValue(refreshed);
    await act(async () => finish(refreshed));
    expect(await screen.findByTestId("diff")).toHaveTextContent("Instructions → New reviewed instructions");
    expect(publish).toBeEnabled();
    fireEvent.click(publish);
    await waitFor(() => expect(api.publish).toHaveBeenCalledWith(expect.objectContaining({ revision: 2 })));
  });

  it("fails closed on a comparison refresh error despite cached data and permits retry", async () => {
    const cached = { files: [{ path: "SKILL.md", status: "unchanged", toSha256: createHash("sha256").update(draft.files[0].content).digest("hex") }] };
    api.compare.mockReturnValue({ data: cached });
    api.refreshCompare.mockResolvedValueOnce({ data: cached, isError: true });
    renderDialog({ draft: { ...draft, baseVersion: 1 } });
    await screen.findByText("Couldn't load file changes");
    expect(screen.queryByText(/has no changes\./)).not.toBeInTheDocument();
    const button = screen.getByRole("button", { name: "Publish", exact: true });
    expect(button).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(button).toBeEnabled());
    expect(api.refreshCompare).toHaveBeenCalledTimes(2);
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

  it("stops publishing on a draft conflict and reloads the server draft into the editor", async () => {
    api.publish.mockRejectedValueOnce({ code: "draft_conflict", detail: { revision: 8 } });
    const onDraftChange = vi.fn();
    const onClose = vi.fn();
    renderDialog({ onDraftChange, onClose });
    const button = screen.getByRole("button", { name: "Publish", exact: true });
    await waitFor(() => expect(button).toBeEnabled());
    fireEvent.click(button);
    const reload = await screen.findByRole("button", { name: "Reload theirs" });
    expect(screen.getByText(/Nothing was published/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Publish", exact: true })).not.toBeInTheDocument();
    fireEvent.click(reload);
    await waitFor(() => expect(onDraftChange).toHaveBeenCalledWith(expect.objectContaining({ revision: 9, files: [{ path: "SKILL.md", content: "Other tab" }] })));
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Version note")).toBeInTheDocument();
    expect(api.publish).toHaveBeenCalledOnce();
    expect(api.save).not.toHaveBeenCalled();
    expect(api.navigate).not.toHaveBeenCalled();
  });

  it("overwrites only with the freshly fetched revision, then returns to review instead of publishing automatically", async () => {
    api.publish.mockRejectedValueOnce({ code: "draft_conflict", detail: { revision: 8 } });
    const onDraftChange = vi.fn();
    const onClose = vi.fn();
    renderDialog({ onDraftChange, onClose });
    const button = screen.getByRole("button", { name: "Publish", exact: true });
    await waitFor(() => expect(button).toBeEnabled());
    fireEvent.change(screen.getByLabelText("Version note"), { target: { value: "Keep my review note" } });
    fireEvent.click(button);
    fireEvent.click(await screen.findByRole("button", { name: "Overwrite with mine" }));
    await waitFor(() => expect(onDraftChange).toHaveBeenCalled());
    expect(onClose).not.toHaveBeenCalled();
    expect(api.save).toHaveBeenCalledExactlyOnceWith({ files: draft.files, note: "Keep my review note", baseVersion: null, revision: 9 });
    expect(onDraftChange).toHaveBeenCalledWith(expect.objectContaining({ revision: 10, files: draft.files }));
    expect(api.publish).toHaveBeenCalledOnce();
    expect(api.navigate).not.toHaveBeenCalled();
  });

  it("preserves reviewed files if another writer wins again during overwrite", async () => {
    api.publish.mockRejectedValueOnce({ code: "draft_conflict" });
    api.save.mockRejectedValueOnce({ code: "draft_conflict" });
    const onClose = vi.fn();
    const onDraftChange = vi.fn();
    renderDialog({ onClose, onDraftChange });
    const button = screen.getByRole("button", { name: "Publish", exact: true });
    await waitFor(() => expect(button).toBeEnabled());
    fireEvent.click(button);
    fireEvent.click(await screen.findByRole("button", { name: "Overwrite with mine" }));
    await screen.findByText(/Couldn't overwrite this draft/);
    expect(onClose).not.toHaveBeenCalled();
    expect(onDraftChange).not.toHaveBeenCalled();
    api.refetch.mockResolvedValueOnce({ data: { ...draft, revision: 12 } });
    fireEvent.click(screen.getByRole("button", { name: "Overwrite with mine" }));
    await waitFor(() => expect(onDraftChange).toHaveBeenCalled());
    expect(onClose).not.toHaveBeenCalled();
    expect(api.save).toHaveBeenLastCalledWith(expect.objectContaining({ files: draft.files, revision: 12 }));
    expect(api.publish).toHaveBeenCalledOnce();
  });

  it("does not overwrite when fetching the current draft fails", async () => {
    api.publish.mockRejectedValueOnce({ code: "draft_conflict" });
    api.refetch.mockResolvedValueOnce({ isError: true, error: new Error("offline") });
    renderDialog();
    const button = screen.getByRole("button", { name: "Publish", exact: true });
    await waitFor(() => expect(button).toBeEnabled());
    fireEvent.click(button);
    fireEvent.click(await screen.findByRole("button", { name: "Overwrite with mine" }));
    await screen.findByText(/Couldn't load the current draft/);
    expect(api.save).not.toHaveBeenCalled();
    expect(api.publish).toHaveBeenCalledOnce();
  });

  it("returns to detail when the conflicted draft was removed", async () => {
    api.publish.mockRejectedValueOnce({ code: "draft_not_found" });
    api.refetch.mockResolvedValueOnce({ data: null });
    renderDialog();
    const button = screen.getByRole("button", { name: "Publish", exact: true });
    await waitFor(() => expect(button).toBeEnabled());
    fireEvent.click(button);
    fireEvent.click(await screen.findByRole("button", { name: "Reload theirs" }));
    await waitFor(() => expect(api.navigate).toHaveBeenCalledWith("/dashboard/skills/skill-id"));
    expect(api.save).not.toHaveBeenCalled();
  });

  it('does not show or publish a diff for a different server draft, including unchanged files', async () => {
    api.compare.mockReturnValue({ data: { files: [
      { path: 'SKILL.md', status: 'unchanged', toSha256: createHash('sha256').update('Other tab instructions').digest('hex') },
      { path: 'removed.md', status: 'removed', toSha256: null },
    ] } });
    renderDialog({ draft: { ...draft, baseVersion: 1 } });
    fireEvent.click(screen.getByRole('button', { name: 'Attach reviewed run' }));
    fireEvent.change(screen.getByLabelText('Version note'), { target: { value: 'Keep my note' } });
    await screen.findByText(/The server draft changed after this review opened/);
    expect(screen.getByRole('button', { name: 'Publish', exact: true })).toBeDisabled();
    expect(screen.queryByLabelText('File changes')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Review changed draft' }));
    expect(screen.getByRole('button', { name: 'Overwrite with mine' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Close', exact: true }));
    expect(screen.getByLabelText('Version note')).toHaveValue('Keep my note');
    expect(screen.getByText('Test evidence: shared-run')).toBeInTheDocument();
    expect(api.publish).not.toHaveBeenCalled();
  });

  it('blocks publication while a previously matching comparison is refetching', async () => {
    const data = { files: [{ path: 'SKILL.md', status: 'modified', fromText: 'Old', toText: 'Instructions', toSha256: createHash('sha256').update('Instructions').digest('hex') }] };
    api.compare.mockReturnValue({ data, isFetching: false });
    const view = renderDialog({ draft: { ...draft, baseVersion: 1 } });
    const publish = screen.getByRole('button', { name: 'Publish', exact: true });
    await waitFor(() => expect(publish).toBeEnabled());
    api.compare.mockReturnValue({ data, isFetching: true });
    view.rerender(<PublishDialog open onClose={vi.fn()} skill={skill} draft={{ ...draft, baseVersion: 1 }} />);
    expect(publish).toBeDisabled();
    expect(screen.queryByTestId('diff')).not.toBeInTheDocument();
  });

  it("does not run checks when closed", () => {
    renderDialog({ open: false });
    expect(api.checks).not.toHaveBeenCalled();
  });
});

vi.mock("src/sections/skills/tests/TestEvidence", () => ({ default: ({ onSelectRun, selectedRunId }) => <div>Test evidence: {selectedRunId}<button onClick={() => onSelectRun('shared-run')}>Attach reviewed run</button><button onClick={() => onSelectRun(null)}>Remove reviewed run</button></div> }));
vi.mock("src/sections/skills/assist/AssistPanel", () => ({ default: ({ onUseSummary }) => <button onClick={() => onUseSummary('Reviewed AI suggestion')}>Use summary suggestion</button> }));

 it('preserves explicitly attached evidence and note through a release conflict', async () => {
   api.publish.mockRejectedValueOnce({ code: 'release_conflict' });
   renderDialog();
   fireEvent.click(screen.getByRole('button', { name: 'Attach reviewed run' }));
   fireEvent.change(screen.getByLabelText('Version note'), { target: { value: 'Keep this note' } });
   const publish = screen.getByRole('button', { name: 'Publish', exact: true });
   await waitFor(() => expect(publish).toBeEnabled());
   fireEvent.click(publish);
   fireEvent.click(await screen.findByRole('button', { name: 'Reload release state' }));
   await waitFor(() => expect(publish).toBeEnabled());
   expect(screen.getByLabelText('Version note')).toHaveValue('Keep this note');
   expect(screen.getByText('Test evidence: shared-run')).toBeInTheDocument();
   fireEvent.click(publish);
   await waitFor(() => expect(api.publish).toHaveBeenLastCalledWith(expect.objectContaining({ evidenceRunId: 'shared-run', note: 'Keep this note' })));
 });

 it('allows ordinary publication while disabling a member’s protected move', async () => {
   api.currentSkill = { ...skill, protectedLabelNames: ["production"], permissions: { canManageLabelProtection: false } };
   renderDialog({ skill: { ...skill, protectedLabelNames: ['production'], permissions: { canManageLabelProtection: false } } });
   expect(screen.getByRole('checkbox', { name: 'production', exact: true })).toBeDisabled();
   expect(screen.getByText(/You can publish a version/)).toBeInTheDocument();
   const publish = screen.getByRole('button', { name: 'Publish', exact: true });
   await waitFor(() => expect(publish).toBeEnabled());
   fireEvent.click(publish);
   await waitFor(() => expect(api.publish).toHaveBeenCalledWith(expect.objectContaining({ labels: [] })));
 });

 it('uses a summary suggestion only as editable version-note text', async () => {
   renderDialog();
   fireEvent.click(screen.getByRole('button', { name: 'Summarize changes' }));
   fireEvent.click(screen.getByRole('button', { name: 'Use summary suggestion' }));
   expect(screen.getByLabelText('Version note')).toHaveValue('Reviewed AI suggestion');
   fireEvent.change(screen.getByLabelText('Version note'), { target: { value: 'Human reviewed note' } });
   expect(screen.getByLabelText('Version note')).toHaveValue('Human reviewed note');
   expect(api.publish).not.toHaveBeenCalled();
   expect(api.save).not.toHaveBeenCalled();
 });

it('shows no summary assistance, and no explanation of it, when AI drafting is off for the workspace', async () => {
  api.assistEnabled = false;
  renderDialog();
  await screen.findByRole('button', { name: 'Publish', exact: true });
  expect(screen.queryByRole('button', { name: 'Summarize changes' })).not.toBeInTheDocument();
  expect(screen.queryByText('AI drafting is off for this workspace')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Use summary suggestion' })).not.toBeInTheDocument();
});

it('names each label move on the publish action and explains the production consequence', async () => {
  renderDialog({ skill: { ...skill, latestVersion: 2, labels: [{ name: 'production', version: 2, canMove: true }] } });
  expect(screen.getByRole('button', { name: 'Publish', exact: true })).toBeInTheDocument();
  expect(screen.getByRole('status')).toHaveTextContent('Creates v3. Production stays on v2.');
  fireEvent.click(screen.getByRole('checkbox', { name: 'production', exact: true }));
  expect(screen.getByRole('button', { name: 'Publish and move production' })).toBeInTheDocument();
  expect(screen.getByRole('status')).toHaveTextContent('Creates v3 and moves production to v3.');
  fireEvent.click(screen.getByRole('checkbox', { name: 'staging', exact: true }));
  expect(screen.getByRole('button', { name: 'Publish and move labels' })).toBeInTheDocument();
  expect(screen.getByText(/moves production and staging to v3/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('checkbox', { name: 'production', exact: true }));
  expect(screen.getByRole('button', { name: 'Publish and move staging' })).toBeInTheDocument();
});

it('does not promise a new version when the reviewed bytes already match latest', async () => {
  api.compare.mockReturnValue({ data: { files: [{ path: 'SKILL.md', status: 'unchanged', toSha256: createHash('sha256').update(draft.files[0].content).digest('hex') }] }, isError: false });
  renderDialog({ skill: { ...skill, latestVersion: 2, labels: [{ name: 'production', version: 1, canMove: true }] }, draft: { ...draft, baseVersion: 2 } });
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Reuses v2. Production stays on v1.'));
  fireEvent.click(screen.getByRole('checkbox', { name: 'production', exact: true }));
  expect(screen.getByRole('status')).toHaveTextContent('Reuses v2 and moves production to v2.');
});

it('keeps production visible but unavailable while a credential warning is open', async () => {
  api.checks.mockResolvedValue({ summary: { errors: 0, warnings: 1 }, items: [{ severity: 'warning', code: 'secret_suspected', message: 'Possible credential found.', path: 'SKILL.md' }] });
  renderDialog();
  await screen.findByText("Production can't be set while a possible credential warning is open.");
  expect(screen.getByRole('checkbox', { name: 'production', exact: true })).toBeDisabled();
  expect(screen.getByRole('checkbox', { name: 'production', exact: true })).not.toBeChecked();
  fireEvent.click(screen.getByRole('checkbox', { name: /I reviewed the warnings/ }));
  await waitFor(() => expect(screen.getByRole('button', { name: 'Publish', exact: true })).toBeEnabled());
  expect(screen.getByRole('checkbox', { name: 'production', exact: true })).toBeDisabled();
});

describe('UXF-09: the label rule beside the label choice', () => {
  const RULE = "Production is a protected label: only workspace admins can move it, and automation can't.";
  it("states the skill's current rule for a member, with no Change rules link", async () => {
    const member = { ...skill, protectedLabelNames: ['production'], permissions: { canManageLabelProtection: false, canRunTests: true } };
    api.currentSkill = member;
    renderDialog({ skill: member });
    expect(screen.getByText(RULE)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Change rules' })).not.toBeInTheDocument();
    await screen.findByRole('button', { name: 'Publish', exact: true });
    expect(document.body.textContent.replace(/protected labels?/gi, '').match(/protected/i)).toBeNull();
  });
  it('lets an admin open the rules, and publishing still sends the reviewed release revision', async () => {
    const admin = { ...skill, protectedLabelNames: ['production'], permissions: { canManageLabelProtection: true, canRunTests: true } };
    api.currentSkill = admin;
    renderDialog({ skill: admin });
    fireEvent.click(screen.getByRole('button', { name: 'Change rules' }));
    expect(screen.getByRole('dialog', { name: 'Manage label protection' })).toBeInTheDocument();
    const publish = screen.getByRole('button', { name: 'Publish', exact: true });
    await waitFor(() => expect(publish).toBeEnabled());
    fireEvent.click(publish);
    await waitFor(() => expect(api.publish).toHaveBeenCalledWith(expect.objectContaining({ labels: [], expectedReleaseRevision: 3 })));
  });
  it('uses the server copy of the rules over the dialog prop', () => {
    api.currentSkill = { ...skill, protectedLabelNames: ['staging'] };
    renderDialog();
    expect(screen.getByText("Staging is a protected label: only workspace admins can move it, and automation can't.")).toBeInTheDocument();
    expect(screen.queryByText(RULE)).not.toBeInTheDocument();
  });
});
