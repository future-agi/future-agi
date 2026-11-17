// The additive part of omega-embed/1 (CP P7, P8, P15, P19), read from
// omega-embed-1.additions.json. That file is a byte-identical copy of the one
// the EE Console publishes (apps/console/src/shared/embed/), so every name,
// code, pattern and bound below comes from the one definition both sides
// hold; nothing here restates a value by hand.
//
//   child  -> parent  embed.hello      { protocol, build?, want? }
//   parent -> child   embed.key        { token, left? }
//   child  -> parent  embed.telemetry  { kind: "error" | "vital", ... }
//   child  -> parent  embed.badge      { inbox }
//
// Every reader returns a clean copy with only the known fields, or null.
import additions from "./omega-embed-1.additions.json";

export const EMBED_ADDITIONS = additions;

export const EMBED_ADDED_MESSAGE = Object.freeze({
  telemetry: "embed.telemetry",
  badge: "embed.badge",
});

const hello = additions.messages["embed.hello"].addedFields;
const key = additions.messages["embed.key"].addedFields;
const telemetry = additions.messages["embed.telemetry"].variants;
const badge = additions.messages["embed.badge"].fields;

const BUILD = new RegExp(hello.build.pattern);
export const WANT_MIN = hello.want.minimum;
export const WANT_MAX = hello.want.maximum;
export const LEFT_MAX = key.left.maximum;

const RELEASE = new RegExp(telemetry.error.fields.release.pattern);
const HTTP_CODE = new RegExp(telemetry.error.fields.code.pattern);
const NAMED_CODES = new Set(telemetry.error.fields.code.or);
const REQUEST_ID = new RegExp(telemetry.error.fields.requestId.pattern);
export const TELEMETRY_ERROR_NAMES = Object.freeze([
  ...telemetry.error.fields.name,
]);
export const TELEMETRY_VITAL_NAMES = Object.freeze([
  ...telemetry.vital.fields.name,
]);
const VALUE_MS = telemetry.vital.fields.valueMs;
const INBOX = badge.inbox;

const isInt = (value, min, max) =>
  Number.isInteger(value) && value >= min && value <= max;

// A page template the Console declares ("/agents/:agentId"), "/" or "*".
// The contract says a template is never a concrete path; the host checks the
// shape as a second line: literal segments are lower-case words joined by
// hyphens (no digits, so an id such as "agt-7" or a UUID is refused) and
// parameters are ":name".
const TEMPLATE_SEGMENT = /^(?:[a-z]+(?:-[a-z]+)*|:[A-Za-z][A-Za-z0-9]{0,39})$/;
const TEMPLATE_MAX_LENGTH = 128;
const TEMPLATE_MAX_SEGMENTS = 8;

export function isRouteTemplate(value) {
  if (typeof value !== "string") return false;
  if (value === "*" || value === "/") return true;
  if (value.length > TEMPLATE_MAX_LENGTH || value[0] !== "/") return false;
  const segments = value.slice(1).split("/");
  return (
    segments.length <= TEMPLATE_MAX_SEGMENTS &&
    segments.every((segment) => TEMPLATE_SEGMENT.test(segment))
  );
}

/** The optional hello fields: `build` (P19) and `want` (P7). */
export function readHelloAdditions(data) {
  const out = {};
  if (typeof data.build === "string" && BUILD.test(data.build)) {
    out.build = data.build;
  }
  if (isInt(data.want, WANT_MIN, WANT_MAX)) out.want = data.want;
  return out;
}

/** How many keys a hello asks for: `want` when it is a valid count, else one. */
export const keysWanted = (message) =>
  isInt(message?.want, WANT_MIN, WANT_MAX) ? message.want : 1;

const isTelemetryCode = (code) =>
  typeof code === "string" && (HTTP_CODE.test(code) || NAMED_CODES.has(code));

function readError(data) {
  if (!TELEMETRY_ERROR_NAMES.includes(data.name)) return null;
  if (!isRouteTemplate(data.routeTemplate)) return null;
  if (typeof data.release !== "string" || !RELEASE.test(data.release)) {
    return null;
  }
  if (!isTelemetryCode(data.code)) return null;
  const out = {
    type: EMBED_ADDED_MESSAGE.telemetry,
    kind: "error",
    name: data.name,
    routeTemplate: data.routeTemplate,
    release: data.release,
    code: data.code,
  };
  // A request id that is not one is dropped, not the report.
  if (typeof data.requestId === "string" && REQUEST_ID.test(data.requestId)) {
    out.requestId = data.requestId;
  }
  return out;
}

function readVital(data) {
  if (!TELEMETRY_VITAL_NAMES.includes(data.name)) return null;
  if (!isRouteTemplate(data.routeTemplate)) return null;
  if (typeof data.release !== "string" || !RELEASE.test(data.release)) {
    return null;
  }
  if (!isInt(data.valueMs, VALUE_MS.minimum, VALUE_MS.maximum)) return null;
  return {
    type: EMBED_ADDED_MESSAGE.telemetry,
    kind: "vital",
    name: data.name,
    routeTemplate: data.routeTemplate,
    release: data.release,
    valueMs: data.valueMs,
  };
}

/**
 * embed.telemetry from the child. Only the two published variants are read;
 * any other kind (a product event, CONS2) is dropped in the browser.
 */
export function readTelemetry(data) {
  if (data?.kind === "error") return readError(data);
  if (data?.kind === "vital") return readVital(data);
  return null;
}

/** embed.badge from the child: the Inbox count, 0-9999. */
export function readBadge(data) {
  return isInt(data?.inbox, INBOX.minimum, INBOX.maximum)
    ? { type: EMBED_ADDED_MESSAGE.badge, inbox: data.inbox }
    : null;
}

/**
 * The parent's embed.key. `left` says how many more keys this answer to one
 * hello carries; it is left out on the last key, which ends the answer.
 */
export function keyMessageWithLeft(token, left) {
  return isInt(left, 1, LEFT_MAX)
    ? { type: "embed.key", token, left }
    : { type: "embed.key", token };
}
