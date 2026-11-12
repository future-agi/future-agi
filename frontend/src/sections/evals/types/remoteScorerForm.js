// W7-4 remote scorers: the editor's form, pure. Converts the form to the
// create / PATCH body (remote + typed credentials) and a stored detail back
// to the form. Validation is the shared model's (remoteModel.validateRemoteDef).
import {
  REMOTE_CALLS_AT_A_TIME,
  REMOTE_RETRIES,
  REMOTE_TIMEOUT_MS,
  scoredText,
  validateRemoteDef,
} from "src/sections/common/EvalTypes/remoteModel";

/** Choices of a choice output: [{label, score}] (score 0 to 1). */
export function parseChoices(text) {
  return String(text ?? "")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const m = /^(.*?)\s*=\s*(-?\d*\.?\d+)\s*$/.exec(line);
      return m
        ? { label: m[1].trim(), score: Number(m[2]) }
        : { label: line, score: null };
    });
}

export const choicesText = (choices = []) =>
  (choices ?? []).map((c) => `${c.label} = ${c.score}`).join("\n");

/** A new scorer's form: the definition's own defaults. */
export const emptyRemoteForm = () => ({
  url: "",
  authKind: "hmac",
  token: "",
  tokenUrl: "",
  clientId: "",
  clientSecret: "",
  scope: "",
  timeoutS: String(REMOTE_TIMEOUT_MS.default / 1000),
  retries: String(REMOTE_RETRIES.default),
  retryOn: ["429", "5xx", "timeout"],
  callsAtATime: String(REMOTE_CALLS_AT_A_TIME.default),
  send: "row",
  outputType: "score",
  choices: "",
  inputs: [],
  passThreshold: "",
  description: "",
});

/** The form of a stored scorer (GET {template_id}/remote/). */
export function formFromDetail(d) {
  const r = d?.remote ?? {};
  return {
    ...emptyRemoteForm(),
    url: r.url ?? "",
    authKind: r.auth?.kind ?? "hmac",
    tokenUrl: r.auth?.oauth?.token_url ?? "",
    clientId: r.auth?.oauth?.client_id ?? "",
    scope: r.auth?.oauth?.scope ?? "",
    timeoutS: String((r.timeout_ms ?? REMOTE_TIMEOUT_MS.default) / 1000),
    retries: String(r.retries ?? REMOTE_RETRIES.default),
    retryOn: r.retry_on ?? [],
    callsAtATime: String(r.calls_at_a_time ?? REMOTE_CALLS_AT_A_TIME.default),
    send: r.send ?? "row",
    outputType: r.output?.type ?? "score",
    choices: choicesText(r.output?.choices ?? []),
    inputs: Object.entries(r.mapping ?? {}).map(([name, column]) => ({
      name,
      column,
    })),
    passThreshold: d?.pass_threshold != null ? String(d.pass_threshold) : "",
    description: d?.description ?? "",
  };
}

const num = (s) => (String(s ?? "").trim() === "" ? undefined : Number(s));

/** The remote definition of a form (before validation). */
export function remoteOfForm(f) {
  return {
    url: f.url.trim(),
    auth: {
      kind: f.authKind,
      ...(f.authKind === "oauth"
        ? {
            oauth: {
              token_url: f.tokenUrl.trim(),
              client_id: f.clientId.trim(),
              scope: f.scope.trim(),
            },
          }
        : {}),
    },
    timeout_ms:
      num(f.timeoutS) === undefined
        ? undefined
        : Math.round(num(f.timeoutS) * 1000),
    retries: num(f.retries),
    retry_on: f.retryOn,
    calls_at_a_time: num(f.callsAtATime),
    send: f.send,
    output: {
      type: f.outputType,
      ...(f.outputType === "choice"
        ? { choices: parseChoices(f.choices) }
        : {}),
    },
    mapping: Object.fromEntries(
      f.inputs
        .filter((x) => x.name.trim())
        .map((x) => [x.name.trim(), x.column.trim()]),
    ),
  };
}

/**
 * The create / PATCH body of a form: the definition, plus a credential the
 * author typed (sent once; never read back).
 */
export function bodyOfForm(f, { name } = {}) {
  const remote = remoteOfForm(f);
  if (f.authKind === "bearer" && f.token) remote.auth.token = f.token;
  if (f.authKind === "oauth" && f.clientSecret)
    remote.auth.oauth = { ...remote.auth.oauth, client_secret: f.clientSecret };
  return {
    ...(name !== undefined ? { name } : {}),
    description: f.description,
    ...(f.passThreshold !== ""
      ? { pass_threshold: Number(f.passThreshold) }
      : {}),
    remote,
  };
}

/**
 * Field errors of a form: for saving (runs) or for Test on one row. Saving
 * also needs the credential the scorer signs in with (typed once; a stored
 * one counts).
 */
export function formErrors(f, { forTest = false, detail = null } = {}) {
  const errors = validateRemoteDef(remoteOfForm(f), { forTest }).errors;
  if (forTest) return errors;
  const auth = detail?.remote?.auth ?? {};
  if (f.authKind === "bearer" && !f.token && !auth.token_last4)
    errors.auth = "Enter the bearer token your scorer expects";
  if (f.authKind === "oauth" && !f.clientSecret && !auth.client_secret_set)
    errors.auth = "Enter the client secret";
  return errors;
}

// The signing secret a create answered with, kept in this page's memory only
// until the new scorer's detail shows it (once) and drops it.
const handed = new Map();

/** Keep a just-made secret for the scorer's detail to show once. */
export const handOverSecret = (id, secret) => {
  if (id && secret) handed.set(id, secret);
};

/** The secret handed over for `id`, removed as it is read (null after). */
export function takeHandedSecret(id) {
  const s = handed.get(id) ?? null;
  handed.delete(id);
  return s;
}

export const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;

/**
 * The toast of a finished job. Cells whose call or answer failed are
 * counted as failed, with the first reason; they are never called scored.
 */
export function scoreRunToast(r, name) {
  if (!r || r.unchanged)
    return `Scored ${r?.from_label ?? "the run"} with ${name}: no score moved. ${r?.from_label ?? "The run"} keeps its scores.`;
  const failed = r.failed ?? 0;
  const what = r.new_eval
    ? scoredText({
        scored: r.scored ?? r.moved_cells,
        failed,
        unit: r.unit ?? "row",
        error: r.error,
      })
    : [
        `${plural(r.flipped_rows.length, "row")} changed`,
        failed ? `${failed} failed${r.error ? `: ${r.error}` : ""}` : "",
      ]
        .filter(Boolean)
        .join(", ");
  return `Scored ${r.from_label} with ${name}: ${what}. ${r.from_label} keeps its scores.`;
}
