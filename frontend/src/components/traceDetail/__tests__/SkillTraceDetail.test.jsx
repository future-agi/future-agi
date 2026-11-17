import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { render } from "src/utils/test-utils";
import { forbiddenRuntimeWords } from "src/sections/skills/runtime/runtimeCopy";
import { setSkillTracesOverride } from "src/sections/skills/runtime/skillTracesFlag";
import {
  mapTraceSkillsDetail,
  ranAfterLoading,
  skillLoadIdentity,
  traceSkillsChipText,
} from "src/sections/projects/LLMTracing/skillFilters";
import { getSpanTypeConfig, getTypeConfig } from "../spanTypeConfig";
import { SkillSpanSection } from "../SpanDetailPane";
import { TraceSkillsStrip } from "../TraceDetailDrawerV2";

vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("notistack", async (load) => ({ ...(await load()), enqueueSnackbar: vi.fn() }));

const SKILL = "11111111-1111-4111-8111-111111111111";
const V3 = "33333333-3333-4333-8333-333333333333";
const CTX = "55555555-5555-4555-8555-555555555555";
const DIGEST = `sha256:${"a".repeat(64)}`;

const loadSpan = (attributes, status = "OK") => ({
  id: "span-load",
  name: "skill.load",
  status,
  observation_type: "unknown",
  span_attributes: attributes,
});
const LOADED = loadSpan({
  "fi.skill.id": SKILL,
  "fi.skill.name": "refund-policy",
  "fi.skill.version_id": V3,
  "fi.skill.version": 3,
  "fi.skill.digest": DIGEST,
  "fi.skill.requested_label": "production",
  "fi.skill.load.via": "sdk.read_file",
  "fi.skill.load.evidence": "delivered",
  "fi.skill.load.cached": false,
  "fi.skill.load.root_file": true,
  "fi.skill.files": 4,
  "fi.skill.bytes": 2048,
});
const FAILED = loadSpan(
  {
    "fi.skill.name": "refund-policy",
    "fi.skill.requested_label": "production",
    "fi.skill.load.via": "sdk.read_file",
    "error.type": "integrity",
    // A failed load never carries an identity, even if a client sends one.
    "fi.skill.version_id": V3,
  },
  "ERROR",
);

beforeEach(() => setSkillTracesOverride(true));
afterEach(() => setSkillTracesOverride(null));

describe("span helpers", () => {
  it("reads a skill.load identity", () => {
    expect(skillLoadIdentity(LOADED)).toMatchObject({
      failed: false,
      skillId: SKILL,
      name: "refund-policy",
      versionId: V3,
      version: 3,
      digest: DIGEST,
      requestedLabel: "production",
      evidence: "delivered",
      cached: false,
      rootFile: true,
      files: 4,
      bytes: 2048,
    });
    expect(skillLoadIdentity({ name: "llm.call" })).toBeNull();
  });

  it("never gives a failed load an identity", () => {
    expect(skillLoadIdentity(FAILED)).toMatchObject({ failed: true, versionId: null, skillId: null, digest: null, errorType: "integrity" });
  });

  it("reads Ran after loading from fi.skills lists, including JSON text", () => {
    const span = { span_attributes: { "fi.skills.refs": JSON.stringify(["refund-policy@v3", "tone@v1"]), "fi.skills.version_ids": [V3] } };
    expect(ranAfterLoading(span).refs.map((ref) => [ref.name, ref.version, ref.versionId])).toEqual([
      ["refund-policy", 3, V3],
      ["tone", 1, null],
    ]);
    expect(ranAfterLoading({ span_attributes: {} }).refs).toEqual([]);
  });

  it("gives skill and context loads a glyph by name, not a new type", () => {
    expect(getSpanTypeConfig("unknown", "skill.load").mdiIcon).toBe("solar:book-bookmark-linear");
    expect(getSpanTypeConfig("unknown", "skill.load").label).toBe(getTypeConfig("unknown").label);
    expect(getSpanTypeConfig("llm", "chat")).toBe(getTypeConfig("llm"));
  });
});

