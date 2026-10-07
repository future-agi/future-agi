import { describe, expect, it } from "vitest";
import { prepareSourceFolder, MAX_UPLOAD_FILES } from "./sourceUpload";

const file = (path, size = 1) => ({
  name: path.split("/").at(-1),
  size,
  webkitRelativePath: path,
});

describe("prepareSourceFolder", () => {
  it("removes the browser folder root and excludes secrets and dependencies", () => {
    const result = prepareSourceFolder([
      file("agent/agent.py", 10),
      file("agent/.env", 20),
      file("agent/.env.example", 30),
      file("agent/node_modules/pkg/index.js", 40),
    ]);

    expect(result.name).toBe("agent");
    expect(result.paths).toEqual(["agent.py", ".env.example"]);
    expect(result.excludedCount).toBe(2);
    expect(result.totalBytes).toBe(40);
  });

  it("rejects a folder containing only excluded files", () => {
    expect(() => prepareSourceFolder([file("agent/.env")])).toThrow(
      "no uploadable source files",
    );
  });

  it("rejects a folder above the runner's file-count limit before uploading", () => {
    const many = Array.from({ length: MAX_UPLOAD_FILES + 1 }, (_, i) =>
      file(`agent/src/f${i}.py`),
    );
    expect(() => prepareSourceFolder(many)).toThrow(
      `at most ${MAX_UPLOAD_FILES}`,
    );
  });

  it("accepts a folder exactly at the file-count limit", () => {
    const exact = Array.from({ length: MAX_UPLOAD_FILES }, (_, i) =>
      file(`agent/src/f${i}.py`),
    );
    expect(() => prepareSourceFolder(exact)).not.toThrow();
  });
});
