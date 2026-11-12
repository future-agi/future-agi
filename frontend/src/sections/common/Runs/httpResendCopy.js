// SRC S1b: the one place an HTTP endpoint re-send is worded. A request that
// reached an endpoint may already have done something there (created a
// record, sent a message, made a payment), and a request that timed out or
// was interrupted may have reached it too. Every re-send is an explicit
// action that says so first; nothing re-sends a request on its own after an
// unknown outcome. Edit of an HTTP source only saves it, and points to the
// explicit Retry actions (each asks first), never to a one-click rerun.
// Every message about an action that did not start says nothing was sent.
// Add new rows and a new HTTP variant say how many requests they send.
// Plain, sentence case.
import { getRequestErrorMessage } from "src/utils/errorUtils";

export const HTTP_RESEND_COPY = Object.freeze({
  priorEffect:
    "The endpoint may already have processed the earlier request. If that request created a record, sent a message or made a payment, sending it again can repeat it.",
  priorEffectMany:
    "The endpoint may already have processed the earlier requests. If those requests created records, sent messages or made payments, sending them again can repeat them.",
  unknownOutcome:
    "The earlier request did not finish, so it is not known whether the endpoint processed it.",
  cancel: "Cancel",
  close: "Close",
  checkAgain: "Check again",
  checkAgainHelp:
    "Could not confirm that this started. Check again repeats the same request, so the rows are sent at most once.",
  unconfirmed:
    "Could not confirm whether this request started. The endpoint may already have processed it, so check the results before sending it again.",
  cellTitle: "Send this row again?",
  cellConfirm: "Send again",
  cellStarted: "Sending the row again",
  cellFailed: "Could not send the row again.",
  failedRowsAction: "Retry failed rows",
  failedRowsTitle: "Retry failed rows?",
  failedRowsConfirm: "Retry failed rows",
  failedRowsStarted: "Retrying failed rows",
  failedRowsFailed: "Could not retry the failed rows.",
  addRowsAction: "Add new rows",
  addRowsTitle: "Add new rows?",
  addRowsConfirm: "Add rows",
  addRowsStarted: "Adding new rows",
  addRowsFailed: "Could not add the new rows.",
  addRowsReadFailed: "Could not count the dataset rows.",
  nothingSent: "Nothing was sent.",
  addRowsNone: "There are no new dataset rows to add, so nothing was sent.",
  addRowsTooMany:
    "Nothing was sent. An HTTP experiment holds at most 10,000 rows, and this dataset has more. To run other rows, create a new experiment and choose dataset splits with 10,000 rows or fewer.",
  addRowsSelected:
    "This experiment ran on selected or sampled rows. Add new rows does not keep that selection: it adds every other row, not a new sample.",
  appendSameEndpoint:
    "The original variant already sent these rows to this endpoint, and the endpoint may already have processed those requests. If they created records, sent messages or made payments, the new variant can repeat them.",
  appendCopied: "Headers and variable mapping are copied from the original variant.",
  editSaveOnly:
    "Saving sends no requests to the endpoint and keeps the existing results. Evaluations you add or change score a row only when it is sent again, so existing scores stay as they are. To send requests with the new settings, use Retry failed rows on the Data tab, or the retry button on a failed cell. Each asks before it sends.",
  editSave: "Save changes",
  rerunPromptBlocked:
    "not available on an experiment with HTTP runs. Create a new experiment to change a prompt run.",
  rerunNothingToCopy:
    "This experiment's HTTP runs cannot be copied, and a prompt run cannot be added to an experiment with HTTP runs. Create a new experiment to change a prompt run.",
  editHttpOnly:
    "Edit changes the settings of this experiment's HTTP runs only. To add an HTTP run, use Re-run with changes. To remove a run, or to add or change a prompt or agent run, create a new experiment.",
});

const formatCount = (n) => Number(n).toLocaleString("en-US");

/** `api.acme.ai` of a URL, or "" when it is not one (pure). */
export function hostOfUrl(url) {
  try {
    return new URL(String(url ?? "").trim()).host;
  } catch {
    return "";
  }
}

