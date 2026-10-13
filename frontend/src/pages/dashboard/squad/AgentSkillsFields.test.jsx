import { act, render, screen, userEvent, waitFor } from "src/utils/test-utils";
import { describe, expect, it, vi } from "vitest";
import PropTypes from "prop-types";
import { renderHook } from "@testing-library/react";
import AgentSkillsFields from "./AgentSkillsFields";
import {
  useAgentSkills,
  validateSkillCatalog,
  validateSkillRevisions,
} from "./useAgentSkills";

const ID_A = "skill_11111111-1111-4111-8111-111111111111";
const ID_B = "skill_22222222-2222-4222-8222-222222222222";
const digest = (hex) => "sha256:" + hex.repeat(64).slice(0, 64);
const revision = (id, number, overrides = {}) => ({
  id,
  revision: number,
  name: id === ID_A ? "Evidence brief" : "Triage",
  description: "Explain evidence.",
  digest: digest(number === 1 ? "a" : "b"),
  createdAt: `2026-10-13T0${number}:00:00.000Z`,
  ...overrides,
});
const catalog = (skills = [revision(ID_A, 2), revision(ID_B, 1)]) => ({
  available: true,
  schemaVersion: "omega-workspace-skills/v1",
  canManage: true,
  skills: skills.map((item) => ({
    ...item,
    status: "active",
    updatedAt: item.createdAt,
  })),
});
const revisions = (skill, entries) => ({
  available: true,
  schemaVersion: "omega-workspace-skills/v1",
  skillId: skill,
  revisions: entries,
});

function HookHarness({ hookProps, onValue }) {
  const value = useAgentSkills(hookProps);
  onValue(value);
  return null;
}
HookHarness.propTypes = {
  hookProps: PropTypes.object.isRequired,
  onValue: PropTypes.func.isRequired,
};

