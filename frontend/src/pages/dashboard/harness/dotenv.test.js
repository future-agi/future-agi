import { describe, expect, it } from "vitest";

import { parseDotEnv } from "./dotenv";

describe("parseDotEnv", () => {
  it("parses common dotenv syntax without exposing or transforming secrets", () => {
    expect(
      parseDotEnv(`
        # provider credentials
        export OPENAI_API_KEY="sk-test=value"
        DATABASE_URL='postgres://db/test'
        REGION=us-east-1 # deployment region
        EMPTY=
      `),
    ).toEqual({
      OPENAI_API_KEY: "sk-test=value",
      DATABASE_URL: "postgres://db/test",
      REGION: "us-east-1",
      EMPTY: "",
    });
  });

  it("rejects malformed assignments", () => {
    expect(() => parseDotEnv("NOT-VALID=value")).toThrow(
      "Invalid environment name",
    );
    expect(() => parseDotEnv('TOKEN="not closed')).toThrow(
      "Unclosed double quote",
    );
  });
});
