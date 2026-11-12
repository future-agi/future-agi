// omega-embed/1: the message protocol between Future AGI (the parent) and a
// framed Omega surface (Studio, the EE Console). omega-embed-1.json is the
// byte-identical RC1 packages/studio-protocol/openapi/omega-embed-1.json:
// it publishes directional message examples, not a validation schema.
// Host-only bounds live in omega-embed-1.host-additions.json. The EE Console's
// fields/messages (hello.build/want, key.left, telemetry, badge) still come
// from its byte-identical omega-embed-1.additions.json via additions.js.
//
// The document nonce `doc` (its bounds are in the host additions):
//   child  -> parent  embed.hello  { protocol, doc?, build?, want? }
//   child  -> parent  embed.gate   { state, doc? }
//   parent -> child   embed.key    { token, doc?, left? }  (echoes the hello's doc)
// The EE Console sends doc on every hello and gate; Studio sends none yet. A
// doc that is present but not base64url of 16 bytes makes the message invalid.
// Whether a doc is the frame's CURRENT document is the bridge's check.
//
// A cross-origin frame load has no document identity. The host may suspend a
// pre-load hello's doc and send embed.probe {challenge}, a fresh 128-bit nonce.
// A probe-aware child answers embed.proof {protocol, doc, challenge, build?,
// want?, state?}; state is its current authenticated gate, if known. This is
// an additive extension REQUIRED for safe pre-load authentication plus load
// revalidation. The older EE child ignores probes and remains fail-closed.
import fixture from "./omega-embed-1.json";
import hostAdditions from "./omega-embed-1.host-additions.json";
import {
  EMBED_ADDED_MESSAGE,
  keyMessageWithLeft,
  readBadge,
  readHelloAdditions,
  readTelemetry,
} from "./additions";

export const EMBED_PROTOCOL = fixture.protocol;

export const EMBED_MESSAGE = Object.freeze(
  Object.fromEntries(
    [...fixture.childToParent, ...fixture.parentToChild].map(({ type }) => [
      type.slice("embed.".length),
      type,
    ]),
  ),
);

export const EMBED_DOCUMENT_MESSAGE = Object.freeze({
  probe: "embed.probe",
  proof: "embed.proof",
});

export const EMBED_GATE_STATES = Object.freeze(
  fixture.childToParent
    .filter(({ type }) => type === EMBED_MESSAGE.gate)
    .map(({ state }) => state),
);
export const EMBED_THEME_MODES = Object.freeze(
  fixture.parentToChild
    .filter(({ type }) => type === EMBED_MESSAGE.theme)
    .map(({ mode }) => mode),
);

const PATH_MAX_LENGTH = hostAdditions.path.maxLength;
const DOC_PATTERN = new RegExp(hostAdditions.doc.pattern);
const DOC_LENGTH = Math.ceil((hostAdditions.doc.bytes * 8) / 6);
const HELLO_FIELDS = new Set(["type", "protocol", "doc", "build", "want"]);
const GATE_FIELDS = new Set(["type", "state", "doc"]);
const PROOF_FIELDS = new Set([...HELLO_FIELDS, "challenge", "state"]);
const onlyFields = (data, allowed) =>
  Object.keys(data).every((key) => allowed.has(key));
// eslint-disable-next-line no-control-regex
const CONTROL_CHARS = /[\u0000-\u001f\u007f]/;

// A child route is a path such as "/runs/run_1?tab=trace". It must start with
// exactly one "/" so it can never become a protocol-relative or absolute URL.
export function normalizeEmbedPath(path) {
  if (typeof path !== "string") return null;
  if (path.length === 0 || path.length > PATH_MAX_LENGTH) return null;
  if (path[0] !== "/" || path[1] === "/" || path[1] === "\\") return null;
  if (path.includes("\\") || CONTROL_CHARS.test(path)) return null;
  return path;
}

const isRecord = (value) =>
  value !== null && typeof value === "object" && !Array.isArray(value);

/** Whether `value` has the shape of a document nonce. */
export const isEmbedDoc = (value) =>
  typeof value === "string" &&
  value.length === DOC_LENGTH &&
  DOC_PATTERN.test(value);

