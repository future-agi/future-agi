// RE-01 (options-truth L3 G10): pure helpers of the `Experiment not found`
// page.

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * The dataset the user came from: a same-origin referrer under
 * `/dashboard/develop/<dataset uuid>` (its Data / Experiments / Summary
 * tabs or its compare pages), else null.
 * @param {string} referrer  `document.referrer`
 * @param {string} origin  `window.location.origin`
 */
export function datasetOfReferrer(referrer, origin) {
  if (!referrer || !origin) return null;
  let url;
  try {
    url = new URL(referrer);
  } catch {
    return null;
  }
  if (url.origin !== origin) return null;
  const m = /^\/dashboard\/develop\/([^/?#]+)/.exec(url.pathname);
  return m && UUID.test(m[1]) ? m[1] : null;
}

/**
 * True once the experiment's own read answered 404 (the mock and the API
 * answer `Experiment not found` with that status): the page is terminal.
 * @param {{isError?: boolean, error?: object}} q  the detail query
 * @param {(e: object) => number|null} statusOf
 */
export function experimentMissing(q, statusOf) {
  return Boolean(q?.isError) && statusOf(q?.error) === 404;
}
