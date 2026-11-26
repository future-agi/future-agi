import { afterEach, describe, expect, it, vi } from "vitest";
import { useDesignDraftStore } from "src/sections/simulate/environments/design/useDesignDraftStore";
import { STAGE_MS, VALIDATE_MS } from "../designBuild";
import { DraftReviewSchema } from "../draftReview.contract";
import {
  designDraftsOf,
  designEnvironmentOf,
  designReviewOf,
  designVersionsOf,
  saveDesignDrafts,
} from "../designEnvironment";

const store = () => useDesignDraftStore.getState();

describe("designEnvironment", () => {
  afterEach(() => {
    vi.useRealTimers();
    store().reset();
    localStorage.clear();
  });

  it("fills in as the build runs, waits for review, and saves v1 only on Save", () => {
    vi.useFakeTimers();
    const id = store().createDesignDraft({
      description: "A customer asks for a refund on a late order.",
    });
    store().startDesignBuild(id);

    const first = designEnvironmentOf(store().drafts[id]);
    expect(first.env.seed.tables).toEqual([]);
    expect(first.envState).toBeNull();

    vi.advanceTimersByTime(STAGE_MS);
    expect(designEnvironmentOf(store().drafts[id]).env.seed.tables).not.toEqual(
      [],
    );
    expect(designReviewOf(id).changes).toEqual([]);

    vi.advanceTimersByTime(3 * STAGE_MS + VALIDATE_MS);
    const review = designReviewOf(id);
    expect(() => DraftReviewSchema.parse(review)).not.toThrow();
    expect(review.changes.length).toBeGreaterThan(0);
    expect(review.counts.undecided).toBe(review.changes.length);
    expect(designDraftsOf(id).environment).toBeDefined();
    expect(designVersionsOf(id).environment).toEqual([]);

    saveDesignDrafts(id, "First");
    expect(designVersionsOf(id).environment[0]).toMatchObject({
      label: "v1",
      is_default: true,
    });
    expect(designDraftsOf(id)).toEqual({});
    expect(designReviewOf(id).changes).toEqual([]);
  });
});