describe("workspace skill selection contract", () => {
  it("rejects foreign, malformed, and stale catalog/revision projections", () => {
    expect(
      validateSkillCatalog({
        ...catalog(),
        skills: [{ ...catalog().skills[0], id: ID_B }, catalog().skills[1]],
      }).ok,
    ).toBe(false);
    expect(
      validateSkillRevisions(
        revisions(ID_A, [revision(ID_A, 1)]),
        catalog().skills[0],
      ).ok,
    ).toBe(false);
    expect(
      validateSkillRevisions(
        revisions(ID_A, [
          revision(ID_A, 1),
          revision(ID_A, 2, { digest: digest("c") }),
        ]),
        catalog().skills[0],
      ).ok,
    ).toBe(false);
  });

  it("loads only a selected skill's history and validates an older pinned ref", async () => {
    const calls = [];
    const api = {
      listWorkspaceSkills: vi.fn().mockResolvedValue(catalog()),
      listWorkspaceSkillRevisions: vi.fn((id) => {
        calls.push(id);
        return Promise.resolve(
          revisions(
            id,
            id === ID_A
              ? [revision(ID_A, 1), revision(ID_A, 2)]
              : [revision(ID_B, 1)],
          ),
        );
      }),
    };
    let latest;
    render(
      <HookHarness
        hookProps={{
          client: api,
          allowed: true,
          refs: [{ id: ID_A, revision: 1 }],
        }}
        onValue={(value) => {
          latest = value;
        }}
      />,
    );
    await waitFor(() => expect(latest?.currentValid).toBe(true));
    expect(calls).toEqual([ID_A]);
    expect(latest.revisions[ID_B]).toBeUndefined();
    expect(latest.revisions[ID_A]).toHaveLength(2);
    expect(latest.unavailableRefs).toEqual([]);
  });

  it("drops a late scope response and does not preserve the old catalog", async () => {
    let resolveFirst;
    const first = {
      listWorkspaceSkills: vi.fn(
        () =>
          new Promise((resolve) => {
            resolveFirst = resolve;
          }),
      ),
      listWorkspaceSkillRevisions: vi.fn(),
    };
    const second = {
      listWorkspaceSkills: vi
        .fn()
        .mockResolvedValue(catalog([revision(ID_B, 1)])),
      listWorkspaceSkillRevisions: vi
        .fn()
        .mockResolvedValue(revisions(ID_B, [revision(ID_B, 1)])),
    };
    let latest;
    const view = render(
      <HookHarness
        hookProps={{ client: first, allowed: true, refs: [] }}
        onValue={(value) => {
          latest = value;
        }}
      />,
    );
    view.rerender(
      <HookHarness
        hookProps={{ client: second, allowed: true, refs: [] }}
        onValue={(value) => {
          latest = value;
        }}
      />,
    );
    await waitFor(() =>
      expect(latest?.catalog.map((skill) => skill.id)).toEqual([ID_B]),
    );
    await act(async () => resolveFirst(catalog()));
    expect(latest.catalog.map((skill) => skill.id)).toEqual([ID_B]);
  });

  it("selects exact latest revisions, allows pinning an older revision, and explicitly clears", async () => {
    const onChange = vi.fn();
    const selection = {
      allowed: true,
      canSelect: true,
      loading: false,
      currentValid: true,
      errors: {},
      catalog: catalog().skills,
      revisions: {
        [ID_A]: [revision(ID_A, 1), revision(ID_A, 2)],
        [ID_B]: [revision(ID_B, 1)],
      },
      refresh: vi.fn(),
    };
    const view = render(
      <AgentSkillsFields
        value={[]}
        onChange={onChange}
        selection={selection}
      />,
    );
    await userEvent.click(
      screen.getByRole("checkbox", { name: /Evidence brief/ }),
    );
    expect(onChange).toHaveBeenLastCalledWith([{ id: ID_A, revision: 2 }]);
    view.rerender(
      <AgentSkillsFields
        value={[{ id: ID_A, revision: 2 }]}
        onChange={onChange}
        selection={selection}
      />,
    );
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Revision for Evidence brief" }),
      "1",
    );
    expect(onChange).toHaveBeenLastCalledWith([{ id: ID_A, revision: 1 }]);
    await userEvent.click(
      screen.getByRole("button", { name: "Clear skill selection" }),
    );
    expect(onChange).toHaveBeenLastCalledWith([]);
  });

  it("allows removing archived or unavailable refs but never selecting archived skills", async () => {
    const archived = { ...catalog().skills[0], status: "archived" };
    const onChange = vi.fn();
    render(
      <AgentSkillsFields
        value={[
          { id: ID_A, revision: 1 },
          { id: "skill_33333333-3333-4333-8333-333333333333", revision: 1 },
        ]}
        onChange={onChange}
        selection={{
          allowed: true,
          canSelect: true,
          loading: false,
          currentValid: false,
          errors: {},
          catalog: [archived],
          revisions: {},
          refresh: vi.fn(),
        }}
      />,
    );
    expect(
      screen.queryByRole("checkbox", { name: /Evidence brief/ }),
    ).not.toBeInTheDocument();
    expect(screen.getByText(/Archived skill/)).toBeInTheDocument();
    expect(screen.getByText(/Unavailable skill/)).toBeInTheDocument();
    await userEvent.click(screen.getAllByRole("button", { name: "Remove" })[0]);
    expect(onChange).toHaveBeenCalledWith([
      { id: "skill_33333333-3333-4333-8333-333333333333", revision: 1 },
    ]);
  });

  it("does not mutate invalid refs and disables new selections once eight are selected", () => {
    const onChange = vi.fn();
    const eight = Array.from({ length: 8 }, (_, index) => ({
      id: `skill_${String(index).padStart(8, "0")}-0000-4000-8000-000000000000`,
      revision: 1,
    }));
    render(
      <AgentSkillsFields
        value={eight}
        onChange={onChange}
        selection={{
          allowed: true,
          canSelect: true,
          catalog: [{ ...catalog().skills[0], id: ID_A }],
          revisions: { [ID_A]: [revision(ID_A, 1), revision(ID_A, 2)] },
          currentValid: false,
        }}
      />,
    );
    expect(
      screen.getByText(/8 of 8 skill revisions selected/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("checkbox", { name: /Evidence brief/ }),
    ).toBeDisabled();
    expect(onChange).not.toHaveBeenCalled();
  });

  it("keeps omitted refs untouched, and requires capability before making requests", async () => {
    const api = {
      listWorkspaceSkills: vi.fn(),
      listWorkspaceSkillRevisions: vi.fn(),
    };
    const { result, rerender } = renderHook((props) => useAgentSkills(props), {
      initialProps: {
        client: api,
        open: true,
        allowed: false,
        refs: undefined,
      },
    });
    expect(result.current.currentValid).toBe(true);
    expect(result.current.canSelect).toBe(false);
    expect(api.listWorkspaceSkills).not.toHaveBeenCalled();
    rerender({ client: api, open: false, allowed: true, refs: undefined });
    expect(api.listWorkspaceSkills).not.toHaveBeenCalled();
    const onChange = vi.fn();
    render(
      <AgentSkillsFields onChange={onChange} selection={result.current} />,
    );
    expect(onChange).not.toHaveBeenCalled();
    expect(
      screen.queryByRole("button", { name: "Clear skill selection" }),
    ).not.toBeInTheDocument();
  });

  it("preserves the selected older revision across library refresh without refetching on a new refs array", async () => {
    const api = {
      listWorkspaceSkills: vi
        .fn()
        .mockResolvedValue(catalog([revision(ID_A, 2)])),
      listWorkspaceSkillRevisions: vi
        .fn()
        .mockResolvedValue(
          revisions(ID_A, [revision(ID_A, 1), revision(ID_A, 2)]),
        ),
    };
    const { result, rerender } = renderHook((props) => useAgentSkills(props), {
      initialProps: {
        client: api,
        allowed: true,
        refs: [{ id: ID_A, revision: 1 }],
      },
    });
    await waitFor(() => expect(result.current.currentValid).toBe(true));
    rerender({ client: api, allowed: true, refs: [{ id: ID_A, revision: 1 }] });
    expect(api.listWorkspaceSkills).toHaveBeenCalledTimes(1);
    api.listWorkspaceSkills.mockResolvedValueOnce(catalog([revision(ID_A, 3)]));
    api.listWorkspaceSkillRevisions.mockResolvedValueOnce(
      revisions(ID_A, [
        revision(ID_A, 1),
        revision(ID_A, 2),
        revision(ID_A, 3),
      ]),
    );
    await act(async () => result.current.refresh());
    await waitFor(() => expect(result.current.currentValid).toBe(true));
    expect(result.current.catalog[0].revision).toBe(3);
    expect(result.current.revisions[ID_A][0].revision).toBe(1);
  });

  it("rejects a foreign revision list and lets the user remove the unavailable pin", async () => {
    const api = {
      listWorkspaceSkills: vi
        .fn()
        .mockResolvedValue(catalog([revision(ID_A, 2)])),
      listWorkspaceSkillRevisions: vi
        .fn()
        .mockResolvedValue(revisions(ID_B, [revision(ID_B, 1)])),
    };
    const { result } = renderHook(() =>
      useAgentSkills({
        client: api,
        allowed: true,
        refs: [{ id: ID_A, revision: 1 }],
      }),
    );
    await waitFor(() => expect(result.current.errors[ID_A]).toBeTruthy());
    expect(result.current.currentValid).toBe(false);
    expect(result.current.revisions[ID_A]).toBeUndefined();
    const onChange = vi.fn();
    render(
      <AgentSkillsFields
        value={[{ id: ID_A, revision: 1 }]}
        onChange={onChange}
        selection={result.current}
      />,
    );
    expect(
      screen.getByRole("checkbox", { name: /Evidence brief/ }),
    ).toBeEnabled();
    expect(
      screen.getByRole("combobox", { name: "Revision for Evidence brief" }),
    ).toHaveValue("1");
    expect(
      screen.getByRole("combobox", { name: "Revision for Evidence brief" }),
    ).toBeDisabled();
    await userEvent.click(
      screen.getByRole("checkbox", { name: /Evidence brief/ }),
    );
    expect(onChange).toHaveBeenLastCalledWith([]);
  });

  it("pins the catalog revision before loading history, and clears malformed refs only by an explicit user action", async () => {
    const onChange = vi.fn();
    const selection = {
      catalog: catalog().skills,
      revisions: {},
      canSelect: true,
      allowed: true,
      errors: {},
      currentValid: false,
    };
    const view = render(
      <AgentSkillsFields
        value={[]}
        onChange={onChange}
        selection={selection}
      />,
    );
    expect(
      screen.getByRole("checkbox", { name: /Evidence brief/ }),
    ).toBeEnabled();
    await userEvent.click(
      screen.getByRole("checkbox", { name: /Evidence brief/ }),
    );
    expect(onChange).toHaveBeenLastCalledWith([{ id: ID_A, revision: 2 }]);
    onChange.mockClear();
    view.rerender(
      <AgentSkillsFields
        value={[{ id: ID_A, revision: -1 }]}
        onChange={onChange}
        selection={selection}
      />,
    );
    expect(onChange).not.toHaveBeenCalled();
    await userEvent.click(
      screen.getByRole("button", { name: "Clear skill selection" }),
    );
    expect(onChange).toHaveBeenCalledWith([]);
  });

  it("makes no unselected history requests even for a full 200-skill library, then gates a new pin until its history is verified", async () => {
    const allSkills = Array.from({ length: 200 }, (_, index) =>
      revision(
        "skill_" +
          String(index).padStart(8, "0") +
          "-0000-4000-8000-000000000000",
        1,
      ),
    );
    let acceptHistory;
    const api = {
      listWorkspaceSkills: vi.fn().mockResolvedValue(catalog(allSkills)),
      listWorkspaceSkillRevisions: vi.fn(
        () =>
          new Promise((resolve) => {
            acceptHistory = resolve;
          }),
      ),
    };
    const { result, rerender } = renderHook((props) => useAgentSkills(props), {
      initialProps: { client: api, allowed: true, refs: [] },
    });
    await waitFor(() => expect(result.current.catalog).toHaveLength(200));
    expect(api.listWorkspaceSkillRevisions).not.toHaveBeenCalled();
    expect(result.current.currentValid).toBe(true);
    const selected = { id: allSkills[82].id, revision: 1 };
    rerender({ client: api, allowed: true, refs: [selected] });
    expect(result.current.currentValid).toBe(false);
    await waitFor(() =>
      expect(api.listWorkspaceSkillRevisions).toHaveBeenCalledTimes(1),
    );
    expect(api.listWorkspaceSkillRevisions).toHaveBeenCalledWith(selected.id);
    await act(async () =>
      acceptHistory(revisions(selected.id, [allSkills[82]])),
    );
    expect(result.current.currentValid).toBe(true);
    rerender({ client: api, allowed: true, refs: [{ ...selected }] });
    expect(api.listWorkspaceSkillRevisions).toHaveBeenCalledTimes(1);
  });

  it("ignores an old revision response after a refresh or workspace switch", async () => {
    let oldResolve;
    const api = {
      listWorkspaceSkills: vi
        .fn()
        .mockResolvedValue(catalog([revision(ID_A, 2)])),
      listWorkspaceSkillRevisions: vi
        .fn()
        .mockImplementationOnce(
          () =>
            new Promise((resolve) => {
              oldResolve = resolve;
            }),
        )
        .mockResolvedValueOnce(
          revisions(ID_A, [revision(ID_A, 1), revision(ID_A, 2)]),
        ),
    };
    const { result, rerender } = renderHook((props) => useAgentSkills(props), {
      initialProps: {
        client: api,
        allowed: true,
        refs: [{ id: ID_A, revision: 1 }],
      },
    });
    await waitFor(() => expect(oldResolve).toBeTypeOf("function"));
    await act(async () => result.current.refresh());
    await waitFor(() => expect(result.current.currentValid).toBe(true));
    const second = {
      listWorkspaceSkills: vi.fn().mockResolvedValue(catalog([])),
      listWorkspaceSkillRevisions: vi.fn(),
    };
    rerender({ client: second, allowed: true, refs: [] });
    expect(result.current.catalog).toEqual([]);
    await act(async () =>
      oldResolve(revisions(ID_A, [revision(ID_A, 1), revision(ID_A, 2)])),
    );
    expect(result.current.revisions).toEqual({});
    expect(result.current.catalog).toEqual([]);
  });

  it.each([
    { revision: 0 },
    { digest: "sha256:fake" },
    { instructions: "not-a-metadata-projection" },
    { permissions: ["secret-access"] },
    { createdAt: "not-a-date" },
  ])("rejects malformed catalog metadata: %j", (change) => {
    const response = catalog();
    response.skills[0] = { ...response.skills[0], ...change };
    expect(validateSkillCatalog(response).ok).toBe(false);
  });
});
