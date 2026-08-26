import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("src/utils/axios", () => ({
  default: { post: vi.fn(() => Promise.resolve({ data: { ok: true } })) },
}));

import axios from "src/utils/axios";
import { cancelHarnessJob } from "./harness";

const JOB_ID = "d1fd7560-0143-4cb8-88ed-36518b848cbf";
const URL = `/simulate/api/harness-jobs/${JOB_ID}/cancel/`;

describe("cancelHarnessJob", () => {
  beforeEach(() => {
    axios.post.mockClear();
  });

  // The endpoint is runtimeRequestValidation: true against HarnessJobAction. Sending no
  // body makes the validator parse `undefined` and reject before the request is sent,
  // which is indistinguishable in the UI from a dead button.
  it("always sends an object body", async () => {
    await cancelHarnessJob(JOB_ID);
    expect(axios.post).toHaveBeenCalledWith(URL, {});
  });

  it("includes a reason when one is given", async () => {
    await cancelHarnessJob(JOB_ID, "took too long");
    expect(axios.post).toHaveBeenCalledWith(URL, { reason: "took too long" });
  });

  it("omits an empty or whitespace reason rather than sending it", async () => {
    await cancelHarnessJob(JOB_ID, "   ");
    expect(axios.post).toHaveBeenCalledWith(URL, {});
  });

  it("keeps reason within the contract's 500 character limit", async () => {
    await cancelHarnessJob(JOB_ID, "x".repeat(600));
    const [, body] = axios.post.mock.calls[0];
    expect(body.reason).toHaveLength(500);
  });
});
