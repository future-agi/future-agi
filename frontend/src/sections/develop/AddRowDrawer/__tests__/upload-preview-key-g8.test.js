// v2.1 g8 (rows:upload-file, proof walk #4): uploading the same file twice
// recomputes the preview once the dataset moved (a new version or a pending
// change), and keeps it while nothing changed.
import { describe, expect, it } from "vitest";
import { filePreviewKey } from "../fileImport";

const file = { name: "rows.csv", text: "a,b\n1,2\n" };

describe("upload preview key", () => {
  it("same file, same dataset state: same key", () => {
    expect(
      filePreviewKey({ datasetId: "d", versionKey: "v7", source: file }),
    ).toEqual(
      filePreviewKey({ datasetId: "d", versionKey: "v7", source: { ...file } }),
    );
  });

  it("a saved version or a pending change gives a new key", () => {
    const a = filePreviewKey({
      datasetId: "d",
      versionKey: "v7",
      pendingCount: 0,
      source: file,
    });
    expect(
      filePreviewKey({
        datasetId: "d",
        versionKey: "v8",
        pendingCount: 0,
        source: file,
      }),
    ).not.toEqual(a);
    expect(
      filePreviewKey({
        datasetId: "d",
        versionKey: "v7",
        pendingCount: 3,
        source: file,
      }),
    ).not.toEqual(a);
  });

  it("the dataset prefix clears every preview of the dataset", () => {
    expect(filePreviewKey({ datasetId: "d" }).slice(0, 2)).toEqual([
      "im1-file-preview",
      "d",
    ]);
  });
});
