import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import HotfixHeadReview from "../../editor/HotfixHeadReview";
import RecoverableDraftChanges from "../../editor/RecoverableDraftChanges";
import ContextLinksPanel from "../../contexts/ContextLinksPanel";
import ContextSafetyDialog from "../../contexts/ContextSafetyDialog";
import ContextImportDialog from "../../contexts/ContextImportDialog";
import MigrationPlanPanel from "../../library/MigrationPlanPanel";

const scopeKey = "org:workspace:author:resource";
const digest = `sha256:${"a".repeat(64)}`;
const recoveryDigest = `sha256:${'b'.repeat(64)}`;
const future = "2099-01-01T00:00:00Z";
const deferred = () => { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; };
const button = (name) => screen.getByRole("button", { name, exact: true });
const click = (name) => fireEvent.click(button(name));
const choose = (name, option) => { fireEvent.mouseDown(screen.getByRole("combobox", { name })); fireEvent.click(screen.getByRole("option", { name: option, exact: true })); };
const check = (name) => fireEvent.click(screen.getByRole("checkbox", { name }));
const draft = () => {
  const value = { baseVersion: 4, revision: 12, digest, recoveryDigest, files: [{ path: "AGENTS.md", content: "Original standing rules" }], description: "Purpose", note: "", dirty: false, editSequence: 0, contextSpec: { links: [{ skillId: "child", selector: { label: "production" } }] }, resolvedLock: { links: [{ skillId: "child", mount: "skills/refunds/", requested: { label: "production" }, version: 8, digest }] } };
  value.flush = vi.fn(async () => value);
  value.mutateDraft = vi.fn(async (operation) => operation(value));
  value.reload = vi.fn();
  return value;
};
const headPreview = { review: { subjectId: "resource", draftRevision: 12, candidateDigest: digest, baseVersion: 4, expectedHeadVersion: 6, reviewFingerprint: "head-fingerprint" }, originChanges: [], headChanges: [] };
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("historical head review", () => {
  it("reviews origin v4 and head v6 without replacing bytes, then acknowledges exact preconditions", async () => {
    const session = draft(); const preview = vi.fn(async () => headPreview); const acknowledge = vi.fn(async () => ({ ...session, revision: 13 }));
    render(<HotfixHeadReview scopeKey={scopeKey} session={session} onPreview={preview} onAcknowledge={acknowledge} />);
    click("Review against current latest");
    await screen.findByText(/v4-derived/);
    expect(session.flush).toHaveBeenCalledOnce();
    check("I reviewed both comparisons and the new-latest consequence.");
    click("Acknowledge reviewed head");
    await waitFor(() => expect(acknowledge).toHaveBeenCalledOnce());
    expect(acknowledge.mock.calls[0][0]).toEqual({ revision: 12, expectedDigest: digest, expectedHeadVersion: 6, reviewFingerprint: "head-fingerprint" });
    expect(session.files[0].content).toBe("Original standing rules");
    expect(session.mutateDraft).toHaveBeenCalledOnce();
  });
  it("does not acknowledge a head when either full comparison is missing", async () => {
    render(<HotfixHeadReview scopeKey={scopeKey} session={draft()} onPreview={async () => ({ review: headPreview.review })} onAcknowledge={vi.fn()} />);
    click("Review against current latest"); await screen.findByText(/v4-derived/);
    expect(screen.getAllByText(/complete file comparison is unavailable/)).toHaveLength(2);
    expect(screen.getByRole("checkbox")).toBeDisabled(); expect(button("Acknowledge reviewed head")).toBeDisabled();
    expect(screen.queryByText("No file changes.")).not.toBeInTheDocument();
  });
  it("a failed flush cannot preview a runnable snapshot", async () => {
    const session = draft(); session.flush.mockResolvedValue(null); const preview = vi.fn();
    render(<HotfixHeadReview scopeKey={scopeKey} session={session} onPreview={preview} />);
    click("Review against current latest");
    await screen.findByText(/could not be confirmed/);
    expect(preview).not.toHaveBeenCalled();
  });
  it("immediately invalidates head acknowledgment after any local mutation", async () => {
    const session = draft(); const props = { scopeKey, session, onPreview: async () => headPreview, onAcknowledge: vi.fn() };
    const view = render(<HotfixHeadReview {...props} />);
    click("Review against current latest"); await screen.findByText(/v4-derived/);
    check("I reviewed both comparisons and the new-latest consequence.");
    view.rerender(<HotfixHeadReview {...props} session={{ ...session, dirty: true, editSequence: 1 }} />);
    expect(button("Acknowledge reviewed head")).toBeDisabled();
  });
  it("ignores a delayed old-scope preview rather than filling the new view", async () => {
    const pending = deferred(); const view = render(<HotfixHeadReview scopeKey={scopeKey} session={draft()} onPreview={() => pending.promise} />);
    click("Review against current latest");
    view.rerender(<HotfixHeadReview scopeKey="other:scope" session={draft()} onPreview={vi.fn()} />);
    await act(async () => pending.resolve(headPreview));
    expect(screen.queryByText(/v4-derived/)).not.toBeInTheDocument();
  });
});

