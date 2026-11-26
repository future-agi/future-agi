import { describe, expect, it } from "vitest";
import { overUploadLimit, participantFilesOf } from "../sources.constants";

const MB = 1024 * 1024;

describe("participantFilesOf", () => {
  it("gives the agent its own and tool files, the grader every file, the caller none", () => {
    const files = participantFilesOf([
      { name: "guide.md", visibility: "agent" },
      { name: "orders.csv", visibility: "tool" },
      { name: "answers.json", visibility: "evaluator" },
    ]);
    expect(files.agent.map(({ source }) => source.name)).toEqual([
      "guide.md",
      "orders.csv",
    ]);
    expect(files.caller).toEqual([]);
    expect(files.evaluator).toHaveLength(3);
  });
});

describe("overUploadLimit", () => {
  const limits = {
    max_files: 2,
    max_file_bytes: 25 * MB,
    max_total_bytes: 30 * MB,
  };

  it("lets a pick within every limit through", () => {
    expect(overUploadLimit([{ size: MB }, { size: MB }], limits)).toBeNull();
  });

  it("refuses too many files, a file too big, or too much in all", () => {
    expect(
      overUploadLimit([{ size: 1 }, { size: 1 }, { size: 1 }], limits),
    ).not.toBeNull();
    expect(overUploadLimit([{ size: 26 * MB }], limits)).not.toBeNull();
    expect(
      overUploadLimit([{ size: 20 * MB }, { size: 20 * MB }], limits),
    ).not.toBeNull();
  });
});
