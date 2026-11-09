// v2.1 g8 (org:filter): a refused filter link shows the server's sentence.
// The app's axios rejects with `{...body, statusCode}`, not `response`.
import { describe, expect, it } from "vitest";
import { queryErrorSentence } from "../useDatasetListQuery";

describe("queryErrorSentence", () => {
  it("reads the app axios error shape", () => {
    expect(
      queryErrorSentence({
        statusCode: 400,
        status: false,
        result: "Filtering by Last pass isn't supported by this list.",
      }),
    ).toBe("Filtering by Last pass isn't supported by this list.");
  });

  it("still reads a raw axios error", () => {
    expect(
      queryErrorSentence({
        response: { status: 400, data: { result: "Unknown field “x”." } },
      }),
    ).toBe("Unknown field “x”.");
  });

  it("is null for other statuses or a non-sentence body", () => {
    expect(queryErrorSentence({ statusCode: 500, result: "x" })).toBeNull();
    expect(queryErrorSentence({ statusCode: 400, result: {} })).toBeNull();
    expect(queryErrorSentence(null)).toBeNull();
  });
});