describe("accepted dependency review", () => {
  const availableSkills = [{ id: "child", name: "refunds", kind: "skill", latestVersion: 9 }, { id: "pin", name: "chargebacks", kind: "skill", latestVersion: 2 }, { id: "parent", name: "other-context", kind: "agent_context", latestVersion: 1 }];
  it("keeps accepted v8 while v9 is available and sends only an explicit refresh", async () => {
    const session = draft(); const preview = vi.fn(async () => ({ revision: 12, fingerprint: "plan", candidateDigest: digest, changes: [], checks: { summary: { errors: 0 } } })); const apply = vi.fn(async () => ({ ...session, revision: 13 }));
    render(<ContextLinksPanel scopeKey={scopeKey} session={session} availableSkills={availableSkills} upstream={{ child: { version: 9 } }} onPreview={preview} onApply={apply} />);
    expect(screen.getByText("v9 available; this draft still includes v8.")).toBeVisible();
    expect(preview).not.toHaveBeenCalled();
    check("Review update for refunds"); click("Preview dependency changes");
    await screen.findByText(/Compiled candidate/);
    expect(preview).toHaveBeenCalledWith({ revision: 12, links: session.contextSpec.links, refreshSkillIds: ["child"] });
    check("Accept these exact resolved versions and selector policies."); click("Apply selected updates to draft");
    await waitFor(() => expect(apply).toHaveBeenCalledOnce());
    expect(apply.mock.calls[0][0].expectedFingerprint).toBe("plan");
    expect(session.mutateDraft).toHaveBeenCalledOnce();
  });
  it("excludes contexts from the picker and exact pins from refresh", () => {
    const session = draft(); session.contextSpec.links[0].selector = { version: 8 };
    render(<ContextLinksPanel scopeKey={scopeKey} session={session} availableSkills={availableSkills} />);
    expect(screen.queryByRole("checkbox", { name: /Review update/ })).not.toBeInTheDocument();
    fireEvent.mouseDown(screen.getByRole("combobox", { name: "Published leaf skill" }));
    expect(screen.queryByRole("option", { name: "other-context" })).not.toBeInTheDocument();
  });
  it("blocks applying a stale revision or failed compiled closure", async () => {
    const session = draft(); const props = { scopeKey, session, onPreview: async () => ({ revision: 12, fingerprint: "plan", checks: { summary: { errors: 1 } } }), onApply: vi.fn() };
    const view = render(<ContextLinksPanel {...props} />);
    click("Preview dependency changes"); await screen.findByText(/blocking checks/);
    check("Accept these exact resolved versions and selector policies.");
    expect(button("Apply selected updates to draft")).toBeDisabled();
    view.rerender(<ContextLinksPanel {...props} session={{ ...session, revision: 13 }} />);
    expect(screen.getByText(/draft changed/)).toBeVisible();
  });
  it("is entirely read-only for a retained release", () => {
    render(<ContextLinksPanel scopeKey={scopeKey} session={draft()} readOnly />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.getByText(/Included v8/)).toBeVisible();
  });
});

