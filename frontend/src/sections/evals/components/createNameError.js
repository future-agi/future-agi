// L7c g7 (evalcreate:name): the eval-name refusal a create or publish
// answers with (`An evaluation named x already exists.`, or a name the
// naming rules refuse), read from the rejected request so the form can say
// it at the Name field. null for any other failure.

const NAME_REFUSAL = /already exists|^Name can only contain/i;

const textOf = (v) => (typeof v === "string" ? v.trim() : "");

/**
 * @param {*} error  what axios rejected with (utils/axios customError:
 *   the response body spread, plus statusCode), or an Error
 * @returns {string|null}
 */
export function createNameErrorOf(error) {
  const candidates = [
    error?.result,
    error?.message,
    error?.detail,
    error?.error,
    error?.response?.data?.result,
    error?.response?.data?.message,
  ].map(textOf);
  const msg = candidates.find((m) => m && NAME_REFUSAL.test(m));
  return msg ?? null;
}

// FA-EVALS-02 (evalcreate:name): the characters an eval name can use. The
// server refuses any other (model_hub eval update: ^[a-z0-9_-]+$), so the
// field keeps what was typed and says what to change instead of dropping it.
const EVAL_NAME_CHARS = /^[a-z0-9_-]+$/;

export const EVAL_NAME_HINT =
  "Use lowercase letters, numbers, hyphens (-) and underscores (_). Names must be unique.";

/**
 * @param {string} name  the Name field as typed
 * @returns {string|null} what to change, or null for an empty or valid name
 */
export function evalNameFormatError(name) {
  const trimmed = typeof name === "string" ? name.trim() : "";
  if (!trimmed || EVAL_NAME_CHARS.test(trimmed)) return null;
  return "This name has characters an eval name can't use. Use only lowercase letters, numbers, hyphens (-) and underscores (_), with no spaces.";
}
