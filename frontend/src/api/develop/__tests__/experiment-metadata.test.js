// exp.rename: the plain words of the rename controls and the reads a saved
// rename refreshes (experiment-metadata.js). Refusal shapes are the backend
// half's contract (exp-rename backend HANDOFF apiContract.refusalShapes), as
// src/utils/axios rejects them ({...body, statusCode, transportCode}).
import { afterEach, describe, expect, it, vi } from "vitest";
import { QueryClient } from "@tanstack/react-query";
import {
  EXPERIMENT_DESCRIPTION_MAX,
  EXPERIMENT_NAME_MAX,
  RENAME_COPY,
  charCount,
  descriptionProblem,
  nameProblem,
  refreshAfterRename,
  renameErrorOf,
  renameErrorText,
  renameSuccessText,
} from "../experiment-metadata";

const refused = (status, body) => ({
  ...body,
  statusCode: status,
  transportCode: "ERR_BAD_REQUEST",
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("name and description drafts", () => {
  it("uses dev's limits", () => {
    expect(EXPERIMENT_NAME_MAX).toBe(255);
    expect(EXPERIMENT_DESCRIPTION_MAX).toBe(4000);
  });

  it.each([
    ["", RENAME_COPY.emptyName],
    ["   ", RENAME_COPY.emptyName],
    ["x".repeat(256), RENAME_COPY.longName],
    [`  ${"x".repeat(255)}  `, null],
    ["Prompt v2", null],
    // Dev counts characters: 200 emoji are 200, not 400.
    ["\u{1F600}".repeat(200), null],
    ["\u{1F600}".repeat(256), RENAME_COPY.longName],
  ])("name %j -> %j", (draft, expected) => {
    expect(nameProblem(draft)).toBe(expected);
  });

  it("counts a description in characters, as dev does", () => {
    expect(charCount("\u{1F600}a")).toBe(2);
    expect(descriptionProblem("\u{1F600}".repeat(4000))).toBeNull();
    expect(descriptionProblem("\u{1F600}".repeat(4001))).toBe(
      RENAME_COPY.longDescription,
    );
  });

  it("says each problem in plain words", () => {
    expect(RENAME_COPY.emptyName).toBe("Enter an experiment name.");
    expect(RENAME_COPY.longName).toBe(
      "Use 255 characters or fewer for the experiment name.",
    );
    expect(RENAME_COPY.duplicateName).toBe(
      "An experiment with this name already exists in this dataset. Choose another name.",
    );
    expect(descriptionProblem("x".repeat(4001))).toBe(
      "Use 4000 characters or fewer for the description.",
    );
    expect(descriptionProblem("x".repeat(4000))).toBeNull();
    expect(descriptionProblem("")).toBeNull();
  });
});

describe("renameErrorOf", () => {
  it("puts dev's field words next to the field", () => {
    const duplicate = refused(400, {
      status: false,
      type: "validation_error",
      code: "invalid",
      detail:
        "name: An experiment with this name already exists in this dataset. Choose another name.",
      result:
        "name: An experiment with this name already exists in this dataset. Choose another name.",
      attr: "name",
      details: {
        name: [
          "An experiment with this name already exists in this dataset. Choose another name.",
        ],
      },
    });
    expect(renameErrorOf(duplicate)).toEqual({
      fields: {
        name: "An experiment with this name already exists in this dataset. Choose another name.",
      },
      message: null,
      unknown: false,
    });
    const long = refused(400, {
      attr: "description",
      details: {
        description: ["Use 4000 characters or fewer for the description."],
      },
    });
    expect(renameErrorOf(long).fields).toEqual({
      description: "Use 4000 characters or fewer for the description.",
    });
  });

  it("reads the field from attr when details are missing, without its prefix", () => {
    expect(
      renameErrorOf(
        refused(400, { attr: "name", detail: "name: Enter an experiment name." }),
      ).fields,
    ).toEqual({ name: "Enter an experiment name." });
  });

  it("403: says the reason dev gives", () => {
    const denied = refused(403, {
      status: false,
      type: "permission_error",
      code: "permission_denied",
      detail: "You do not have permission to edit experiments in this workspace.",
    });
    expect(renameErrorOf(denied)).toEqual({
      fields: {},
      message:
        "You do not have permission to edit experiments in this workspace.",
      unknown: false,
    });
    expect(renameErrorOf(refused(403, {})).message).toBe(RENAME_COPY.denied);
  });

  it("404: the experiment is gone", () => {
    expect(
      renameErrorOf(refused(404, { detail: "Experiment not found" })).message,
    ).toBe(RENAME_COPY.gone);
  });

  it("a form-level 400 shows dev's words", () => {
    const mixed = refused(400, {
      result: "Save the name and description separately from run settings.",
      details: {
        non_field_errors: [
          "Save the name and description separately from run settings.",
        ],
      },
    });
    expect(renameErrorOf(mixed)).toEqual({
      fields: {},
      message: "Save the name and description separately from run settings.",
      unknown: false,
    });
  });

  it.each([
    ["network", { message: "Network Error", transportCode: "ERR_NETWORK" }],
    ["timeout", { message: "timeout", transportCode: "ECONNABORTED" }],
    ["server", refused(502, { detail: "Bad gateway" })],
  ])("%s: the outcome is unknown", (_label, error) => {
    expect(renameErrorOf(error)).toEqual({
      fields: {},
      message: RENAME_COPY.unconfirmed,
      unknown: true,
    });
  });

  it("renameErrorText prefers a field's words", () => {
    expect(renameErrorText({ fields: { name: "a" }, message: "b" })).toBe("a");
    expect(renameErrorText({ fields: {}, message: "b" })).toBe("b");
    expect(renameErrorText(null)).toBe(RENAME_COPY.failed);
  });
});

describe("renameSuccessText", () => {
  it("names what dev saved, not the draft", () => {
    expect(
      renameSuccessText(
        { data: { status: true, result: { name: "Trimmed name" } } },
        { id: "e1", name: "  Trimmed name  " },
      ),
    ).toBe("Renamed to “Trimmed name”");
    expect(renameSuccessText({ data: {} }, { name: " Draft " })).toBe(
      "Renamed to “Draft”",
    );
  });

  it("a description-only save says so", () => {
    expect(renameSuccessText({}, { description: "Why" })).toBe(
      "Description saved",
    );
    expect(renameSuccessText({}, { description: "" })).toBe(
      "Description cleared",
    );
  });
});

describe("refreshAfterRename", () => {
  it("reads again everything that shows the name", () => {
    const client = new QueryClient();
    const spy = vi.spyOn(client, "invalidateQueries");
    refreshAfterRename(client, { datasetId: "ds1", experimentId: "e1" });
    const keys = spy.mock.calls.map(([arg]) => JSON.stringify(arg.queryKey));
    expect(keys.sort()).toEqual(
      [
        ["develop", "dataset-runs", "ds1"],
        ["develop", "run-results", "ds1"],
        ["experiment-list"],
        ["datasets", "list"],
        ["experiment-validate-name", "ds1"],
        ["experiment-suggested-name", "ds1"],
        ["experiment", "e1"],
      ]
        .map((k) => JSON.stringify(k))
        .sort(),
    );
  });
});
