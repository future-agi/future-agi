// create:upload-file (L2): dev's strict upload route answered 400 to every
// DSX upload (column_roles + import_options). The body now carries only the
// fields the contract declares; a choice dev cannot honour blocks Import.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { unknownFields } from "src/api/contracts/strictBody";
import {
  ROLES_NOT_KEPT_LINE,
  UPLOAD_BODY,
  effectiveRoles,
  rolesNoteOf,
  rolesOffered,
  uploadAccepts,
  uploadFormData,
  uploadServerBlock,
} from "../uploadBody";
import { guessRole } from "../importFile";

const file = new File(["question,answer\nq,a\n"], "rows.csv", {
  type: "text/csv",
});

describe("uploadFormData", () => {
  it("real build: only what CreateDatasetFromLocalFileRequest declares", () => {
    expect(uploadAccepts("column_roles", false)).toBe(false);
    expect(uploadAccepts("import_options", false)).toBe(false);
    const fd = uploadFormData(
      {
        name: "rows",
        modelType: "GenerativeLLM",
        file,
        roles: { question: "input", answer: "expected" },
        flatten: false,
        parseJson: false,
      },
      false,
    );
    expect([...fd.keys()].sort()).toEqual([
      "file",
      "model_type",
      "new_dataset_name",
    ]);
    expect(unknownFields(fd, UPLOAD_BODY)).toEqual([]);
    expect(fd.get("new_dataset_name")).toBe("rows");
  });

  it("prototype mode keeps the DSX fields (its mocks read them)", () => {
    const fd = uploadFormData(
      { name: "rows", modelType: "GenerativeLLM", file, flatten: true },
      true,
    );
    expect(JSON.parse(fd.get("import_options"))).toEqual({
      flatten: true,
      parse_json: false,
    });
    expect(fd.get("column_roles")).toBe("{}");
  });
});

describe("uploadServerBlock", () => {
  const columns = ["question", "answer", "id", "split"];
  const guessed = Object.fromEntries(columns.map((c) => [c, guessRole(c)]));

  it("Input, Expected and Metadata import as plain columns: no block", () => {
    expect(
      uploadServerBlock(
        {
          roles: { question: "input", answer: "expected", x: "metadata" },
          columns: ["question", "answer", "x"],
        },
        false,
      ),
    ).toBeNull();
    expect(rolesNoteOf(false)).toBe(ROLES_NOT_KEPT_LINE);
  });

  it("a role dev cannot honour blocks Import, naming the column", () => {
    // guessRole puts `id` on Ignore: dev would import it anyway.
    expect(guessed.id).toBe("ignore");
    expect(uploadServerBlock({ roles: guessed, columns }, false)).toBe(
      "The Ignore role isn't supported: every column of the file is imported. Set “id” to Input, Expected or Metadata to import.",
    );
    expect(
      uploadServerBlock(
        { roles: { ...guessed, id: "metadata" }, columns },
        false,
      ),
    ).toBe(
      "The Split role isn't supported: every column of the file is imported. Set “split” to Input, Expected or Metadata to import.",
    );
  });

  it("an import option dev cannot honour blocks Import", () => {
    expect(uploadServerBlock({ flatten: true }, false)).toMatch(
      /^Flatten nested JSON isn't supported/,
    );
    expect(uploadServerBlock({ parseJson: true }, false)).toMatch(
      /^Read JSON in text cells isn't supported/,
    );
  });

  it("prototype mode blocks nothing and shows no roles note", () => {
    expect(
      uploadServerBlock({ roles: guessed, columns, flatten: true }, true),
    ).toBeNull();
    expect(rolesNoteOf(true)).toBeNull();
  });
});

describe("role pickers (L2 fix round 1)", () => {
  const SRC = (name) =>
    readFileSync(
      join(dirname(fileURLToPath(import.meta.url)), "..", name),
      "utf8",
    );

  it("a real build offers no role picker and acts on no role", () => {
    expect(rolesOffered(false)).toBe(false);
    const guessed = { id: guessRole("id"), question: "input" };
    expect(guessed.id).toBe("ignore");
    // The guessed Ignore on `id` no longer blocks Import: no role is acted on.
    expect(effectiveRoles(guessed, false)).toEqual({});
    expect(
      uploadServerBlock(
        { roles: effectiveRoles(guessed, false), columns: ["id", "question"] },
        false,
      ),
    ).toBeNull();
  });

  it("prototype mode keeps the pickers and the roles", () => {
    expect(rolesOffered(true)).toBe(true);
    const roles = { id: "ignore" };
    expect(effectiveRoles(roles, true)).toBe(roles);
  });

  it("the dialog passes a role handler only where roles are offered", () => {
    const modal = SRC("UploadFileModal.jsx");
    expect(modal).toMatch(/const rolesShown = rolesOffered\(\);/);
    expect(modal).toMatch(/const actRoles = effectiveRoles\(roles\);/);
    expect(modal).toMatch(/roles=\{actRoles\}\s*onRole=\{\s*rolesShown\s*\?/);
    // Every server-facing use reads the effective roles.
    expect(modal.match(/roles: actRoles/g)?.length).toBe(3);
    const pane = SRC("ImportPreviewPane.jsx");
    expect(pane).toMatch(/\{onRole && \(\s*<Select/);
  });
});
