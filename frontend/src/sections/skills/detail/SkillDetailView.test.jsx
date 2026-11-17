import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { dsPalette, dsTypography } from "src/theme/ds-tokens";
import { downloadVersionZip, useArchiveSkill, useSkill } from "src/api/skills";
import { enqueueSnackbar } from "notistack";
import VersionsTab from "../versions/VersionsTab";
import { setSkillTracesOverride } from "../runtime/skillTracesFlag";
import SkillDetailView from "./SkillDetailView";

const access = vi.hoisted(() => ({ isViewer: false, archivedReady: false, canAdmin: true }));
vi.mock("src/api/skills/context-dependents", () => ({ useContextDependents: vi.fn(() => ({ data: undefined })) }));
vi.mock("../utils/access", () => ({ useSkillsAccess: () => ({ ready: true, isViewer: access.isViewer, canWrite: !access.isViewer, canAdmin: access.canAdmin }) }));
vi.mock("../sources/GitHubSourcePanel", () => ({ default: () => null, GitHubSourceStatus: () => null }));
vi.mock("src/api/skills", () => ({
  useSkill: vi.fn(),
  useSkillVersion: () => ({ data: { digest: "sha256:" + "a".repeat(64) } }),
  useArchiveSkill: vi.fn(),
  useSkillVersions: vi.fn(() => ({ data: { results: [{ id: "version-1", number: 1 }, { id: "version-2", number: 2 }] } })),
  archivedSkillsReady: () => access.archivedReady,
  downloadVersionZip: vi.fn(),
  getSkillErrorCode: (error) => error?.code,
}));
vi.mock("../components/VersionSelect", () => ({ default: ({ value, onChange, label, ariaLabel }) => <select aria-label={ariaLabel || label} value={value} onChange={(event) => onChange(Number(event.target.value))}><option value="1">v1</option><option value="2">v2</option><option value="label:production">Production</option><option value="label:latest">Latest</option></select> }));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock('src/api/skills/j1-migration-export', () => ({ useMigrationExport: () => ({ mutateAsync: vi.fn() }) }));
vi.mock("src/config-global", () => ({ HOST_API: "https://api.example.test" }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("./FilesTab", () => ({ default: () => <p>Published file viewer</p> }));
vi.mock("./UsageTab", () => ({ default: () => <p>Usage view</p> }));
vi.mock("./UseSkillPanel", () => ({ default: () => <p>Install commands</p> }));
vi.mock("../versions/VersionsTab", () => ({
  default: vi.fn(() => <p>Version history</p>),
}));
// Stream A components (lane ST10) are stand-ins here: this suite checks only where the shell
// places them and what it hands them (SKILL-TRACES §10 UXF5 commit 2).
const runtime = vi.hoisted(() => ({ tab: vi.fn(), chip: vi.fn(), check: vi.fn() }));
vi.mock("../runtime/RuntimeTab", () => ({
  default: (props) => { runtime.tab(props); return <section aria-label="Runtime view"><button type="button" onClick={() => props.onRollback(1)}>Roll back to v1</button><button type="button" onClick={props.onCheckTracing}>Check tracing from diagnosis</button></section>; },
}));
vi.mock("../runtime/MoveStateChip", () => ({
  default: (props) => { runtime.chip(props); return <span>Watching v2 · 18 h left</span>; },
}));
vi.mock("../runtime/CheckTracing", () => ({
  default: (props) => { runtime.check(props); return <section aria-label="Check tracing">{props.canCheck ? "can check" : "viewer"}</section>; },
}));
const skill = {
  id: "skill-a",
  name: "support-policy", permissions: { canArchive: true }, releaseRevision: 2,
  description: "Use when answering support policy questions.",
  latestVersion: 4,
  labels: [{ name: "production", version: 2 }],
  hasDraft: true,
  drafts: [
    { isMine: true, user: { name: "Me" } },
    { isMine: false, user: { name: "Avery" } },
  ],
};
const theme = createTheme({
  palette: dsPalette("light"),
  typography: dsTypography,
});
const success = (data) => ({
  data,
  isPending: false,
  isError: false,
  refetch: vi.fn(),
});
function Location() {
  const location = useLocation();
  return (
    <output aria-label="URL">
      {location.pathname}
      {location.search}
    </output>
  );
}
function show(search = "") {
  return render(
    <ThemeProvider theme={theme}>
      <MemoryRouter initialEntries={[`/dashboard/skills/skill-a${search}`]}>
        <Routes>
          <Route
            path="/dashboard/skills/:skillId"
            element={<SkillDetailView />}
          />
          <Route path="/dashboard/skills" element={<p>Skills library</p>} />
        </Routes>
        <Location />
      </MemoryRouter>
    </ThemeProvider>,
  );
}
beforeEach(() => {
  access.archivedReady = false; access.canAdmin = true;
  vi.clearAllMocks();
  access.isViewer = false;
  useSkill.mockReturnValue(success(skill));
  useArchiveSkill.mockReturnValue({
    mutateAsync: vi.fn().mockResolvedValue(undefined),
    isPending: false,
  });
  downloadVersionZip.mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText: vi.fn().mockResolvedValue(undefined) },
  });
});
afterEach(() => { cleanup(); setSkillTracesOverride(null); });

