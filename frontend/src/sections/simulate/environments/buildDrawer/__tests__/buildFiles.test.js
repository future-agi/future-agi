import { describe, expect, it } from "vitest";
import { mergeFileRows } from "../forms/shared/buildFiles";

const file = (name, size = 1000) => ({ id: `${name}-${size}`, name, size });

describe("mergeFileRows", () => {
  it("replaces a same-name file with new contents and skips an identical one", () => {
    const { files, note } = mergeFileRows(
      [file("orders.csv", 100), file("policy.md", 50)],
      [file("orders.csv", 200), file("policy.md", 50), file("faq.md")],
    );
    expect(files.map((f) => [f.name, f.size])).toEqual([
      ["orders.csv", 200],
      ["policy.md", 50],
      ["faq.md", 1000],
    ]);
    expect(note).toEqual({
      added: ["faq.md"],
      replaced: ["orders.csv"],
      skipped: ["policy.md"],
    });
  });
});
