import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { dsPalette, dsTypography } from "src/theme/ds-tokens";

const ORG = "22222222-2222-4222-8222-222222222222";
const WS = "33333333-3333-4333-8333-333333333333";
const SKILL_ID = "44444444-4444-4444-8444-444444444444";
const VERSION_ID = "55555555-5555-4555-8555-555555555555";
const DIGEST = `sha256:${"a".repeat(64)}`;

const access = vi.hoisted(() => ({ isViewer: false, canAdmin: true }));
const origin = vi.hoisted(() => ({ value: { origin: "https://api.example.test", error: null } }));
const hooks = vi.hoisted(() => ({
  version: { data: undefined, isError: false },
  list: { data: { results: [] }, isLoading: false, isError: false },
  issue: { mutate: () => {}, isPending: false, isError: false, data: undefined },
  revoke: { mutate: () => {}, isPending: false, isError: false },
}));
vi.mock("src/config-global", () => ({ HOST_API: "https://api.example.test" }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("../utils/access", () => ({ useSkillsAccess: () => access }));
vi.mock("../review/ReleaseReviewBody", () => ({ ReviewState: () => null }));
vi.mock("../utils/origin", () => ({ publicApiOrigin: () => origin.value }));
vi.mock("src/api/skills/testing", () => ({
  useSkillV11Scope: () => ({ scope: { organizationId: "22222222-2222-4222-8222-222222222222", workspaceId: "33333333-3333-4333-8333-333333333333" } }),
}));
vi.mock("src/api/skills", () => ({
  useSkillVersion: () => hooks.version,
  useContextCredentials: () => hooks.list,
  useIssueContextCredential: () => hooks.issue,
  useRevokeContextCredential: () => hooks.revoke,
}));
vi.mock("../components/VersionSelect", () => ({
  default: vi.fn(({ value, onChange }) => (
    <select
      aria-label="Pinned version"
      value={value}
      onChange={(event) => onChange(Number(event.target.value))}
    >
      <option value="1">v1</option>
      <option value="2">v2</option>
    </select>
  )),
}));

const { default: UseSkillPanel } = await import("./UseSkillPanel");
const { default: ContextUsePanel } = await import("../contexts/ContextUsePanel");

const skill = {
  id: SKILL_ID,
  name: "support-policy",
  latestVersion: 2,
  labels: [
    { name: "production", version: 1 },
    { name: "canary", version: 2 },
  ],
  tags: ["support", "policy"],
};
const contextProps = {
  scopeKey: "scope", state: "ready", productionVersion: 1, viewingVersion: 1,
  snapshot: { name: "support-agent", kind: "agent_context", version: 1, versionId: VERSION_ID, digest: DIGEST },
  origin: "https://api.example.test", organizationId: ORG, workspaceId: WS,
};
const qualification = {
  released: true,
  packageVersion: "0.0.1",
  profiles: [
    { id: "codex", version: "fixture-profile", state: "verified" },
    { id: "claude-code", version: "fixture-profile", state: "verified" },
  ],
};
const theme = createTheme({ palette: dsPalette("light"), typography: dsTypography });
const wrap = (node) => <ThemeProvider theme={theme}>{node}</ThemeProvider>;
function show(overrides = {}, props = {}) {
  return render(wrap(<UseSkillPanel skill={{ ...skill, ...overrides }} {...props} />));
}
async function selectLabel(label) {
  fireEvent.mouseDown(screen.getByRole("combobox", { name: "Version to install" }));
  fireEvent.click(await screen.findByRole("option", { name: label, exact: true }));
}
const headings = () => screen.getAllByRole("heading", { level: 3 }).map((heading) => heading.textContent);
const grant = (extra) => ({ kind: "skill", resourceId: SKILL_ID, labels: [], versionIds: [], ...extra });
const credential = (id, grants, extra = {}) => ({
  id, purpose: "consume", bindingIds: [], createdAt: "2026-11-10T00:00:00Z", lastUsedAt: null,
  expiresAt: "2099-01-01T00:00:00Z", revokedAt: null, grants, ...extra,
});
const openDisclosure = (name) => fireEvent.click(screen.getByRole("button", { name }));
const FULL_KEYS = /FI_API_KEY|FI_SECRET_KEY|X-Api-Key|X-Secret-Key/;

afterEach(cleanup);
beforeEach(() => {
  access.isViewer = false;
  access.canAdmin = true;
  origin.value = { origin: "https://api.example.test", error: null };
  hooks.version = { data: { id: VERSION_ID, digest: DIGEST, number: 1 }, isError: false };
  hooks.list = { data: { results: [] }, isLoading: false, isError: false };
  hooks.issue = { mutate: vi.fn(), isPending: false, isError: false, data: undefined };
  hooks.revoke = { mutate: vi.fn(), isPending: false, isError: false };
});

describe("one install mechanism", () => {
  it("renders the same steps for a skill and a context", () => {
    show();
    const skillHeadings = headings();
    cleanup();
    render(wrap(<ContextUsePanel {...contextProps} />));
    expect(skillHeadings).toEqual(["1. Choose your tool", "2. Create a read-only key", "3. Preview the changes", "4. Install"]);
    expect(headings()).toEqual(skillHeadings);
  });

  it("uses the Python CLI with the read-only key's variables and the preview opt-in", () => {
    show();
    const plan = screen.getByLabelText("Skill install plan command");
    expect(plan).toHaveTextContent("python -m fi.skills plan --name 'support-policy' --label 'production' --client 'claude-code' --project '.' --allow-preview --out 'skill-plan.json'");
    expect(plan).toHaveTextContent("python -m fi.skills inspect --plan 'skill-plan.json' --diff");
    expect(plan).toHaveTextContent("# needs your read-only key in FI_CONTEXT_CREDENTIAL");
    expect(screen.getByLabelText("Skill apply command")).toHaveTextContent("python -m fi.skills apply --plan 'skill-plan.json' --quiescent");
    expect(plan).not.toHaveTextContent("curl");
    expect(plan).not.toHaveTextContent("unzip");
  });

  it("copies the complete plan with its environment lines, and the apply command on its own", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    show();
    fireEvent.click(screen.getByRole("button", { name: "Copy plan command" }));
    await screen.findByText("Copied the complete plan command.");
    expect(writeText.mock.calls[0][0]).toContain("export FI_CONTEXT_ORIGIN='https://api.example.test'");
    expect(writeText.mock.calls[0][0]).toContain(`export FI_CONTEXT_ORGANIZATION_ID='${ORG}'`);
    expect(writeText.mock.calls[0][0]).toContain(`export FI_CONTEXT_WORKSPACE_ID='${WS}'`);
    fireEvent.click(screen.getByRole("button", { name: "Copy apply command" }));
    await screen.findByText("Copied the apply command.");
    expect(writeText).toHaveBeenLastCalledWith("python -m fi.skills apply --plan 'skill-plan.json' --quiescent");
  });

  it("pins a numbered version with --version and no label", async () => {
    show();
    await selectLabel("Pinned version");
    fireEvent.change(screen.getByRole("combobox", { name: "Pinned version", exact: true }), { target: { value: "2" } });
    const plan = screen.getByLabelText("Skill install plan command");
    expect(plan).toHaveTextContent("--version 2");
    expect(plan).not.toHaveTextContent("--label");
    expect(screen.getByText(/Installs v2; installed files don't follow later releases/)).toBeVisible();
  });

  it("offers Codex and Claude Code, and changes where the skill is written", () => {
    show();
    expect(screen.getByText(/Writes the skill into \.claude\/skills\//)).toBeVisible();
    fireEvent.click(screen.getByRole("tab", { name: "Codex" }));
    expect(screen.getByLabelText("Skill install plan command")).toHaveTextContent("--client 'codex'");
    expect(screen.getByText(/Writes the skill into \.agents\/skills\//)).toBeVisible();
  });

  it("shows the preview badge on both tabs, and on neither once the installer is qualified", () => {
    const view = show();
    expect(screen.getByText("Preview: not yet tested with Claude Code")).toBeVisible();
    view.unmount();
    render(wrap(<ContextUsePanel {...contextProps} />));
    expect(screen.getByText("Preview: not yet tested with Claude Code")).toBeVisible();
    cleanup();
    show({}, { qualification });
    expect(screen.queryByText(/Preview: not yet tested/)).not.toBeInTheDocument();
    expect(screen.getByLabelText("Skill install plan command")).toHaveTextContent("python -m pip install 'futureagi==0.0.1'");
    cleanup();
    render(wrap(<ContextUsePanel {...contextProps} qualification={qualification} />));
    expect(screen.queryByText(/Preview: not yet tested/)).not.toBeInTheDocument();
  });

  it("fails closed when the workspace has no public API address, on every tab", () => {
    origin.value = { origin: null, error: "not_https" };
    show();
    expect(screen.getByText(/no public HTTPS API address configured/)).toBeVisible();
    expect(screen.queryByRole("button", { name: "Copy plan command" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Application", exact: true }));
    expect(screen.getAllByText(/no public HTTPS API address configured/).length).toBeGreaterThan(0);
  });
});

describe("no command or snippet needs the full API keys", () => {
  it("never mentions the key pair or the API keys page, on any tab or disclosure", async () => {
    const { container } = show();
    openDisclosure("Update an installed skill");
    openDisclosure("Without Python");
    openDisclosure("Before you install");
    expect(container.textContent).not.toMatch(FULL_KEYS);
    expect(screen.queryByRole("link", { name: "Manage API keys" })).not.toBeInTheDocument();
    expect(screen.queryByText(/do not make them read-only/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Application", exact: true }));
    for (const client of ["Python", "TypeScript", "cURL"]) {
      fireEvent.mouseDown(screen.getByRole("combobox", { name: "Fetch with" }));
      fireEvent.click(await screen.findByRole("option", { name: client, exact: true }));
      expect(container.textContent).not.toMatch(FULL_KEYS);
    }
    fireEvent.click(screen.getByRole("tab", { name: "MCP", exact: true }));
    expect(container.textContent).not.toMatch(FULL_KEYS);
  });

  it("keeps curl and the file fallback under Without Python with the read-only variables", () => {
    const { container } = show();
    expect(container.textContent).not.toContain("curl");
    openDisclosure("Without Python");
    const script = [...container.querySelectorAll("pre")].map((node) => node.textContent).join("\n");
    expect(script).toContain("Authorization: Context $FI_CONTEXT_CREDENTIAL");
    expect(script).toContain("/model-hub/skills/consumption/resolve/");
    expect(script).toContain("X-Context-Resolution: $lease");
    expect(script).toContain("dir='.claude/skills/support-policy'");
    expect(script).not.toMatch(FULL_KEYS);
    expect(script).not.toContain("unzip");
  });

  it("changes the folder for each coding client and for a personal install", () => {
    const { container } = show();
    openDisclosure("Without Python");
    fireEvent.mouseDown(screen.getByRole("combobox", { name: "Coding client" }));
    fireEvent.click(screen.getByRole("option", { name: "Cursor", exact: true }));
    expect(container.textContent).toContain("dir='.cursor/skills/support-policy'");
    fireEvent.click(screen.getByRole("tab", { name: "Personal" }));
    expect(container.textContent).toContain(`dir="$HOME"/'.cursor/skills/support-policy'`);
  });

  it("shows an update path that checks first, then writes a plan to review", () => {
    const { container } = show();
    openDisclosure("Update an installed skill");
    expect(container.textContent).toContain("python -m fi.skills update --project '.' --name 'support-policy' --check --allow-preview");
    expect(container.textContent).toContain("--plan 'skill-plan.json'");
  });
});

describe("the read-only key matches the install command", () => {
  it("issues a label grant for a label install, with no version IDs", () => {
    show();
    expect(screen.getByText("Create a key first")).toBeVisible();
    expect(screen.getByText(/limited to the production label of this skill/)).toBeVisible();
    expect(screen.queryByText(/can't read drafts, other versions or moving labels/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Create key" }));
    const [input] = hooks.issue.mutate.mock.calls[0];
    expect(input.purpose).toBe("consume");
    expect(input.grants).toEqual([grant({ labels: ["production"] })]);
  });

  it("issues an exact-version grant for a pinned install", async () => {
    show();
    await selectLabel("Pinned version");
    expect(screen.getByText(/bound to v2 of this skill/)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Create key" }));
    expect(hooks.issue.mutate.mock.calls[0][0].grants).toEqual([grant({ versionIds: [VERSION_ID] })]);
  });

  it("does not offer a pinned key until the exact version has loaded", async () => {
    hooks.version = { data: undefined, isError: false };
    show();
    await selectLabel("Pinned version");
    expect(screen.getByText("Loading this version…")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Create key" })).not.toBeInTheDocument();
  });

  it("counts only a key that grants the selected label", async () => {
    hooks.list = { data: { results: [credential("66666666-6666-4666-8666-666666666666", [grant({ labels: ["canary"] })])] }, isLoading: false, isError: false };
    show();
    expect(screen.getByText("Create a key first")).toBeVisible();
    expect(screen.queryByText("66666666")).not.toBeInTheDocument();
    cleanup();
    hooks.list = { data: { results: [credential("77777777-7777-4777-8777-777777777777", [grant({ labels: ["production"] })])] }, isLoading: false, isError: false };
    show();
    expect(screen.queryByText("Create a key first")).not.toBeInTheDocument();
    expect(screen.getByText("production label")).toBeVisible();
  });

  it("asks a member who cannot create keys to ask an admin, and holds the copy buttons for viewers", async () => {
    access.canAdmin = false;
    show();
    expect(screen.getByText("Ask a workspace admin for access.")).toBeVisible();
    expect(screen.queryByText("Create a key first")).not.toBeInTheDocument();
    cleanup();
    access.isViewer = true;
    show();
    expect(screen.getByRole("button", { name: "Copy plan command" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Copy apply command" })).toBeDisabled();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    fireEvent.click(screen.getByRole("button", { name: "Copy request for admin" }));
    await screen.findByText("Copied the request for an admin.");
    expect(writeText).toHaveBeenCalledWith("Please create a read-only key for the production label of support-policy in Future AGI.");
  });
});

describe("selecting what agents receive", () => {
  it("warns about a missing label without silently substituting latest", async () => {
    show();
    await selectLabel("staging");
    expect(screen.getByText(/no staging label/)).toBeInTheDocument();
    expect(screen.getByLabelText("Skill install plan command")).toHaveTextContent("--label 'staging'");
    await selectLabel("latest");
    expect(screen.queryByText(/do not fall back to latest/)).not.toBeInTheDocument();
    expect(screen.getByLabelText("Skill install plan command")).toHaveTextContent("--label 'latest'");
  });

  it("offers the first unpromoted release without a failing production command", () => {
    show({ labels: [], latestVersion: 1 });
    expect(screen.getByLabelText("Skill install plan command")).toHaveTextContent("--version 1");
    expect(screen.getByLabelText("Skill install plan command")).not.toHaveTextContent("--label");
    expect(screen.getByText(/Installs v1; installed files don/)).toBeVisible();
    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual(["Coding agent", "Application", "MCP", "Codex", "Claude Code"]);
  });

  it("shows publish guidance for an unpublished skill", () => {
    show({ latestVersion: null });
    expect(screen.getByText("Publish before using this skill")).toBeInTheDocument();
    expect(screen.queryByRole("tab")).not.toBeInTheDocument();
  });

  it("no longer offers the tag install, which the read-only key cannot download", () => {
    show();
    expect(screen.queryByText("Install every skill with a tag")).not.toBeInTheDocument();
  });
});

describe("application and MCP", () => {
  it("fetches with the SDK client and the read-only key's variables", async () => {
    const { container } = show();
    fireEvent.click(screen.getByRole("tab", { name: "Application", exact: true }));
    expect(screen.getByText("1. Create a read-only key")).toBeVisible();
    expect(screen.getByText("2. Fetch the skill")).toBeVisible();
    const code = () => [...container.querySelectorAll("pre")].map((node) => node.textContent).join("\n");
    expect(code()).toContain("export FI_CONTEXT_ORIGIN='https://api.example.test'");
    expect(code()).toContain('from fi.skills import SkillsClient');
    expect(code()).toContain('auth=ContextAuth(os.environ["FI_CONTEXT_CREDENTIAL"])');
    expect(code()).toContain('.resolve("support-policy", label="production")');
    fireEvent.mouseDown(screen.getByRole("combobox", { name: "Fetch with" }));
    fireEvent.click(await screen.findByRole("option", { name: "TypeScript", exact: true }));
    expect(code()).toContain('import { Client, SkillsClient } from "@future-agi/sdk";');
    expect(code()).toContain('auth: { type: "context", credential: process.env.FI_CONTEXT_CREDENTIAL! }');
    fireEvent.mouseDown(screen.getByRole("combobox", { name: "Fetch with" }));
    fireEvent.click(await screen.findByRole("option", { name: "cURL", exact: true }));
    expect(code()).toContain("consumption/resolve/");
    expect(code()).toContain("label=production");
  });

  it("shows the immutable version for a pinned Application snippet and no Personal tab", async () => {
    const { container } = show();
    await selectLabel("Pinned version");
    fireEvent.change(screen.getByRole("combobox", { name: "Pinned version", exact: true }), { target: { value: "2" } });
    fireEvent.click(screen.getByRole("tab", { name: "Application", exact: true }));
    expect(container.textContent).toContain('.resolve("support-policy", version=2)');
    expect(screen.queryByRole("tab", { name: "Personal" })).not.toBeInTheDocument();
  });

  it("keeps MCP as protocol guidance with the selected version", async () => {
    const { container } = show();
    await selectLabel("Pinned version");
    fireEvent.click(screen.getByRole("tab", { name: "MCP", exact: true }));
    expect(container.querySelector("pre")).toHaveTextContent("get_skill");
    expect(container.querySelector("pre")).toHaveTextContent('"version": 2');
    expect(screen.getByRole("combobox", { name: "Version to fetch" })).toBeVisible();
  });

  it("distinguishes installing from fetching in the selector wording", () => {
    show();
    expect(screen.getByRole("combobox", { name: "Version to install" })).toHaveTextContent("Production (currently v1)");
    expect(screen.getByText(/Production resolves to v1 when you run the plan/)).toBeVisible();
    fireEvent.click(screen.getByRole("tab", { name: "Application", exact: true }));
    expect(screen.getByRole("combobox", { name: "Version to fetch" })).toBeVisible();
    expect(screen.getByText("Production resolves when requested; a numbered version stays fixed.")).toBeVisible();
  });

  it("renders the footer slot below the install instructions", () => {
    show({}, { footer: <p>Check tracing slot</p> });
    expect(screen.getByText("Check tracing slot")).toBeVisible();
  });

  it("does not report success when copying a snippet fails", async () => {
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: vi.fn().mockRejectedValue(new Error("denied")) } });
    show();
    fireEvent.click(screen.getByRole("tab", { name: "Application", exact: true }));
    fireEvent.click(screen.getAllByRole("button", { name: "Copy", exact: true })[0]);
    expect(await screen.findByRole("status")).toHaveTextContent("Couldn't copy. Select the command and copy it manually.");
  });
});
