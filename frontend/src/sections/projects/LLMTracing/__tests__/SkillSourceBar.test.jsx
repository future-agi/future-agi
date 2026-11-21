import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook, screen } from "@testing-library/react";
import { render } from "src/utils/test-utils";
import { forbiddenRuntimeWords } from "src/sections/skills/runtime/runtimeCopy";
import { setSkillTracesOverride } from "src/sections/skills/runtime/skillTracesFlag";
import SkillSourceBar, { otherSideLink, sourceBarText, useSkillFilterLabels, windowWords } from "../SkillSourceBar";
import { parseSkillSource } from "../skillFilters";

const hooks = vi.hoisted(() => ({
  skill: vi.fn(),
  versions: vi.fn(),
  context: vi.fn(),
  comparison: vi.fn(),
  values: vi.fn(),
}));
vi.mock("src/api/skills", () => ({ useSkill: hooks.skill, useSkillVersions: hooks.versions }));
vi.mock("src/api/skills/context-authoring", () => ({ useContextDetail: hooks.context }));
vi.mock("src/api/skills/runtime", () => ({ useSkillComparison: hooks.comparison, useRuntimeFilterValues: hooks.values }));
vi.mock("src/components/iconify", () => ({ default: () => null }));

const SKILL = "11111111-1111-4111-8111-111111111111";
const V3 = "33333333-3333-4333-8333-333333333333";
const V4 = "44444444-4444-4444-8444-444444444444";
const CTX = "55555555-5555-4555-8555-555555555555";
const EVENT = "66666666-6666-4666-8666-666666666666";

const ok = (data) => ({ data, isPending: false, isError: false, fetchStatus: "idle" });
const idle = () => ({ data: undefined, isPending: true, isError: false, fetchStatus: "idle" });
const failed = () => ({ data: undefined, isPending: false, isError: true, fetchStatus: "idle" });
const linkItem = (column, value) => ({ column_id: column, filter_config: { col_type: "SKILL", filter_type: "text", filter_op: "equals", filter_value: value } });
const MOVE = {
  label: "production",
  fromVersion: 3,
  toVersion: 4,
  links: {
    before: { failed_requests: { project_id: "p1", from: "2026-11-16T06:00:00Z", to: "2026-11-17T06:00:00Z", filters: [linkItem("skill.version_id", V3), linkItem("skill.root_status", "error")] } },
    after: { failed_requests: { project_id: "p1", from: "2026-11-17T06:00:00Z", to: "2026-11-17T12:00:00Z", filters: [linkItem("skill.version_id", V4), linkItem("skill.root_status", "error")] } },
  },
};
const SOURCE = parseSkillSource(JSON.stringify({
  skill_id: SKILL,
  version_id: V4,
  agent_key: ["p1", "refund-svc", "refund-agent"],
  metric: "failed_requests",
  event_id: EVENT,
  side: "after",
}));

// §6.6 `source` has no window yet; a source that names one gets the switch.
const WINDOWED = { ...SOURCE, window: "24h" };

beforeEach(() => {
  setSkillTracesOverride(true);
  hooks.skill.mockReturnValue(ok({ id: SKILL, name: "refund-policy" }));
  hooks.versions.mockReturnValue(ok({ results: [{ id: V3, number: 3 }, { id: V4, number: 4 }] }));
  hooks.context.mockReturnValue(idle());
  hooks.comparison.mockReturnValue(ok(MOVE));
});
afterEach(() => setSkillTracesOverride(null));

it('resolves a version-only URL from its own project/window, never from a different version', () => {
  const scope = { projectId: 'p1', from: '2026-11-16T06:00:00Z', to: '2026-11-17T06:00:00Z' };
  hooks.values.mockReturnValue(ok({ values: [{ value: V4, versionId: V4, label: 'refund-policy v4' }] }));
  const { result, rerender } = renderHook(({ id }) => useSkillFilterLabels([linkItem('skill.version_id', id)], scope), { initialProps: { id: V4 } });
  expect(hooks.values).toHaveBeenLastCalledWith({ ...scope, field: 'skill.version_id', q: V4, limit: 1, enabled: true });
  expect(result.current['skill.version_id'][V4]).toBe('refund-policy v4');
  rerender({ id: V3 });
  expect(result.current).toEqual({});
});

