import { afterEach, describe, expect, it } from "vitest";
import { MAX_DESIGN_DRAFTS, useDesignDraftStore } from "../useDesignDraftStore";

describe("useDesignDraftStore", () => {
  afterEach(() => {
    useDesignDraftStore.getState().reset();
    localStorage.clear();
  });

  it("keeps only the most recent drafts, dropping the oldest", () => {
    const drafts = Object.fromEntries(
      Array.from({ length: MAX_DESIGN_DRAFTS }, (_, i) => [
        `d${i}`,
        { id: `d${i}`, files: [], createdAt: i + 1 },
      ]),
    );
    useDesignDraftStore.setState({ drafts });

    const id = useDesignDraftStore
      .getState()
      .createDesignDraft({ description: "Refund requests by email" });

    const kept = useDesignDraftStore.getState().drafts;
    expect(Object.keys(kept)).toHaveLength(MAX_DESIGN_DRAFTS);
    expect(kept.d0).toBeUndefined();
    expect(kept[id]).toBeDefined();
  });
});