describe("recoverable draft choices", () => {
  it("requires explicit preservation and a successful flush before suspension", async () => {
    const session = draft(); const suspend = vi.fn(async () => ({ checkpointId: "checkpoint", expiresAt: future }));
    render(<RecoverableDraftChanges scopeKey={scopeKey} session={session} onSuspend={suspend} targetVersion={4} />);
    expect(button("Preserve and suspend draft")).toBeDisabled();
    check("Save a recovery checkpoint and suspend my current draft."); click("Preserve and suspend draft");
    await waitFor(() => expect(suspend).toHaveBeenCalledOnce());
    expect(suspend.mock.calls[0][0]).toEqual({ revision: 12, expectedDigest: recoveryDigest, reason: "switch" });
  });
  it("does not suspend when a server conflict prevents preserving local work", async () => {
    const session = draft(); session.flush.mockResolvedValue(null); const suspend = vi.fn();
    render(<RecoverableDraftChanges scopeKey={scopeKey} session={session} onSuspend={suspend} />);
    check("Save a recovery checkpoint and suspend my current draft."); click("Preserve and suspend draft");
    await screen.findByText(/reviewed state changed/); expect(suspend).not.toHaveBeenCalled();
  });
  it("shows checkpoint expiry and refuses an expired restore", () => {
    render(<RecoverableDraftChanges scopeKey={scopeKey} session={draft()} hasLiveDraft={false} checkpoints={[{ id: "old", draftRevision: 2, reason: "switch", digest, expiresAt: "2000-01-01T00:00:00Z" }]} onRestore={vi.fn()} />);
    choose("Recovery checkpoint", "r2 · switch");
    expect(screen.getByText(/checkpoint expired/)).toBeVisible();
    expect(button("Restore private checkpoint")).toBeDisabled();
  });
  it("restores the selected checkpoint under CAS without changing its identity", async () => {
    const session = draft(); const restore = vi.fn(async () => ({ ...session, revision: 18 }));
    render(<RecoverableDraftChanges scopeKey={scopeKey} session={session} hasLiveDraft={false} checkpoints={[{ id: "retained", draftRevision: 2, reason: "switch", digest, expiresAt: future }]} onRestore={restore} />);
    choose("Recovery checkpoint", "r2 · switch"); check("Restore this checkpoint as a new draft revision, without changing production."); click("Restore private checkpoint");
    await waitFor(() => expect(session.reload).toHaveBeenCalledWith(expect.objectContaining({ revision: 18 })));
    expect(restore.mock.calls[0][0]).toEqual({ checkpointId: "retained", revision: 12, expectedDigest: digest });
  });
});

