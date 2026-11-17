import { describe, expect, it, vi } from "vitest";
import { EDIT_FAILED_FALLBACK, postEditShowingAnswer } from "../editAnswer";

const URL = "/model-hub/develops/dataset-1/edit_and_run_user_eval/eval-1/";
const BODY = { config: {}, run: true, experiment_id: "exp-1" };
const HTTP_SAVED =
  "Saved. This experiment calls an HTTP endpoint, so a row is scored with this change only when it is sent again. Retry failed rows from the Data tab or a failed cell; to score every row, create a new experiment. The scores shown are from before your change.";
const QUEUED = "Column evaluation updated and queued for processing";
const RUN_GOING =
  "A run of this experiment is going. Update this evaluation when it finishes. Nothing was saved.";

describe("postEditShowingAnswer", () => {
  it("shows the server's own sentence when the edit is saved", async () => {
    const response = { data: { status: true, result: HTTP_SAVED } };
    const post = vi.fn().mockResolvedValue(response);
    const notify = vi.fn();

    await expect(postEditShowingAnswer(post, URL, BODY, notify)).resolves.toBe(
      response,
    );

    expect(post).toHaveBeenCalledWith(URL, BODY);
    expect(notify).toHaveBeenCalledTimes(1);
    expect(notify).toHaveBeenCalledWith(HTTP_SAVED, { variant: "info" });
    expect(notify).not.toHaveBeenCalledWith(QUEUED, expect.anything());
  });

  it("shows the queued sentence where the work is queued", async () => {
    const post = vi
      .fn()
      .mockResolvedValue({ data: { status: true, result: QUEUED } });
    const notify = vi.fn();

    await postEditShowingAnswer(post, URL, BODY, notify);

    expect(notify).toHaveBeenCalledWith(QUEUED, { variant: "info" });
  });

  it("shows nothing when the answer carries no sentence", async () => {
    const post = vi.fn().mockResolvedValue({ data: { status: true } });
    const notify = vi.fn();

    await postEditShowingAnswer(post, URL, BODY, notify);

    expect(notify).not.toHaveBeenCalled();
  });

  it("shows a refusal's own sentence and keeps the editor open", async () => {
    const refusal = { statusCode: 400, result: RUN_GOING };
    const post = vi.fn().mockRejectedValue(refusal);
    const notify = vi.fn();

    await expect(postEditShowingAnswer(post, URL, BODY, notify)).rejects.toBe(
      refusal,
    );

    expect(notify).toHaveBeenCalledTimes(1);
    expect(notify).toHaveBeenCalledWith(RUN_GOING, { variant: "error" });
  });

  it("says the update failed when a refusal carries no sentence", async () => {
    const post = vi.fn().mockRejectedValue({ statusCode: 500 });
    const notify = vi.fn();

    await expect(
      postEditShowingAnswer(post, URL, BODY, notify),
    ).rejects.toBeTruthy();

    expect(notify).toHaveBeenCalledWith(EDIT_FAILED_FALLBACK, {
      variant: "error",
    });
  });
});
