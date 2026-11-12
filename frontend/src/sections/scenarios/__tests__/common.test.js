import { describe, it, expect } from "vitest";
import {
  CreateScenarioDefaultValues,
  CreateScenarioType,
  CreateScenarioValidationSchema,
} from "../common";

// Messages the create form shows under "No. of scenarios" for a given count.
// The rest of the default form is incomplete (no source, no name), so only
// the issues on this one field are read.
const noOfRowsErrors = (noOfRows) => {
  const result = CreateScenarioValidationSchema.safeParse({
    ...CreateScenarioDefaultValues,
    noOfRows,
  });
  if (result.success) return [];
  return result.error.issues
    .filter((issue) => issue.path[0] === "noOfRows")
    .map((issue) => issue.message);
};

describe("Create scenario: No. of scenarios validation", () => {
  it("asks for at least 10 scenarios in plain words", () => {
    expect(noOfRowsErrors(3)).toEqual(["Enter at least 10 scenarios"]);
  });

  it("asks for 20,000 scenarios or fewer in plain words", () => {
    expect(noOfRowsErrors(20001)).toEqual(["Enter 20,000 scenarios or fewer"]);
  });

  // Guard: the rule itself is unchanged (holds at the base too).
  it("still accepts 10 and 20,000", () => {
    expect(noOfRowsErrors(10)).toEqual([]);
    expect(noOfRowsErrors(20000)).toEqual([]);
  });
});

// Messages the create form shows under the upload box on the "Upload script"
// and "Call / Chat SOP" tabs. Only the issues on that one field are read.
const fileErrors = (kind, field, value) => {
  const result = CreateScenarioValidationSchema.safeParse({
    ...CreateScenarioDefaultValues,
    kind,
    config: { [field]: value },
  });
  if (result.success) return [];
  return result.error.issues
    .filter((issue) => issue.path.join(".") === `config.${field}`)
    .map((issue) => issue.message);
};

describe("Create scenario: script and SOP file validation", () => {
  const { SCRIPT, SOP } = CreateScenarioType;

  // "" is the value the tab starts with; null is what the trash icon sets.
  it("asks for a script file in plain words when none was uploaded", () => {
    expect(fileErrors(SCRIPT, "scriptUrl", "")).toEqual([
      "Upload a script file",
    ]);
  });

  it("asks for a script file in plain words after the file is removed", () => {
    expect(fileErrors(SCRIPT, "scriptUrl", null)).toEqual([
      "Upload a script file",
    ]);
  });

  it("asks for an SOP file in plain words when none was uploaded", () => {
    expect(fileErrors(SOP, "sopUrl", "")).toEqual(["Upload an SOP file"]);
  });

  it("asks for an SOP file in plain words after the file is removed", () => {
    expect(fileErrors(SOP, "sopUrl", null)).toEqual(["Upload an SOP file"]);
  });

  // Guard: an uploaded file still passes (holds at the base too).
  it("still accepts an uploaded file", () => {
    const file = new File(["hello"], "notes.txt", { type: "text/plain" });
    const upload = { file, name: file.name, size: file.size };
    expect(fileErrors(SCRIPT, "scriptUrl", upload)).toEqual([]);
    expect(fileErrors(SOP, "sopUrl", upload)).toEqual([]);
  });
});
