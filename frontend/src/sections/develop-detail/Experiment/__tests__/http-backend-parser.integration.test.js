import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import process from "node:process";
import { describe, expect, it } from "vitest";
import {
  ModelHubExperimentsV2CreateBody,
  ModelHubExperimentsV2UpdateBody,
} from "src/generated/api-contracts/api.zod";

// Repository root: the nearest directory at or above the working directory that holds api_contracts/
// (vitest runs from frontend/). Resolved from the working directory, not import.meta.url, so this file
// runs in the default jsdom environment with the shared setup.
const REPO_ROOT = (() => {
  let dir = process.cwd();
  while (!existsSync(join(dir, "api_contracts"))) {
    const parent = dirname(dir);
    if (parent === dir) throw new Error(`api_contracts/ not found at or above ${process.cwd()}`);
    dir = parent;
  }
  return dir;
})();
const repoFile = (relative) => join(REPO_ROOT, relative);

const fixture = JSON.parse(readFileSync(repoFile("api_contracts/fixtures/http-experiment-create.json"), "utf8"));

describe("HTTP parsed-entry backend compatibility", () => {
  it.each([
    ["create", ModelHubExperimentsV2CreateBody],
    ["update", ModelHubExperimentsV2UpdateBody],
  ])("%s emits exactly the fixture accepted by the strict backend serializer", (operation, schema) => {
    const parsed = schema.parse(fixture);
    // The backend's test_generated_http_parse_fixture_passes_strict_backend_entry_serializer
    // validates this same entry, closing the parser -> serializer contract.
    expect(parsed.prompt_config, operation).toEqual(fixture.prompt_config);
    for (const entry of parsed.prompt_config) {
      for (const field of ["model_params", "configuration", "output_format", "model", "prompt_id", "prompt_version", "agent_id", "agent_version"]) {
        expect(entry).not.toHaveProperty(field);
      }
    }
  });
});
