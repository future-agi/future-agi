import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { ModelHubR3SavedViewsListResponse } from "src/generated/api-contracts/api.zod";

const response = (next) => ({ status: true, result: { results: [], next } });

describe("saved-view continuation contract", () => {
  it("accepts the terminal page returned by the real list endpoint", () => {
    expect(ModelHubR3SavedViewsListResponse.parse(response(null))).toEqual(
      response(null),
    );
  });

  it("accepts UUID cursors but rejects malformed or missing continuations", () => {
    const next = "00000000-0000-4000-8000-000000000001";
    expect(ModelHubR3SavedViewsListResponse.parse(response(next))).toEqual(
      response(next),
    );
    for (const invalid of ["", "not-a-uuid", 0, {}, undefined]) {
      expect(
        ModelHubR3SavedViewsListResponse.safeParse(response(invalid)).success,
      ).toBe(false);
    }
  });

  it("exposes the same nullable cursor to TypeScript consumers", () => {
    const source = fs.readFileSync(
      path.resolve("src/generated/api-contracts/api.schemas.ts"),
      "utf8",
    );
    const block = source.match(
      /export interface DatasetViewListResultApi \{([^}]+)\}/,
    );
    expect(block?.[1]).toMatch(/next: string \| null;/);
  });
});
