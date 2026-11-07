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
import { buildSkillTraceLink } from "./traces";

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

describe("observed adoption", () => {
  it.each(["light", "dark"])(
    "shows mixed exact versions without an outdated/fleet claim in %s",
    (mode) => {
      show(AdoptionPanel, { skill }, { mode });
      expect(
        screen.getByText("Observed fetches after production moved to v1"),
      ).toBeInTheDocument();
      expect(screen.getByText("Both fetched")).toBeInTheDocument();
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
