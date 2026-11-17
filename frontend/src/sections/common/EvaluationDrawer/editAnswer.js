// The classic experiment page saves an edited evaluation with one direct
// request (it has no re-score confirm). The server's answer is the only place
// that says what happened: queued for scoring, saved without scoring, or
// refused with nothing saved. Show it, whichever it is.
import { getRequestErrorMessage } from "src/utils/errorUtils";

export const EDIT_FAILED_FALLBACK = "Could not update the evaluation.";

/**
 * POST the edit and show the server's answer.
 * @param {(url: string, body: object) => Promise<object>} post
 * @param {string} url
 * @param {object} body
 * @param {(message: string, options: object) => void} notify
 * @returns {Promise<object>} the response; a refusal is shown, then rethrown
 *   so the editor stays open with the person's edits.
 */
export async function postEditShowingAnswer(post, url, body, notify) {
  let response;
  try {
    response = await post(url, body);
  } catch (error) {
    notify(getRequestErrorMessage(error, EDIT_FAILED_FALLBACK), {
      variant: "error",
    });
    throw error;
  }
  const answer = response?.data?.result;
  if (typeof answer === "string" && answer.trim()) {
    notify(answer.trim(), { variant: "info" });
  }
  return response;
}
