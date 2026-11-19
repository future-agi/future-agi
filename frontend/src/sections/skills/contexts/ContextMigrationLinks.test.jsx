import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import ContextLinksPanel from "./ContextLinksPanel";
import ContextImportDialog from "./ContextImportDialog";
import ContextMigrationExport from "./ContextMigrationExport";

vi.mock('src/api/skills/context-skill-inventory', () => ({ useLinkedSkillUpstream: () => ({}) }));
vi.mock('src/api/skills/j1-migration-export', () => ({ useMigrationCollectionExport: () => ({ mutateAsync: vi.fn(), isPending: false }) }));
const digest = `sha256:${"a".repeat(64)}`;
const session = (links = []) => {
  const value = { revision: 1, dirty: false, editSequence: 0, contextSpec: { links }, resolvedLock: { links: [] } };
  value.flush = vi.fn(async () => value);
  value.mutateDraft = vi.fn(async (operation) => operation(value));
  return value;
};
afterEach(cleanup);

it("seeds an empty draft with migration proposals pinned to exact versions, applied only after explicit acceptance", async () => {
  const draft = session();
  const preview = vi.fn(async () => ({ revision: 1, fingerprint: "plan", candidateDigest: digest, changes: [], checks: { summary: { errors: 0 } } }));
  const apply = vi.fn(async () => ({ ...draft, revision: 2, contextSpec: { links: [] } }));
  const proposed = [{ skillId: "one", version: 1 }, { skillId: "two", version: 3 }, { skillId: "one", version: 1 }, { skillId: "bad", version: 0 }];
  render(<ContextLinksPanel scopeKey="scope" session={draft} proposedLinks={proposed} availableSkills={[{ id: "one", name: "refunds", kind: "skill", latestVersion: 1 }]} onPreview={preview} onApply={apply} />);
  expect(screen.getByText(/2 skills from your migration are proposed/)).toBeVisible();
  expect(apply).not.toHaveBeenCalled();
  // The read-only preview runs once on arrival; nothing is applied yet.
  await screen.findByText(/Compiled candidate/);
  expect(preview).toHaveBeenCalledOnce();
  expect(preview).toHaveBeenCalledWith({ revision: 1, links: [{ skillId: "one", selector: { version: 1 } }, { skillId: "two", selector: { version: 3 } }], refreshSkillIds: [] });
  expect(screen.getByRole("button", { name: "Apply selected updates to draft" })).toBeDisabled();
  fireEvent.click(screen.getByRole("checkbox", { name: "Accept these skill versions and update rules into 1 context draft." }));
  fireEvent.click(screen.getByRole("button", { name: "Apply selected updates to draft" }));
  await waitFor(() => expect(apply).toHaveBeenCalledOnce());
});

it("never replaces already accepted links with proposals", async () => {
  render(<ContextLinksPanel scopeKey="scope" session={session([{ skillId: "kept", selector: { label: "production" } }])} proposedLinks={[{ skillId: "one", version: 1 }]} availableSkills={[{ id: "kept", name: "kept", latestVersion: 1 }]} />);
  expect(screen.queryByText(/from your migration are proposed/)).not.toBeInTheDocument();
  // Only the accepted link is listed: its own refresh choice, and no proposed row beside it.
  expect(await screen.findByText('kept', { exact: true })).toBeInTheDocument();
  expect(screen.queryByText('one', { exact: true })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Actions for kept' }));
  expect(screen.getAllByRole('menuitem', { name: 'Remove from context' })).toHaveLength(1);
});

it("tells the author which migration skills will be proposed before creating the draft", () => {
  render(<ContextImportDialog scopeKey="scope" open onClose={vi.fn()} onCreate={vi.fn()} proposedLinkCount={14} />);
  expect(screen.getByText(/14 published skills from your migration will be proposed/)).toBeVisible();
});

it("says the download is not what the context installs, and keeps the measured button name", () => {
  render(<ContextMigrationExport contextName="support-agent" links={[{ skillId: "one" }, { skillId: "two" }, { skillId: "one" }]} />);
  expect(screen.getByText("Download newer skill versions (not what this context locks)")).toBeVisible();
  expect(screen.getByText(/These are the newest published versions, which may differ from the versions this context installs\./)).toBeVisible();
  expect(screen.getByRole("button", { name: "Download 2 linked skills" })).toBeEnabled();
  expect(screen.queryByText(/latest versions/)).not.toBeInTheDocument();
});
