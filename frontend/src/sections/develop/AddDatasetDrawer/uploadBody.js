// Add dataset > Upload a file on the real backend (create:upload-file, L2).
// Dev's develops/create-dataset-from-local-file/ is strict
// (CreateDatasetFromLocalFileRequest: file, new_dataset_name, model_type,
// source): the `column_roles` and `import_options` the DSX dialog always
// added made every upload answer 400. So the form body carries only the
// fields the contract declares, and a choice dev cannot honour blocks Import
// with its reason instead of being dropped:
//   - Flatten nested JSON / Read JSON in text cells: dev imports the file as
//     it is;
//   - the Ignore, ID, Split and Tags roles: dev imports every column of the
//     file as a column.
// Input, Expected and Metadata columns are imported as columns on dev too;
// the role itself is not kept, and the dialog says so in one line.
// Prototype mode sends the DSX body unchanged (its mocks read every field).
import { isSurfaceShown } from "src/launch/launchSwitch";
import { isPrototypeMode } from "src/utils/prototype";
import { acceptedFields } from "src/api/contracts/strictBody";
import { ROLE_OPTIONS } from "./importFile";

export const UPLOAD_BODY = "CreateDatasetFromLocalFileRequest";

// Roles under which a column is imported as a plain column, as dev does.
export const COLUMN_AS_IS_ROLES = Object.freeze([
  "input",
  "expected",
  "metadata",
]);

export const ROLES_NOT_KEPT_LINE =
  "Column roles aren't saved yet: every column is imported as a plain column.";

const roleLabel = (value) =>
  ROLE_OPTIONS.find((r) => r.value === value)?.label ?? String(value);

/** True where the upload route declares `field` (or in prototype mode). */
export const uploadAccepts = (field, mockMode = isPrototypeMode()) =>
  mockMode ||
  (acceptedFields(UPLOAD_BODY).includes(field) &&
    (field !== "column_roles" ||
      isSurfaceShown("dataset.imports.columnRoles")) &&
    (field !== "import_options" ||
      isSurfaceShown("dataset.imports.transforms")));

/**
 * The multipart body of an upload.
 * @param {{name: string, modelType: string, file: File, roles?: object,
 *   flatten?: boolean, parseJson?: boolean}} form
 */
export function uploadFormData(
  { name, modelType, file, roles = {}, flatten = false, parseJson = false },
  mockMode = isPrototypeMode(),
) {
  const fd = new FormData();
  fd.append("new_dataset_name", name);
  fd.append("model_type", modelType);
  fd.append("file", file);
  if (uploadAccepts("column_roles", mockMode))
    fd.append("column_roles", JSON.stringify(roles ?? {}));
  if (uploadAccepts("import_options", mockMode))
    fd.append(
      "import_options",
      JSON.stringify({
        flatten: Boolean(flatten),
        parse_json: Boolean(parseJson),
      }),
    );
  return fd;
}

/**
 * Why Import is blocked because dev cannot honour a choice, or null.
 * @param {{roles?: object, columns?: string[], flatten?: boolean,
 *   parseJson?: boolean}} s
 */
export function uploadServerBlock(
  { roles = {}, columns = [], flatten = false, parseJson = false },
  mockMode = isPrototypeMode(),
) {
  if (flatten && !uploadAccepts("import_options", mockMode))
    return "Flatten nested JSON isn't supported: the file is imported as it is. Turn Flatten off to import.";
  if (parseJson && !uploadAccepts("import_options", mockMode))
    return "Read JSON in text cells isn't supported: the file is imported as it is. Turn it off to import.";
  if (!uploadAccepts("column_roles", mockMode)) {
    const names = columns?.length ? columns : Object.keys(roles ?? {});
    const bad = names.find(
      (c) => roles?.[c] && !COLUMN_AS_IS_ROLES.includes(roles[c]),
    );
    if (bad)
      return `The ${roleLabel(roles[bad])} role isn't supported: every column of the file is imported. Set “${bad}” to Input, Expected or Metadata to import.`;
  }
  return null;
}

/**
 * True where the upload route keeps column roles (prototype mode only on
 * dev). Where it does not, the dialog shows no role pickers (L2 fix round 1:
 * a picker that changes nothing is not offered) and every column of the file
 * is imported, as dev does.
 */
export const rolesOffered = (mockMode = isPrototypeMode()) =>
  uploadAccepts("column_roles", mockMode);

const NO_ROLES = Object.freeze({});

/** The roles the dialog acts on: none where roles are not offered. */
export const effectiveRoles = (roles, mockMode = isPrototypeMode()) =>
  rolesOffered(mockMode) ? roles ?? NO_ROLES : NO_ROLES;

/** The one line the dialog shows when roles are not kept (null otherwise). */
export const rolesNoteOf = (mockMode = isPrototypeMode()) =>
  uploadAccepts("column_roles", mockMode) ? null : ROLES_NOT_KEPT_LINE;
