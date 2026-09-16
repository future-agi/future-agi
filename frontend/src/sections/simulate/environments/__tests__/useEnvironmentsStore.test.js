import { describe, it, expect, beforeEach } from "vitest";
import {
  useEnvironmentsStore,
  resetEnvironmentsStore,
  BUILD_STAGE,
} from "../store/useEnvironmentsStore";

describe("useEnvironmentsStore", () => {
  beforeEach(() => {
    sessionStorage.clear();
    resetEnvironmentsStore();
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
      useEnvironmentsStore
        .getState()
        .acceptAudit({ envId: "env-1", answers });
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

  describe("draft persist", () => {
    it("persists only the draft to sessionStorage", () => {
      useEnvironmentsStore.getState().setDraft({ kind: "repo" });
      useEnvironmentsStore
        .getState()
        .acceptAudit({ envId: "env-1", answers: { q: {} } });

      const persisted = JSON.parse(
        sessionStorage.getItem("simulate-environments-draft"),
      );
      expect(Object.keys(persisted.state)).toEqual(["draft"]);
      expect(persisted.state.draft).toEqual({ kind: "repo" });
    });
  });
});
