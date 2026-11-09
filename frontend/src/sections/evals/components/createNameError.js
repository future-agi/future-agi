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