describe("lifecycle authority and exact consequences", () => {
  const resource = { id: "original-uuid", name: "refunds" };
  const review = { subjectId: "original-uuid", releaseRevision: 7, reviewFingerprint: digest, labels: [{ name: "production", version: 4, isProtected: true }] };
  it("denies recovery without current authority and displays retained protected labels", () => {
    render(<ContextSafetyDialog scopeKey={scopeKey} open onClose={vi.fn()} action="unarchive" resource={resource} review={review} onApply={vi.fn()} />);
    expect(screen.getByText("Retained production → v4 · protected")).toBeVisible();
    expect(button("Recover original identity")).toBeDisabled();
  });
  it("binds unarchive to reviewed release revision and refuses a namesake review", async () => {
    const apply = vi.fn(async () => ({ id: resource.id })); const props = { scopeKey, open: true, onClose: vi.fn(), action: "unarchive", resource, review, canAct: true, onApply: apply };
    const view = render(<ContextSafetyDialog {...props} review={{ ...review, subjectId: "namesake" }} />);
    expect(screen.getByRole("checkbox")).toBeDisabled();
    view.rerender(<ContextSafetyDialog {...props} />);
    fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "Recover reviewed policy" } });
    check("I reviewed this exact identity and its retained-label and load consequences."); click("Recover original identity");
    await waitFor(() => expect(apply).toHaveBeenCalledOnce());
    expect(apply.mock.calls[0][0]).toEqual({ expectedReleaseRevision: 7, expectedReviewFingerprint: digest, note: "Recover reviewed policy" });
  });
  it("uses the version safety revision rather than the resource-wide security counter", async () => {
    const apply = vi.fn(async () => ({ revision: 3 }));
    render(<ContextSafetyDialog scopeKey={scopeKey} open onClose={vi.fn()} action="quarantine" resource={resource} review={{ subjectId: resource.id, versionId: "retained-version", revision: 2, securityRevision: 99 }} canAct onApply={apply} />);
    fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "Confirmed unsafe policy" } });
    check("I reviewed this exact identity and its retained-label and load consequences."); click("Quarantine version");
    await waitFor(() => expect(apply).toHaveBeenCalledOnce());
    expect(apply.mock.calls[0][0]).toEqual({ revision: 2, quarantined: true, reason: "Confirmed unsafe policy", replacementVersionId: null });
  });
  it("retries an unknown mutation using the original operation key", async () => {
    const apply = vi.fn().mockRejectedValueOnce(new Error("Response lost")).mockResolvedValueOnce({ id: resource.id });
    render(<ContextSafetyDialog scopeKey={scopeKey} open onClose={vi.fn()} action="unarchive" resource={resource} review={review} canAct onApply={apply} />);
    fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "Recover" } }); check("I reviewed this exact identity and its retained-label and load consequences."); click("Recover original identity");
    await screen.findByText(/could not be confirmed/); click("Recover original identity");
    await waitFor(() => expect(apply).toHaveBeenCalledTimes(2));
    expect(apply.mock.calls[0]).toEqual(apply.mock.calls[1]);
  });
});

describe("additional recovery boundaries", () => {
  it.each(["suspend", "restore"])("retains edits typed during a pending %s instead of navigating or reloading", async (action) => {
    const session = draft(); const pending = deferred(); const preserved = vi.fn(); const write = vi.fn(() => pending.promise);
    const props = { scopeKey, session, onPreserved: preserved, hasLiveDraft: action === "suspend", onSuspend: write, onRestore: write, checkpoints: [{ id: "saved", draftRevision: 2, reason: "switch", digest, expiresAt: future }] };
    const view = render(<RecoverableDraftChanges {...props} />);
    if (action === "suspend") { check("Save a recovery checkpoint and suspend my current draft."); click("Preserve and suspend draft"); }
    else { choose("Recovery checkpoint", "r2 · switch"); check("Restore this checkpoint as a new draft revision, without changing production."); click("Restore private checkpoint"); }
    await waitFor(() => expect(write).toHaveBeenCalledOnce());
    view.rerender(<RecoverableDraftChanges {...props} session={{ ...session, editSequence: 1, dirty: true, files: [{ path: "AGENTS.md", content: "New unsent edits" }] }} />);
    await act(async () => pending.resolve({ ...session, revision: 20 }));
    expect(session.reload).not.toHaveBeenCalled(); expect(preserved).not.toHaveBeenCalled();
    expect(screen.getByText(/newer local edits remain here/)).toBeVisible();
  });
  it("keeps the dialog close control visible but does not dismiss an in-flight mutation", async () => {
    const pending = deferred(); const close = vi.fn();
    render(<ContextSafetyDialog scopeKey={scopeKey} open onClose={close} action="unarchive" resource={{ id: "resource", name: "policy" }} review={{ subjectId: "resource", releaseRevision: 1, reviewFingerprint: digest }} canAct onApply={() => pending.promise} />);
    fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "Recover" } }); check("I reviewed this exact identity and its retained-label and load consequences."); click("Recover original identity");
    expect(button("Close")).toBeVisible(); click("Close"); expect(close).not.toHaveBeenCalled();
    await act(async () => pending.resolve({ id: "resource" }));
  });
  it("requires a new export after local edits before adopting the server draft", async () => {
    const session = draft();
    const url = globalThis.URL;
    vi.stubGlobal("URL", class extends url { static createObjectURL = vi.fn(() => "blob:private"); static revokeObjectURL = vi.fn(); });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    const props = { scopeKey, session, serverDraft: { ...session, revision: 14 }, onReload: vi.fn() };
    const view = render(<RecoverableDraftChanges {...props} />);
    expect(button("Use server draft after export")).toBeDisabled();
    click("Download unsent edits");
    expect(button("Use server draft after export")).not.toBeDisabled();
    view.rerender(<RecoverableDraftChanges {...props} session={{ ...session, editSequence: 1 }} />);
    expect(button("Use server draft after export")).toBeDisabled();
    await act(async () => new Promise((resolve) => setTimeout(resolve, 0)));
    vi.unstubAllGlobals();
  });
  it("requires impact review before archive and acknowledges a successful 204", async () => {
    const applied = vi.fn(); const apply = vi.fn(async () => undefined);
    const props = { scopeKey, open: true, onClose: vi.fn(), action: "archive", resource: { id: "original", name: "context" }, review: { subjectId: "original", releaseRevision: 1 }, canAct: true, onApply: apply, onApplied: applied };
    const view = render(<ContextSafetyDialog {...props} />);
    expect(screen.getByRole("checkbox")).toBeDisabled();
    view.rerender(<ContextSafetyDialog {...props} review={{ ...props.review, impactFingerprint: "impact" }} />);
    fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "Retire" } });
    check("I reviewed this exact identity and its retained-label and load consequences."); click("Archive context");
    await waitFor(() => expect(applied).toHaveBeenCalledWith({ acknowledged: true }));
    expect(apply.mock.calls[0][0].expectedImpactFingerprint).toBe("impact");
  });
  it("replaces displayed links only when the server accepts a different lock", () => {
    const session = draft(); const view = render(<ContextLinksPanel scopeKey={scopeKey} session={session} readOnly />);
    expect(screen.getByText(/Included v8/)).toBeVisible();
    view.rerender(<ContextLinksPanel scopeKey={scopeKey} session={{ ...session, resolvedLock: { links: [{ ...session.resolvedLock.links[0], version: 9 }] } }} readOnly />);
    expect(screen.getByText(/Included v9/)).toBeVisible();
  });
});

