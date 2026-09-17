import { describe, it, expect, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import {
  useEnvironmentsStore,
  resetEnvironmentsStore,
  BUILD_STAGE,
} from "../useEnvironmentsStore";
import { emptyEnvState, useEnvState } from "../envState";

describe("useEnvironmentsStore", () => {
  beforeEach(() => {
    sessionStorage.clear();
    resetEnvironmentsStore();
  });

  it("emptyEnvState carries an empty additional-agents list and no active agent", () => {
    const state = emptyEnvState();
    expect(state.additionalAgents).toEqual([]);
    expect(state.activeAgentId).toBeNull();
  });

  it("starts with a null choice and draft", () => {
    const state = useEnvironmentsStore.getState();
    expect(state.choice).toBeNull();
    expect(state.draft).toBeNull();
  });

  it("sets and clears the choice", () => {
    useEnvironmentsStore.getState().setChoice("source");
    expect(useEnvironmentsStore.getState().choice).toBe("source");
    useEnvironmentsStore.getState().clearChoice();
    expect(useEnvironmentsStore.getState().choice).toBeNull();
  });

  it("sets and clears the draft", () => {
    useEnvironmentsStore.getState().setDraft({ kind: "repo" });
    expect(useEnvironmentsStore.getState().draft).toEqual({ kind: "repo" });
    useEnvironmentsStore.getState().clearDraft();
    expect(useEnvironmentsStore.getState().draft).toBeNull();
  });

  it("reset() returns to the initial state", () => {
    useEnvironmentsStore.getState().setChoice("hosted");
    useEnvironmentsStore.getState().setDraft({ kind: "platform" });
    useEnvironmentsStore.getState().reset();
    const state = useEnvironmentsStore.getState();
    expect(state.choice).toBeNull();
    expect(state.draft).toBeNull();
  });

  it("resetEnvironmentsStore() works imperatively", () => {
    useEnvironmentsStore.getState().setChoice("upload");
    resetEnvironmentsStore();
    expect(useEnvironmentsStore.getState().choice).toBeNull();
  });

  describe("build slice", () => {
    it("starts with the initial build slice values", () => {
      const s = useEnvironmentsStore.getState();
      expect(s.buildStage).toBeNull();
      expect(s.envId).toBeNull();
      expect(s.readerAnswers).toBeNull();
      expect(s.retriedSections).toEqual([]);
      expect(s.buildProgress).toEqual({
        done: [],
        running: false,
        failure: null,
      });
    });

    it("startPreflight() enters the preflight stage", () => {
      useEnvironmentsStore.getState().startPreflight();
      expect(useEnvironmentsStore.getState().buildStage).toBe(
        BUILD_STAGE.PREFLIGHT,
      );
      expect(BUILD_STAGE.PREFLIGHT).toBe("preflight");
    });

    it("acceptAudit() enters building with the env id and answers", () => {
      const answers = { q: { pick: 0 } };
      useEnvironmentsStore.getState().acceptAudit({ envId: "env-1", answers });
      const s = useEnvironmentsStore.getState();
      expect(s.buildStage).toBe(BUILD_STAGE.BUILDING);
      expect(s.buildStage).toBe("building");
      expect(s.envId).toBe("env-1");
      expect(s.readerAnswers).toBe(answers);
    });

    it("startPreflight() again resets the building state but leaves the draft", () => {
      useEnvironmentsStore.getState().setDraft({ kind: "repo" });
      useEnvironmentsStore
        .getState()
        .acceptAudit({ envId: "env-1", answers: { q: { pick: 0 } } });
      useEnvironmentsStore
        .getState()
        .setBuildProgress({ done: ["understand"], running: true });

      useEnvironmentsStore.getState().startPreflight();

      const s = useEnvironmentsStore.getState();
      expect(s.envId).toBeNull();
      expect(s.readerAnswers).toBeNull();
      expect(s.buildProgress.done).toEqual([]);
      expect(s.buildProgress.running).toBe(false);
      expect(s.draft).toEqual({ kind: "repo" });
    });

    it("retrySection() dedup-appends", () => {
      useEnvironmentsStore.getState().retrySection("rules");
      useEnvironmentsStore.getState().retrySection("rules");
      expect(useEnvironmentsStore.getState().retriedSections).toEqual([
        "rules",
      ]);
    });

    it("retryAll() marks every section retried", () => {
      useEnvironmentsStore.getState().retryAll();
      expect(useEnvironmentsStore.getState().retriedSections).toEqual([
        "tools",
        "rules",
        "data",
        "behavior",
      ]);
    });

    it("setBuildProgress() merges the patch", () => {
      useEnvironmentsStore
        .getState()
        .setBuildProgress({ done: ["understand"], running: true });
      expect(useEnvironmentsStore.getState().buildProgress).toEqual({
        done: ["understand"],
        running: true,
        failure: null,
      });
    });

    it("reset() clears the build slice and the draft", () => {
      useEnvironmentsStore.getState().setDraft({ kind: "repo" });
      useEnvironmentsStore
        .getState()
        .acceptAudit({ envId: "env-1", answers: { q: {} } });
      useEnvironmentsStore.getState().retryAll();

      useEnvironmentsStore.getState().reset();

      const s = useEnvironmentsStore.getState();
      expect(s.draft).toBeNull();
      expect(s.buildStage).toBeNull();
      expect(s.envId).toBeNull();
      expect(s.readerAnswers).toBeNull();
      expect(s.retriedSections).toEqual([]);
      expect(s.buildProgress).toEqual({
        done: [],
        running: false,
        failure: null,
      });
    });
  });

  describe("env slices", () => {
    it("adoptEnvironment() prepends the record and seeds an empty state", () => {
      useEnvironmentsStore
        .getState()
        .adoptEnvironment({ id: "env-a", name: "A" }, "2026-09-17T00:00:00Z");
      useEnvironmentsStore
        .getState()
        .adoptEnvironment({ id: "env-b", name: "B" }, "2026-09-17T01:00:00Z");

      const s = useEnvironmentsStore.getState();
      // Prepend: the most recently adopted id comes first.
      expect(Object.keys(s.workspaceEnvs)).toEqual(["env-b", "env-a"]);
      expect(s.workspaceEnvs["env-a"]).toMatchObject({
        id: "env-a",
        name: "A",
        adoptedAt: "2026-09-17T00:00:00Z",
      });
      expect(s.byEnv["env-a"]).toEqual(emptyEnvState());
    });

    it("adoptEnvironment() is idempotent by id", () => {
      useEnvironmentsStore
        .getState()
        .adoptEnvironment({ id: "env-a", name: "A" }, "t0");
      useEnvironmentsStore.getState().patchEnvState("env-a", {
        scenarios: [{ id: "s1" }],
      });
      // A second adopt with the same id must not overwrite the record or wipe
      // the state.
      useEnvironmentsStore
        .getState()
        .adoptEnvironment({ id: "env-a", name: "A renamed" }, "t1");

      const s = useEnvironmentsStore.getState();
      expect(Object.keys(s.workspaceEnvs)).toEqual(["env-a"]);
      expect(s.workspaceEnvs["env-a"].name).toBe("A");
      expect(s.byEnv["env-a"].scenarios).toEqual([{ id: "s1" }]);
    });

    it("patchEnvironment() merges into an adopted record and no-ops otherwise", () => {
      useEnvironmentsStore
        .getState()
        .adoptEnvironment({ id: "env-a", name: "A", buildStatus: "building" }, "t0");
      useEnvironmentsStore
        .getState()
        .patchEnvironment("env-a", { buildStatus: "ready" });
      expect(
        useEnvironmentsStore.getState().workspaceEnvs["env-a"].buildStatus,
      ).toBe("ready");

      useEnvironmentsStore.getState().patchEnvironment("missing", { x: 1 });
      expect(useEnvironmentsStore.getState().workspaceEnvs.missing).toBeUndefined();
    });

    it("patchEnvState() merges into emptyEnvState for an unseen env", () => {
      useEnvironmentsStore
        .getState()
        .patchEnvState("env-a", { seededFromTemplate: true, evals: ["e1"] });
      expect(useEnvironmentsStore.getState().byEnv["env-a"]).toEqual({
        ...emptyEnvState(),
        seededFromTemplate: true,
        evals: ["e1"],
      });
    });

    it("recordRun() upserts by id keeping position", () => {
      useEnvironmentsStore.getState().recordRun("env-a", { id: "r1", status: "running" });
      useEnvironmentsStore.getState().recordRun("env-a", { id: "r2", status: "running" });
      // Update r1 in place — it must keep its position (index 1), not jump to front.
      useEnvironmentsStore.getState().recordRun("env-a", { id: "r1", status: "passed" });

      const runs = useEnvironmentsStore.getState().byEnv["env-a"].runs;
      expect(runs.map((r) => r.id)).toEqual(["r2", "r1"]);
      expect(runs[1]).toEqual({ id: "r1", status: "passed" });
    });

    it("addAgentVersion() appends to the version list", () => {
      useEnvironmentsStore.getState().addAgentVersion("env-a", { label: "v1" });
      useEnvironmentsStore.getState().addAgentVersion("env-a", { label: "v2" });
      expect(
        useEnvironmentsStore.getState().byEnv["env-a"].agentVersions,
      ).toEqual([{ label: "v1" }, { label: "v2" }]);
    });

    it("forkEnvironment() prepends the fork record and writes its state", () => {
      useEnvironmentsStore
        .getState()
        .adoptEnvironment({ id: "env-a", name: "A" }, "t0");
      useEnvironmentsStore.getState().forkEnvironment("env-a", {
        env: { id: "env-a-fork-1", name: "A · fork" },
        envState: { ...emptyEnvState(), seededFromTemplate: false },
      });

      const s = useEnvironmentsStore.getState();
      expect(Object.keys(s.workspaceEnvs)).toEqual(["env-a-fork-1", "env-a"]);
      expect(s.byEnv["env-a-fork-1"]).toEqual({
        ...emptyEnvState(),
        seededFromTemplate: false,
      });
    });
  });

  describe("scoped reset", () => {
    it("resetEntryState() clears the entry + build slice but keeps env slices", () => {
      useEnvironmentsStore.getState().setChoice("source");
      useEnvironmentsStore.getState().setDraft({ kind: "repo" });
      useEnvironmentsStore
        .getState()
        .acceptAudit({ envId: "env-1", answers: { q: {} } });
      useEnvironmentsStore.getState().retryAll();
      useEnvironmentsStore
        .getState()
        .adoptEnvironment({ id: "env-a", name: "A" }, "t0");
      useEnvironmentsStore.getState().patchEnvState("env-a", { evals: ["e1"] });

      useEnvironmentsStore.getState().resetEntryState();

      const s = useEnvironmentsStore.getState();
      expect(s.choice).toBeNull();
      expect(s.draft).toBeNull();
      expect(s.buildStage).toBeNull();
      expect(s.envId).toBeNull();
      expect(s.readerAnswers).toBeNull();
      expect(s.retriedSections).toEqual([]);
      expect(s.buildProgress).toEqual({
        done: [],
        running: false,
        failure: null,
      });
      // The env slices survive — a visit to Home must not wipe workspaces.
      expect(s.workspaceEnvs["env-a"]).toBeDefined();
      expect(s.byEnv["env-a"].evals).toEqual(["e1"]);
    });

    it("reset() clears the env slices too", () => {
      useEnvironmentsStore
        .getState()
        .adoptEnvironment({ id: "env-a", name: "A" }, "t0");
      useEnvironmentsStore.getState().reset();
      const s = useEnvironmentsStore.getState();
      expect(s.workspaceEnvs).toEqual({});
      expect(s.byEnv).toEqual({});
    });
  });

  describe("persistence", () => {
    it("persists exactly the draft, byEnv and workspaceEnvs to sessionStorage", () => {
      useEnvironmentsStore.getState().setDraft({ kind: "repo" });
      useEnvironmentsStore
        .getState()
        .adoptEnvironment({ id: "env-a", name: "A" }, "t0");
      useEnvironmentsStore
        .getState()
        .acceptAudit({ envId: "env-1", answers: { q: {} } });

      const persisted = JSON.parse(
        sessionStorage.getItem("simulate-environments-draft"),
      );
      expect(Object.keys(persisted.state)).toEqual([
        "draft",
        "byEnv",
        "workspaceEnvs",
      ]);
      expect(persisted.state.draft).toEqual({ kind: "repo" });
      expect(persisted.state.workspaceEnvs["env-a"].name).toBe("A");
    });
  });

  describe("useEnvState(envId, bootstrap)", () => {
    it("returns the bootstrap when the slice is absent and writes it once", () => {
      const bootstrap = {
        ...emptyEnvState(),
        agent: { via: "endpoint" },
        scenarios: [{ id: "s1" }],
      };
      const { result, rerender } = renderHook(() =>
        useEnvState("env-boot", bootstrap),
      );

      // First render: slice absent, so the bootstrap is surfaced and canRun is
      // derived from it.
      expect(result.current.envState).toMatchObject(bootstrap);
      expect(result.current.canRun).toBe(true);

      // The effect wrote the bootstrap into the store exactly once.
      expect(useEnvironmentsStore.getState().byEnv["env-boot"]).toMatchObject(
        bootstrap,
      );

      // A later external change must not be clobbered by a re-bootstrap.
      act(() => {
        useEnvironmentsStore
          .getState()
          .patchEnvState("env-boot", { seededFromTemplate: true });
      });
      rerender();
      expect(
        useEnvironmentsStore.getState().byEnv["env-boot"].seededFromTemplate,
      ).toBe(true);
      expect(useEnvironmentsStore.getState().byEnv["env-boot"].agent).toEqual({
        via: "endpoint",
      });
    });

    it("exposes patch/recordRun/addAgentVersion bound to the env id", () => {
      const { result } = renderHook(() => useEnvState("env-bound"));
      act(() => result.current.patch({ evals: ["e1"] }));
      act(() => result.current.recordRun({ id: "r1", status: "running" }));
      act(() => result.current.addAgentVersion({ label: "v1" }));

      const slice = useEnvironmentsStore.getState().byEnv["env-bound"];
      expect(slice.evals).toEqual(["e1"]);
      expect(slice.runs).toEqual([{ id: "r1", status: "running" }]);
      expect(slice.agentVersions).toEqual([{ label: "v1" }]);
    });
  });
});
