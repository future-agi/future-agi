import { describe, it, expect } from "vitest";
import {
  summarizeAddResults,
  addResultToast,
} from "../add-items-results";

// Backend body is { status, result: { added, duplicates, errors } }; the axios
// interceptor hands back the full response, so it lives at resp.data.result.
const resp = (added, duplicates = 0, errors = []) => ({
  data: { result: { added, duplicates, errors } },
});

describe("summarizeAddResults", () => {
  it("accumulates added/duplicates/errors across batched responses", () => {
    const s = summarizeAddResults([
      resp(2, 1, ["Not found: observation_span=a"]),
      resp(3, 0, []),
      resp(0, 2, ["Not found: observation_span=b"]),
    ]);
    expect(s).toEqual({
      added: 5,
      duplicates: 3,
      errors: [
        "Not found: observation_span=a",
        "Not found: observation_span=b",
      ],
    });
  });

  it("tolerates an unwrapped body and missing fields", () => {
    expect(summarizeAddResults([{ data: { added: 4 } }, {}])).toEqual({
      added: 4,
      duplicates: 0,
      errors: [],
    });
  });
});

describe("addResultToast", () => {
  it("reports a real success", () => {
    expect(addResultToast({ added: 3, duplicates: 0, errors: [] })).toEqual({
      message: "3 items added to queue",
      variant: "success",
    });
  });

  it("warns when some succeeded but others were skipped", () => {
    const t = addResultToast({ added: 2, duplicates: 1, errors: ["x"] });
    expect(t.variant).toBe("warning");
    expect(t.message).toContain("2 items added");
    expect(t.message).toContain("1 already in queue");
  });

  // The core regression: added:0 with errors used to render a green
  // "3 items added" (the requested count). It must now be an error toast.
  it("surfaces an error instead of a false success when nothing was added", () => {
    const t = addResultToast({
      added: 0,
      duplicates: 0,
      errors: ["Not found: observation_span=a"],
    });
    expect(t.variant).toBe("error");
    expect(t.message).toContain("Not found: observation_span=a");
  });

  it("reports all-duplicates as info, not success", () => {
    const t = addResultToast({ added: 0, duplicates: 3, errors: [] });
    expect(t.variant).toBe("info");
    expect(t.message).toContain("already in the queue");
  });

  it("never claims success when added is 0", () => {
    expect(
      addResultToast({ added: 0, duplicates: 0, errors: [] }).variant,
    ).not.toBe("success");
  });
});