describe("retained source intake", () => {
  it("creates an AGENTS-only draft without SKILL.md, labels or publication", async () => {
    const create = vi.fn(async () => ({ context: { id: "new" }, published: false }));
    render(<ContextImportDialog scopeKey={scopeKey} open onClose={vi.fn()} onCreate={create} />);
    choose("Start from", "Write or paste AGENTS.md");
    fireEvent.change(screen.getByLabelText("AGENTS.md"), { target: { value: "# Approved standing rules\r\n" } });
    fireEvent.change(screen.getByLabelText("Immutable name"), { target: { value: "support-agent" } });
    fireEvent.change(screen.getByLabelText("Purpose"), { target: { value: "Support policy" } }); click("Create draft");
    await waitFor(() => expect(create).toHaveBeenCalledOnce());
    expect(create.mock.calls[0][0]).toEqual({ name: "support-agent", description: "Support policy", tags: [], files: [{ path: "AGENTS.md", content: "# Approved standing rules\n" }] });
  });
  it("keeps unsupported roots visible and never permits their selection", async () => {
    const preview = vi.fn(async () => ({ previewId: "retained", expiresAt: future, roots: [{ key: "bad", root: "bad/", kind: "agent_context", blocked: true, blockers: ["Nested AGENTS.md"] }, { key: "ok", root: "ok/", kind: "agent_context", files: [{ path: "AGENTS.md", sizeBytes: 20 }] }], rejected: [{ path: "bad/nested/AGENTS.md", reason: "Nested instructions" }] }));
    const view = render(<ContextImportDialog scopeKey={scopeKey} open onClose={vi.fn()} onPreview={preview} onImport={vi.fn()} />);
    fireEvent.change(view.container.ownerDocument.querySelector('input[type="file"]'), { target: { files: [new File(["synthetic"], "bundle.zip")] } });
    await screen.findByText("Nested AGENTS.md");
    expect(screen.getByRole("checkbox", { name: "Include bad/" })).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: "Include ok/" })).not.toBeDisabled();
    expect(button("Import as draft")).toBeDisabled();
  });
  it("records omission-preserving tag intent and immutable second-import mapping", async () => {
    const item = { key: "source-id", name: "refunds", root: "refunds", kind: "skill", sourceOrigin: "langfuse", sourceProject: "project", sourceResource: "immutable-source", sourceRevision: "v2", destinationId: "destination", expectedHeadVersion: 6, expectedHeadDigest: digest, sourceChanges: [], hubChanges: [], diverged: true };
    const plan = { id: "plan-id", fingerprint: "plan-fingerprint", expiresAt: future, items: [item] };
    const accept = vi.fn(async () => ({ id: "receipt" }));
    render(<MigrationPlanPanel scopeKey={scopeKey} plan={plan} onKeep={accept} />);
    choose("Review item", "refunds");
    check("Apply this exact decision to the reviewed identity and head, without changing production."); click("Record keep decision");
    await waitFor(() => expect(accept).toHaveBeenCalledOnce());
    expect(accept.mock.calls[0][0]).toMatchObject({ planId: "plan-id", expectedFingerprint: "plan-fingerprint", itemKey: "source-id", expectedHeadVersion: 6, expectedHeadDigest: digest });
    expect(accept.mock.calls[0][0]).not.toHaveProperty("tags");
  });
  it("recovers an unknown acceptance with the same operation after the retained plan expires", async () => {
    const plan = { id: "plan", fingerprint: "fp", expiresAt: future, items: [{ key: "one", name: "refunds", sourceChanges: [], hubChanges: [], expectedHeadVersion: 6, expectedHeadDigest: digest }] };
    const accept = vi.fn().mockRejectedValueOnce(new Error("Lost response")).mockResolvedValueOnce({ id: "receipt" });
    const view = render(<MigrationPlanPanel scopeKey={scopeKey} plan={plan} onKeep={accept} />);
    choose("Review item", "refunds"); check("Apply this exact decision to the reviewed identity and head, without changing production."); click("Record keep decision");
    await screen.findByText(/could not be confirmed/);
    view.rerender(<MigrationPlanPanel scopeKey={scopeKey} plan={{ ...plan, expiresAt: "2000-01-01T00:00:00Z" }} onKeep={accept} />);
    expect(button("Retry exact decision")).not.toBeDisabled(); click("Retry exact decision");
    await waitFor(() => expect(accept).toHaveBeenCalledTimes(2));
    expect(accept.mock.calls[0]).toEqual(accept.mock.calls[1]);
  });
  it.each([false, true])("preserves omitted tags versus explicit clear in replacement (%s)", async (clear) => {
    const accept = vi.fn(async () => ({ id: "replacement-receipt" }));
    render(<MigrationPlanPanel scopeKey={scopeKey} plan={{ id: "plan", fingerprint: "fp", expiresAt: future, items: [{ key: "one", name: "refunds", sourceChanges: [], hubChanges: [], expectedHeadVersion: 6, expectedHeadDigest: digest }] }} onAccept={accept} />);
    choose("Review item", "refunds");
    check("Apply this exact decision to the reviewed identity and head, without changing production.");
    expect(button("Record keep decision")).toBeDisabled();
    expect(accept).not.toHaveBeenCalled();
    choose("Source update decision", "Replace with reviewed source");
    if (clear) choose("Destination tags", "Clear every tag");
    check("I have reviewed source rights, licenses and retained notices.");
    check("Apply this exact decision to the reviewed identity and head, without changing production."); click("Import reviewed item as draft");
    await waitFor(() => expect(accept).toHaveBeenCalledOnce());
    expect(accept.mock.calls[0][0]).toMatchObject({ mode: "snapshot", rightsConfirmed: true });
    expect(accept.mock.calls[0][0]).not.toHaveProperty("decision");
    if (clear) expect(accept.mock.calls[0][0].tags).toEqual([]);
    else expect(accept.mock.calls[0][0]).not.toHaveProperty("tags");
  });
  it("blocks replacing a diverged destination with a live personal draft", () => {
    render(<MigrationPlanPanel scopeKey={scopeKey} plan={{ id: "plan", fingerprint: "fp", expiresAt: future, items: [{ key: "one", name: "refunds", hasDraft: true, diverged: true }] }} onAccept={vi.fn()} />);
    choose("Review item", "refunds"); choose("Source update decision", "Replace with reviewed source");
    check("I have reviewed source rights, licenses and retained notices."); check("Apply this exact decision to the reviewed identity and head, without changing production.");
    expect(button("Import reviewed item as draft")).toBeDisabled();
    expect(screen.getByText(/existing draft blocks replacement/)).toBeVisible();
  });
});