/** Retry cell: what the confirm says before one row is sent again (pure). */
export function httpCellResendLines({ url, unknownOutcome = false } = {}) {
  const host = hostOfUrl(url);
  return [
    `This sends a new request to ${host || "the endpoint"} for this row.`,
    HTTP_RESEND_COPY.priorEffect,
    ...(unknownOutcome ? [HTTP_RESEND_COPY.unknownOutcome] : []),
  ];
}

/** Retry failed rows: what the confirm says before the rows are sent (pure). */
export function httpFailedRowsLines(count = 1) {
  const which = count === 1 ? "this HTTP run" : `these ${count} HTTP runs`;
  return [
    `Each failed row of ${which} gets a new request to its endpoint. Rows that succeeded are not sent again.`,
    HTTP_RESEND_COPY.priorEffectMany,
  ];
}

/**
 * How many requests Add new rows sends, for `newRows` rows (pure).
 * `requests` is the server's own count from its preview; the split per HTTP
 * run is stated only when it agrees with that count.
 */
export function httpAddRowsCountLine(newRows, count = 1, requests = null) {
  const rows = Math.max(Number(newRows) || 0, 0);
  const runs = Math.max(Number(count) || 1, 1);
  const total =
    Number.isSafeInteger(requests) && requests >= 0 ? requests : rows * runs;
  const unit = total === 1 ? "request" : "requests";
  const sends = `${formatCount(total)} ${unit}`;
  const each =
    runs > 1 && total === rows * runs
      ? `, ${formatCount(rows)} to each HTTP run`
      : "";
  return rows === 1
    ? `This sends ${sends}${each}, for the one new row.`
    : `This sends ${sends}${each}, one for each of the ${formatCount(rows)} new rows.`;
}

/**
 * Add new rows: what the confirm says before new rows are sent (pure).
 * `newRows` is how many dataset rows the experiment does not include and
 * `requests` how many requests they send (both from the server's preview);
 * `selected` is true when the experiment ran on selected or sampled rows.
 */
export function httpAddRowsLines({
  count = 1,
  scoped = false,
  newRows = null,
  requests = null,
  selected = false,
} = {}) {
  const which = count === 1 ? "The HTTP run" : `Each of the ${count} HTTP runs`;
  return [
    `${which} sends a request for every dataset row${scoped ? " in this experiment's splits" : ""} that the experiment does not include.`,
    ...(selected ? [HTTP_RESEND_COPY.addRowsSelected] : []),
    ...(Number.isSafeInteger(newRows) && newRows > 0
      ? [httpAddRowsCountLine(newRows, count, requests)]
      : []),
    "Rows already in the experiment are not sent again, and prompt and agent runs do not run on the new rows.",
  ];
}

/** Run new variant: how many requests the new HTTP variant sends (pure). */
export function httpVariantCountLine(rows) {
  const n = Number(rows);
  if (!Number.isSafeInteger(n) || n < 1)
    return "The new variant sends one request to its endpoint for each row of this experiment.";
  return n === 1
    ? "The new variant sends 1 request to its endpoint, for the experiment's one row."
    : `The new variant sends ${formatCount(n)} requests to its endpoint, one for each of the experiment's ${formatCount(n)} rows.`;
}

/** The edit confirm's summary line when the save only saves (pure). */
export function httpEditSummaryText(text) {
  return String(text ?? "").replace(/^Re-run: /, "Changed: ");
}

/**
 * Whether our own request's outcome is unknown: no answer, or a server
 * failure. Only a 4xx answer says the request was not admitted (pure).
 */
export function isUnknownRequestOutcome(error) {
  const status = Number(error?.statusCode ?? error?.response?.status);
  return !(status >= 400 && status < 500);
}

/** A definite refusal's reason; a 403 body carries it only in `detail`. */
export function httpRefusalMessage(error, fallback) {
  const detail = typeof error?.detail === "string" ? error.detail.trim() : "";
  return Number(error?.statusCode) === 403 && detail
    ? detail
    : getRequestErrorMessage(error, fallback);
}

/** A new key for one explicit HTTP action; its recovery reuses it. */
export function newHttpActionKey() {
  const id =
    globalThis.crypto?.randomUUID?.() ??
    `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
  return `fa-http-${id}`;
}
