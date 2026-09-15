import { describe, it, expect, beforeEach } from "vitest";
import {
  useEnvironmentsStore,
  resetEnvironmentsStore,
} from "../store/useEnvironmentsStore";

describe("useEnvironmentsStore", () => {
  beforeEach(() => resetEnvironmentsStore());

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
});