describe("Skill detail route", () => {
  it('archives with the release revision reviewed when confirmation opened', async () => {
    const current = { ...skill, releaseRevision: 2 };
    useSkill.mockReturnValue(success(current));
    show();
    fireEvent.click(screen.getByRole('button', { name: 'Skill actions' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Archive' }));
    // A later refetch/policy change must not silently advance this precondition.
    current.releaseRevision = 5;
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Archive' }));
    await waitFor(() => expect(useArchiveSkill().mutateAsync).toHaveBeenCalledWith({ id: skill.id, expectedReleaseRevision: 2 }));
  });
  it("reads the route ID, shows production and own/other draft actions", () => {
    show();
    expect(useSkill).toHaveBeenCalledWith("skill-a");
    expect(
      screen.getByRole("heading", { name: "support-policy" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/Production.*v2/)).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Resume your draft" }),
    ).toHaveAttribute("href", "/dashboard/skills/skill-a/edit");
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Avery has a draft in progress",
    );
    expect(screen.getByRole("alert")).not.toHaveTextContent("Me");
  });
  it("names latest explicitly instead of implying that Edit uses the viewed historical release", () => {
    useSkill.mockReturnValue(
      success({ ...skill, hasDraft: false, drafts: [] }),
    );
    show('?version=2');
    expect(screen.getByRole('link', { name: 'Edit latest v4' })).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: 'Viewing' })).toHaveValue('2');
    expect(screen.getByText('Production v2')).toBeVisible();
    expect(screen.getByText('Latest v4')).toBeVisible();
    expect(screen.getByText("Editing starts from latest v4, not the v2 you're viewing.")).toBeVisible();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
  it("restores the URL tab and preserves deep links when changing it", () => {
    show("?tab=versions&version=2&path=references%2Fpolicy.md&source=shared");
    expect(screen.getByRole("tab", { name: "Versions" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(VersionsTab).toHaveBeenCalledWith(
      expect.objectContaining({ skill }),
      expect.anything(),
    );
    fireEvent.click(screen.getByRole("tab", { name: "Activity" }));
    expect(screen.getByText("Usage view")).toBeInTheDocument();
    expect(screen.getByLabelText("URL")).toHaveTextContent(
      "tab=usage&version=2&path=references%2Fpolicy.md&source=shared",
    );
    fireEvent.click(
      screen.getByRole("tab", { name: "Install", exact: true }),
    );
    expect(screen.getByText("Install commands")).toBeInTheDocument();
    expect(
      screen.getByRole("tab", { name: "Install", exact: true }),
    ).toHaveAttribute("aria-selected", "true");
  });
  it("falls back to Files for an unknown tab", () => {
    show("?tab=unknown");
    expect(screen.getByText("Published file viewer")).toBeInTheDocument();
    expect(screen.getByRole("tabpanel")).toHaveAttribute(
      "aria-labelledby",
      "skill-tab-files",
    );
  });
  it.each([
    ["?version=2", 2],
    ["?version=label:production", 2],
    ["", 4],
  ])(
    "downloads the selected immutable version for %s",
    async (search, number) => {
      show(search);
      fireEvent.click(screen.getByRole("button", { name: "Skill actions" }));
      fireEvent.click(screen.getByRole("menuitem", { name: "Download ZIP" }));
      await waitFor(() =>
        expect(downloadVersionZip).toHaveBeenCalledWith("skill-a", number),
      );
    },
  );
  it("disables downloads for malformed or missing selectors instead of falling back", () => {
    show("?version=9007199254740993");
    fireEvent.click(screen.getByRole("button", { name: "Skill actions" }));
    expect(
      screen.getByRole("menuitem", { name: "Download ZIP" }),
    ).toHaveAttribute("aria-disabled", "true");
  });
  it("reports download and clipboard failures", async () => {
    downloadVersionZip.mockRejectedValueOnce(new Error("Unavailable"));
    navigator.clipboard.writeText.mockRejectedValueOnce(new Error("Denied"));
    show();
    fireEvent.click(screen.getByRole("button", { name: "Skill actions" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Download ZIP" }));
    expect(await screen.findByText("Couldn't reach the server. Nothing was changed.")).toBeVisible();
    expect(enqueueSnackbar).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Skill actions" }));
    fireEvent.click(
      screen.getByRole("menuitem", { name: "Copy install command" }),
    );
    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenLastCalledWith(
        expect.stringContaining("Could not copy"),
        { variant: "error" },
      ),
    );
  });
  it("does not copy a production command for a published skill without labels", () => {
    useSkill.mockReturnValue(success({ ...skill, labels: [] }));
    show();
    fireEvent.click(screen.getByRole("button", { name: "Skill actions" }));
    const copy = screen.getByRole("menuitem", { name: "Copy install command" });
    expect(copy).toHaveAttribute("aria-disabled", "true");
    expect(copy).toHaveAccessibleDescription(/Assign production, or open Use skill/);
    fireEvent.click(copy);
    expect(navigator.clipboard.writeText).not.toHaveBeenCalled();
    expect(enqueueSnackbar).not.toHaveBeenCalled();
  });
  it("copies a production command with environment variables, not credentials", async () => {
    show();
    fireEvent.click(screen.getByRole("button", { name: "Skill actions" }));
    fireEvent.click(
      screen.getByRole("menuitem", { name: "Copy install command" }),
    );
    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith("Install command copied", {
        variant: "success",
      }),
    );
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
      expect.stringContaining("names=support-policy&label=production"),
    );
    expect(navigator.clipboard.writeText.mock.calls[0][0]).toContain(
      "$FI_API_KEY",
    );
  });
  it("archives only after confirmation, then returns to the library", async () => {
    show();
    fireEvent.click(screen.getByRole("button", { name: "Skill actions" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Archive" }));
    const dialog = within(screen.getByRole("dialog"));
    expect(useArchiveSkill().mutateAsync).not.toHaveBeenCalled();
    fireEvent.click(
      dialog.getByRole("button", { name: "Archive", exact: true }),
    );
    await waitFor(() =>
      expect(screen.getByText("Skills library")).toBeInTheDocument(),
    );
    expect(useArchiveSkill().mutateAsync).toHaveBeenCalledWith({ id: "skill-a", expectedReleaseRevision: 2 });
  });
  it("retains the page and persistent feedback on an uncertain archive outcome", async () => {
    useArchiveSkill().mutateAsync.mockRejectedValueOnce(new Error("Denied"));
    show();
    fireEvent.click(screen.getByRole("button", { name: "Skill actions" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Archive" }));
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Archive",
        exact: true,
      }),
    );
    expect(await screen.findByText("We couldn't confirm whether this went through. Check before trying again.")).toBeVisible();
    expect(enqueueSnackbar).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.getByLabelText("URL")).toHaveTextContent(
      "/dashboard/skills/skill-a",
    );
  });
  it.each(["loading", "error", "empty"])(
    "exposes the %s review state",
    (state) => {
      show(`?state=${state}`);
      if (state === "loading")
        expect(screen.getByLabelText("Loading skill")).toBeInTheDocument();
      if (state === "error")
        expect(
          screen.getByText("Couldn't load this skill"),
        ).toBeInTheDocument();
      if (state === "empty")
        expect(screen.getByText("Not published yet")).toBeInTheDocument();
    },
  );
  it("clears the error preview when retrying without losing the tab", () => {
    show("?state=error&tab=use");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(screen.getByText("Install commands")).toBeInTheDocument();
    expect(screen.getByLabelText("URL")).toHaveTextContent("?tab=use");
    expect(useSkill().refetch).toHaveBeenCalledTimes(1);
  });
  it("distinguishes not found from transport failure", () => {
    useSkill.mockReturnValue({
      isError: true,
      error: { code: "skill_not_found" },
    });
    const view = show();
    expect(screen.getByRole("heading", { level: 1, name: "Skill not found" })).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Back to Skills" }),
    ).toHaveAttribute("href", "/dashboard/skills");
    view.unmount();
    useSkill.mockReturnValue({
      isError: true,
      error: { code: "network_error" },
      refetch: vi.fn(),
    });
    show();
    expect(screen.getByText("Couldn't load this skill")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });
});

vi.mock("src/sections/skills/tests/TestsTab", () => ({ default: () => <div>Skill tests</div> }));
vi.mock("src/sections/skills/sources/GitHubSourcePanel", () => ({ default: () => <div>GitHub source</div>, GitHubSourceStatus: () => null }));

it('mounts Tests without losing immutable result deep links or competing primary actions', () => {
  show('?tab=tests&run=run-id&case=case-id&attempt=attempt-id');
  expect(screen.getByText('Skill tests')).toBeInTheDocument();
  expect(screen.getByLabelText('URL')).toHaveTextContent('run=run-id&case=case-id&attempt=attempt-id');
  expect(screen.getByRole('link', { name: 'Resume your draft' })).toHaveClass('MuiButton-outlined');
});

vi.mock("src/api/skills/testing", () => ({ useSkillV11Scope: () => ({ identity: "org:ws:user" }) }));
vi.mock("src/api/skills/context-follower-update", () => ({ useFollowerUpdates: () => ({ prepare: vi.fn(), apply: vi.fn() }) }));

it("switches the viewed version in the identity row and resets the file path", () => {
  useSkill.mockReturnValue(success(skill));
  show("?version=1&path=references%2Fpolicy.md&tab=files");
  fireEvent.change(screen.getByLabelText("Viewing"), { target: { value: "2" } });
  expect(screen.getByLabelText("URL")).toHaveTextContent("version=2&path=SKILL.md&tab=files");
});

it('keeps viewer edit visibly disabled and Use skill outlined', () => {
  access.isViewer = true;
  useSkill.mockReturnValue(success({ ...skill, hasDraft: false, permissions: { canArchive: false } }));
  show();
  expect(screen.getByRole('button', { name: 'Edit latest v4' })).toBeDisabled();
  expect(screen.getByText("Viewers can't edit. Ask a workspace admin for edit access.")).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Use skill' })).toHaveClass('MuiButton-outlined');
  fireEvent.click(screen.getByRole('button', { name: 'Use skill' }));
  expect(screen.getByLabelText('URL')).toHaveTextContent('tab=use');
  fireEvent.click(screen.getByRole('button', { name: 'Skill actions' }));
  expect(screen.getByRole('menuitem', { name: 'Archive' })).toHaveAttribute('aria-disabled', 'true');
  expect(screen.getByText("You can't archive this skill. Ask a workspace admin.")).toBeInTheDocument();
});

vi.mock("../contexts/ContextDependentsPanel", () => ({ default: () => <p>Linked contexts panel</p> }));

describe("header shape in every state (UX iteration 3, LIB4)", () => {
  const showNamed = (name) => render(
    <ThemeProvider theme={theme}>
      <MemoryRouter initialEntries={[{ pathname: "/dashboard/skills/skill-a", state: name ? { skillName: name } : null }]}>
        <Routes><Route path="/dashboard/skills/:skillId" element={<SkillDetailView />} /></Routes>
      </MemoryRouter>
    </ThemeProvider>,
  );
  it("keeps the breadcrumb and title above the error and not-found states", () => {
    useSkill.mockReturnValue({ isError: true, error: { code: "network_error" }, refetch: vi.fn() });
    const view = showNamed("refund-policy");
    const crumbs = screen.getByRole("navigation", { name: "Breadcrumb" });
    expect(within(crumbs).getByRole("link", { name: "Skills" })).toHaveAttribute("href", "/dashboard/skills");
    expect(within(crumbs).getByText("refund-policy")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: "refund-policy" })).toBeInTheDocument();
    expect(screen.getByText("Couldn't load this skill")).toBeInTheDocument();
    view.unmount();
    useSkill.mockReturnValue({ isError: true, error: { code: "skill_not_found" } });
    showNamed(null);
    expect(within(screen.getByRole("navigation", { name: "Breadcrumb" })).getByText("Not found")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: "Skill not found" })).toBeInTheDocument();
    expect(screen.getAllByText("Skill not found")).toHaveLength(2);
  });
  it("loads as a header-shaped skeleton: one progressbar named Loading skill", () => {
    useSkill.mockReturnValue({ isPending: true });
    showNamed("refund-policy");
    expect(screen.getAllByRole("progressbar")).toHaveLength(1);
    expect(screen.getByRole("progressbar", { name: "Loading skill" })).toBeInTheDocument();
  });
  it("calls the dependents tab Linked contexts without changing its URL value (UX-18)", () => {
    show();
    const tab = screen.getByRole("tab", { name: "Linked contexts" });
    expect(screen.queryByRole("tab", { name: "Dependents" })).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Used by" })).not.toBeInTheDocument();
    fireEvent.click(tab);
    expect(screen.getByLabelText("URL")).toHaveTextContent("?tab=dependents");
    expect(screen.getByText("Linked contexts panel")).toBeInTheDocument();
  });
  it("keeps every tab's URL value and order; Activity keeps its name and no runtime tab appears unflagged", () => {
    show();
    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual(["Files", "Tests", "Linked contexts", "Versions", "Activity", "Install"]);
    expect(screen.queryByRole("tab", { name: "Runtime" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Activity" }));
    expect(screen.getByLabelText("URL")).toHaveTextContent("?tab=usage");
  });
});

describe("Archive confirm (UX iteration 4, LIB1)", () => {
  const openArchive = () => {
    fireEvent.click(screen.getByRole("button", { name: "Skill actions" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Archive" }));
    return within(screen.getByRole("dialog"));
  };
  it("says there is no restore path here, and which label stops resolving", () => {
    show();
    const dialog = openArchive();
    expect(dialog.getByText("Archive support-policy?")).toBeInTheDocument();
    expect(dialog.getByText("Agents can no longer fetch it, including through labels.")).toBeInTheDocument();
    expect(dialog.getByText("production points at v2. Agents following production will stop receiving this skill.")).toBeInTheDocument();
    expect(dialog.getByText("Restoring an archived skill isn't available in this workspace; contact support to restore it.")).toBeInTheDocument();
  });
  it("points a workspace admin to Archived skills when the workspace can restore, and drops the label line without labels", () => {
    access.archivedReady = true;
    useSkill.mockReturnValue(success({ ...skill, labels: [] }));
    show();
    const dialog = openArchive();
    expect(dialog.getByText("You can restore it from More → Archived skills.")).toBeInTheDocument();
    expect(dialog.queryByText(/points at/)).not.toBeInTheDocument();
    expect(dialog.queryByText(/isn't available in this workspace/)).not.toBeInTheDocument();
  });
});

describe("Archive confirm for non-admins (UX iteration 5, LIB1)", () => {
  it("sends a member with archive permission to a workspace admin for restore", () => {
    access.archivedReady = true;
    access.canAdmin = false;
    show();
    fireEvent.click(screen.getByRole("button", { name: "Skill actions" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Archive" }));
    const dialog = within(screen.getByRole("dialog"));
    expect(dialog.getByText("A workspace admin can restore it from More → Archived skills.")).toBeInTheDocument();
    expect(dialog.queryByText(/contact support/)).not.toBeInTheDocument();
  });
});

describe("header button weight (UX iteration 5, LIB2)", () => {
  const edit = () => screen.getByRole("link", { name: "Resume your draft" });
  it.each(["files", "tests", "dependents", "versions", "usage", "use"])("is outlined on %s, so the header never changes weight", (tab) => {
    show(`?tab=${tab}`);
    expect(edit()).toHaveClass("MuiButton-outlined");
    expect(edit()).not.toHaveClass("MuiButton-contained");
  });
});

// SKILL-TRACES §7.3 and §10 UXF5 commit 2, with `skillTraces` on: the Runtime tab between Linked
// contexts and Versions (URL value "runtime"), the header move chip, and Check tracing on Install.
describe("Stream A wiring with skillTraces on", () => {
  beforeEach(() => setSkillTracesOverride(true));

  it("orders the tabs Files, Tests, Linked contexts, Runtime, Versions, Activity, Install", () => {
    show();
    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual(["Files", "Tests", "Linked contexts", "Runtime", "Versions", "Activity", "Install"]);
    fireEvent.click(screen.getByRole("tab", { name: "Runtime" }));
    expect(screen.getByLabelText("URL")).toHaveTextContent("?tab=runtime");
    expect(screen.getByRole("region", { name: "Runtime view" })).toBeInTheDocument();
  });

  it("hands the Runtime tab the skill, its versions, production and the viewed version", () => {
    show("?tab=runtime&version=1");
    const props = runtime.tab.mock.calls.at(-1)[0];
    expect(props.skill).toEqual({ id: "skill-a", name: "support-policy" });
    expect(props.versions).toEqual([{ id: "version-1", number: 1 }, { id: "version-2", number: 2 }]);
    expect(props.productionVersion).toBe(2);
    expect(props.viewingVersion).toBe(1);
  });

  it("opens the existing Versions flow for a rollback and never moves a label from the card", () => {
    show("?tab=runtime&move=event-1&window=24h");
    fireEvent.click(screen.getByRole("button", { name: "Roll back to v1" }));
    const url = screen.getByLabelText("URL").textContent;
    expect(url).toContain("tab=versions");
    expect(url).toContain("version=1");
    expect(url).not.toContain("move=");
    expect(screen.getByText("Version history")).toBeInTheDocument();
  });

  it("sends Check tracing from the diagnosis to Install", () => {
    show("?tab=runtime");
    fireEvent.click(screen.getByRole("button", { name: "Check tracing from diagnosis" }));
    expect(screen.getByLabelText("URL")).toHaveTextContent("?tab=use");
  });

  it("shows the move chip beside the production and latest chips", () => {
    show();
    expect(screen.getByText("Watching v2 · 18 h left")).toBeInTheDocument();
    expect(runtime.chip).toHaveBeenCalledWith({ skillId: "skill-a" });
  });

  it("puts Check tracing below the install instructions, disabled for viewers", () => {
    show("?tab=use");
    expect(screen.getByText("Install commands")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Check tracing" })).toHaveTextContent("can check");
    expect(runtime.check.mock.calls.at(-1)[0]).toMatchObject({ kind: "skill", id: "skill-a", name: "support-policy" });
    cleanup();
    access.isViewer = true;
    show("?tab=use");
    expect(screen.getByRole("region", { name: "Check tracing" })).toHaveTextContent("viewer");
  });
});

describe("with skillTraces off, nothing from runtime/ renders", () => {
  beforeEach(() => setSkillTracesOverride(false));
  it("has no Runtime tab, chip or Check tracing, and ?tab=runtime falls back to Files", () => {
    show("?tab=runtime");
    expect(screen.queryByRole("tab", { name: "Runtime" })).not.toBeInTheDocument();
    expect(screen.getByText("Published file viewer")).toBeInTheDocument();
    expect(runtime.tab).not.toHaveBeenCalled();
    expect(runtime.chip).not.toHaveBeenCalled();
    cleanup();
    show("?tab=use");
    expect(screen.getByText("Install commands")).toBeInTheDocument();
    expect(runtime.check).not.toHaveBeenCalled();
    expect(screen.queryByRole("region", { name: "Check tracing" })).not.toBeInTheDocument();
  });
});