it("unlocks a normalized name_taken rejection without losing the context input", async () => {
  const create = vi.fn().mockRejectedValueOnce({ statusCode: 409, code: "name_taken" }).mockResolvedValueOnce({ contextId: "new" });
  render(<ContextImportDialog scopeKey={scopeKey} open onClose={vi.fn()} onCreate={create} />);
  fireEvent.change(screen.getByLabelText("AGENTS.md"), { target: { value: "Standing instructions" } });
  fireEvent.change(screen.getByLabelText("Immutable name"), { target: { value: "taken" } });
  fireEvent.change(screen.getByLabelText("Purpose"), { target: { value: "Preserved purpose" } });
  click("Create draft");
  await screen.findByText(/This name is already in use/);
  expect(screen.getByLabelText("Immutable name")).toBeEnabled();
  expect(screen.getByLabelText("AGENTS.md")).toHaveValue("Standing instructions");
  expect(screen.getByLabelText("Purpose")).toHaveValue("Preserved purpose");
  fireEvent.change(screen.getByLabelText("Immutable name"), { target: { value: "available" } });
  click("Create draft");
  await waitFor(() => expect(create).toHaveBeenCalledTimes(2));
  expect(create.mock.calls[1][0]).toMatchObject({ name: "available", description: "Preserved purpose", files: [{ path: "AGENTS.md", content: "Standing instructions" }] });
  expect(create.mock.calls[1][1].idempotencyKey).not.toBe(create.mock.calls[0][1].idempotencyKey);
});

