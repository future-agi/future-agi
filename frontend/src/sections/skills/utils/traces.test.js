import { createElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { dsPalette, dsTypography } from "src/theme/ds-tokens";
import { formatDate } from "src/utils/report-utils";
import AdoptionPanel from "../governance/AdoptionPanel";
import LabelProtectionDialog from "../governance/LabelProtectionDialog";
import {
  OBSERVE_LINK_SORT_PARAM,
  SKILL_SOURCE_PARAM,
  buildContextBindingTraceLink,
  buildSkillTraceLink,
  runtimeTabUrl,
} from "./traces";
import { setSkillTracesOverride } from "../runtime/skillTracesFlag";

const mocks = vi.hoisted(() => ({
  adoption: vi.fn(),
  protection: vi.fn(),
  history: vi.fn(),
  save: vi.fn(),
  reload: vi.fn(),
  scope: vi.fn(),
  projects: vi.fn(),
}));
vi.mock("src/api/skills", () => ({ getSkillErrorCode: (error) => error.code }));
vi.mock("src/api/skills/adoption", () => ({
  useSkillAdoption: mocks.adoption,
}));
vi.mock("src/api/skills/governance", () => ({
  useSkillLabelProtection: mocks.protection,
  useSkillLabelProtectionHistory: mocks.history,
  useSetSkillLabelProtection: () => ({
    mutateAsync: mocks.save,
    isPending: false,
  }),
}));
vi.mock("src/api/skills/testing", () => ({
  useSkillV11Scope: mocks.scope,
  useSkillV11Query: mocks.projects,
}));
vi.mock("src/utils/axios", () => ({
  endpoints: { project: { listProjects: () => "/authorized-projects/" } },
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
const versionId = "11111111-2222-4333-8444-555555555555";
const otherVersionId = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee";
const start = "2026-10-30T18:00:00Z";
const end = "2026-11-06T18:00:00Z";
const skill = { id: "skill-a", name: "support-policy" };
const success = (data) => ({
  data,
  isPending: false,
  isFetching: false,
  isError: false,
  refetch: mocks.reload,
});
const policy = {
  protectedLabelNames: ["production"],
  releaseRevision: 4,
  canManage: true,
};
const observations = {
  target: {
    label: "production",
    version: 1,
    versionId,
    promotedAt: start,
    promotionEventId: "event-a",
  },
  window: { start, end, asOf: end },
  summary: { currentOnly: 0, otherOnly: 0, mixed: 1, notSeen: 0, ambiguous: 0 },
  count: 1,
  page: 1,
  pageSize: 25,
  totalPages: 1,
  results: [
    {
      consumerId: "opaque-local-id",
      consumerLabel: "Synthetic integration",
      channel: "mcp",
      state: "mixed",
      lastAt: end,
      versions: [
        { version: 1, versionId, lastAt: end },
        { version: 3, versionId: otherVersionId, lastAt: end },
      ],
    },
  ],
};
const wrapped = (component, props, { mode = "light", search = "" } = {}) =>
  createElement(
    ThemeProvider,
    {
      theme: createTheme({
        palette: dsPalette(mode),
        typography: dsTypography,
      }),
    },
    createElement(
      MemoryRouter,
      { initialEntries: [`/${search}`] },
      createElement(component, props),
    ),
  );
const show = (component, props, options) =>
  render(wrapped(component, props, options));

beforeEach(() => {
  vi.clearAllMocks();
  mocks.scope.mockReturnValue({ identity: "org-a/workspace-a/user-a" });
  mocks.adoption.mockReturnValue(success(observations));
  mocks.protection.mockReturnValue(success(policy));
  mocks.history.mockReturnValue(success({ count: 0, results: [] }));
  mocks.projects.mockReturnValue(
    success([{ id: "project-a", name: "Authorized project" }]),
  );
  mocks.save.mockResolvedValue({});
  mocks.reload.mockResolvedValue(success({ ...policy, releaseRevision: 6 }));
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  setSkillTracesOverride(null);
});

describe("immutable skill trace links", () => {
  it("uses exactly one span-attribute identity filter and Observe-compatible date chips", () => {
    const link = buildSkillTraceLink({
      projectId: "project-a",
      versionId,
      windowStart: start,
      windowEnd: end,
    });
    const url = new URL(link, "https://example.invalid");
    expect(url.pathname).toBe("/dashboard/observe/project-a/llm-tracing");
    expect(url.searchParams.get("tab")).toBe("traces");
    expect(url.searchParams.get("selectedTab")).toBe("trace");
    expect(JSON.parse(url.searchParams.get("observeLinkFilter"))).toEqual([
      {
        column_id: "fi.skill.version_id",
        filter_config: {
          col_type: "SPAN_ATTRIBUTE",
          filter_type: "text",
          filter_op: "equals",
          filter_value: versionId,
        },
      },
    ]);
    expect(JSON.parse(url.searchParams.get("primaryTraceDateFilter"))).toEqual({
      dateFilter: [formatDate(new Date(start)), formatDate(new Date(end))],
      dateOption: "Custom",
    });
    expect(link).not.toContain("fi.skill.name");
  });
  it("uses an explicit last-seven-days default and safely encodes the route segment", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(end));
    const link = buildSkillTraceLink({ projectId: "a/b?x=1", versionId });
    expect(link).toMatch(/^\/dashboard\/observe\/a%2Fb%3Fx%3D1\/llm-tracing\?/);
    const date = JSON.parse(
      new URL(link, "https://example.invalid").searchParams.get(
        "primaryTraceDateFilter",
      ),
    );
    expect(date.dateFilter).toEqual([
      formatDate(new Date(start)),
      formatDate(new Date(end)),
    ]);
  });
  it.each([
    { projectId: "" },
    { versionId: "not-a-version" },
    { windowStart: start },
    { windowStart: "invalid", windowEnd: end },
    { windowStart: end, windowEnd: start },
    { windowStart: end, windowEnd: end },
    { windowStart: "", windowEnd: "" },
    { windowStart: null, windowEnd: null },
  ])("rejects incomplete or invalid identity/date inputs %j", (override) => {
    expect(
      buildSkillTraceLink({ projectId: "project-a", versionId, ...override }),
    ).toBeNull();
  });
});

// §6.6 link objects (UX-02): the server builds the §6.3 items; the client
// passes them through unchanged so the list lands on exactly the counted cohort.
const sourceObject = {
  skill_id: "skill-a",
  version_id: versionId,
  agent_key: null,
  metric: "failed_requests",
  event_id: "event-a",
  side: "after",
};
const failedLink = {
  project_id: "project-a",
  from: start,
  to: end,
  filters: [
    {
      column_id: "skill.version_id",
      filter_config: { col_type: "SKILL", filter_type: "text", filter_op: "equals", filter_value: versionId },
    },
    {
      column_id: "skill.root_status",
      filter_config: { col_type: "SKILL", filter_type: "text", filter_op: "equals", filter_value: "error" },
    },
  ],
  source: sourceObject,
};
const slowestLink = {
  ...failedLink,
  filters: [failedLink.filters[0]],
  sort: { column_id: "latency", direction: "desc" },
  source: { ...sourceObject, metric: "latency_p95" },
};
const params = (link) => new URL(link, "https://example.invalid").searchParams;

describe("runtime trace links with skillTraces on", () => {
  it("passes the server's SKILL items through unchanged, with the window and source bar", () => {
    const link = buildSkillTraceLink({ link: failedLink, kind: "failed", skillTraces: true });
    expect(new URL(link, "https://example.invalid").pathname).toBe("/dashboard/observe/project-a/llm-tracing");
    expect(JSON.parse(params(link).get("observeLinkFilter"))).toEqual(failedLink.filters);
    expect(JSON.parse(params(link).get(SKILL_SOURCE_PARAM))).toEqual(sourceObject);
    expect(JSON.parse(params(link).get("primaryTraceDateFilter")).dateFilter).toEqual([
      formatDate(new Date(start)),
      formatDate(new Date(end)),
    ]);
    expect(params(link).has(OBSERVE_LINK_SORT_PARAM)).toBe(false);
  });
  it("adds the slowest-first sort for the p95 link", () => {
    const link = buildSkillTraceLink({ link: slowestLink, kind: "slowest", skillTraces: true });
    expect(JSON.parse(params(link).get(OBSERVE_LINK_SORT_PARAM))).toEqual({ column_id: "latency", direction: "desc" });
  });
  it("opens the project chosen in the project menu for a multi-project version row", () => {
    const link = buildSkillTraceLink({
      link: { ...failedLink, project_id: null },
      kind: "all",
      projectId: "project-b",
      skillTraces: true,
    });
    expect(link).toMatch(/^\/dashboard\/observe\/project-b\/llm-tracing\?/);
    expect(buildSkillTraceLink({ link: { ...failedLink, project_id: null }, kind: "all", skillTraces: true })).toBeNull();
  });
  it("uses a SKILL version item for the version-only call shape", () => {
    const link = buildSkillTraceLink({ projectId: "project-a", versionId, windowStart: start, windowEnd: end, skillTraces: true });
    expect(JSON.parse(params(link).get("observeLinkFilter"))).toEqual([
      {
        column_id: "skill.version_id",
        filter_config: { col_type: "SKILL", filter_type: "text", filter_op: "equals", filter_value: versionId },
      },
    ]);
  });
  it.each([
    [{ link: { ...failedLink, filters: [] } }],
    [{ link: { ...failedLink, filters: [{ column_id: "x" }] } }],
    [{ link: failedLink, kind: "random" }],
    [{ link: { ...failedLink, from: end, to: start } }],
  ])("rejects a malformed link %#", (override) => {
    expect(buildSkillTraceLink({ kind: "failed", skillTraces: true, ...override })).toBeNull();
  });
  it("reads the flag when no explicit mode is given", () => {
    setSkillTracesOverride(true);
    const link = buildSkillTraceLink({ link: failedLink, kind: "failed" });
    expect(JSON.parse(params(link).get("observeLinkFilter"))[0].filter_config.col_type).toBe("SKILL");
  });
});

describe("runtime trace links with skillTraces off", () => {
  it("keeps only the all link, as the original scalar version filter", () => {
    const link = buildSkillTraceLink({ link: failedLink, kind: "all", skillTraces: false });
    expect(JSON.parse(params(link).get("observeLinkFilter"))).toEqual([
      {
        column_id: "fi.skill.version_id",
        filter_config: { col_type: "SPAN_ATTRIBUTE", filter_type: "text", filter_op: "equals", filter_value: versionId },
      },
    ]);
    expect(params(link).has(SKILL_SOURCE_PARAM)).toBe(false);
    expect(params(link).has(OBSERVE_LINK_SORT_PARAM)).toBe(false);
  });
  it.each(["failed", "slowest", "eval_failed"])("has no %s link", (kind) => {
    expect(buildSkillTraceLink({ link: failedLink, kind, skillTraces: false })).toBeNull();
  });
});

describe("context binding and runtime tab links", () => {
  const contextId = "cccccccc-1111-4222-8333-444444444444";
  const bindingId = "bbbbbbbb-1111-4222-8333-444444444444";
  it("filters by context and binding when skillTraces is on", () => {
    const link = buildContextBindingTraceLink({ projectId: "project-a", contextId, bindingId, windowStart: start, windowEnd: end, skillTraces: true });
    expect(JSON.parse(params(link).get("observeLinkFilter")).map((item) => [item.column_id, item.filter_config.col_type, item.filter_config.filter_value])).toEqual([
      ["skill.context_id", "SKILL", contextId],
      ["skill.binding_id", "SKILL", bindingId],
    ]);
  });
  it("has no binding link when skillTraces is off", () => {
    expect(buildContextBindingTraceLink({ projectId: "project-a", contextId, bindingId, skillTraces: false })).toBeNull();
  });
  it("builds the stable move-card URL", () => {
    expect(runtimeTabUrl({ id: "skill-a", eventId: "event-a" })).toBe("/dashboard/skills/skill-a?tab=runtime&move=event-a&window=24h");
    expect(runtimeTabUrl({ kind: "agent_context", id: "ctx-a" })).toBe("/dashboard/skills/contexts/ctx-a?tab=runtime");
    expect(runtimeTabUrl({ id: "" })).toBeNull();
  });
});

describe("observed adoption", () => {
  it.each(["light", "dark"])(
    "shows mixed exact versions without an outdated/fleet claim in %s",
    (mode) => {
      show(AdoptionPanel, { skill }, { mode });
      expect(screen.getByText("Fetch adoption after a label move")).toBeInTheDocument();
      // The first line answers the question; the row names the versions the consumer received.
      expect(screen.getByText("1 consumer still received v3 after production moved.")).toBeInTheDocument();
      expect(screen.getByText("v1 and v3")).toBeInTheDocument();
      // The limits of fetch evidence sit in a labelled disclosure; open it to read them.
      fireEvent.click(screen.getByRole("button", { name: "About this data" }));
      expect(
        screen.getByText(
          /Fetches show retrieval, not installation or successful use/,
        ),
      ).toBeInTheDocument();
      expect(screen.getByTitle(otherVersionId)).toHaveTextContent("v3");
      expect(
        screen.queryByText(/outdated agents|fleet percentage/i),
      ).not.toBeInTheDocument();
    },
  );
  it("names a consumer as a person or key, never a bare email plus the channel", () => {
    const row = observations.results[0];
    mocks.adoption.mockReturnValue(success({ ...observations, count: 3, results: [
      { ...row, consumerId: "a", consumerLabel: "maya.chen@acme-support.example", channel: "api" },
      { ...row, consumerId: "b", consumerLabel: "maya.chen@acme-support.example", channel: "api", keyName: "ci-release-key" },
      { ...row, consumerId: "c", consumerLabel: "Synthetic integration", channel: "mcp" },
    ] }));
    show(AdoptionPanel, { skill });
    expect(screen.getByText("Maya Chen's API key")).toBeInTheDocument();
    expect(screen.getByText("ci-release-key")).toBeInTheDocument();
    expect(screen.getByText("Synthetic integration · MCP")).toBeInTheDocument();
    expect(screen.queryByText(/@acme-support\.example/)).not.toBeInTheDocument();
  });
  it.each([
    ["everyone is on production", { currentOnly: 2, otherOnly: 0, mixed: 0, notSeen: 0, ambiguous: 0 }, "Everyone who fetched support-policy in the last 7 days got v1."],
    ["nobody has fetched since the move", { currentOnly: 0, otherOnly: 0, mixed: 0, notSeen: 0, ambiguous: 0 }, "No fetches since production moved to v1."],
    ["some consumers have not fetched since", { currentOnly: 1, otherOnly: 0, mixed: 0, notSeen: 2, ambiguous: 0 }, "1 consumer got v1 in the last 7 days. 2 consumers haven't fetched since."],
  ])("leads with the answer when %s", (_name, summary, sentence) => {
    mocks.adoption.mockReturnValue(success({ ...observations, summary, count: 1, results: [{ ...observations.results[0], state: "current_only", versions: [{ version: 1, versionId, lastAt: end }] }] }));
    show(AdoptionPanel, { skill, days: 7 });
    expect(screen.getByText(sentence)).toBeInTheDocument();
  });
  it("keeps every explanatory note in one About this data disclosure and every count in one Counts and details", () => {
    show(AdoptionPanel, { skill });
    expect(screen.getAllByRole("button", { name: "About this data" })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: "Counts and details" })).toHaveLength(1);
    for (const name of ["Observation window and limits", "Observation counters", "What this activity can tell you"]) {
      expect(screen.queryByRole("button", { name })).not.toBeInTheDocument();
    }
    fireEvent.click(screen.getByRole("button", { name: "About this data" }));
    expect(screen.getByText(/Window:/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Counts and details" }));
    expect(screen.getByText(/on v1 only/)).toBeInTheDocument();
  });
  it("distinguishes absent production from no observations", () => {
    mocks.adoption.mockReturnValue(
      success({ ...observations, target: null, results: [], count: 0 }),
    );
    const view = show(AdoptionPanel, { skill });
    expect(screen.getByText("Production is not assigned")).toBeInTheDocument();
    mocks.adoption.mockReturnValue(
      success({ ...observations, results: [], count: 0 }),
    );
    view.rerender(wrapped(AdoptionPanel, { skill }));
    expect(
      screen.getByText("No observed identities in this window"),
    ).toBeInTheDocument();
  });
  it.each(["loading", "error", "empty"])(
    "supports the %s preview state",
    (state) => {
      show(AdoptionPanel, { skill }, { search: `?state=${state}` });
      expect(
        screen.queryByText("Synthetic integration"),
      ).not.toBeInTheDocument();
    },
  );
  it("opens only a selected authorized project with the immutable version filter", async () => {
    const open = vi.spyOn(window, "open").mockImplementation(() => null);
    show(AdoptionPanel, { skill });
    fireEvent.click(screen.getByRole("button", { name: "View traces" }));
    expect(screen.getByRole("button", { name: "Open traces" })).toBeDisabled();
    fireEvent.mouseDown(screen.getByRole("combobox", { name: "Project" }));
    fireEvent.click(
      await screen.findByRole("option", { name: "Authorized project" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Open traces" }));
    expect(open).toHaveBeenCalledWith(
      expect.stringContaining("/project-a/llm-tracing?"),
      "_blank",
      "noopener,noreferrer",
    );
    expect(mocks.projects.mock.calls.at(-1)[0].parts).toEqual([
      "traceProjects",
    ]);
    open.mockRestore();
  });
  it("does not silently restore default dates when both visible dates are cleared", async () => {
    show(AdoptionPanel, { skill });
    fireEvent.click(screen.getByRole("button", { name: "View traces" }));
    fireEvent.mouseDown(screen.getByRole("combobox", { name: "Project" }));
    fireEvent.click(
      await screen.findByRole("option", { name: "Authorized project" }),
    );
    fireEvent.change(screen.getByLabelText("From (local time)"), {
      target: { value: "" },
    });
    fireEvent.change(screen.getByLabelText("To (local time)"), {
      target: { value: "" },
    });
    expect(screen.getByRole("button", { name: "Open traces" })).toBeDisabled();
  });
  it("closes trace selection and resets dates/page when the principal scope changes", () => {
    const view = show(AdoptionPanel, { skill });
    fireEvent.click(screen.getByRole("button", { name: "View traces" }));
    expect(
      screen.getByRole("button", { name: "Open traces" }),
    ).toBeInTheDocument();
    mocks.scope.mockReturnValue({ identity: "org-b/workspace-b/user-b" });
    view.rerender(wrapped(AdoptionPanel, { skill }));
    expect(
      screen.queryByRole("button", { name: "Open traces" }),
    ).not.toBeInTheDocument();
  });
});

describe("label protection", () => {
  it("keeps the reviewed revision across refetch and sends additive release CAS", async () => {
    const view = show(LabelProtectionDialog, {
      open: true,
      skill,
      onClose: vi.fn(),
    });
    fireEvent.change(screen.getByLabelText("Protected label names"), {
      target: { value: "production, staging" },
    });
    mocks.protection.mockReturnValue(
      success({ ...policy, releaseRevision: 8 }),
    );
    view.rerender(
      wrapped(LabelProtectionDialog, { open: true, skill, onClose: vi.fn() }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Save protection" }));
    await waitFor(() =>
      expect(mocks.save).toHaveBeenCalledWith({
        protectedLabelNames: ["production", "staging"],
        expectedReleaseRevision: 4,
      }),
    );
  });
  it("requires explicit conflict reload and preserves chosen names", async () => {
    mocks.save.mockRejectedValueOnce({ code: "release_conflict" });
    show(LabelProtectionDialog, { open: true, skill, onClose: vi.fn() });
    fireEvent.change(screen.getByLabelText("Protected label names"), {
      target: { value: "staging" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save protection" }));
    await screen.findByRole("button", { name: "Reload current policy" });
    expect(
      screen.getByRole("button", { name: "Save protection" }),
    ).toBeDisabled();
    fireEvent.click(
      screen.getByRole("button", { name: "Reload current policy" }),
    );
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Save protection" }),
      ).toBeEnabled(),
    );
    expect(screen.getByLabelText("Protected label names")).toHaveValue(
      "staging",
    );
    fireEvent.click(screen.getByRole("button", { name: "Save protection" }));
    await waitFor(() =>
      expect(mocks.save).toHaveBeenLastCalledWith({
        protectedLabelNames: ["staging"],
        expectedReleaseRevision: 6,
      }),
    );
  });
  it("keeps inputs after a background read failure", () => {
    const view = show(LabelProtectionDialog, {
      open: true,
      skill,
      onClose: vi.fn(),
    });
    fireEvent.change(screen.getByLabelText("Protected label names"), {
      target: { value: "staging" },
    });
    mocks.protection.mockReturnValue({ ...success(policy), isError: true });
    view.rerender(
      wrapped(LabelProtectionDialog, { open: true, skill, onClose: vi.fn() }),
    );
    expect(screen.getByLabelText("Protected label names")).toHaveValue(
      "staging",
    );
    expect(
      screen.getByRole("button", { name: "Save protection" }),
    ).toBeDisabled();
  });
  it("shows actual legacy policy and disables forbidden changes", () => {
    mocks.protection.mockReturnValue(
      success({
        protectedLabelNames: [],
        releaseRevision: 0,
        canManage: false,
      }),
    );
    show(LabelProtectionDialog, { open: true, skill, onClose: vi.fn() });
    expect(screen.getByText(/Production is not protected/)).toBeInTheDocument();
    expect(screen.getByLabelText("Protected label names")).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Save protection" }),
    ).toBeDisabled();
  });
  it.each(["latest", "production, production", "Invalid Label"])(
    "rejects invalid policy %s",
    (value) => {
      show(LabelProtectionDialog, { open: true, skill, onClose: vi.fn() });
      fireEvent.change(screen.getByLabelText("Protected label names"), {
        target: { value },
      });
      expect(
        screen.getByRole("button", { name: "Save protection" }),
      ).toBeDisabled();
    },
  );
});
