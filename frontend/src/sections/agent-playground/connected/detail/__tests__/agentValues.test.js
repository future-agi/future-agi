import { describe, it, expect } from "vitest";
import {
  changeBody,
  draftEmpty,
  draftSummary,
  emptyDraft,
  newNameProblem,
  removeName,
  setValue,
  summaryLines,
  undoName,
} from "../agentValues";

const rows = [
  { name: "DB_URL", required: true, madeByEnvironment: false },
  { name: "AGENT_API_TOKEN", required: false, madeByEnvironment: true },
];

describe("agentValues", () => {
  it("keeps a name set or removed, never both", () => {
    let draft = setValue(emptyDraft(), "DB_URL", "pg://x");
    draft = removeName(draft, "DB_URL");
    expect(draft).toEqual({ set: {}, remove: ["DB_URL"] });
    draft = setValue(draft, "DB_URL", "pg://y");
    expect(draft).toEqual({ set: { DB_URL: "pg://y" }, remove: [] });
    expect(draftEmpty(undoName(draft, "DB_URL"))).toBe(true);
  });

  it("sends only the parts the change has", () => {
    expect(changeBody(removeName(emptyDraft(), "A"))).toEqual({
      kind: "credentials",
      remove_names: ["A"],
    });
    expect(changeBody(setValue(emptyDraft(), "A", "1"))).toEqual({
      kind: "credentials",
      secret_values: { A: "1" },
    });
  });

  it("says what changes and whether the names change", () => {
    let draft = setValue(emptyDraft(), "NEW", "1");
    draft = setValue(draft, "DB_URL", "2");
    const summary = draftSummary(removeName(draft, "AGENT_API_TOKEN"), rows);
    expect(summary).toEqual({
      added: ["NEW"],
      changed: ["DB_URL"],
      removed: ["AGENT_API_TOKEN"],
      namesChange: true,
      removedRequired: [],
    });
    expect(summaryLines(summary)).toEqual([
      "Adds NEW",
      "Changes DB_URL",
      "Removes AGENT_API_TOKEN",
    ]);
    const rotate = draftSummary(setValue(emptyDraft(), "DB_URL", "3"), rows);
    expect(rotate.namesChange).toBe(false);
  });

  it("names a required value being removed", () => {
    expect(
      draftSummary(removeName(emptyDraft(), "DB_URL"), rows).removedRequired,
    ).toEqual(["DB_URL"]);
  });

  it.each([
    ["", "Enter the variable's name"],
    ["1BAD", "Letters, digits and underscores, not starting with a digit"],
    ["DB_URL", "This agent already has this value; change it in its row"],
    [
      "agent_api_token",
      "The test environment makes this value itself; yours isn't needed",
    ],
    ["NEW_ONE", null],
  ])("checks the new name %j", (name, problem) => {
    expect(
      newNameProblem(name, rows, ["AGENT_API_TOKEN", "agent_api_token"]),
    ).toBe(problem);
  });
});
