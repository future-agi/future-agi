/** The recording cannot be reached at all — the server refused it, or there is
 *  no URL to fetch. Retrying asks the same question and gets the same answer,
 *  so this variant offers no button. */
export const UNAVAILABLE = "unavailable";

/** The recording is there, but it did not come down or would not decode this
 *  time. Another attempt is worth offering. */
export const LOAD_FAILED = "load-failed";