it('looks up an Observe calendar-day selection only through now without changing the original bounds', () => {
  const clock = vi.spyOn(Date, 'now').mockReturnValue(Date.parse('2026-11-18T06:00:00Z'));
  try {
    const scope = { projectId: 'p1', from: '2026-11-12T00:00:00Z', to: '2026-11-19T00:00:00Z' };
    hooks.values.mockReturnValue(ok({ values: [] }));
    const { rerender } = renderHook(() => useSkillFilterLabels([linkItem('skill.version_id', V4)], scope));
    expect(hooks.values.mock.lastCall[0]).toMatchObject({ from: scope.from, to: '2026-11-18T06:00:00.000Z', enabled: true });
    clock.mockReturnValue(Date.parse('2026-11-18T06:05:00Z')); rerender();
    expect(hooks.values.mock.lastCall[0].to).toBe('2026-11-18T06:00:00.000Z');
    expect(scope.to).toBe('2026-11-19T00:00:00Z');
  } finally { clock.mockRestore(); }
});

describe("sourceBarText", () => {
  it("names the skill, version, agent, metric and the move window", () => {
    expect(sourceBarText({ source: WINDOWED, skillName: "refund-policy", version: 4, move: MOVE }))
      .toBe("refund-policy v4 · refund-agent · failed requests · 24 h after production moved to v4");
  });

  it("leaves the window out when the source does not name one", () => {
    expect(sourceBarText({ source: SOURCE, skillName: "refund-policy", version: 4, move: MOVE }))
      .toBe("refund-policy v4 · refund-agent · failed requests · after production moved to v4");
  });

  it("leaves out the move when the comparison is unavailable", () => {
    expect(sourceBarText({ source: SOURCE, skillName: "refund-policy", version: 4, move: null }))
      .toBe("refund-policy v4 · refund-agent · failed requests");
  });

  it("reads a window in words", () => {
    expect(windowWords("24h")).toBe("24 h");
    expect(windowWords("7d")).toBe("7 days");
    expect(windowWords("nonsense")).toBe("24 h");
  });
});

describe("otherSideLink", () => {
  it("swaps to the before-window link for the same metric", () => {
    const other = otherSideLink({ source: WINDOWED, move: MOVE });
    expect(other.link).toBe(MOVE.links.before.failed_requests);
    expect(other.label).toBe("Compare with v3 before the move");
    expect(other.href).toMatch(/^\/dashboard\/observe\/p1\/llm-tracing\?/);
    expect(decodeURIComponent(other.href)).toContain(V3);
  });

  it("has no switch without a side or a matching link", () => {
    expect(otherSideLink({ source: { ...WINDOWED, side: null }, move: MOVE })).toBeNull();
    expect(otherSideLink({ source: { ...WINDOWED, metric: "latency_p95" }, move: MOVE })).toBeNull();
    expect(otherSideLink({ source: SOURCE, move: MOVE })).toBeNull();
  });
});

describe("SkillSourceBar", () => {
  it("renders the bar with the way back and the comparison switch", () => {
    const { container } = render(<SkillSourceBar source={WINDOWED} />);
    expect(screen.getByRole("region", { name: "Opened from a skill" })).toHaveTextContent(
      "refund-policy v4 · refund-agent · failed requests · 24 h after production moved to v4",
    );
    const back = screen.getByRole("link", { name: "Back to refund-policy" });
    expect(back.getAttribute("href")).toBe(`/dashboard/skills/${SKILL}?tab=runtime&move=${EVENT}&window=24h`);
    expect(screen.getByRole("link", { name: "Compare with v3 before the move" })).toBeInTheDocument();
    expect(forbiddenRuntimeWords(container.textContent)).toEqual([]);
  });

  it("hands the other side's link to the list view instead of navigating", () => {
    const onSwitch = vi.fn();
    render(<SkillSourceBar source={WINDOWED} onSwitch={onSwitch} />);
    screen.getByRole("button", { name: "Compare with v3 before the move" }).click();
    expect(onSwitch).toHaveBeenCalledWith(expect.objectContaining({ link: MOVE.links.before.failed_requests }));
  });

  it("offers no switch and reads no comparison without a window", () => {
    render(<SkillSourceBar source={SOURCE} />);
    expect(screen.queryByRole("link", { name: /Compare with/ })).toBeNull();
    expect(hooks.comparison).toHaveBeenLastCalledWith(SKILL, expect.objectContaining({ enabled: false }));
  });

  it("hides the switch when the comparison read fails", () => {
    hooks.comparison.mockReturnValue(failed());
    render(<SkillSourceBar source={WINDOWED} />);
    expect(screen.queryByRole("link", { name: /Compare with/ })).toBeNull();
    expect(screen.getByRole("region", { name: "Opened from a skill" })).toHaveTextContent("refund-policy v4 · refund-agent · failed requests");
  });

  it("says when the list could not apply the link's sort", () => {
    render(<SkillSourceBar source={{ ...SOURCE, metric: "slowest", side: null }} sortNotApplied />);
    expect(screen.getByText("The list can't sort these traces by latency yet, so the slowest aren't first.")).toBeInTheDocument();
  });

  it("explains an unreadable link even without a source", () => {
    render(<SkillSourceBar source={null} linkUnreadable />);
    expect(screen.getByRole("status")).toHaveTextContent("This link's skill filters couldn't be read");
  });

  it("names a context link by its context and links back to it", () => {
    hooks.context.mockReturnValue(ok({ context: { id: CTX, name: "support-agent" } }));
    const source = parseSkillSource(JSON.stringify({ context_id: CTX, binding_id: EVENT, metric: "all" }));
    render(<SkillSourceBar source={source} />);
    expect(screen.getByRole("region", { name: "Opened from a skill" })).toHaveTextContent("support-agent · traces");
    expect(screen.getByRole("link", { name: "Back to support-agent" }).getAttribute("href")).toBe(`/dashboard/skills/contexts/${CTX}?tab=runtime`);
  });

  it("renders nothing without a source or a notice", () => {
    const { container } = render(<SkillSourceBar source={null} />);
    expect(container.textContent).toBe("");
  });
});

