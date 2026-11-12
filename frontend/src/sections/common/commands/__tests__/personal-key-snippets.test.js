import { describe, expect, it } from "vitest";
import { fiGetRequest, fiRequest, requestSnippets } from "../requestSnippets";

describe("personal API key snippets", () => {
  it.each([
    ["GET", fiGetRequest("/model-hub/datasets/d/rows/r/")],
    ["POST", fiRequest("/model-hub/datasets/imports/", { op: "commit" })],
  ])(
    "documents the key pair and Keys page for %s in every language",
    (_, request) => {
      const snippets = requestSnippets(request);
      expect(Object.keys(snippets)).toEqual(["curl", "python", "typescript"]);
      for (const snippet of Object.values(snippets)) {
        expect(snippet).toContain("/dashboard/keys");
        expect(snippet).toContain("X-Api-Key");
        expect(snippet).toContain("X-Secret-Key");
        expect(snippet).toContain("FI_API_KEY");
        expect(snippet).toContain("FI_SECRET_KEY");
        expect(snippet).not.toMatch(/Authorization|Bearer|JWT|Cookie/i);
      }
      expect(snippets.curl).toContain('-H "X-Api-Key: $FI_API_KEY"');
      expect(snippets.curl).toContain('-H "X-Secret-Key: $FI_SECRET_KEY"');
      expect(snippets.python).toContain(
        '"X-Api-Key": os.environ["FI_API_KEY"]',
      );
      expect(snippets.python).toContain(
        '"X-Secret-Key": os.environ["FI_SECRET_KEY"]',
      );
      expect(snippets.typescript).toContain(
        '"X-Api-Key": process.env.FI_API_KEY!',
      );
      expect(snippets.typescript).toContain(
        '"X-Secret-Key": process.env.FI_SECRET_KEY!',
      );
    },
  );

  it("never copies session or literal credential headers from a caller", () => {
    const value = ["do", "not", "copy"].join("-");
    const request = fiGetRequest("/model-hub/datasets/d/rows/r/");
    request.headers = {
      authorization: value,
      "Proxy-Authorization": value,
      Cookie: value,
      "x-api-key": value,
      "X-SECRET-KEY": value,
      "X-Workspace-Id": "workspace-placeholder",
    };
    for (const snippet of Object.values(requestSnippets(request))) {
      // Boolean assertions do not print a credential value if the test fails.
      expect(snippet.includes(value)).toBe(false);
      expect(/authorization|cookie/i.test(snippet)).toBe(false);
      expect(snippet).toContain("FI_API_KEY");
      expect(snippet).toContain("FI_SECRET_KEY");
      expect(snippet).toContain("X-Workspace-Id");
    }
  });
});
