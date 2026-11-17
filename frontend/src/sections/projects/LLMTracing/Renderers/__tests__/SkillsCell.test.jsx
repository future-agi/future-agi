import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, renderHook, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "src/utils/test-utils";
import { forbiddenRuntimeWords } from "src/sections/skills/runtime/runtimeCopy";
import SkillsCell, {
  chunkTraceIds,
  mapTraceSkillsEntry,
  skillsCellModel,
  skillsColumnDef,
  useTracePageSkills,
} from "../SkillsCell";

vi.mock("src/components/iconify", () => ({ default: () => null }));

const SKILL = "11111111-1111-4111-8111-111111111111";
const V3 = "33333333-3333-4333-8333-333333333333";
const skill = (name, version, extra = {}) => ({
  skill_id: SKILL,
  name,
  version_id: V3,
  version,
  requested_label: "production",
  agent: "refund-agent",
  evidence: "delivered",
  loads: 1,
  digest_status: "verified",
  ...extra,
});
const ENTRY = {
  skills: [skill("refund-policy", 3), skill("tone", 1), skill("shipping", 2), skill("escalation", 5)],
  failed: [{ skill_name: "billing", error_type: "integrity" }],
  more: 2,
};
const cell = (context, data = { trace_id: "t1" }) => render(<SkillsCell data={data} context={context} />);

describe("skillsCellModel", () => {
  it("shows three chips, then +N including the server's more count", () => {
    const model = skillsCellModel(ENTRY);
    expect(model.visible.map((chip) => chip.label)).toEqual(["refund-policy v3", "tone v1", "shipping v2"]);
    expect(model.hidden).toBe(4);
  });

  it("explains a chip on hover: asked for, agent and evidence", () => {
    expect(skillsCellModel(ENTRY).visible[0].tooltip).toBe("Asked for production · refund-agent · Given to the app");
    expect(skillsCellModel({ skills: [skill("x", 1, { digest_status: "mismatch" })] }).visible[0].tooltip)
      .toContain("Contents don't match the published version");
  });

  it("shows a failed load in its own words", () => {
    const model = skillsCellModel({ failed: [{ skill_name: "refund-policy", error_type: "integrity" }] });
    expect(model.visible[0]).toMatchObject({ kind: "failed", label: "refund-policy · load failed", tooltip: "refund-policy · load failed · integrity check" });
  });

  it("is empty for a trace with no loads", () => {
    expect(skillsCellModel(undefined).empty).toBe(true);
  });
});

describe("mapTraceSkillsEntry", () => {
  it("drops values of the wrong shape", () => {
    expect(mapTraceSkillsEntry({ skills: [{ skill_id: "not-a-uuid", name: 3, version: "3" }, null], failed: [{ error_type: "x" }], more: -1 }))
      .toEqual({
        skills: [{ skill_id: null, name: null, version_id: null, version: null, requested_label: null, agent: null, evidence: null, loads: null, digest_status: "verified" }],
        failed: [],
        more: 0,
      });
  });
});

describe("SkillsCell", () => {
  it("shows a skeleton while the page's skills load", () => {
    cell({ traceSkills: { status: "loading", byTrace: {} } });
    expect(screen.getByLabelText("Loading skills")).toBeInTheDocument();
  });

  it("shows a dash when the skills could not be read", () => {
    cell({ traceSkills: { status: "error", byTrace: {} } });
    expect(screen.getByLabelText("Skills unavailable")).toHaveTextContent("—");
  });

  it("renders chips and the overflow count", () => {
    const { container } = cell({ traceSkills: { status: "ready", byTrace: { t1: ENTRY } } });
    expect(screen.getByText("refund-policy v3")).toBeInTheDocument();
    expect(screen.getByText("+4")).toBeInTheDocument();
    expect(forbiddenRuntimeWords(container.textContent)).toEqual([]);
  });

  it("adds the clicked version as its own filter group", () => {
    const onAddSkillFilters = vi.fn();
    cell({ traceSkills: { status: "ready", byTrace: { t1: { skills: [skill("refund-policy", 3)] } } }, onAddSkillFilters });
    fireEvent.click(screen.getByText("refund-policy v3"));
    expect(onAddSkillFilters).toHaveBeenCalledTimes(1);
    const build = onAddSkillFilters.mock.calls[0][0];
    const existing = [{ column_id: "skill.id", filter_config: { col_type: "SKILL", filter_type: "text", filter_op: "equals", filter_value: SKILL } }];
    expect(build(existing).map((item) => [item.column_id, item.group])).toEqual([["skill.id", 1], ["skill.version_id", 1]]);
  });

  it("waits, rather than reading no loads, for a shown trace outside the last request", () => {
    cell({ traceSkills: { status: "ready", byTrace: {}, requested: ["t2"] } });
    expect(screen.getByLabelText("Loading skills")).toBeInTheDocument();
  });

  it("reads a requested trace without loads as empty", () => {
    const { container } = cell({ traceSkills: { status: "ready", byTrace: {}, requested: ["t1"] } });
    expect(container.textContent).toBe("");
  });

  it("renders nothing for a trace without loads", () => {
    const { container } = cell({ traceSkills: { status: "ready", byTrace: {} } });
    expect(container.textContent).toBe("");
  });

  it("defines a column that never asks for attribute keys", () => {
    expect(skillsColumnDef({ id: "skills", name: "Skills", isVisible: false })).toMatchObject({ colId: "skills", headerName: "Skills", hide: true, sortable: false });
  });
});

describe("chunkTraceIds", () => {
  it("splits a large page into requests of at most 100 ids", () => {
    const ids = Array.from({ length: 230 }, (_, index) => `t${index}`);
    expect(chunkTraceIds(ids).map((chunk) => chunk.length)).toEqual([100, 100, 30]);
  });
});

describe("useTracePageSkills", () => {
  const wrapper = ({ children }) => (
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{children}</QueryClientProvider>
  );

  it("stays idle without trace ids", () => {
    const { result } = renderHook(() => useTracePageSkills({ projectId: "p1", traceIds: [] }), { wrapper });
    expect(result.current.status).toBe("idle");
  });

  it("reports the read as unavailable until the route is in the contract", async () => {
    const { result } = renderHook(() => useTracePageSkills({ projectId: "p1", traceIds: ["t1"] }), { wrapper });
    await waitFor(() => expect(result.current.status).toBe("error"));
    expect(result.current.errorKind).toBe("unavailable");
  });
});