it("keeps the exact creation locked when a recovery attempt is denied", async () => {
  const create = vi.fn().mockRejectedValueOnce(new Error("Lost response")).mockRejectedValueOnce({ statusCode: 403, code: "forbidden" });
  render(<ContextImportDialog scopeKey={scopeKey} open onClose={vi.fn()} onCreate={create} />);
  fireEvent.change(screen.getByLabelText("AGENTS.md"), { target: { value: "Standing instructions" } });
  fireEvent.change(screen.getByLabelText("Immutable name"), { target: { value: "new-context" } });
  fireEvent.change(screen.getByLabelText("Purpose"), { target: { value: "Purpose" } });
  click("Create draft");
  await screen.findByRole("button", { name: "Retry same operation" });
  expect(screen.getByLabelText("Immutable name")).toBeDisabled();
  click("Retry same operation");
  await screen.findByText(/current access does not allow/);
  expect(screen.getByLabelText("Immutable name")).toBeDisabled();
  expect(create.mock.calls[1]).toEqual(create.mock.calls[0]);
});


it("allows a new upload selection after an uncertain read-only preview", async () => {
  const preview = vi.fn().mockRejectedValueOnce(new Error("Network unavailable")).mockResolvedValueOnce({ previewId: "next", expiresAt: future, roots: [], rejected: [], skipped: [] });
  const view = render(<ContextImportDialog scopeKey={scopeKey} open onClose={vi.fn()} onPreview={preview} onImport={vi.fn()} />);
  const upload = view.container.ownerDocument.querySelector('input[type="file"]');
  fireEvent.change(upload, { target: { files: [new File(["first"], "first.zip")] } });
  await screen.findByText(/could not be confirmed/);
  fireEvent.change(upload, { target: { files: [new File(["second"], "second.zip")] } });
  await screen.findByText(/Retained preview next/);
  expect(preview).toHaveBeenCalledTimes(2);
  expect(preview.mock.calls[1][0].get('files').name).toBe('second.zip');
});