it('switches a bounded snapshot both ways without refetching or widening the cohort', () => {
  const project = '77777777-7777-4777-8777-777777777777';
  const make = (side, version, token) => ({
    project_id: project, from: '2026-11-01T11:00:00Z', to: '2026-11-01T12:00:00Z',
    filters: [linkItem('skill.trace_cohort', token.repeat(64))],
    source: { skill_id: SKILL, version_id: version, event_id: EVENT, metric: 'traces', side, window: '1h' },
  });
  const snapshot = { label: 'production', from_version: 3, to_version: 4,
    links: { before: { traces: make('before', V3, 'a') }, after: { traces: make('after', V4, 'b') } } };
  const source = parseSkillSource({ ...snapshot.links.after.traces.source, snapshot });
  expect(source).not.toBeNull();
  const before = otherSideLink({ source, move: null });
  expect(before.link.filters[0].filter_config.filter_value).toBe('a'.repeat(64));
  const after = otherSideLink({ source: parseSkillSource(before.link.source), move: null });
  expect(after.link.filters[0].filter_config.filter_value).toBe('b'.repeat(64));
  expect(after.href.length).toBeLessThan(6000);
  render(<SkillSourceBar source={source} />);
  expect(hooks.comparison.mock.calls.at(-1)[1].enabled).toBe(false);
  expect(screen.getByRole('link', { name: 'Compare with v3 before the move' })).toBeInTheDocument();
});

it('rejects snapshot descriptors with arbitrary URLs or mixed project sets', () => {
  const source = { skill_id: SKILL, version_id: V4, event_id: EVENT, metric: 'traces', side: 'after', window: '1h' };
  expect(parseSkillSource({ ...source, snapshot: { label: 'production', from_version: 3, to_version: 4,
    links: { before: { traces: { url: 'https://example.invalid' } }, after: {} } } })).toBeNull();
});

it('preserves the measured microsecond interval when switching slowest pages', () => {
  const make = (side) => ({project_id: '77777777-7777-4777-8777-777777777777',
    from: '2026-11-01T11:00:00.123456Z', to: '2026-11-01T12:00:00.987654Z',
    filters: [linkItem('skill.trace_cohort', 'a'.repeat(64))], sort: {column_id: 'latency', direction: 'desc'},
    source: {skill_id: SKILL, version_id: V4, event_id: EVENT, metric: 'latency_p95', side, window: '1h'}});
  const after = make('after'), before = make('before');
  const source = parseSkillSource({...after.source, snapshot: {label: 'production', from_version: 3, to_version: 4,
    links: {before: {latency_p95: before}, after: {latency_p95: after}}}});
  const {href} = otherSideLink({source, move: null});
  expect(JSON.parse(new URL(href, 'https://example.invalid').searchParams.get('primaryTraceDateFilter')).dateFilter)
    .toEqual([before.from, before.to]);
});
