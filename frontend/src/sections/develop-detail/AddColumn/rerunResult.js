// v2.1 g8 (dyncol:rerun-scope): Save on a dynamic column says what the
// chosen re-run scope did (`Re-running 12 rows`, `No rows match that
// scope`), naming the column type it saved. Pure.

/**
 * @param {string} kind  the column type as the drawer names it
 * @param {object} res   the axios response of the save
 * @returns {{text: string, variant: "success"|"info"}}
 */
export function rerunResultToast(kind, res) {
  const message = res?.data?.result?.message;
  const rows = res?.data?.result?.rows;
  const none = rows === 0 || /^No rows match/i.test(String(message ?? ""));
  return {
    text: message
      ? `${kind} column saved · ${message}`
      : `${kind} column saved`,
    variant: none ? "info" : "success",
  };
}
