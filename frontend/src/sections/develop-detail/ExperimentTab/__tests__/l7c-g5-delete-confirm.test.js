// L7c g5 (lb:delete): Delete asks first, then deletes at once. The confirm
// names what goes, says a running run is stopped, and promises no Undo.
import { describe, expect, it } from "vitest";
import { deleteConfirmBody } from "../useLeaderboardActions";

describe("deleteConfirmBody", () => {
  it("names the runs that go and promises nothing back", () => {
    const one = deleteConfirmBody({ ids: ["e3"], runCount: 1, running: [] });
    expect(one).toBe(
      "1 run and its results are deleted. This cannot be undone.",
    );
    expect(one).not.toMatch(/undo for|Undo/);
  });
  it("says which runs are stopped first", () => {
    expect(
      deleteConfirmBody({
        ids: ["e7"],
        runCount: 2,
        running: [{ label: "#7A" }, { label: "#7B" }],
      }),
    ).toBe(
      "#7A, #7B are still running and will be stopped. 2 runs and their results are deleted. This cannot be undone.",
    );
  });
});
