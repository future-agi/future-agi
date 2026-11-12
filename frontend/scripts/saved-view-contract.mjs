/* eslint-env node */
import fs from "node:fs";
import path from "node:path";

// Orval drops Swagger 2.0 x-nullable. Keep this compatibility adapter tied to
// the serializer's schema rather than changing the wire contract or hand-editing
// generated clients. Remove it when the generator preserves x-nullable natively.
export function normalizeSavedViewContract(schemaPath, outputDir) {
  const schema = JSON.parse(fs.readFileSync(schemaPath, "utf8"));
  const cursor = schema.definitions?.DatasetViewListResult?.properties?.next;
  if (
    cursor?.type !== "string" ||
    cursor.format !== "uuid" ||
    cursor["x-nullable"] !== true
  ) {
    throw new Error(
      "Saved-view cursor must be a nullable UUID in the source schema",
    );
  }

  const replaceInExport = (file, declaration, pattern, replacement) => {
    const filename = path.join(outputDir, file);
    const text = fs.readFileSync(filename, "utf8");
    const start = text.indexOf(declaration);
    if (start < 0)
      throw new Error(`Missing generated declaration: ${declaration}`);
    const next = text.indexOf("\nexport ", start + declaration.length);
    const end = next < 0 ? text.length : next;
    const block = text.slice(start, end);
    if (!pattern.test(block)) {
      throw new Error(`Generated cursor changed shape: ${declaration}`);
    }
    fs.writeFileSync(
      filename,
      text.slice(0, start) +
        block.replace(pattern, replacement) +
        text.slice(end),
    );
  };

  replaceInExport(
    "api.schemas.ts",
    "export interface DatasetViewListResultApi {",
    /next: string;/,
    "next: string | null;",
  );
  replaceInExport(
    "api.zod.ts",
    "export const ModelHubR3SavedViewsListResponse =",
    /("?next"?: zod\.string\(\)\.uuid\(\))/,
    "$1.nullable()",
  );
}