describe("SkillSpanSection", () => {
  it("shows the identity card for a skill.load span", () => {
    const { container } = render(<SkillSpanSection span={LOADED} projectId="p1" />);
    const card = screen.getByRole("region", { name: "Skill load" });
    expect(card).toHaveTextContent("refund-policy");
    expect(card).toHaveTextContent("v3");
    expect(card).toHaveTextContent(DIGEST);
    expect(card).toHaveTextContent("Asked for production");
    expect(card).toHaveTextContent("Given to the app");
    expect(screen.getByRole("link", { name: "Open skill version" })).toHaveAttribute("href", `/dashboard/skills/${SKILL}?version=3`);
    expect(screen.getByRole("link", { name: "All traces that loaded this version" }).getAttribute("href")).toMatch(/^\/dashboard\/observe\/p1\/llm-tracing\?/);
    expect(forbiddenRuntimeWords(container.textContent)).toEqual([]);
  });

  it("shows a failed load in the error card, matched by name", () => {
    render(<SkillSpanSection span={FAILED} projectId="p1" />);
    const card = screen.getByRole("region", { name: "Skill load failed" });
    expect(card).toHaveTextContent("integrity check");
    expect(card).toHaveTextContent("Matched to a skill by the name the app reported");
    expect(screen.queryByRole("link", { name: "All traces that loaded this version" })).toBeNull();
  });

  it("lists Ran after loading and explains a span that started before the first load", () => {
    render(
      <SkillSpanSection
        span={{ id: "s2", name: "llm", span_attributes: { "fi.skills.refs": ["refund-policy@v3"] } }}
        beforeFirstLoad="refund-policy"
      />,
    );
    expect(screen.getByRole("region", { name: "Ran after loading" })).toHaveTextContent("refund-policy v3");
    expect(screen.getByText("This span started before refund-policy was loaded, so it has no skill attributes.")).toBeInTheDocument();
  });

  it("renders nothing with the flag off", () => {
    setSkillTracesOverride(false);
    const { container } = render(<SkillSpanSection span={LOADED} />);
    expect(container.textContent).toBe("");
  });
});

const DETAIL_BODY = {
  skills: [
    { skill_id: SKILL, name: "refund-policy", version_id: V3, version: 3, requested_label: "production", agent: "refund-agent", agent_key: ["p1", "svc", "refund-agent"], evidence: "delivered", loads: 2, digest_status: "verified", span_ids: ["span-load"] },
    { skill_id: SKILL, name: "tone", version_id: V3, version: 1, evidence: "agent_read", loads: 1, digest_status: "mismatch", span_ids: [] },
  ],
  failed: [{ skill_name: "billing", error_type: "integrity", span_id: "span-failed" }],
  contexts: [{ context_id: CTX, name: "support-agent", version: 2 }],
  linked_not_loaded: ["escalation-tone", "shipping-delays"],
  coverage: { span_ids_before_first_load: ["s0"] },
};

describe("trace skills detail", () => {
  it("maps endpoint 7 and builds the chip", () => {
    const detail = mapTraceSkillsDetail(DETAIL_BODY);
    expect(detail.coverage).toEqual({ spanIdsBeforeFirstLoad: ["s0"], firstSkillName: "refund-policy" });
    expect(traceSkillsChipText(detail)).toBe("Skills: refund-policy v3, tone v1 · 1 failed to load");
    expect(traceSkillsChipText(mapTraceSkillsDetail({}))).toBeNull();
  });

  const query = (data, extra = {}) => ({ data, isPending: false, isError: false, fetchStatus: "idle", refetch: vi.fn(), ...extra });

  it("expands into rows that select the load span", () => {
    const onSelectSpan = vi.fn();
    const { container } = render(<TraceSkillsStrip query={query(mapTraceSkillsDetail(DETAIL_BODY))} onSelectSpan={onSelectSpan} />);
    fireEvent.click(screen.getByText("Skills: refund-policy v3, tone v1 · 1 failed to load"));
    const rows = screen.getByRole("list", { name: "Skills loaded" });
    expect(rows).toHaveTextContent("Asked for production");
    expect(rows).toHaveTextContent("Read by the agent");
    expect(rows).toHaveTextContent("billing · load failed · integrity check");
    expect(screen.getByText("Linked in support-agent v2 and not loaded in this trace: escalation-tone, shipping-delays")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "How v3 is doing" })).toHaveAttribute("href", `/dashboard/skills/${SKILL}?tab=runtime&version=3`);
    fireEvent.click(screen.getByText("refund-policy"));
    expect(onSelectSpan).toHaveBeenCalledWith("span-load");
    fireEvent.click(screen.getByText("billing · load failed · integrity check"));
    expect(onSelectSpan).toHaveBeenCalledWith("span-failed");
    expect(forbiddenRuntimeWords(container.textContent)).toEqual([]);
  });

  it("shows no chip for a trace with no loads", () => {
    const { container } = render(<TraceSkillsStrip query={query(mapTraceSkillsDetail({}))} onSelectSpan={vi.fn()} />);
    expect(container.textContent).toBe("");
  });

  it("offers Retry when the read fails", () => {
    const refetch = vi.fn();
    render(<TraceSkillsStrip query={query(undefined, { isError: true, refetch })} onSelectSpan={vi.fn()} />);
    expect(screen.getByText("Skills unavailable")).toBeInTheDocument();
  });
});
