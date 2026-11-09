import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import { ModelHubDatasetsTransferCreateResponse } from "src/generated/api-contracts/api.zod";

// DatasetTransferResultSerializer and dataset_transfer.describe emit this copy preview.
const preview = {
  status: true,
  result: {
    mode: "copy",
    dry_run: true,
    source_dataset_id: "bc523a55-4378-4090-a0bd-d7041ca94082",
    dataset_id: null,
    dataset_name: "Copy of source",
    from_workspace: {
      id: "bc523a55-4378-4090-a0bd-d7041ca94083",
      name: "Source workspace",
    },
    to_workspace: {
      id: "bc523a55-4378-4090-a0bd-d7041ca94084",
      name: "Target workspace",
    },
    plan: {
      rows: 2,
      columns: 2,
      columns_left_out: 0,
      leaves_folder: false,
    },
  },
};

const parseResult = (fields) =>
  ModelHubDatasetsTransferCreateResponse.safeParse({
    ...preview,
    result: { ...preview.result, ...fields },
  });

describe("generated dataset transfer response contract", () => {
  it("emits a required nullable TypeScript ID without widening the source ID", () => {
    const schemas = fs.readFileSync(
      path.resolve("src/generated/api-contracts/api.schemas.ts"),
      "utf8",
    );
    const declaration = schemas.match(
      /^export interface DatasetTransferResultApi \{([\s\S]*?)^\}/m,
    );
    expect(declaration).not.toBeNull();
    expect(declaration[1]).toMatch(/^ {2}dataset_id: string \| null;$/m);
    expect(declaration[1]).toMatch(/^ {2}source_dataset_id: string;$/m);
  });

  it("accepts the explicit null dataset ID of a copy preview", () => {
    expect(ModelHubDatasetsTransferCreateResponse.parse(preview)).toEqual(
      preview,
    );
  });

  it.each([
    ["copy", false],
    ["move", true],
    ["move", false],
  ])("accepts a UUID for %s with dry_run=%s", (mode, dry_run) => {
    expect(
      parseResult({
        mode,
        dry_run,
        dataset_id: "bc523a55-4378-4090-a0bd-d7041ca94085",
      }).success,
    ).toBe(true);
  });

  it.each([undefined, "", "not-a-uuid", 17])(
    "rejects a missing or invalid dataset ID: %s",
    (dataset_id) => {
      const parsed = parseResult({ dataset_id });
      expect(parsed.success).toBe(false);
      if (!parsed.success) {
        expect(parsed.error.issues.map((issue) => issue.path)).toContainEqual([
          "result",
          "dataset_id",
        ]);
      }
    },
  );

  it.each([null, undefined, "not-a-uuid"])(
    "does not widen the source dataset ID to accept %s",
    (source_dataset_id) => {
      const parsed = parseResult({ source_dataset_id });
      expect(parsed.success).toBe(false);
      if (!parsed.success) {
        expect(parsed.error.issues.map((issue) => issue.path)).toContainEqual([
          "result",
          "source_dataset_id",
        ]);
      }
    },
  );
});