// The doc of a hello or gate: none (Studio) is no field; a doc that is not
// one is no message (null).
const readDoc = (data) => {
  if (data.doc === undefined) return {};
  return isEmbedDoc(data.doc) ? { doc: data.doc } : null;
};

// Validates a message received from the child. Returns a clean copy that only
// carries the known fields, or null for anything malformed or unknown.
export function parseChildMessage(data) {
  if (!isRecord(data) || typeof data.type !== "string") return null;
  switch (data.type) {
    case EMBED_DOCUMENT_MESSAGE.proof: {
      if (
        !onlyFields(data, PROOF_FIELDS) ||
        !isEmbedDoc(data.doc) ||
        !isEmbedDoc(data.challenge) ||
        (data.state !== undefined && !EMBED_GATE_STATES.includes(data.state))
      ) {
        return null;
      }
      const hello = parseChildMessage({
        type: EMBED_MESSAGE.hello,
        protocol: data.protocol,
        doc: data.doc,
        ...(data.build === undefined ? {} : { build: data.build }),
        ...(data.want === undefined ? {} : { want: data.want }),
      });
      return hello
        ? {
            ...hello,
            type: data.type,
            challenge: data.challenge,
            ...(data.state === undefined ? {} : { state: data.state }),
          }
        : null;
    }
    case EMBED_MESSAGE.hello: {
      if (data.protocol !== EMBED_PROTOCOL) return null;
      const doc = readDoc(data);
      if (!doc) return null;
      const additions = readHelloAdditions(data);
      // Nonce-bearing handshakes fail closed. Preserve Studio's established
      // strip-unknown/ignore-malformed-optional-field behavior without a doc.
      if (
        doc.doc &&
        (!onlyFields(data, HELLO_FIELDS) ||
          (data.build !== undefined && additions.build === undefined) ||
          (data.want !== undefined && additions.want === undefined))
      ) {
        return null;
      }
      return {
        type: data.type,
        protocol: data.protocol,
        ...doc,
        ...additions,
      };
    }
    case EMBED_MESSAGE.route: {
      const path = normalizeEmbedPath(data.path);
      return path ? { type: data.type, path } : null;
    }
    case EMBED_MESSAGE.open:
      return typeof data.href === "string" &&
        data.href.length > 0 &&
        data.href.length <= PATH_MAX_LENGTH
        ? { type: data.type, href: data.href }
        : null;
    case EMBED_MESSAGE.gate: {
      if (!EMBED_GATE_STATES.includes(data.state)) return null;
      const doc = readDoc(data);
      if (!doc || (doc.doc && !onlyFields(data, GATE_FIELDS))) return null;
      return { type: data.type, state: data.state, ...doc };
    }
    case EMBED_ADDED_MESSAGE.telemetry:
      return readTelemetry(data);
    case EMBED_ADDED_MESSAGE.badge:
      return readBadge(data);
    case "embed.score":
      // The published additions permit a non-scoring host to ignore this
      // request and send no embed.scored. This bridge has no viewer-scoped
      // score writer: never forward its free text or claim a score was saved.
      return null;
    default:
      return null;
  }
}

// `left` (CP P7): how many more keys this answer to one hello carries. It is
// left out on a single key and on the last key of a batch. `doc`
// is the doc of the hello this key answers, on every key of the answer; a
// hello without one (Studio) gets the key as before.
export const keyMessage = (token, left, doc) => {
  const message = keyMessageWithLeft(token, left);
  return isEmbedDoc(doc) ? { ...message, doc } : message;
};

export const documentProbeMessage = (challenge) => ({
  type: EMBED_DOCUMENT_MESSAGE.probe,
  challenge,
});

export const themeMessage = (mode) => ({
  type: EMBED_MESSAGE.theme,
  mode: mode === "dark" ? "dark" : "light",
});

export const navigateMessage = (path) => ({
  type: EMBED_MESSAGE.navigate,
  path,
});

// Resolves an embed.open href against the parent's origin and returns the URL
// only when it stays on that origin. Anything else (another site, javascript:,
// data:, a loopback server) is refused.
export function sameOriginUrl(href, parentOrigin) {
  if (typeof href !== "string" || !parentOrigin) return null;
  let url;
  try {
    url = new URL(href, parentOrigin);
  } catch {
    return null;
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") return null;
  return url.origin === parentOrigin ? url : null;
}
