import { describe, expect, it } from "vitest";

import { moveProblem } from "../lib/files";

describe("the folder a file moves into (unit)", () => {
  const file = { from: "notes.txt", directory: false };
  const nested = { from: "src/app/main.py", directory: false };
  const folder = { from: "src", directory: true };

  it("takes a folder inside /workspace however it is typed", () => {
    expect(moveProblem("docs", file)).toBeNull();
    expect(moveProblem("/workspace/docs/", file)).toBeNull();
    expect(moveProblem("./docs/sub", file)).toBeNull();
    expect(moveProblem("/workspace", nested)).toBeNull();
    expect(moveProblem("lib", folder)).toBeNull();
  });

  it("refuses what the Run tab refuses for its working folder", () => {
    expect(moveProblem("/etc", file)).toBe("Use a folder inside /workspace");
    expect(moveProblem("../etc", file)).toBe(
      "Use a folder inside /workspace, without . or ..",
    );
    expect(moveProblem("docs\\sub", file)).toBe("Use / between folders");
  });

  it("asks for a folder, and refuses the one it is already in", () => {
    expect(moveProblem("", file)).toBe("Choose a folder.");
    expect(moveProblem("  ", file)).toBe("Choose a folder.");
    expect(moveProblem("/workspace", file)).toBe(
      "notes.txt is already in /workspace.",
    );
    expect(moveProblem("src/app/", nested)).toBe(
      "main.py is already in /workspace/src/app.",
    );
  });

  it("never moves a folder into itself or a folder inside it", () => {
    expect(moveProblem("src", folder)).toBe(
      "A folder can't be moved into itself.",
    );
    expect(moveProblem("src/inner", folder)).toBe(
      "A folder can't be moved into itself.",
    );
    // Only the folder itself: a sibling that starts with its name is fine.
    expect(moveProblem("src-old", folder)).toBeNull();
  });
});
